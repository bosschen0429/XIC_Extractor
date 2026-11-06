import csv
import json
from contextlib import contextmanager, nullcontext
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.applications.untargeted.application as application_module
import xic_extractor.applications.untargeted.publication as publication_module
import xic_extractor.output.alignment.pipeline_outputs as output_owner
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.cid_nl_raw_fixture import _FakeRaw, _settings
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.discovery_pipeline_fixtures import (
    _OwnerBoundFakeRawHandle,
    _scan_event,
)
from tests.discovery_pipeline_fixtures import (
    _peak_config as _discovery_peak_config,
)
from tests.discovery_pipeline_fixtures import (
    _settings as _discovery_settings,
)
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.applications.untargeted.application import (
    AlignmentProcessExecutionError,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.execution_profile import UntargetedExecutionPolicy
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.discovery import pipeline as discovery_pipeline
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_records,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.xic_models import XICTrace


def _bind_mock_publication_inputs(completed, kwargs):
    completed.processing_attempt_id = "attempt-test"
    completed.initial_groups = ()
    candidates = kwargs["candidates_by_sample"]
    reference = kwargs.get("known_istd_reference")
    completed.publication_inputs = SimpleNamespace(
        candidates_by_sample=candidates.feature_view(),
        ms1_apex_mz_by_candidate=candidates.ms1_apex_mz_by_candidate,
        alignment_config=kwargs["alignment_config"],
        known_istd_reference_provenance=(
            None if reference is None else reference.explanation_provenance
        ),
    )
    return completed


@pytest.mark.parametrize("writer_fails", (False, True))
@pytest.mark.parametrize("with_reference", (False, True))
@pytest.mark.parametrize("raw_workers", (1, 2))
def test_pure_cid_nl_wrapper_reports_retained_route_and_typed_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_fails: bool,
    with_reference: bool,
    raw_workers: int,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    closed: list[bool] = []

    class MockCompleted:
        application_result = SimpleNamespace(children=())
        processing_request_id = "request-test"
        processing_attempt_id = "attempt-test"

        def close(self) -> None:
            closed.append(True)

    completed = MockCompleted()
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    retained_sample_metadata = (
        SampleMetadata(
            sample_name="S1",
            raw_stem="S1",
            injection_order=4,
            sample_role="blank",
            excluded=True,
            exclusion_reason="operator exclusion",
        ),
    )
    captured_retained: dict[str, object] = {}
    captured_write: dict[str, object] = {}
    reference_provenance = {
        "workbook_sha256": "a" * 64,
        "target_snapshot": '{"definitions": []}',
    }
    reference = SimpleNamespace(
        provenance=reference_provenance,
        explanation_provenance={
            **reference_provenance,
            "calibration": {
                "schema_version": "known_istd_calibration",
                "samples": [],
            },
        },
    )
    monkeypatch.setattr(
        application_module,
        "read_known_istd_reference",
        lambda _path: reference,
    )

    def complete(**kwargs: object) -> object:
        captured_retained.update(kwargs)
        return _bind_mock_publication_inputs(completed, kwargs)

    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        complete,
    )
    monkeypatch.setattr(
        output_owner,
        "project_completed_untarget_batch_writer_inputs",
        lambda value, **_kwargs: (
            writer_inputs if value is completed.application_result else pytest.fail()
        ),
    )
    monkeypatch.setattr(
        output_owner,
        "supports_streamed_untargeted_outputs",
        lambda _outputs: False,
    )

    def write_outputs(*_args: object, **kwargs: object) -> None:
        captured_write.update(kwargs)
        if writer_fails:
            raise OSError("Matrix publication failed")
        matrix_path = tmp_path / "out" / "alignment_matrix.tsv"
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text("Mz\tRT\tS1\n", encoding="utf-8")

    report = tmp_path / "out" / "alignment_explanation.sqlite"

    @contextmanager
    def prepare_explanation(path: Path, **kwargs: object):
        assert kwargs["completed"] is completed
        assert kwargs["known_istd_reference_provenance"] == (
            reference.explanation_provenance
            if with_reference and not writer_fails
            else None
        )

        def finalize(matrix_path: Path) -> Path:
            assert matrix_path.read_text(encoding="utf-8") == "Mz\tRT\tS1\n"
            path.write_text("published", encoding="utf-8")
            return path

        yield SimpleNamespace(consume_child=lambda _child: None, finalize=finalize)

    monkeypatch.setattr(output_owner, "write_outputs_atomic", write_outputs)
    monkeypatch.setattr(
        publication_module, "prepare_alignment_explanation", prepare_explanation
    )

    if writer_fails:
        with pytest.raises(OSError, match="Matrix publication failed"):
            application_module.run_untargeted_application(
                UntargetedRequest(
                    discovery_batch_index=batch_index,
                    raw_dir=tmp_path / "raw",
                    dll_dir=tmp_path / "dll",
                    output_dir=tmp_path / "out",
                    alignment_config=AlignmentConfig(),
                    peak_config=_peak_config(),
                    discovery_settings=_settings(),
                    execution=UntargetedExecutionPolicy(raw_workers=raw_workers),
                    publication=UntargetedPublicationPolicy(
                        emit_alignment_explanation=True
                    ),
                )
            )
        assert not report.exists()
        assert closed == [True]
        return

    application_module.run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            sample_metadata=retained_sample_metadata,
            known_istd_reference_workbook=tmp_path / "reference.xlsx"
            if with_reference
            else None,
            execution=UntargetedExecutionPolicy(raw_workers=raw_workers),
            publication=UntargetedPublicationPolicy(emit_alignment_explanation=True),
        )
    )

    assert report.read_text(encoding="utf-8") == "published"
    assert captured_retained["sample_metadata"] is retained_sample_metadata
    assert captured_retained["raw_workers"] == raw_workers
    assert captured_retained["known_istd_reference"] is (
        reference if with_reference else None
    )
    metadata = captured_write["metadata"]
    assert isinstance(metadata, dict)
    assert metadata.get("known_istd_reference_target_snapshot") == (
        reference.provenance["target_snapshot"] if with_reference else None
    )
    assert closed == [True]


def test_pure_cid_nl_cancellation_after_completion_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    closed: list[bool] = []
    completed = SimpleNamespace(
        close=lambda: closed.append(True),
        application_result=SimpleNamespace(children=()),
    )
    cancellation_checks = iter((False, True))
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: _bind_mock_publication_inputs(completed, kwargs),
    )
    monkeypatch.setattr(
        output_owner,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value: pytest.fail("cancelled run reached writer projection"),
    )
    monkeypatch.setattr(
        output_owner,
        "write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
            ),
            UntargetedExecutionContext(should_cancel=lambda: next(cancellation_checks)),
        )
    assert closed == [True]
    assert not (tmp_path / "out" / "alignment_matrix.tsv").exists()


def test_pure_cid_nl_cancellation_during_projection_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    closed: list[bool] = []
    completed = SimpleNamespace(
        close=lambda: closed.append(True),
        application_result=SimpleNamespace(children=()),
    )
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    cancellation_checks = iter((False, False, True))
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: _bind_mock_publication_inputs(completed, kwargs),
    )

    def project(value: object, **_kwargs: object) -> object:
        assert value is completed.application_result
        return writer_inputs

    monkeypatch.setattr(
        output_owner,
        "project_completed_untarget_batch_writer_inputs",
        project,
    )
    monkeypatch.setattr(
        publication_module,
        "prepare_alignment_explanation",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace(consume_child=None)),
    )
    monkeypatch.setattr(
        output_owner,
        "write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
            ),
            UntargetedExecutionContext(should_cancel=lambda: next(cancellation_checks)),
        )
    assert closed == [True]
    assert not (tmp_path / "out" / "alignment_matrix.tsv").exists()


def test_pure_cid_nl_runtime_failure_uses_alignment_error_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))

    def fail_runtime(**_kwargs: object) -> object:
        raise RuntimeError("retained runtime failed")

    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        fail_runtime,
    )

    with pytest.raises(AlignmentProcessExecutionError, match="retained runtime failed"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
            )
        )


def test_pure_cid_nl_collision_rejects_before_retained_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "alignment_matrix.tsv").write_text("owned\n", encoding="utf-8")
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **_kwargs: pytest.fail("collision reached retained runtime"),
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                execution=UntargetedExecutionPolicy(raw_workers=2),
            )
        )


def test_pure_cid_nl_rejects_invalid_worker_budget_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_workers"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                execution=UntargetedExecutionPolicy(raw_workers=0),
            )
        )


def test_pure_cid_nl_rejects_invalid_worker_backend_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_worker_backend"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                execution=UntargetedExecutionPolicy(
                    raw_worker_backend="invalid_backend"
                ),
            )
        )


def test_pure_cid_nl_projection_failure_publishes_no_owned_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    closed: list[bool] = []
    completed = SimpleNamespace(
        close=lambda: closed.append(True),
        application_result=SimpleNamespace(children=()),
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: _bind_mock_publication_inputs(completed, kwargs),
    )

    def reject_projection(value: object, **_kwargs: object) -> object:
        assert value is completed.application_result
        raise ValueError("invalid finalized projection")

    monkeypatch.setattr(
        output_owner,
        "project_completed_untarget_batch_writer_inputs",
        reject_projection,
    )
    monkeypatch.setattr(
        output_owner,
        "supports_streamed_untargeted_outputs",
        lambda _outputs: False,
    )
    monkeypatch.setattr(
        publication_module,
        "prepare_alignment_explanation",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace(consume_child=None)),
    )

    with pytest.raises(ValueError, match="invalid finalized projection"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
            )
        )

    assert not (output_dir / "alignment_matrix.tsv").exists()
    assert not (output_dir / "alignment_results.xlsx").exists()
    assert not (output_dir / "review_report.html").exists()
    assert closed == [True]


@pytest.mark.parametrize("emit_explanation", (True, False))
@pytest.mark.parametrize("failure", (None, "projection", "matrix"))
def test_pipeline_streams_partition_children_once_and_cleans_up(
    tmp_path,
    monkeypatch,
    failure,
    emit_explanation,
):
    import gc
    import sqlite3

    from tests.untargeted_cid_nl_bounded_runtime_fixtures import _plan
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )
    from xic_extractor.execution.result_store import StreamedResultStore
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.untargeted_execution.execution import (
        execute_untargeted_plan,
    )

    plan, _ = _plan(tmp_path, monkeypatch)
    config = AlignmentConfig(anchor_min_seed_events=1)
    completed = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "runtime",
        alignment_config=config,
        max_partition_jobs=4,
        retain_partition_memory=False,
    )
    assert len(completed.application_result.children) == 3
    candidates = plan.context.candidates_by_sample
    completed = completed.bind_publication_inputs(
        candidates_by_sample=candidates, alignment_config=config
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    matrix_path = output_dir / "alignment_matrix.tsv"
    report_path = output_dir / "alignment_explanation.sqlite"
    has_membership_risk = any(
        decision.accepted_risk is not None and decision.accepted_risk.risk_flags
        for group in completed.initial_groups
        for decision in group.inference_decisions
    )
    reference = tmp_path / "reference.sqlite"

    def rows(path):
        with sqlite3.connect(path) as connection:
            return {
                table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
                for table in (
                    "metadata",
                    "candidates",
                    "records",
                    "links",
                    "curve_payloads",
                )
            }

    if failure is None:
        writer = output_owner.project_completed_untarget_batch_writer_inputs(
            completed.application_result
        )
        write_alignment_matrix_tsv(
            matrix_path,
            writer.matrix,
            production_decisions=writer.production_decisions,
        )
        matrix_bytes = matrix_path.read_bytes()
        write_alignment_explanation(
            reference,
            completed=completed,
            candidates_by_sample=candidates,
            matrix_path=matrix_path,
            alignment_config=config,
        )
        matrix_path.unlink()

    batch_index = _write_batch(tmp_path, tuple(candidates))
    monkeypatch.setattr(
        application_module.DiscoveryInputSource,
        "feature_view",
        lambda _source: candidates,
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **_kwargs: completed,
    )
    restored_ids = set()
    restores = 0
    read_record = StreamedResultStore._read_record

    def live_children():
        gc.collect()
        return sum(
            id(value) in restored_ids
            and type(value) is FinalizedUntargetApplicationResult
            for value in gc.get_objects()
        )

    def counted_read(store, *args):
        nonlocal restores
        # The preceding loop item may still be live; earlier partitions must be gone.
        assert live_children() <= 1
        values = read_record(store, *args)
        for value in values:
            if type(value) is FinalizedUntargetApplicationResult:
                restores += 1
                restored_ids.add(id(value))
        return values

    monkeypatch.setattr(StreamedResultStore, "_read_record", counted_read)

    if failure == "projection":
        project = output_owner.iter_completed_untarget_batch_writer_inputs

        def fail_projection(*args, **kwargs):
            yield from project(*args, **kwargs)
            raise OSError("projection failed")

        monkeypatch.setattr(
            output_owner,
            "iter_completed_untarget_batch_writer_inputs",
            fail_projection,
        )
    elif failure == "matrix":
        from xic_extractor.output.alignment import pipeline_outputs

        write_rows = pipeline_outputs.write_alignment_tsv_row_batches

        def fail_matrix(*args, **kwargs):
            write_rows(*args, **kwargs)
            raise OSError("matrix failed")

        monkeypatch.setattr(
            pipeline_outputs, "write_alignment_tsv_row_batches", fail_matrix
        )

    def run():
        return application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                output_dir=output_dir,
                alignment_config=config,
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                publication=UntargetedPublicationPolicy(
                    emit_alignment_explanation=emit_explanation
                ),
            )
        )

    try:
        if failure is None:
            result = run()
            assert matrix_path.read_bytes() == matrix_bytes
            if not emit_explanation and not has_membership_risk:
                # Nothing consumes the children, so nothing is written for them
                # and the run still releases every one.
                assert not report_path.exists()
                assert result.evidence is None
            else:
                assert result.evidence.report_path == report_path
                assert (
                    result.evidence.processing_attempt_id
                    == completed.processing_attempt_id
                )
                assert rows(report_path) == rows(reference)
                assert read_alignment_explanation(
                    report_path
                ) == read_alignment_explanation(reference)
                for values in candidates.values():
                    for candidate in values:
                        assert read_alignment_explanation(
                            report_path,
                            candidate_id=candidate.candidate_id,
                        ) == read_alignment_explanation(
                            reference, candidate_id=candidate.candidate_id
                        )
        else:
            with pytest.raises(OSError, match=f"{failure} failed"):
                run()
            assert not matrix_path.exists()
            assert not report_path.exists()
        assert restores == 3
        assert not report_path.with_name(report_path.name + ".tmp").exists()
        assert live_children() == 0
    finally:
        completed.close()


@pytest.mark.parametrize("canonical_window, override_window", ((21, 15), (15, 21)))
def test_pipeline_rejects_profile_config_conflict_before_read_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canonical_window: int,
    override_window: int,
) -> None:
    touched = False

    def fail_if_touched(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("entry validation must fail before batch or RAW work")

    monkeypatch.setattr(
        application_module, "complete_untargeted_alignment", fail_if_touched
    )

    with pytest.raises(ValueError, match="conflicts with peak_config"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=tmp_path / "batch.csv",
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=replace(
                    _peak_config(),
                    signal_processing_window_points=canonical_window,
                ),
                discovery_settings=_settings(),
                signal_processing_profile=resolve_signal_processing_profile(
                    signal_processing_window_points=override_window,
                ),
            )
        )

    assert touched is False


@pytest.mark.parametrize("suffix", ("", ".tmp", ".bak"))
def test_pipeline_rejects_owned_output_collision_before_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    sentinel = output_dir / f"alignment_matrix.tsv{suffix}"
    sentinel.write_bytes(b"sentinel")
    opened = False

    def fail_opener(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("RAW must not open after an output collision")

    monkeypatch.setattr(
        application_module, "complete_untargeted_alignment", fail_opener
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=batch_index,
                raw_dir=raw_dir,
                dll_dir=tmp_path / "dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
            )
        )

    assert opened is False
    assert sentinel.read_bytes() == b"sentinel"


@pytest.mark.parametrize(
    "settings", [None, replace(_settings(), tag_combine_mode="intersection")]
)
def test_pipeline_rejects_unsupported_discovery_before_acquisition_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    output_dir = tmp_path / "out"

    def fail_if_touched(*_args, **_kwargs):
        pytest.fail("unsupported discovery reached acquisition or output")

    monkeypatch.setattr(
        application_module, "complete_untargeted_alignment", fail_if_touched
    )
    monkeypatch.setattr(publication_module, "output_paths", fail_if_touched)

    with pytest.raises(ValueError, match="pure single-provider CID-NL"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=tmp_path / "missing-batch.csv",
                raw_dir=tmp_path / "missing-raw",
                dll_dir=tmp_path / "missing-dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=settings,
            )
        )

    assert not output_dir.exists()


def test_known_reference_provenance_survives_sqlite_summary(tmp_path, monkeypatch):
    from tests.alignment_drift_evidence_fixtures import _known_reference_workbook
    from tests.alignment_explanation_fixtures import run_alignment_fixture
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )

    completed, writer, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    arguments = dict(
        completed=completed,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    plain = write_alignment_explanation(tmp_path / "plain.sqlite", **arguments)
    plain_summary = read_alignment_explanation(plain)
    assert plain_summary["known_istd_reference"] is None
    matrix_bytes = matrix.read_bytes()
    reference_path = tmp_path / "reference.xlsx"
    _known_reference_workbook(reference_path)
    provenance = read_known_istd_reference(reference_path).provenance
    assert "definitions" in json.loads(provenance["target_snapshot"])
    report = write_alignment_explanation(
        tmp_path / "reference.sqlite",
        **arguments,
        known_istd_reference_provenance=provenance,
    )
    assert read_alignment_explanation(report) == {
        **plain_summary,
        "known_istd_reference": provenance,
    }
    candidate_id = candidates["S1"][0].candidate_id
    assert read_alignment_explanation(report, candidate_id=candidate_id) == (
        read_alignment_explanation(plain, candidate_id=candidate_id)
    )
    assert matrix.read_bytes() == matrix_bytes


def test_pure_cid_nl_targets_csv_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    targets_csv = tmp_path / "targets.csv"
    targets_csv.write_text(
        "label,mz,expected_rt_min,cid_nl_tag,role,istd_pair\nISTD1,200.0,3,dR,istd,\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").touch()

    closed = []
    completed = SimpleNamespace(
        application_result=SimpleNamespace(children=()),
        close=lambda: closed.append(True),
    )
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    fake_ref = SimpleNamespace(
        provenance={"workbook_sha256": "b" * 64},
        explanation_provenance={
            "workbook_sha256": "b" * 64,
            "calibration": {
                "schema_version": "known_istd_calibration",
                "samples": [],
            },
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        application_module,
        "extract_batch_known_istd_reference",
        lambda **kwargs: fake_ref,
    )
    monkeypatch.setattr(
        application_module,
        "prepare_batch_mass_recalibration",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: (
            captured.update(kwargs) or _bind_mock_publication_inputs(completed, kwargs)
        ),
    )
    monkeypatch.setattr(
        output_owner,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value, **_kwargs: writer_inputs,
    )
    monkeypatch.setattr(
        output_owner,
        "write_outputs_atomic",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        output_owner,
        "supports_streamed_untargeted_outputs",
        lambda _outputs: False,
    )
    monkeypatch.setattr(
        publication_module,
        "prepare_alignment_explanation",
        lambda path, **_kwargs: nullcontext(
            SimpleNamespace(
                consume_child=None,
                finalize=lambda _matrix: path,
            )
        ),
    )
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    application_module.run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            targets_csv=targets_csv,
        )
    )

    assert captured["known_istd_reference"] is fake_ref
    assert closed == [True]


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_reduced_discovery_persistence_keeps_physical_facts_and_qualifies_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_enabled: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    raw_paths = (Path("sample-a.raw"), Path("sample-b.raw"))
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode("utf-8"))
    settings = _discovery_settings(ms2_precursor_tol_da=1.6)
    spec = compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings,
    )
    rt = np.linspace(7.2, 8.4, 25)

    class SelectiveTraceRaw(_OwnerBoundFakeRawHandle):
        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += len(requests)
            return tuple(
                XICTrace.from_arrays(
                    self._rt,
                    self._intensity if request.mz > 600.0 else np.zeros_like(self._rt),
                )
                for request in requests
            )

    discovery_handles = {
        raw_path: SelectiveTraceRaw(
            spec.acquisition_inputs[index].acquisition_source,
            [
                _scan_event(
                    scan_number=scan_number,
                    rt=7.8,
                    precursor_mz=precursor_mz + 1.0,
                    masses=[
                        precursor_mz - 116.0474,
                        precursor_mz + 1.0 - 116.0474,
                    ],
                    intensities=[10_000.0, 12_000.0],
                )
                for scan_number, precursor_mz in ((5, 500.0), (13, 700.0))
            ],
            rt=rt,
            intensity=(
                100.0 + (index + 1) * 500.0 * np.exp(-0.5 * ((rt - 7.8) / 0.08) ** 2)
            ),
        )
        for index, raw_path in enumerate(raw_paths)
    }
    discovery = discovery_pipeline.run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "discovery",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, _dll_dir: discovery_handles[path],
        run_spec=spec,
    )
    batch = read_discovery_batch_index(
        discovery.batch_index_csv,
    )

    def alignment_raw(index: int) -> _FakeRaw:
        raw = _FakeRaw(
            (7.8,),
            scan_precursor_mz=701.0,
            neutral_loss_das=(116.0474,),
        )
        raw.rt = rt
        raw.intensity = 100.0 + (index + 1) * 500.0 * np.exp(
            -0.5 * ((rt - 7.8) / 0.08) ** 2
        )
        raw.scans = tuple(
            replace(
                scan,
                scan_number=13,
                masses=np.asarray((583.9526, 584.9526)),
                intensities=np.asarray((10_000.0, 12_000.0)),
            )
            for scan in raw.scans
        )
        return raw

    install_runtime_raws(
        monkeypatch,
        sources_by_path={
            raw_paths[0].resolve(): alignment_raw(0),
            raw_paths[1].resolve(): alignment_raw(1),
        },
        tmp_path=tmp_path,
    )
    alignment = application_module.run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=discovery.batch_index_csv,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "alignment",
            alignment_config=AlignmentConfig(backfill_enabled=backfill_enabled),
            peak_config=_discovery_peak_config(tmp_path),
            discovery_settings=settings,
            execution=UntargetedExecutionPolicy(
                raw_workers=1, raw_worker_backend="thread"
            ),
            publication=UntargetedPublicationPolicy(
                output_level="machine", emit_alignment_cells=True
            ),
        )
    )

    # Exercise the declared CLI's real application and publication path with
    # the same scientific fixture; recipe/config parsing has its own CLI tests.
    from scripts import run_alignment as cli

    monkeypatch.setattr(
        cli,
        "_resolve_preset",
        lambda _args: (
            SimpleNamespace(name="DNA dR"),
            SimpleNamespace(
                discovery_settings=settings,
                alignment_config=AlignmentConfig(),
            ),
        ),
    )
    monkeypatch.setattr(
        cli, "_peak_config", lambda *_args, **_kwargs: _discovery_peak_config(tmp_path)
    )
    cli_output = tmp_path / "cli-alignment"
    assert (
        cli.main(
            [
                "--discovery-batch-index",
                str(discovery.batch_index_csv),
                "--raw-dir",
                str(tmp_path),
                "--dll-dir",
                str(tmp_path),
                "--output-dir",
                str(cli_output),
                "--preset",
                "dna_dr",
                "--backfill" if backfill_enabled else "--no-backfill",
                "--allow-uncorrected",
                "--output-level",
                "machine",
                "--emit-alignment-cells",
                "--raw-workers",
                "1",
                "--raw-worker-backend",
                "thread",
            ]
        )
        == 0
    )

    metadata = json.loads(
        (cli_output / "alignment_run_metadata.json").read_text(encoding="utf-8")
    )
    assert "mass_recalibration_by_sample" in metadata
    assert metadata["processing_attempt_id"]
    if not backfill_enabled:
        assert metadata["backfill_execution"] == "not_run"
    assert alignment.matrix_tsv is not None
    assert alignment.cells_tsv is not None
    with alignment.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        matrix = tuple(csv.DictReader(handle, delimiter="	"))
    with alignment.cells_tsv.open(encoding="utf-8", newline="") as handle:
        cells = tuple(csv.DictReader(handle, delimiter="	"))
    assert (cli_output / "alignment_matrix.tsv").read_bytes() == (
        alignment.matrix_tsv.read_bytes()
    )
    with (cli_output / "alignment_cells.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        cli_cells = tuple(csv.DictReader(handle, delimiter="\t"))
    value_columns = (
        "sample_stem",
        "source_candidate_id",
        "family_center_mz",
        "status",
        "area",
        "height",
        "apex_rt",
        "peak_start_rt",
        "peak_end_rt",
    )
    assert [tuple(row[column] for column in value_columns) for row in cli_cells] == [
        tuple(row[column] for column in value_columns) for row in cells
    ]
    assert alignment.review_tsv is not None
    assert (cli_output / "alignment_review.tsv").read_bytes() == (
        alignment.review_tsv.read_bytes()
    )
    # Product-derived m/z 700 retains its physical facts below, but supplies no
    # independent NL support. Only selected-precursor m/z 701 reaches Matrix.
    assert [row["Mz"] for row in matrix] == ["701.0000"]
    by_candidate = {row["source_candidate_id"]: row for row in cells}
    assert set(by_candidate) == {
        f"{stem}#13@mz701_p584.9526" for stem in ("sample-a", "sample-b")
    }
    assert {row["status"] for row in cells} == {"detected"}
    # Fixture apex = 100 baseline + (sample index + 1) * 500.
    heights = {
        (row["sample_stem"], row["family_center_mz"]): float(row["height"])
        for row in cells
    }
    assert heights == {
        ("sample-a", "701"): 600.0,
        ("sample-b", "701"): 1100.0,
    }
    areas = {row["sample_stem"]: float(row["area"]) for row in cells}
    assert areas["sample-b"] / areas["sample-a"] == pytest.approx(2.0, rel=0.01)

    assert {
        stem: batch.candidate_receipts[stem].candidate_count
        for stem in batch.sample_order
        if batch.candidate_receipts[stem] is not None
    } == {"sample-a": 3, "sample-b": 3}
    assert {
        output.candidates_csv.parent.name: len(
            json.loads(output.physical_facts_json.read_text(encoding="utf-8"))[
                "records"
            ]
        )
        for output in discovery.per_sample
        if output.physical_facts_json is not None
    } == {"sample-a": 3, "sample-b": 3}
    retained_inferred_areas = {}
    for acquisition, output in zip(
        spec.acquisition_inputs,
        discovery.per_sample,
        strict=True,
    ):
        assert output.physical_facts_json is not None
        assert (
            len(
                read_discovery_physical_fact_records(
                    output.physical_facts_json,
                    expected_run_spec=spec,
                    expected_acquisition_input=acquisition,
                )
            )
            == 3
        )
        with output.candidates_csv.open(encoding="utf-8-sig", newline="") as handle:
            inferred = [
                row
                for row in csv.DictReader(handle)
                if float(row["precursor_mz"]) == 700.0
            ]
        assert len(inferred) == 1
        assert inferred[0]["precursor_mz_basis"] == "product_plus_neutral_loss"
        assert inferred[0]["neutral_loss_mass_error_ppm"] == ""
        retained_inferred_areas[output.candidates_csv.parent.name] = float(
            inferred[0]["ms1_area"]
        )
    assert retained_inferred_areas["sample-a"] > 0.0
    assert retained_inferred_areas["sample-b"] / retained_inferred_areas[
        "sample-a"
    ] == pytest.approx(2.0, rel=0.01)


@pytest.mark.parametrize(
    "filename", ["targets.csv", "targets.xlsx", "targeted-method.xlsx"]
)
@pytest.mark.parametrize(
    "method,tag,count",
    [("dna_dr", "dR", 6), ("rna_ribose", "R", 1), ("rna_mer", "MeR", 0)],
)
def test_mixed_target_list_only_supplies_istds_to_untargeted_alignment(
    tmp_path,
    monkeypatch,
    filename,
    method,
    tag,
    count,
):
    import xic_extractor.acquisition.raw_paths as raw_sources
    from xic_extractor.configuration.targets import read_targets
    from xic_extractor.presets import apply_to_discovery, load_preset
    from xic_extractor.sample_metadata import SampleMetadata

    source = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "configuration"
        / "data"
        / filename
    )
    all_targets = read_targets(source)
    batch_index = _write_batch(tmp_path, ("S1",))
    captured = {}
    metadata = (SampleMetadata("S1", "S1", batch_id="declared-batch"),)

    def extract_reference(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(raw_sources, "existing_raw_paths", lambda **kwargs: {})
    monkeypatch.setattr(
        application_module,
        "extract_batch_known_istd_reference",
        extract_reference,
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        application_module, "publish_untargeted_outputs", lambda *args, **kwargs: None
    )
    application_module.run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=apply_to_discovery(load_preset(method)),
            targets_csv=source,
            sample_metadata=metadata,
        )
    )
    if not count:
        assert not captured
    else:
        assert captured["targets"] == tuple(
            t for t in all_targets if t.is_istd and t.cid_nl_tag == tag
        )
        assert len(captured["targets"]) == count
        assert captured["sample_metadata"] == metadata


def test_alignment_outputs_publish_no_live_workflow_result() -> None:
    """A run that closes its partition store must not hand that store back.

    The formal run sets retain_partition_memory False, so children and
    assessments load through a spool that close() deletes on the way out of
    run_alignment. A returned live result still answers lengths and identity
    after that, and fails only when something reads actual content, so the
    object looks complete while already being unreadable. Callers get the
    durable locator in `evidence` instead.
    """

    field_names = {
        field.name for field in fields(application_module.AlignmentRunOutputs)
    }
    assert "untargeted_cid_nl_result" not in field_names
    assert "evidence" in field_names
