from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.test_targeted_real_data_adapter import _settings
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.process_execution import AlignmentProcessExecutionError
from xic_extractor.peak_detection.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_pure_single_provider_cid_nl_dispatches_with_route_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    expected = pipeline_module.AlignmentRunOutputs(
        matrix_tsv=tmp_path / "out" / "alignment_matrix.tsv"
    )
    calls: list[dict[str, object]] = []

    def run_cid_nl(**kwargs: object):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        run_cid_nl,
    )

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        owner_build_xic_backend="raw_superwindow",
    )

    assert result is expected
    assert len(calls) == 1
    assert calls[0]["settings"] == _settings()
    assert calls[0]["outputs"] == expected
    assert calls[0]["owner_build_route_receipt"].as_dict() == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }


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
        "content_sha256": "b" * 64,
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
        pipeline_module,
        "read_known_istd_reference",
        lambda _path: reference,
    )

    def complete(**kwargs: object) -> object:
        captured_retained.update(kwargs)
        return completed

    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        complete,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda value, **_kwargs: writer_inputs if value is completed else pytest.fail(),
    )
    monkeypatch.setattr(
        pipeline_module, "supports_streamed_untargeted_outputs", lambda _outputs: False
    )

    def write_outputs(*_args: object, **kwargs: object) -> None:
        captured_write.update(kwargs)
        if writer_fails:
            raise OSError("Matrix publication failed")
        matrix_path = tmp_path / "out" / "alignment_matrix.tsv"
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text("Mz\tRT\tS1\n", encoding="utf-8")

    report = tmp_path / "out" / "cid_nl_explanation.sqlite"

    @contextmanager
    def prepare_explanation(path: Path, **kwargs: object):
        assert kwargs["completed"] is completed
        assert kwargs["known_istd_reference_provenance"] == (
            reference.explanation_provenance
            if with_reference and not writer_fails
            else None
        )

        def finalize(row_count: int, matrix_path: Path) -> Path:
            assert row_count == len(writer_inputs.matrix.clusters)
            assert matrix_path.read_text(encoding="utf-8") == "Mz\tRT\tS1\n"
            path.write_text("published", encoding="utf-8")
            return path

        yield SimpleNamespace(consume_child=lambda _child: None, finalize=finalize)

    monkeypatch.setattr(pipeline_module, "_write_outputs_atomic", write_outputs)
    monkeypatch.setattr(
        pipeline_module, "prepare_alignment_explanation", prepare_explanation
    )

    if writer_fails:
        with pytest.raises(OSError, match="Matrix publication failed"):
            pipeline_module.run_alignment(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                emit_cid_nl_explanation=True,
                raw_workers=raw_workers,
            )
        assert not report.exists()
        assert closed == [True]
        return

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        emit_cid_nl_explanation=True,
        owner_build_xic_backend="raw_superwindow",
        raw_workers=raw_workers,
        sample_metadata=retained_sample_metadata,
        known_istd_reference_workbook=(
            tmp_path / "reference.xlsx" if with_reference else None
        ),
    )

    assert report.read_text(encoding="utf-8") == "published"
    assert result.untargeted_cid_nl_result is completed
    assert captured_retained["sample_metadata"] is retained_sample_metadata
    assert captured_retained["raw_workers"] == raw_workers
    assert captured_retained["known_istd_reference"] is (
        reference if with_reference else None
    )
    assert result.owner_build_route_receipt is not None
    assert result.owner_build_route_receipt.as_dict() == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }
    metadata = captured_write["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["owner_build_xic_backend"] == "raw_superwindow"
    assert metadata["owner_build_xic_backend_role"] == "configured_legacy_option"
    assert metadata["effective_owner_build_route"] == (
        "retained_untargeted_cid_nl_batch"
    )
    assert metadata["effective_owner_build_xic_backend"] == "none"
    assert metadata.get("known_istd_reference_content_sha256") == (
        reference.provenance["content_sha256"] if with_reference else None
    )
    assert closed == [True]


def test_pure_cid_nl_cancellation_after_completion_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    completed = object()
    cancellation_checks = iter((False, True))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value: pytest.fail("cancelled run reached writer projection"),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            should_cancel=lambda: next(cancellation_checks),
        )


def test_pure_cid_nl_cancellation_during_projection_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    completed = object()
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    cancellation_checks = iter((False, False, True))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )

    def project(value: object, **_kwargs: object) -> object:
        assert value is completed
        return writer_inputs

    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        project,
    )
    monkeypatch.setattr(
        pipeline_module,
        "prepare_alignment_explanation",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace(consume_child=None)),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            should_cancel=lambda: next(cancellation_checks),
        )


def test_pure_cid_nl_runtime_failure_uses_alignment_error_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))

    def fail_runtime(**_kwargs: object) -> object:
        raise RuntimeError("retained runtime failed")

    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        fail_runtime,
    )

    with pytest.raises(AlignmentProcessExecutionError, match="retained runtime failed"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
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
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("collision reached retained runtime"),
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_workers=2,
        )


def test_pure_cid_nl_rejects_invalid_worker_budget_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_workers"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_workers=0,
        )


def test_pure_cid_nl_rejects_invalid_worker_backend_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_worker_backend"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_worker_backend="invalid_backend",
        )


def test_pure_cid_nl_projection_failure_publishes_no_owned_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    completed = object()
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )

    def reject_projection(value: object, **_kwargs: object) -> object:
        assert value is completed
        raise ValueError("invalid finalized projection")

    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        reject_projection,
    )
    monkeypatch.setattr(
        pipeline_module, "supports_streamed_untargeted_outputs", lambda _outputs: False
    )
    monkeypatch.setattr(
        pipeline_module,
        "prepare_alignment_explanation",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace(consume_child=None)),
    )

    with pytest.raises(ValueError, match="invalid finalized projection"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )

    assert not (output_dir / "alignment_matrix.tsv").exists()
    assert not (output_dir / "alignment_results.xlsx").exists()
    assert not (output_dir / "review_report.html").exists()


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

    from tests.runtime_telemetry_fixtures import (
        build_runtime_telemetry_samples_from_coordinator,
    )
    from tests.test_untargeted_cid_nl_bounded_runtime import _plan
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.diagnostics.alignment_explanation import (
        read_alignment_explanation,
    )
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )
    from xic_extractor.execution_engine.streamed_result_store import StreamedResultStore
    from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        execute_bounded_untargeted_cid_nl,
    )

    plan, _ = _plan(tmp_path, monkeypatch)
    config = AlignmentConfig(anchor_min_seed_events=1)
    completed = execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "runtime",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "output-test"
        ),
        max_partition_jobs=4,
        retain_partition_memory=False,
    )
    assert len(completed.application_result.children) == 3
    candidates = plan.context.candidates_by_sample
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    matrix_path = output_dir / "alignment_matrix.tsv"
    report_path = output_dir / "cid_nl_explanation.sqlite"
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
        writer = pipeline_module.project_completed_untarget_batch_writer_inputs(
            completed
        )
        write_alignment_matrix_tsv(
            matrix_path,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        matrix_bytes = matrix_path.read_bytes()
        write_alignment_explanation(
            reference,
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix_path,
            alignment_config=config,
        )
        matrix_path.unlink()

    batch_index = _write_batch(tmp_path, tuple(candidates))
    monkeypatch.setattr(
        pipeline_module.DiscoveryInputSource,
        "feature_view",
        lambda _source: candidates,
    )
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
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
        project = pipeline_module.iter_completed_untarget_batch_writer_inputs

        def fail_projection(*args, **kwargs):
            yield from project(*args, **kwargs)
            raise OSError("projection failed")

        monkeypatch.setattr(
            pipeline_module,
            "iter_completed_untarget_batch_writer_inputs",
            fail_projection,
        )
    elif failure == "matrix":
        from xic_extractor.alignment import pipeline_outputs

        write_rows = pipeline_outputs.write_alignment_tsv_row_batches

        def fail_matrix(*args, **kwargs):
            write_rows(*args, **kwargs)
            raise OSError("matrix failed")

        monkeypatch.setattr(
            pipeline_outputs, "write_alignment_tsv_row_batches", fail_matrix
        )

    def run():
        return pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=output_dir,
            alignment_config=config,
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            emit_cid_nl_explanation=emit_explanation,
        )

    try:
        if failure is None:
            result = run()
            assert matrix_path.read_bytes() == matrix_bytes
            if not emit_explanation:
                # Nothing consumes the children, so nothing is written for them
                # and the run still releases every one.
                assert not report_path.exists()
                assert result.evidence is None
            else:
                assert result.evidence.report_path == report_path
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


def test_pipeline_rejects_profile_config_conflict_before_read_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = False

    def fail_if_touched(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("entry validation must fail before batch or RAW work")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_if_touched
    )

    with pytest.raises(ValueError, match="conflicts with peak_config"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=replace(
                _peak_config(),
                signal_processing_window_points=21,
            ),
            discovery_settings=_settings(),
            signal_processing_profile=resolve_signal_processing_profile(
                signal_processing_window_points=15,
            ),
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
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_opener
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
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
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_if_touched
    )
    monkeypatch.setattr(pipeline_module, "_output_paths", fail_if_touched)

    with pytest.raises(ValueError, match="pure single-provider CID-NL"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "missing-batch.csv",
            raw_dir=tmp_path / "missing-raw",
            dll_dir=tmp_path / "missing-dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=settings,
        )

    assert not output_dir.exists()


def test_known_reference_provenance_survives_sqlite_summary(tmp_path, monkeypatch):
    from tests.test_alignment_drift_evidence import _known_reference_workbook
    from tests.test_cid_nl_explanation import _owner_run
    from xic_extractor.alignment.drift_evidence import read_known_istd_reference
    from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    arguments = dict(
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    plain = write_cid_nl_explanation(tmp_path / "plain.sqlite", **arguments)
    plain_summary = read_cid_nl_explanation(plain)
    assert "known_istd_reference" not in plain_summary
    matrix_bytes = matrix.read_bytes()
    reference_path = tmp_path / "reference.xlsx"
    _known_reference_workbook(reference_path)
    provenance = read_known_istd_reference(reference_path).provenance
    assert "content_sha256" in provenance
    report = write_cid_nl_explanation(
        tmp_path / "reference.sqlite",
        **arguments,
        known_istd_reference_provenance=provenance,
    )
    assert read_cid_nl_explanation(report) == {
        **plain_summary,
        "known_istd_reference": provenance,
    }
    candidate_id = candidates["S1"][0].candidate_id
    assert read_cid_nl_explanation(report, candidate_id=candidate_id) == (
        read_cid_nl_explanation(plain, candidate_id=candidate_id)
    )
    assert matrix.read_bytes() == matrix_bytes


def test_pure_cid_nl_targets_csv_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    targets_csv = tmp_path / "targets.csv"
    targets_csv.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max,is_istd,istd_pair\n"
        "ISTD1,200.0,1.0,5.0,10.0,100.0,10.0,20.0,true,\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").touch()

    completed = object()
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
        pipeline_module,
        "extract_batch_known_istd_reference",
        lambda **kwargs: fake_ref,
    )
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **kwargs: captured.update(kwargs) or completed,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value, **_kwargs: writer_inputs,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline_module, "supports_streamed_untargeted_outputs", lambda _outputs: False
    )
    monkeypatch.setattr(
        pipeline_module,
        "prepare_alignment_explanation",
        lambda path, **_kwargs: nullcontext(
            SimpleNamespace(
                consume_child=None,
                finalize=lambda _inputs, _matrix: path,
            )
        ),
    )
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        targets_csv=targets_csv,
    )

    assert result.untargeted_cid_nl_result is completed
    assert captured["known_istd_reference"] is fake_ref


def test_mixed_target_list_only_supplies_istds_to_untargeted_alignment(
    tmp_path,
    monkeypatch,
):
    import xic_extractor.alignment.raw_sources as raw_sources
    from xic_extractor.configuration.targets import read_targets

    source = Path(__file__).parents[1] / "config" / "targets.example.csv"
    all_targets = read_targets(source)
    batch_index = _write_batch(tmp_path, ("S1",))
    captured = {}

    def extract_reference(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(raw_sources, "existing_raw_paths", lambda **kwargs: {})
    monkeypatch.setattr(
        pipeline_module,
        "extract_batch_known_istd_reference",
        extract_reference,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        lambda **kwargs: None,
    )
    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        targets_csv=source,
    )
    assert captured["targets"] == tuple(t for t in all_targets if t.is_istd)
    assert len(captured["targets"]) < len(all_targets)
