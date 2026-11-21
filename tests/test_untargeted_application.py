import csv
import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.applications.untargeted.application as application_module
import xic_extractor.applications.untargeted.publication as publication_module
import xic_extractor.output.alignment.pipeline_outputs as output_owner
from tests.alignment_explanation_fixtures import explanation_discovery_facts
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.cid_nl_raw_fixture import _FakeRaw, _FakeThermoRaw, _settings
from tests.cid_nl_runtime_fixtures import (
    install_runtime_raws,
    load_plan_inputs,
    representative_precursors,
)
from tests.discovery_pipeline_fixtures import (
    _OwnerBoundFakeRawHandle,
    _scan_event,
)
from tests.discovery_pipeline_fixtures import (
    _settings as _discovery_settings,
)
from tests.untargeted_alignment_entry_fixtures import (
    PUBLISHED_TABLES,
    publish_alignment,
    read_tsv,
    tsv_header,
)
from tests.untargeted_cid_nl_bounded_runtime_fixtures import entry_scenario
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.applications.untargeted.application import (
    AlignmentProcessExecutionError,
)
from xic_extractor.applications.untargeted.istd_reference import (
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedRequest,
)
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    UntargetedExecutionPolicy,
    in_process_execution,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
    serialize_mass_recalibration_result,
)
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.discovery import pipeline as discovery_pipeline
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.xic_models import XICTrace


def _bind_mock_publication_inputs(completed, kwargs):
    completed.processing_attempt_id = "attempt-test"
    completed.initial_groups = ()
    candidates = kwargs["peaks_by_sample"]
    completed.publication_inputs = SimpleNamespace(
        peaks_by_sample=candidates.feature_view(),
        ms1_apex_mz_by_candidate=candidates.ms1_apex_mz_by_candidate,
        precursor_mz_by_candidate={
            cid: location.peak_mz for cid, location in candidates.locations.items()
        },
        neutral_loss_by_tag=kwargs["settings"].neutral_loss_by_tag,
        alignment_config=kwargs["alignment_config"],
    )
    return completed


@pytest.mark.parametrize("with_reference", (False, True))
def test_published_run_follows_known_istd_reference_and_injection_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_reference: bool,
) -> None:
    inputs = entry_scenario(tmp_path, "istd")
    stems = tuple(inputs["paths"])
    workbook = inputs.pop("known_istd_reference_workbook")
    sample_metadata = tuple(
        SampleMetadata(
            sample_name=stem,
            raw_stem=stem,
            injection_order=len(stems) - index,
            sample_role="blank" if index == 0 else "sample",
        )
        for index, stem in enumerate(stems)
    )

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        sample_metadata=sample_metadata,
        known_istd_reference_workbook=workbook if with_reference else None,
        **inputs,
    )

    metadata = json.loads(
        (output / "alignment_run_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata.get("known_istd_reference_target_snapshot") == (
        read_known_istd_reference(workbook).provenance["target_snapshot"]
        if with_reference
        else None
    )
    istd_anchors = [
        row["istd_label"]
        for row in read_tsv(output / "alignment_rt_anchors.tsv")
        if row["kind"] == "istd"
    ]
    assert istd_anchors == (["known"] if with_reference else [])
    assert tsv_header(output / "alignment_matrix.tsv")[2:] == list(reversed(stems))


@pytest.mark.parametrize(
    "completed_stage",
    ("alignment.untargeted_cid_nl_runtime", "alignment.prepare_explanation"),
)
def test_cancellation_after_runtime_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_stage: str,
) -> None:
    inputs = entry_scenario(tmp_path, "positive")
    recorder = TimingRecorder("alignment")

    def cancelled() -> bool:
        return any(record.stage == completed_stage for record in recorder.records)

    with pytest.raises(InterruptedError, match="before output publication"):
        publish_alignment(
            tmp_path,
            monkeypatch,
            should_cancel=cancelled,
            timing_recorder=recorder,
            **inputs,
        )
    assert not any(
        (tmp_path / "alignment" / name).exists() for name in PUBLISHED_TABLES
    )


def test_cancellation_during_raw_reads_is_reported_as_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = entry_scenario(tmp_path, "positive")
    read_sources: list[object] = []
    read = _FakeThermoRaw.GetChromatogramData

    def record_read(self, *args):
        read_sources.append(self.source)
        return read(self, *args)

    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", record_read)

    with pytest.raises(InterruptedError, match="cancelled"):
        publish_alignment(
            tmp_path, monkeypatch, should_cancel=lambda: bool(read_sources), **inputs
        )
    assert read_sources
    assert not any(
        (tmp_path / "alignment" / name).exists() for name in PUBLISHED_TABLES
    )


def test_runtime_failure_surfaces_as_alignment_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = entry_scenario(tmp_path, "positive")
    read = _FakeThermoRaw.GetChromatogramData
    failing = inputs["raws"]["S2"]

    def fail_one_sample(self, *args):
        if self.source is failing:
            raise OSError("injected vendor failure")
        return read(self, *args)

    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", fail_one_sample)
    with pytest.raises(AlignmentProcessExecutionError):
        publish_alignment(tmp_path, monkeypatch, **inputs)
    assert not any(
        (tmp_path / "alignment" / name).exists() for name in PUBLISHED_TABLES
    )


@pytest.mark.parametrize("suffix", ("", ".tmp", ".bak"))
def test_owned_output_collision_rejects_before_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    inputs = entry_scenario(tmp_path, "positive")
    output_dir = tmp_path / "alignment"
    output_dir.mkdir()
    sentinel = output_dir / f"alignment_matrix.tsv{suffix}"
    sentinel.write_bytes(b"sentinel")
    opened: set[str] = set()

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        publish_alignment(tmp_path, monkeypatch, opened_samples=opened, **inputs)

    assert opened == set()
    assert sentinel.read_bytes() == b"sentinel"


def test_pure_cid_nl_projection_failure_publishes_no_owned_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    closed: list[bool] = []
    completed = SimpleNamespace(
        close=lambda: closed.append(True),
        application_result=SimpleNamespace(
            child_outputs=(), has_flagged_backfill_risk=False
        ),
        initial_groups=(),
        sample_rt_frame=None,
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: _bind_mock_publication_inputs(completed, kwargs),
    )

    def reject_projection(_paths: object, value: object, **_kwargs: object) -> int:
        assert value is completed.application_result
        raise ValueError("invalid finalized projection")

    monkeypatch.setattr(output_owner, "write_alignment_tsvs", reject_projection)
    monkeypatch.setattr(
        publication_module,
        "prepare_alignment_explanation",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace(consume_child=None)),
    )

    with pytest.raises(ValueError, match="invalid finalized projection"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                execution=UntargetedExecutionPolicy(profile=in_process_execution()),
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                discovery_settings=_settings(),
            )
        )

    assert not (output_dir / "alignment_matrix.tsv").exists()
    assert not (output_dir / "alignment_results.xlsx").exists()
    assert not (output_dir / "review_report.html").exists()
    assert closed == [True]


@pytest.mark.parametrize("emit_explanation", (True, False))
@pytest.mark.parametrize("failure", (None, "projection", "matrix"))
def test_pipeline_publishes_projected_children_and_cleans_up(
    tmp_path,
    monkeypatch,
    failure,
    emit_explanation,
):
    import gc
    import sqlite3

    from tests.untargeted_cid_nl_bounded_runtime_fixtures import _plan
    from tests.untargeted_partition_fixtures import (
        ALL_ROW_OUTPUTS,
        children_of,
        project_composed_child,
    )
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )
    from xic_extractor.applications.untargeted.alignment_explanation_projection import (
        write_alignment_explanation,
    )
    from xic_extractor.execution.result_store import StreamedResultStore
    from xic_extractor.output.alignment.tsv_writer import (
        UntargetChildOutputs,
        write_alignment_tsvs,
    )
    from xic_extractor.untargeted_execution.execution import (
        execute_untargeted_plan,
    )

    plan, _ = _plan(tmp_path, monkeypatch)
    config = AlignmentConfig(anchor_min_seed_events=1)
    candidates = load_plan_inputs(plan)

    def execute(name, project_child):
        return execute_untargeted_plan(
            plan,
            output_dir=tmp_path / name,
            alignment_config=config,
            max_partition_jobs=4,
            retain_partition_memory=False,
            project_child=project_child,
            execution=in_process_execution(),
        ).bind_publication_inputs(
            peaks_by_sample=candidates,
            precursor_mz_by_candidate=representative_precursors(candidates),
            neutral_loss_by_tag=plan.context.settings.neutral_loss_by_tag,
            alignment_config=config,
        )

    expected = execute("expected", project_composed_child)
    completed = execute(
        "runtime",
        publication_module.untargeted_child_projection(
            ALL_ROW_OUTPUTS, UntargetedPublicationPolicy()
        ),
    )
    assert len(completed.application_result.child_outputs) == 3
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
            tables = [
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            return {
                table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
                for table in tables
            }

    if failure is None:
        write_alignment_tsvs({"matrix": matrix_path}, expected.application_result)
        matrix_bytes = matrix_path.read_bytes()
        write_alignment_explanation(
            reference,
            completed=expected,
            children=children_of(expected.application_result),
            **explanation_discovery_facts(expected),
            peaks_by_sample=candidates,
            matrix_path=matrix_path,
            alignment_config=config,
        )
        matrix_path.unlink()
    expected.close()

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

    def live_outputs():
        gc.collect()
        return sum(
            id(value) in restored_ids and type(value) is UntargetChildOutputs
            for value in gc.get_objects()
        )

    def counted_read(store, *args):
        nonlocal restores
        assert live_outputs() <= 1
        values = read_record(store, *args)
        for value in values:
            assert type(value) is not FinalizedUntargetApplicationResult
            if type(value) is UntargetChildOutputs:
                restores += 1
                restored_ids.add(id(value))
        return values

    monkeypatch.setattr(StreamedResultStore, "_read_record", counted_read)

    write_tsvs = output_owner.write_alignment_tsvs
    if failure == "projection":

        def fail_projection(paths, value, **kwargs):
            consume = kwargs.get("consume_child")
            consumed = 0

            def consume_then_fail(child, first_group):
                nonlocal consumed
                if consume is not None:
                    consume(child, first_group)
                consumed += 1
                if consumed == len(value.child_outputs):
                    raise OSError("projection failed")

            return write_tsvs(
                paths, value, **{**kwargs, "consume_child": consume_then_fail}
            )

        monkeypatch.setattr(output_owner, "write_alignment_tsvs", fail_projection)
    elif failure == "matrix":

        def fail_matrix(*args, **kwargs):
            write_tsvs(*args, **kwargs)
            raise OSError("matrix failed")

        monkeypatch.setattr(output_owner, "write_alignment_tsvs", fail_matrix)

    def run():
        return application_module.run_untargeted_application(
            UntargetedRequest(
                execution=UntargetedExecutionPolicy(profile=in_process_execution()),
                discovery_batch_index=batch_index,
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                output_dir=output_dir,
                alignment_config=config,
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
                            candidate_id=candidate.peak_id,
                        ) == read_alignment_explanation(
                            reference, candidate_id=candidate.peak_id
                        )
        else:
            with pytest.raises(OSError, match=f"{failure} failed"):
                run()
            assert not matrix_path.exists()
            assert not report_path.exists()
        assert restores == 3
        assert not report_path.with_name(report_path.name + ".tmp").exists()
        assert live_outputs() == 0
    finally:
        completed.close()


def test_pipeline_rejects_unsupported_profile_before_read_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = False

    def fail_if_touched(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("entry validation must fail before batch or RAW work")

    monkeypatch.setattr(
        application_module, "complete_untargeted_alignment", fail_if_touched
    )

    with pytest.raises(ValueError, match="does not support legacy Alignment"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                execution=UntargetedExecutionPolicy(profile=in_process_execution()),
                discovery_batch_index=tmp_path / "batch.csv",
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                discovery_settings=_settings(),
                signal_processing_profile=resolve_signal_processing_profile(
                    signal_processing_window_points=21,
                ),
            )
        )

    assert touched is False


@pytest.mark.parametrize(
    "settings", [None, replace(_settings(), tag_combine_mode="intersection")]
)
def test_pipeline_rejects_unsupported_discovery_before_acquisition_or_output(
    tmp_path: Path,
    settings,
) -> None:
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="pure single-provider CID-NL"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                execution=UntargetedExecutionPolicy(profile=in_process_execution()),
                discovery_batch_index=tmp_path / "missing-batch.csv",
                raw_dir=tmp_path / "missing-raw",
                dll_dir=tmp_path / "missing-dll",
                output_dir=output_dir,
                alignment_config=AlignmentConfig(),
                discovery_settings=settings,
            )
        )

    assert not output_dir.exists()


def test_pure_cid_nl_targets_csv_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    targets_csv = tmp_path / "targets.csv"
    targets_csv.write_text(
        "label,mz,expected_rt_min,cid_nl_loss_da,role,istd_pair\n"
        "ISTD1,200.0,3,116.0474,istd,\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").touch()

    closed = []
    completed = SimpleNamespace(
        application_result=SimpleNamespace(
            child_outputs=(), has_flagged_backfill_risk=False
        ),
        initial_groups=(),
        sample_rt_frame=None,
        close=lambda: closed.append(True),
    )
    fake_ref = SimpleNamespace(provenance={"workbook_sha256": "b" * 64})
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        application_module,
        "extract_batch_known_istd_reference",
        lambda **kwargs: fake_ref,
    )
    monkeypatch.setattr(
        application_module,
        "complete_untargeted_alignment",
        lambda **kwargs: (
            captured.update(kwargs) or _bind_mock_publication_inputs(completed, kwargs)
        ),
    )
    monkeypatch.setattr(
        publication_module,
        "write_completed_untargeted_outputs_atomic",
        lambda *_args, **_kwargs: 0,
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
            execution=UntargetedExecutionPolicy(profile=in_process_execution()),
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            discovery_settings=replace(
                _settings(), mass_recalibration=MassRecalibration.NONE
            ),
            targets_csv=targets_csv,
        )
    )

    assert captured["known_istd_reference"] is fake_ref
    assert closed == [True]


def test_alignment_applies_discovery_mass_result_without_reopening_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    raw_path = batch_index.parent / "S1" / "owner_closed_acquisition.raw"
    discovery_result = MassRecalibrationResult(
        "applied",
        policy=MassRecalibration.EASY_IC_RESTORE,
        baseline_lm=1.25,
        n_anchor=7,
    )
    with batch_index.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        rows = list(reader)
    rows[0]["raw_file"] = str(raw_path)
    rows[0]["mass_recalibration"] = serialize_mass_recalibration_result(
        discovery_result
    )
    with batch_index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(rows)

    captured: dict[str, object] = {}

    def stop_after_capture(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("captured")

    monkeypatch.setattr(
        application_module, "complete_untargeted_alignment", stop_after_capture
    )

    with pytest.raises(AlignmentProcessExecutionError, match="captured"):
        application_module.run_untargeted_application(
            UntargetedRequest(
                execution=UntargetedExecutionPolicy(profile=in_process_execution()),
                discovery_batch_index=batch_index,
                raw_dir=raw_path.parent,
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                discovery_settings=_settings(),
            )
        )

    prepared = captured["mass_recalibration_by_sample"]["S1"]
    receipt = read_discovery_batch_index(batch_index).peak_receipts["S1"]
    assert prepared.result == discovery_result
    assert prepared.acquisition.source == receipt.acquisition_source
    assert (
        prepared.acquisition.path_identity
        == receipt.acquisition_source.local_file_revision
    )


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_reduced_discovery_persistence_keeps_inferred_peak_and_qualifies_alignment(
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
        execution=in_process_execution(),
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
            discovery_settings=settings,
            execution=UntargetedExecutionPolicy(
                profile=ExecutionProfile(alignment_workers=1, backend="thread")
            ),
            publication=UntargetedPublicationPolicy(
                output_level="machine", emit_alignment_cells=True
            ),
        )
    )

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
        "feature_id",
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
    assert [row["Mz"] for row in matrix] == ["701.0000"]
    by_candidate = {row["source_candidate_id"]: row for row in cells}
    assert set(by_candidate) == {
        f"{stem}@mz701_rt7.8000" for stem in ("sample-a", "sample-b")
    }
    assert {row["status"] for row in cells} == {"detected"}
    heights = {row["sample_stem"]: float(row["height"]) for row in cells}
    assert heights == {"sample-a": 600.0, "sample-b": 1100.0}
    areas = {row["sample_stem"]: float(row["area"]) for row in cells}
    assert areas["sample-b"] / areas["sample-a"] == pytest.approx(2.0, rel=0.01)

    assert {
        stem: batch.peak_receipts[stem].peak_count
        for stem in batch.sample_order
        if batch.peak_receipts[stem] is not None
    } == {"sample-a": 2, "sample-b": 2}
    for output in discovery.per_sample:
        with output.events_csv.open(encoding="utf-8-sig", newline="") as handle:
            (inferred_event,) = [
                row
                for row in csv.DictReader(handle)
                if float(row["precursor_mz"]) == 700.0
            ]
        assert inferred_event["neutral_loss_mass_error_ppm"] == ""
        with output.peaks_csv.open(encoding="utf-8-sig", newline="") as handle:
            inferred = [
                row
                for row in csv.DictReader(handle)
                if row["peak_id"] == inferred_event["peak_id"]
            ]
        assert len(inferred) == 1
        assert inferred[0]["precursor_mz_basis"] == "product_plus_neutral_loss"
        assert (
            float(inferred[0]["ms1_search_rt_min"])
            <= float(inferred[0]["query_rt_min"])
            <= float(inferred[0]["ms1_search_rt_max"])
        )


@pytest.mark.parametrize(
    "method,loss,count",
    [("dna_dr", 116.0474, 6), ("rna_ribose", 132.0423, 1), ("rna_mer", 146.0579, 0)],
)
def test_mixed_target_list_only_supplies_istds_to_untargeted_alignment(
    tmp_path,
    monkeypatch,
    method,
    loss,
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
        / "targets.xlsx"
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
            execution=UntargetedExecutionPolicy(profile=in_process_execution()),
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            discovery_settings=apply_to_discovery(load_preset(method)),
            targets_csv=source,
            sample_metadata=metadata,
        )
    )
    if not count:
        assert not captured
    else:
        assert captured["targets"] == tuple(
            t for t in all_targets if t.is_istd and t.neutral_loss_da == loss
        )
        assert len(captured["targets"]) == count
        assert captured["sample_metadata"] == metadata
