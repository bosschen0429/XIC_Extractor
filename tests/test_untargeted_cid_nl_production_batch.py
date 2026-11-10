"""CID-NL atomic execution, completion and matrix publication."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.cid_nl_completion as cid_nl_completion_owner
import xic_extractor.untargeted_execution.alignment as alignment_adapter
from tests.alignment_pipeline_helpers import (
    render,
    written_cells,
    written_groups,
    written_rows,
    written_source_candidates,
)
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raw,
    install_runtime_raws,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _assert_conservative_coverage_handoff,
    _assert_partition_composition_parity,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
    fixture_assessment,
)
from xic_extractor.alignment.cell_decision.owner import (
    NoWriteCellDecision,
    NumericCellDecision,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.models import (
    DiscoveryPeak,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import (
    alignment_metadata,
    output_paths,
    write_completed_untargeted_outputs_atomic,
)
from xic_extractor.output.alignment.tsv_writer import (
    completed_untarget_batch_sample_order,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionError,
    build_sample_jobs,
)


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_completed_empty_recovery_preserves_each_compatible_source_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_enabled: bool,
) -> None:
    physical_records = {}
    stems = ("SupportA1", "SupportA2", "SupportB1", "SupportB2", "Missing")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"compatible-source-subject:{raw_path.stem}".encode())
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    peak_config = _peak_config(tmp_path)
    source_raws = {}
    selected_candidates = {}
    for raw_path, precursor_mz in zip(
        raw_paths[:4], (500.0, 500.0, 500.0105, 500.0105), strict=True
    ):
        source_raw = _FakeRaw(
            (7.98,),
            scan_precursor_mz=precursor_mz,
            neutral_loss_das=(116.0474,),
        )
        candidates = _current_candidates(
            source_raw,
            raw_file=raw_path,
            settings=settings,
            physical_records=physical_records,
        )
        source_raws[raw_path] = source_raw
        selected_candidates[raw_path.stem] = candidates[0]
    missing_raw = _FakeRaw((7.98,))
    missing_raw.intensity[:] = 0.0
    source_raws[raw_paths[4]] = missing_raw
    peaks_by_sample = {stem: (selected_candidates[stem],) for stem in stems[:4]}
    peaks_by_sample["Missing"] = ()
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=peaks_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            peaks_by_sample,
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (peaks_by_sample).values()
            for c in values
        },
    )
    assert len(batch.subject_batch.subjects) == 2
    assert (
        len({subject.method_subject_id for subject in batch.subject_batch.subjects})
        == 2
    )
    install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    alignment_config = AlignmentConfig(
        anchor_min_seed_events=1,
        product_mz_tolerance_ppm=30.0,
        backfill_enabled=backfill_enabled,
    )
    runtime = execute_dense_partition(
        batch,
        alignment_config=alignment_config,
    )
    assert runtime.succeeded
    completed = complete_dense_partition(
        batch,
        runtime,
        alignment_config=alignment_config,
    )

    (child,) = completed.application_result.children
    (group,) = child.scientific_core.final_population
    source_subject_ids = {
        source.method_assessment.method_subject.method_subject_id
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
        if source.method_result == "SUPPORT"
    }
    assert source_subject_ids == {
        subject.method_subject_id for subject in batch.subject_batch.subjects
    }
    position = next(
        position
        for position in (
            child.scientific_core.cell_observation_handoff.coverage.positions
        )
        if position.sample.raw_stem == "Missing"
        and position.group.group_hypothesis_id == group.group_hypothesis_id
    )
    if backfill_enabled:
        assert position.no_request_decision is not None
        assert position.no_request_decision.variant == "completed_backfill_empty"
    else:
        assert not any(item.backfill_attempts for item in runtime.sample_results)
        assert position.no_request_decision is None
        assert position.planning_reason == "no_discovery_input"
        missing = next(
            cell
            for cell in child.scientific_core.cell_observations
            if cell.canonical_sample.raw_stem == "Missing"
        )
        assert missing.evidence_state == "not_searched"
        assert not missing.direct_hypotheses and not missing.integrations
        assert all(ref.method_result != "SUPPORT" for ref in missing.method_assessments)
        writer = completed.application_result
        destination = tmp_path / "no-backfill-matrix.tsv"
        render("matrix", destination, writer)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        assert rows and all(row["Missing"] == "" for row in rows)
        assert all(float(rows[0][stem]) > 0 for stem in stems[:4])
        from xic_extractor.alignment_explanation.reader import (
            read_alignment_explanation,
        )
        from xic_extractor.diagnostics.alignment_explanation_projection import (
            write_alignment_explanation,
        )

        report = write_alignment_explanation(
            tmp_path / "no-input-review.sqlite",
            completed=completed,
            peaks_by_sample=peaks_by_sample,
            matrix_path=destination,
            alignment_config=alignment_config,
        )
        detail = read_alignment_explanation(
            report, candidate_id=selected_candidates[stems[0]].peak_id
        )
        missing_queries = [
            query
            for group in detail["records"]["final_groups"]
            for query in group["xic_extractions"]
            if query["sample_key"] == missing.sample_key
        ]
        assert len(missing_queries) == 1
        assert missing_queries[0]["group_position"] is True
        assert missing_queries[0]["rt_apex_min"] == pytest.approx(7.98, abs=0.02)
        assert missing_queries[0]["ppm"] == settings.precursor_mz_tolerance_ppm


def test_sample_job_builder_binds_shared_inputs_to_each_subject(
    tmp_path: Path,
) -> None:
    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
    )
    batch = build_dense_partition(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample={"S1": candidates},
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, (raw_path,), {"S1": candidates}
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in ({"S1": candidates}).values()
            for c in values
        },
    )
    first = batch.jobs[0]
    shared = {
        item.name: getattr(first, item.name)
        for item in fields(first)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    work_units = tuple((job.method_subject, job.work_unit_key) for job in batch.jobs)
    assert len(work_units) == 2
    assert build_sample_jobs(**shared, subject_work_units=work_units) == batch.jobs
    for subject, key, message in (
        (None, "changed-subject", "wrong owner type"),
        (work_units[1][0], "", "work_unit_key"),
    ):
        with pytest.raises(UntargetedPartitionError, match=message):
            build_sample_jobs(
                **shared, subject_work_units=(work_units[0], (subject, key))
            )


def test_alignment_adapter_prepares_and_executes_bounded_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "S1.raw"
    raw_path.write_bytes(b"alignment-adapter:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    receipts = build_discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": candidates},
    )
    discovery_batch = DiscoveryBatchInput(
        sample_order=("S1",),
        peak_csvs={"S1": tmp_path / "S1.csv"},
        raw_files={"S1": raw_path},
        event_csvs={"S1": None},
        peak_receipts=receipts,
    )
    built = object()
    application_result = object()

    class MockCompleted:
        def __init__(self):
            self.application_result = application_result

        def bind_publication_inputs(self, **kwargs):
            self.publication_inputs = SimpleNamespace(**kwargs)
            return self

        def with_discovery_inputs(self, prepared):
            self.discovery_inputs = prepared
            return self

        def close(self):
            self.discovery_inputs.close()

    completed = MockCompleted()
    calls: list[tuple[str, object]] = []

    def build(**kwargs: object) -> object:
        calls.append(("build", kwargs))
        return built

    def execute(batch: object, **kwargs: object) -> object:
        assert batch is built
        calls.append(("execute", kwargs))
        return completed

    monkeypatch.setattr(
        alignment_adapter,
        "prepare_eager_untargeted_plan",
        build,
    )
    monkeypatch.setattr(
        alignment_adapter,
        "execute_untargeted_plan",
        execute,
    )

    monkeypatch.setattr(
        alignment_adapter,
        "_load_physical_replay_inputs",
        lambda **kwargs: (kwargs["peaks_by_sample"], {}, None),
    )

    result = alignment_adapter.complete_untargeted_alignment(
        signal_processing_profile=TEST_PROFILE,
        discovery_batch=discovery_batch,
        peaks_by_sample={"S1": candidates},
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=peak_config,
        alignment_config=AlignmentConfig(),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=7,
                sample_role="blank",
                excluded=True,
                exclusion_reason="operator exclusion",
            ),
        ),
    )

    assert result.application_result is application_result
    assert result.discovery_inputs is not None
    result.close()
    assert result.publication_inputs.peaks_by_sample == {"S1": candidates}
    assert result.publication_inputs.alignment_config == AlignmentConfig()
    assert result.publication_inputs.known_istd_reference is None
    assert [name for name, _kwargs in calls] == ["build", "execute"]
    build_kwargs = calls[0][1]
    assert isinstance(build_kwargs, dict)
    assert build_kwargs["raw_paths"] == (raw_path,)
    assert build_kwargs["peaks_by_sample"] == {"S1": candidates}
    assert build_kwargs["discovery_receipts_by_sample"] == receipts
    sample_metadata = build_kwargs["sample_metadata"]
    assert isinstance(sample_metadata, tuple)
    assert tuple(row.raw_stem for row in sample_metadata) == ("S1",)
    assert tuple(row.injection_order for row in sample_metadata) == (7,)
    assert tuple(row.sample_role for row in sample_metadata) == ("blank",)
    assert tuple(row.excluded for row in sample_metadata) == (True,)


def test_production_batch_rejects_non_single_provider_settings(tmp_path: Path) -> None:
    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
    )
    crossed_settings = type(settings)(
        neutral_loss_profiles=(
            settings.neutral_loss_profiles[0],
            settings.neutral_loss_profiles[0],
        ),
        selected_tag_names=settings.selected_tag_names,
        tag_combine_mode="intersection",
        nl_tolerance_ppm=settings.nl_tolerance_ppm,
        precursor_mz_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )

    try:
        build_dense_partition(
            raw_paths=(raw_path,),
            sample_metadata=(SampleMetadata(sample_name="S1", raw_stem="S1"),),
            settings=crossed_settings,
            peak_config=peak_config,
            peaks_by_sample={"S1": candidates},
            discovery_receipts_by_sample=build_discovery_receipts(
                tmp_path,
                (raw_path,),
                {"S1": candidates},
            ),
            dll_dir=tmp_path,
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_peak={
                c.peak_id: physical_records[c.peak_id]
                for values in ({"S1": candidates}).values()
                for c in values
            },
        )
    except ValueError as exc:
        assert "single-provider" in str(exc)
    else:
        raise AssertionError("duplicate tag settings must be rejected")


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    # The atomic phase must contain two disjoint method worksets.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
    )
    batch = build_dense_partition(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample={"S1": candidates},
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in ({"S1": candidates}).values()
            for c in values
        },
    )
    vendor_raw = install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    result = execute_dense_partition(
        batch,
    )

    assert result.succeeded
    assert len(result.sample_results) == len(batch.jobs) == 2
    assert vendor_raw.chromatogram_request_counts == [4]

    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    completed = complete_dense_partition(
        batch,
        result,
        alignment_config=alignment_config,
    )

    assert type(completed) is CompletedUntargetBatchWorkflowResult
    assert completed.application_result.candidate_ids == tuple(
        candidate_id
        for subject in batch.subject_batch.subjects
        for candidate_id, method_subject_id in (
            batch.subject_batch.candidate_memberships
        )
        if method_subject_id == subject.method_subject_id
    )
    writer_inputs = completed.application_result
    assert completed_untarget_batch_sample_order(writer_inputs) == ("S1",)
    assert len(written_groups(writer_inputs)) == 2
    assert len(tuple(written_cells(writer_inputs))) == 2
    feature_by_group = {
        group.group_hypothesis_id: f"F{ordinal:06d}"
        for ordinal, group in enumerate(written_groups(writer_inputs), start=1)
    }
    expected_source_ids: dict[tuple[str, str], str] = {}
    for child in completed.application_result.children:
        candidate_by_hypothesis = {
            (
                subject.sample_key,
                subject.hypothesis.hypothesis_id,
            ): subject.candidate_id
            for subject in child.scientific_core.terminal_subjects
            if type(subject) is ObservedSearchSubject
        }
        for projected_group, cells in child.group_cells:
            for decision in cells:
                hypothesis = decision.evidence.direct_hypotheses[0]
                expected_source_ids[
                    (
                        feature_by_group[projected_group.group_hypothesis_id],
                        decision.subject.canonical_sample.raw_stem,
                    )
                ] = candidate_by_hypothesis[
                    (
                        decision.subject.canonical_sample.sample_name,
                        hypothesis.hypothesis_id,
                    )
                ]
    assert {
        (row["feature_id"], row["sample_stem"]): row["source_candidate_id"]
        for row in written_rows(tmp_path / "written", writer_inputs)["cells"]
    } == expected_source_ids
    assert all(cell.value is not None for _, _, cell in written_cells(writer_inputs))
    writer_dir = tmp_path / "writer"
    outputs = output_paths(
        writer_dir,
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    write_completed_untargeted_outputs_atomic(
        outputs,
        writer_inputs,
        timing_recorder=TimingRecorder.disabled("writer"),
        metadata=alignment_metadata(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
        ),
    )
    assert {path.name for path in writer_dir.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_run_metadata.json",
        "alignment_review.tsv",
    }
    _assert_partition_composition_parity(
        batch,
        result,
        completed,
        alignment_config=alignment_config,
    )


@pytest.mark.parametrize(
    "scenario",
    (
        "mixed",
        "all_hold",
        "hold_no_input",
        "support_no_input",
        "all_reject",
        "hold_reject",
        "other_support",
        "ambiguous_hold_below_threshold",
        "ambiguous_hold_above_threshold",
    ),
)
def test_discovery_review_rows_cannot_become_alignment_roots(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    physical_records = {}
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-hold:{raw_path.stem}".encode())
    # Nearby rejected and supported observations must not share admission.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)

    def observation_only_raw(peak_rts: tuple[float, ...]) -> _FakeRaw:
        # An inferred precursor retains MS1 but cannot supply strict NL support.
        raw = _FakeRaw(tuple(rt - 0.01 for rt in peak_rts))
        raw.scans = tuple(replace(scan, precursor_mz=500.5) for scan in raw.scans)
        raw.intensity[:] = [
            sum(
                1000.0 * math.exp(-0.5 * ((rt - center) / 0.015) ** 2)
                for center in peak_rts
            )
            for rt in raw.rt
        ]
        return raw

    source_raws = {raw_path: _FakeRaw((8.0,)) for raw_path in raw_paths}
    if scenario not in {"all_reject", "hold_reject", "other_support"}:
        source_raws[raw_paths[1]] = observation_only_raw(
            (7.8, 8.2) if ambiguous else (8.0,)
        )
    if scenario in {"all_hold", "hold_no_input", "hold_reject"}:
        source_raws[raw_paths[0]] = observation_only_raw((8.0,))
    rejected_samples = (
        ("Support", "Hold")
        if scenario == "all_reject"
        else ("Hold",)
        if scenario == "hold_reject"
        else ()
    )
    for raw_path, raw in source_raws.items():
        if raw_path.stem in rejected_samples:
            raw.intensity[:] = 0
    # A sample without MS1 signal publishes no Discovery peak.
    support_candidates = _current_candidates(
        source_raws[raw_paths[0]],
        raw_file=raw_paths[0],
        settings=settings,
        physical_records=physical_records,
    )
    direct_hold_candidates = _current_candidates(
        source_raws[raw_paths[1]],
        raw_file=raw_paths[1],
        settings=settings,
        physical_records=physical_records,
    )
    peaks_by_sample: dict[str, tuple[DiscoveryPeak, ...]] = {
        "Support": support_candidates[:1],
        "Hold": direct_hold_candidates[:1],
    }
    if scenario in {"hold_no_input", "support_no_input"}:
        peaks_by_sample["Hold"] = ()
    if ambiguous:
        peaks_by_sample["Hold"] = direct_hold_candidates
    if scenario == "other_support":
        for raw_path in raw_paths:
            first_raw = observation_only_raw((7.98,))
            second_raw = _FakeRaw(
                (7.98,),
                scan_precursor_mz=500.004,
                neutral_loss_das=(116.0474,),
            )
            second_raw.intensity = first_raw.intensity.copy()
            nearby = tuple(
                _current_candidates(
                    raw,
                    raw_file=raw_path,
                    settings=settings,
                    physical_records=physical_records,
                )[0]
                for raw in (first_raw, second_raw)
            )
            source_raw = second_raw
            source_raw.scans = (first_raw.scans[0], second_raw.scans[0])
            source_raws[raw_path] = source_raw
            first, second = sorted(
                nearby, key=lambda item: item.best_event_precursor_mz
            )
            peaks_by_sample[raw_path.stem] = (first, second)
    if above_threshold:
        peaks_by_sample["Support2"] = _current_candidates(
            source_raws[raw_paths[2]],
            raw_file=raw_paths[2],
            settings=settings,
            physical_records=physical_records,
        )
    from xic_extractor.untargeted_execution.execution import execute_untargeted_plan
    from xic_extractor.untargeted_execution.input_planning import (
        prepare_eager_untargeted_plan,
    )

    plan = prepare_eager_untargeted_plan(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=peaks_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            peaks_by_sample,
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (peaks_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    admitted = {
        c.peak_id
        for values in peaks_by_sample.values()
        for c in values
        if c.status == "admitted"
    }
    rejected = {
        c.peak_id for values in peaks_by_sample.values() for c in values
    } - admitted
    assert (
        len(admitted)
        == {
            "mixed": 1,
            "all_hold": 0,
            "hold_no_input": 0,
            "support_no_input": 1,
            "all_reject": 0,
            "hold_reject": 0,
            "other_support": 2,
            "ambiguous_hold_below_threshold": 1,
            "ambiguous_hold_above_threshold": 2,
        }[scenario]
    )
    assert {cid for cid, _ in plan.subject_batch.candidate_memberships} == admitted
    opens_before = sum(map(len, opened.values()))
    completed = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "admission-output",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        raw_workers=1,
    )
    assert set(completed.application_result.candidate_ids) == admitted
    assert not completed.application_result.excluded_candidates
    writer = completed.application_result
    assert completed_untarget_batch_sample_order(writer) == stems
    assert not rejected.intersection(
        written_source_candidates(tmp_path / "written", writer).values()
    )
    if not admitted:
        assert not completed.application_result.children
        assert not written_groups(writer)
        assert sum(map(len, opened.values())) == opens_before


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_production_initial_groups_use_complete_feature_inference_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    physical_records = {}
    from xic_extractor.untargeted_execution import partition_composition as production

    stems = ("Early", "Middle", "Late")
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    settings = replace(_settings(), neutral_loss_profile=None)
    peak_config = _peak_config(tmp_path)
    raws = {path: _FakeRaw((8.0 + index * 0.1,)) for index, path in enumerate(paths)}
    candidates = {}
    for index, path in enumerate(paths):
        path.write_bytes(f"canonical-batch:{path.stem}".encode())
        if index >= supported_samples:
            raws[path].intensity[:] = 0
        candidates[path.stem] = _current_candidates(
            raws[path],
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (candidates).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    initial_contexts = []
    initial_edges = []
    cell_contexts = []
    build_context = production.build_initial_cid_nl_cross_sample_groups
    compose_cells = cid_nl_completion_owner.compose_cid_nl_predecessor_cells

    def retain_context(**kwargs):
        result = build_context(**kwargs)
        initial_contexts.append(result[0])
        return result

    monkeypatch.setattr(
        production, "build_initial_cid_nl_cross_sample_groups", retain_context
    )

    def retain_cells(**kwargs):
        cells = compose_cells(**kwargs)
        cell_contexts.extend(cell.group_context for cell in cells)
        return cells

    monkeypatch.setattr(
        cid_nl_completion_owner, "compose_cid_nl_predecessor_cells", retain_cells
    )
    progress_events = []
    runtime = execute_dense_partition(
        batch,
        alignment_config=AlignmentConfig(),
        progress_callback=progress_events.append,
    )
    assert runtime.succeeded
    membership_events = [
        event
        for event in progress_events
        if event.get("event") == "feature_membership_inference"
    ]
    assert len(membership_events) == 1
    membership_counts = membership_events[0]["operation_counts"]
    assert membership_events[0]["subject_count"] == 1
    assert membership_events[0]["group_count"] == 1
    assert membership_counts["input_owner_rows"] == supported_samples
    assert membership_counts["unique_owners"] == supported_samples
    assert membership_counts["owner_edge_evaluations"] > 0
    complete_dense_partition(batch, runtime, alignment_config=AlignmentConfig())
    (context,) = initial_contexts
    (group,) = context.groups
    assert {owner.sample_stem for owner in group.owners} == set(
        stems[:supported_samples]
    )
    assert (
        group.inference_basis is not None
        and group.inference_basis.disposition == "assigned"
        and group.inference_basis.basis == "bidirectional_membership_agreement"
        and group.inference_basis.reason
        == "both_finite_directional_passes_support_the_same_membership"
    )
    assert initial_edges == []
    assert cell_contexts
    for cell_context in cell_contexts:
        assert cell_context.sample_universe == frozenset(stems)
        assert dict(cell_context.rt_frame.injection_order) == dict(
            zip(stems, (1, 2, 3), strict=True)
        )
        rebound = replace(cell_context, rt_frame=SampleRtFrame())
        assert rebound.group is cell_context.group
        assert rebound.supports is cell_context.supports


def test_known_istd_reference_closes_drifting_group_and_cell_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_records = {}
    from openpyxl import Workbook

    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(2))
    precursor_mzs = (500.0, 500.03)
    configured_loss = 116.0474
    observed_losses = (116.0474, 116.0474005)
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        precursor_mz_tolerance_ppm=20.0,
        rt_max=10,
    )
    peak_config = _peak_config(tmp_path)
    raws = {
        path: _FakeRaw(
            (8.0,),
            scan_precursor_mz=precursor_mz,
            neutral_loss_das=(observed_loss,),
        )
        for path, precursor_mz, observed_loss in zip(
            paths, precursor_mzs, observed_losses, strict=True
        )
    }
    for i, raw in enumerate(raws.values()):
        raw.rt += i * 1.067
        raw.intensity *= 1.0 + i * 0.4
        raw.scans = tuple(replace(scan, rt=scan.rt + i * 1.067) for scan in raw.scans)
    candidates = {}
    for path, raw in zip(paths, raws.values(), strict=True):
        path.write_bytes(path.stem.encode())
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (candidates).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime_plain = execute_dense_partition(
        batch,
        alignment_config=AlignmentConfig(),
    )
    assert runtime_plain.succeeded
    workbook = Workbook()
    targets = workbook.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "ppm tol",
            "NL (Da)",
            "NL ppm warn",
            "NL ppm max",
            "ISTD Pair",
        ]
    )
    targets.append(["known", "ISTD", 500.015, 7, 10, 40, 116.0474, 20, 50, ""])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    for i, path in enumerate(paths):
        results.append([path.stem, "known", "ISTD", 8.0 + i * 1.067, -999])
    reference_path = tmp_path / "reference.xlsx"
    workbook.save(reference_path)
    reference = read_known_istd_reference(reference_path)
    config = AlignmentConfig()
    runtime = execute_dense_partition(
        batch,
        alignment_config=config,
        known_istd_reference=reference,
    )
    assert runtime.succeeded
    assert tuple(
        sorted(subject.neutral_loss_da for subject in batch.subject_batch.subjects)
    ) == (configured_loss, configured_loss)
    plain = complete_dense_partition(
        batch,
        runtime_plain,
        alignment_config=AlignmentConfig(),
    )
    corrected = complete_dense_partition(
        batch,
        runtime,
        alignment_config=config,
        known_istd_reference=reference,
    )
    _assert_partition_composition_parity(
        batch,
        runtime,
        corrected,
        alignment_config=config,
        known_istd_reference=reference,
        subject_worksets=(batch.subject_batch.subjects,),
    )
    plain_matrix = plain.application_result
    corrected_matrix = corrected.application_result
    assert len(written_groups(plain_matrix)) == 2
    assert len(written_groups(corrected_matrix)) == 1
    corrected_cells = tuple(written_cells(corrected_matrix))
    assert all(
        cell.value is not None and cell.value > 0 for *_, cell in corrected_cells
    )
    assert {
        (sample_stem, cell.selected_integration.rt_apex_min, cell.value)
        for _row, sample_stem, cell in corrected_cells
    } == {
        (sample_stem, cell.selected_integration.rt_apex_min, cell.value)
        for _row, sample_stem, cell in written_cells(plain_matrix)
        if cell.value is not None
    }
    assert len({cell.value for *_, cell in corrected_cells}) == len(paths)
    (child,) = corrected.application_result.children
    expected_loss_by_sample = {path.stem: configured_loss for path in paths}
    support_losses_by_sample = {
        cell.canonical_sample.raw_stem: {
            source.method_assessment.method_subject.neutral_loss_da
            for source in cell.predecessor_cells
            if source.method_result == "SUPPORT"
        }
        for cell in child.scientific_core.cell_observations
    }
    assert support_losses_by_sample == {
        sample: {loss} for sample, loss in expected_loss_by_sample.items()
    }
    contexts = tuple(
        source.group_context
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
    )
    assert contexts
    for context in contexts:
        assert context.rt_frame.drift_source == "batch_anchor_calibration"
        assert set(context.rt_frame.known_identity_bindings) == {
            (owner_id, "known") for owner_id in context.group.owner_ids
        }
        left, right = context.group.owners
        assert context.rt_frame.has_direct_reference_pair(left, right)
        # One identity anchor closes the known group, but cannot define a
        # general RT map or override the final shared coordinate owner.
        assert context.rt_frame.corrected_pair_delta_sec(left, right) is None
    without_rt_context = replace(contexts[0], rt_frame=SampleRtFrame())
    assert without_rt_context.group is contexts[0].group
    assert without_rt_context.supports is contexts[0].supports


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch,
    recovery_case: str,
) -> None:
    physical_records = {}
    sample_stems = (
        ("Support1", "Hold")
        if recovery_case == "threshold"
        else ("Support1", "Support2", "Support3", "Hold")
    )
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-recovery:{raw_path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {
        raw_path: _FakeRaw((apex_rt,))
        for raw_path, apex_rt in zip(
            raw_paths,
            (7.97, 8.03) if recovery_case == "threshold" else (7.97, 7.99, 8.01, 8.03),
            strict=True,
        )
    }
    if recovery_case != "positive":
        source_raws[raw_paths[-1]].intensity[:] = 0.0
    peaks_by_sample = {
        raw_path.stem: (
            _current_candidates(
                source_raws[raw_path],
                raw_file=raw_path,
                settings=settings,
                physical_records=physical_records,
            )[0],
        )
        for raw_path in raw_paths[:-1]
    }
    peaks_by_sample["Hold"] = ()
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(sample_stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=peaks_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            peaks_by_sample,
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (peaks_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_dense_partition(
        batch,
    )
    recovered = tuple(
        result
        for result in runtime_result.sample_results
        if result.job.sample.raw_stem == "Hold"
    )
    assert len(recovered) == 1
    assert (recovered[0].backfill_attempt is not None) == (recovery_case != "threshold")
    assert len(fixture_assessment(recovered[0]).candidate_peaks) == (
        recovery_case == "positive"
    )
    opens_before_composition = sum(map(len, opened.values()))

    completed = complete_dense_partition(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    _assert_partition_composition_parity(
        batch,
        runtime_result,
        completed,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.children
    writer_inputs = completed.application_result
    if recovery_case != "positive":
        assert (
            sum(map(len, opened.values()))
            == opens_before_composition
            == len(sample_stems) - (recovery_case == "threshold")
        )
        _assert_conservative_coverage_handoff(
            completed,
            writer_inputs,
            batch,
            runtime_result,
            tmp_path,
            threshold=recovery_case == "threshold",
        )
        return
    from xic_extractor.untargeted_execution.partition.models import (
        project_completed_backfill,
    )

    (child,) = completed.application_result.children
    recovered_source = next(
        source
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
        if source.method_assessment is fixture_assessment(recovered[0])
    )
    with pytest.raises(ValueError, match="completed empty epoch"):
        project_completed_backfill(
            recovered[0], group_context=recovered_source.group_context
        )
    assert completed_untarget_batch_sample_order(writer_inputs) == sample_stems
    ((written_group, row_cells),) = (
        (group, cells)
        for child in writer_inputs.children
        for group, cells in child.group_cells
    )
    cells_by_sample = {
        cell.subject.canonical_sample.raw_stem: cell for cell in row_cells
    }
    written = written_rows(tmp_path / "written", writer_inputs)
    (row,) = written["review"]
    assert (
        tuple(
            cell["sample_stem"]
            for cell in written["cells"]
            if cell["source_candidate_id"]
        )
        == sample_stems[:-1]
    )
    assert row["feature_id"] == "F000001"
    physical_peak_keys = row["physical_peak_keys"].split(";")
    center_rt = float(row["family_center_rt"])
    expected_presentation_facts: dict[str, tuple[float, float]] = {}
    expected_cell_apexes: dict[str, float] = {}
    diagnostic_observation_ids: set[str] = set()
    for child in completed.application_result.children:
        for projected_group, cells in child.group_cells:
            group = next(
                group
                for group in child.scientific_core.final_population
                if group.group_hypothesis_id == projected_group.group_hypothesis_id
            )
            member_ids = {
                peak.physical_peak_key
                for member in group.member_subjects
                for peak in member.physical_peaks
            }
            for decision in cells:
                direct_observations = decision.evidence.direct_observations
                if direct_observations and decision.value is not None:
                    assert len(direct_observations) == 1
                    expected_cell_apexes[decision.subject.canonical_sample.raw_stem] = (
                        direct_observations[0].integration.rt_apex_min
                    )
                for direct in decision.evidence.direct_observations:
                    physical_id = direct.physical_peak_key
                    if physical_id not in member_ids:
                        assert decision.subject.canonical_sample.raw_stem == ("Hold")
                        # The group-bounded MS1 peak fills the Matrix without
                        # joining the group or its presentation facts.
                        assert decision.evidence.evidence_states == (
                            "ms1_only_backfill_observation",
                        )
                        assert decision.subject.member_subject is None
                        assert decision.value is not None
                        diagnostic_observation_ids.add(physical_id)
                        continue
                    expected_presentation_facts[physical_id] = (
                        direct.mz,
                        direct.integration.rt_apex_min,
                    )
    assert len(diagnostic_observation_ids) == 1
    assert not diagnostic_observation_ids & set(physical_peak_keys)
    assert len(expected_presentation_facts) == len(sample_stems) - 1
    assert set(physical_peak_keys) == set(expected_presentation_facts)
    assert float(row["family_center_mz"]) == pytest.approx(
        sum(value[0] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts)
    )
    assert center_rt == pytest.approx(
        sum(value[1] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts),
        rel=1e-5,
    )
    assert len(set(expected_cell_apexes.values())) > 1
    for sample_stem in sample_stems:
        integration = cells_by_sample[sample_stem].selected_integration
        if sample_stem in expected_cell_apexes:
            assert integration.rt_apex_min == pytest.approx(
                expected_cell_apexes[sample_stem]
            )
    assert row["center_mz_basis"] == "cross_sample_owner_arithmetic_mean"

    # The written m/z is the group's own centre, the one its Backfill used.
    assert float(row["family_center_mz"]) == written_group.group_center_mz

    weighted_values = dict(zip(sample_stems, (1.0, 9.0, 2.0, 9.0), strict=True))
    for variant, no_write_sample in (("unequal", None), ("no-write", "Hold")):
        cells = tuple(
            replace(
                cell,
                payload=(
                    NoWriteCellDecision("searched_no_observation")
                    if cell.subject.canonical_sample.raw_stem == no_write_sample
                    else NumericCellDecision(
                        value=weighted_values[cell.subject.canonical_sample.raw_stem],
                        source=cell.source,
                    )
                ),
            )
            for cell in row_cells
        )
        (weighted_child,) = writer_inputs.children
        weighted_matrix = replace(
            writer_inputs,
            children=(replace(weighted_child, group_cells=((written_group, cells),)),),
        )
        accepted = tuple(cell for cell in cells if cell.value is not None)
        legacy_rt = sum(
            cell.selected_integration.rt_apex_min * cell.value for cell in accepted
        ) / sum(cell.value for cell in accepted)
        assert legacy_rt != pytest.approx(center_rt, rel=1e-5)

        writer_dir = tmp_path / f"writer-{variant}"
        outputs = output_paths(
            writer_dir,
            output_level="machine",
            emit_alignment_cells=False,
            emit_alignment_status_matrix=False,
        )
        write_completed_untargeted_outputs_atomic(
            outputs,
            weighted_matrix,
            timing_recorder=TimingRecorder.disabled("writer"),
            metadata=alignment_metadata(
                discovery_batch_index=tmp_path / "discovery_batch_index.csv",
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                owner_backfill_xic_backend="raw",
                output_level="machine",
                peak_config=peak_config,
            ),
        )

        assert outputs.matrix_tsv is not None
        with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
            matrix_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(matrix_row["RT"]) == pytest.approx(center_rt)

        assert outputs.review_tsv is not None
        with outputs.review_tsv.open(encoding="utf-8", newline="") as handle:
            review_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(review_row["matrix_rt"]) == pytest.approx(center_rt)
        assert outputs.run_metadata_json is not None
        metadata = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
        assert (
            metadata["matrix_center_rt_basis"] == "cross_sample_owner_arithmetic_mean"
        )
        assert (
            metadata["matrix_center_weight_basis"]
            == "unique_owner_observations_equal_weight"
        )


@pytest.mark.parametrize(
    "output_level", ("machine", "validation-minimal", "validation", "debug")
)
def test_atomic_publication_preserves_ms1_only_backfill_and_no_peak(
    tmp_path: Path,
    monkeypatch,
    output_level: str,
) -> None:
    physical_records = {}
    sample_stems = ("Support1", "Support2", "Support3", "MS1Only", "NoPeak")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {}
    for index, (raw_path, apex_rt) in enumerate(
        zip(raw_paths, (7.97, 7.99, 8.01, 8.03, 8.05), strict=True),
        start=1,
    ):
        raw_path.write_bytes(f"ms1-only-publication:{raw_path.stem}".encode())
        source_raw = _FakeRaw((apex_rt,))
        source_raw.intensity *= index
        if raw_path.stem in {"MS1Only", "NoPeak"}:
            source_raw.scans = ()
        if raw_path.stem == "NoPeak":
            source_raw.intensity[:] = 0.0
        source_raws[raw_path] = source_raw
    peaks_by_sample = {
        raw_path.stem: tuple(
            _current_candidates(
                source_raws[raw_path],
                raw_file=raw_path,
                settings=settings,
                physical_records=physical_records,
            )
        )
        for raw_path in raw_paths
    }
    assert all(len(peaks_by_sample[stem]) == 1 for stem in sample_stems[:3])
    assert peaks_by_sample["MS1Only"] == peaks_by_sample["NoPeak"] == ()
    for raw_path in raw_paths[3:]:
        assert (
            tuple(
                source_raws[raw_path].iter_ms2_scans(settings.rt_min, settings.rt_max)
            )
            == ()
        )
    assert source_raws[raw_paths[3]].intensity.max() > 0
    assert source_raws[raw_paths[4]].intensity.max() == 0
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(sample_stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=peaks_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, raw_paths, peaks_by_sample
        ),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_peak={
            c.peak_id: physical_records[c.peak_id]
            for values in (peaks_by_sample).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path)

    runtime = execute_dense_partition(
        batch,
    )
    assert runtime.succeeded
    recovered = {
        result.job.sample.raw_stem: result for result in runtime.sample_results
    }
    assert recovered["MS1Only"].backfill_attempt is not None
    assert recovered["NoPeak"].backfill_attempt is not None
    assert fixture_assessment(recovered["MS1Only"]).method_result == "HOLD"
    assert fixture_assessment(recovered["NoPeak"]).method_result == "HOLD"
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    completed = complete_dense_partition(
        batch, runtime, alignment_config=alignment_config
    )
    (child,) = completed.application_result.children
    ((_owner_group, owner_cells),) = child.group_cells
    owner_decisions = {
        decision.subject.canonical_sample.raw_stem: decision for decision in owner_cells
    }
    assert set(owner_decisions) == set(sample_stems)
    assert sum(d.value is not None for d in owner_cells) == 4
    ms1_only = owner_decisions["MS1Only"]
    assert ms1_only.variant == "numeric" and ms1_only.value > 0
    assert ms1_only.evidence.evidence_states == ("ms1_only_backfill_observation",)
    assert len(ms1_only.evidence.ms1_only_backfill_observations) == 1
    assert ms1_only.subject.member_subject is None
    no_peak = owner_decisions["NoPeak"]
    assert no_peak.value is None
    assert no_peak.variant == "unavailable"
    assert no_peak.selected_integration is None
    assert all(owner_decisions[stem].variant == "numeric" for stem in sample_stems[:3])
    assert len({owner_decisions[stem].value for stem in sample_stems[:4]}) == 4

    writer_inputs = completed.application_result
    for _row, sample_stem, cell in written_cells(writer_inputs):
        assert cell is owner_decisions[sample_stem]
    outputs = output_paths(
        tmp_path / "writer",
        output_level="machine",
        emit_alignment_cells=True,
        emit_alignment_status_matrix=False,
    )
    write_completed_untargeted_outputs_atomic(
        outputs,
        writer_inputs,
        timing_recorder=TimingRecorder.disabled("writer"),
        metadata=alignment_metadata(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
        ),
    )
    assert outputs.matrix_tsv is not None
    assert outputs.review_tsv is not None
    assert outputs.cells_tsv is not None
    with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        (matrix_row,) = tuple(csv.DictReader(handle, delimiter="	"))
    with outputs.review_tsv.open(encoding="utf-8", newline="") as handle:
        (review_row,) = tuple(csv.DictReader(handle, delimiter="	"))
    with outputs.cells_tsv.open(encoding="utf-8", newline="") as handle:
        cell_rows = {
            row["sample_stem"]: row for row in csv.DictReader(handle, delimiter="	")
        }
    assert set(cell_rows) == set(sample_stems)
    for stem in sample_stems[:4]:
        value = owner_decisions[stem].value
        assert float(matrix_row[stem]) == pytest.approx(value, rel=5e-6)
        assert float(cell_rows[stem]["area"]) == pytest.approx(value, rel=5e-6)
    assert matrix_row["NoPeak"] == cell_rows["NoPeak"]["area"] == ""
    assert review_row["detected_count"] == "3"
    assert review_row["accepted_backfill_count"] == "1"
    assert review_row["accepted_cell_count"] == "4"
    for stem in sample_stems[:3]:
        assert cell_rows[stem]["status"] == "detected"
        assert cell_rows[stem]["source_candidate_id"] == (
            peaks_by_sample[stem][0].peak_id
        )
    assert cell_rows["MS1Only"]["status"] == "backfilled"
    assert cell_rows["MS1Only"]["source_candidate_id"] == ""
    assert cell_rows["NoPeak"]["status"] == "unavailable"
    assert cell_rows["NoPeak"]["source_candidate_id"] == ""

    from xic_extractor.alignment_explanation.published_cells import read_published_cells
    from xic_extractor.alignment_explanation.reader import read_explanation_metadata
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )
    from xic_extractor.diagnostics.duplicate_quantification import (
        summarize_duplicate_quantification,
    )

    # Workbench omits the optional cell ledger. Every consumer must still get
    # the selected apex from the saved owner decision, including MS1-only cells.
    published = tmp_path / "published"
    published_outputs = output_paths(
        published,
        output_level=output_level,
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    write_completed_untargeted_outputs_atomic(
        published_outputs,
        writer_inputs,
        timing_recorder=TimingRecorder.disabled("writer"),
        metadata={},
    )
    write_alignment_explanation(
        published / "alignment_explanation.sqlite",
        completed=completed,
        peaks_by_sample=peaks_by_sample,
        matrix_path=published_outputs.matrix_tsv,
        alignment_config=alignment_config,
    )
    cells = read_published_cells(published)
    assert {(cell.sample, cell.direct, cell.apex_rt_min) for cell in cells} == {
        ("Support1", True, 7.97),
        ("Support2", True, 7.99),
        ("Support3", True, 8.01),
        ("MS1Only", False, 8.03),
    }
    assert {cell.sample: cell.value for cell in cells} == {
        stem: owner_decisions[stem].value for stem in sample_stems[:4]
    }
    assert summarize_duplicate_quantification(cells)["valued_cells"] == 4
    assert published_outputs.matrix_tsv.read_bytes() == outputs.matrix_tsv.read_bytes()
    metadata = read_explanation_metadata(published / "alignment_explanation.sqlite")
    stems_by_key = {
        sample["sample_key"]: sample["sample_stem"]
        for sample in metadata.summary["samples"]
    }
    assert {
        stems_by_key[sample_key]: fact["apex_rt_min"]
        for (_, sample_key), fact in metadata.records["cell_decisions"].items()
    } == {
        "Support1": 7.97,
        "Support2": 7.99,
        "Support3": 8.01,
        "MS1Only": 8.03,
        "NoPeak": None,
    }
