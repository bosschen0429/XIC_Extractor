from __future__ import annotations

import csv
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

import xic_extractor.alignment.untargeted_finalized_matrix as finalized_matrix
import xic_extractor.extraction.untargeted_cid_nl_alignment as alignment_adapter
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _FakeRaw,
    _install_runtime_raw,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import DiscoveryBatchInput
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.pipeline_outputs import (
    alignment_metadata,
    output_paths,
    write_outputs_atomic,
)
from xic_extractor.alignment.production_decisions import ProductionDecisionSet
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
)
from xic_extractor.peak_detection.evidence_facts import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_writer_projection_rejects_duplicate_physical_presentation_fact() -> None:
    facts = {"physical-1": (500.0, 8.0)}

    with pytest.raises(ValueError, match="duplicate physical identity"):
        finalized_matrix._add_presentation_fact(
            facts,
            physical_id="physical-1",
            mz=500.0,
            rt=8.0,
        )


@pytest.mark.parametrize(
    ("mz", "rt"),
    (
        (float("nan"), 8.0),
        (500.0, float("inf")),
        (True, 8.0),
        (500.0, False),
    ),
)
def test_writer_projection_rejects_invalid_presentation_coordinates(
    mz: float,
    rt: float,
) -> None:
    with pytest.raises(ValueError, match="finite owner-backed"):
        finalized_matrix._add_presentation_fact(
            {},
            physical_id="physical-1",
            mz=mz,
            rt=rt,
        )


def test_writer_projection_keeps_distinct_repaired_facts_with_shared_signal() -> None:
    rt = tuple(7.0 + 0.01 * index for index in range(101))
    raw = tuple(
        1.0 + 100.0 * math.exp(-0.5 * ((index - 50) / 4.0) ** 2) for index in range(101)
    )
    prepared = prepare_physical_signal(
        rt,
        raw,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    evaluations = tuple(
        evaluate_physical_peak(
            prepared,
            selection_subject_id=subject_id,
            query_rt_min=7.5,
        )
        for subject_id in ("repair-subject-a", "repair-subject-b")
    )
    assert evaluations[0].prepared.prepared_signal_id == (
        evaluations[1].prepared.prepared_signal_id
    )
    assert evaluations[0].facts is not None
    assert evaluations[1].facts is not None
    assert evaluations[0].facts.fact_content_id != evaluations[1].facts.fact_content_id

    repaired_observations = tuple(
        SimpleNamespace(
            selected_for_output=True,
            facts=SimpleNamespace(physical_evaluation=evaluation),
            cell=SimpleNamespace(
                backfill_seed_mz=500.0 + index,
                apex_rt=evaluation.facts.apex_rt_min,
            ),
        )
        for index, evaluation in enumerate(evaluations)
    )
    observation = SimpleNamespace(
        direct_observations=(),
        repaired_observations=repaired_observations,
    )

    _mz_values, _rt_values, physical_ids = finalized_matrix._presentation_facts(
        (observation,)
    )

    assert set(physical_ids) == {
        evaluations[0].facts.fact_content_id,
        evaluations[1].facts.fact_content_id,
    }
    assert len(physical_ids) == 2


def test_production_batch_preserves_exact_nearby_subject_membership(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"issue329-production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )

    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:issue329-production-batch",
    )

    assert len(batch.subject_batch.subjects) == 2
    assert tuple(
        subject.precursor_mz for subject in batch.subject_batch.subjects
    ) == tuple(
        subject.precursor_mz
        for subject in sorted(
            batch.subject_batch.subjects,
            key=lambda item: item.method_subject_id,
        )
    )
    assert batch.subject_batch.candidate_memberships == tuple(
        sorted(
            (
                candidate.candidate_id,
                next(
                    subject.method_subject_id
                    for subject in batch.subject_batch.subjects
                    if subject.precursor_mz == candidate.precursor_mz
                ),
            )
            for candidate in candidates
        )
    )
    assert tuple(job.method_subject for job in batch.jobs) == (
        batch.subject_batch.subjects
    )
    assert all(
        job.candidates is batch.context.candidates_by_sample["S1"] for job in batch.jobs
    )


def test_alignment_adapter_builds_executes_and_composes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "S1.raw"
    raw_path.write_bytes(b"issue367-alignment-adapter:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    receipts = _discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": candidates},
    )
    discovery_batch = DiscoveryBatchInput(
        sample_order=("S1",),
        candidate_csvs={"S1": tmp_path / "S1.csv"},
        raw_files={"S1": raw_path},
        review_csvs={"S1": None},
        candidate_receipts=receipts,
    )
    built = object()
    executed = object()
    completed = object()
    calls: list[tuple[str, object]] = []

    def build(**kwargs: object) -> object:
        calls.append(("build", kwargs))
        return built

    def execute(batch: object, **kwargs: object) -> object:
        assert batch is built
        calls.append(("execute", kwargs))
        return executed

    def compose(batch: object, runtime: object, **kwargs: object) -> object:
        assert batch is built
        assert runtime is executed
        calls.append(("compose", kwargs))
        return completed

    monkeypatch.setattr(
        alignment_adapter,
        "build_untargeted_cid_nl_production_batch",
        build,
    )
    monkeypatch.setattr(
        alignment_adapter,
        "execute_untargeted_cid_nl_production_batch",
        execute,
    )
    monkeypatch.setattr(
        alignment_adapter,
        "compose_untargeted_cid_nl_production_result",
        compose,
    )

    result = alignment_adapter.complete_untargeted_cid_nl_alignment(
        discovery_batch=discovery_batch,
        candidates_by_sample={"S1": candidates},
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

    assert result is completed
    assert [name for name, _kwargs in calls] == ["build", "execute", "compose"]
    build_kwargs = calls[0][1]
    assert isinstance(build_kwargs, dict)
    assert build_kwargs["raw_paths"] == (raw_path,)
    assert build_kwargs["candidates_by_sample"] == {"S1": candidates}
    assert build_kwargs["discovery_receipts_by_sample"] == receipts
    sample_metadata = build_kwargs["sample_metadata"]
    assert isinstance(sample_metadata, tuple)
    assert tuple(row.raw_stem for row in sample_metadata) == ("S1",)
    assert tuple(row.injection_order for row in sample_metadata) == (7,)
    assert tuple(row.sample_role for row in sample_metadata) == ("blank",)
    assert tuple(row.excluded for row in sample_metadata) == (True,)


def test_production_batch_rejects_non_single_provider_settings(tmp_path: Path) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"issue329-production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    crossed_settings = type(settings)(
        neutral_loss_profiles=settings.neutral_loss_profiles,
        selected_tag_names=settings.selected_tag_names,
        tag_combine_mode="intersection",
        nl_tolerance_ppm=settings.nl_tolerance_ppm,
        precursor_mz_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )

    try:
        build_untargeted_cid_nl_production_batch(
            raw_paths=(raw_path,),
            sample_metadata=(SampleMetadata(sample_name="S1", raw_stem="S1"),),
            settings=crossed_settings,
            peak_config=peak_config,
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample=_discovery_receipts(
                tmp_path,
                (raw_path,),
                {"S1": candidates},
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:issue329-production-batch",
        )
    except ValueError as exc:
        assert "single-provider" in str(exc)
    else:
        raise AssertionError("intersection route must remain outside CID-NL production")


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"issue329-production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:issue329-production-batch",
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json",
            "issue329-production-batch",
        ),
    )

    assert result.succeeded
    assert len(result.sample_results) == len(batch.jobs) == 2
    assert vendor_raw.chromatogram_request_counts == [2]

    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    completed = compose_untargeted_cid_nl_production_result(
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
    writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
    assert writer_inputs.matrix.sample_order == ("S1",)
    assert len(writer_inputs.matrix.clusters) == 2
    assert len(writer_inputs.matrix.cells) == 2
    assert tuple(row.public_family_id for row in writer_inputs.matrix.clusters) == (
        "FAM000001",
        "FAM000002",
    )
    expected_source_ids: dict[tuple[str, str], str] = {}
    for child in completed.application_result.children:
        candidate_by_hypothesis = {
            (
                subject.canonical_sample_identity_id,
                subject.hypothesis.hypothesis_id,
            ): subject.candidate_id
            for subject in child.scientific_core.terminal_subjects
            if type(subject) is ObservedSearchSubject
        }
        for projected in child.completed_projection.rows:
            for decision in projected.decisions:
                hypothesis = decision.evidence.direct_hypotheses[0]
                expected_source_ids[
                    (
                        projected.row_key.untarget_row_key_id,
                        decision.subject.canonical_sample.metadata.raw_stem,
                    )
                ] = candidate_by_hypothesis[
                    (
                        decision.subject.canonical_sample.identity.sample_identity_id,
                        hypothesis.hypothesis_id,
                    )
                ]
    assert {
        (cell.cluster_id, cell.sample_stem): cell.source_candidate_id
        for cell in writer_inputs.matrix.cells
    } == expected_source_ids
    assert all(cell.group_hypothesis_id for cell in writer_inputs.matrix.cells)
    assert all(
        writer_inputs.production_decisions.cell(
            cell.cluster_id,
            cell.sample_stem,
        ).matrix_value
        is not None
        for cell in writer_inputs.matrix.cells
    )
    writer_dir = tmp_path / "writer"
    outputs = output_paths(
        writer_dir,
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    write_outputs_atomic(
        outputs,
        writer_inputs.matrix,
        metadata=alignment_metadata(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            owner_build_xic_backend="raw",
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
            audit_evidence_mode="none",
            heavy_audit_enabled=False,
            require_discovery_owner_closure=True,
            owner_backfill_peak_fact_provenance_row_count=1,
        ),
        ownership=OwnershipBuildResult(
            owners=(),
            assignments=(),
            ambiguous_records=(),
        ),
        alignment_config=alignment_config,
        production_decisions=writer_inputs.production_decisions,
    )
    assert {path.name for path in writer_dir.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_results.xlsx",
        "alignment_review.tsv",
        "review_report.html",
    }


def test_production_composition_closes_observation_only_hold_without_membership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in ("Support", "Hold"))
    for raw_path in raw_paths:
        raw_path.write_bytes(f"issue329-production-hold:{raw_path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {raw_path: _FakeRaw((8.0,)) for raw_path in raw_paths}
    support = _current_candidates(
        source_raws[raw_paths[0]],
        raw_file=raw_paths[0],
        settings=settings,
        peak_config=peak_config,
    )[0]
    direct_hold = _current_candidates(
        source_raws[raw_paths[1]],
        raw_file=raw_paths[1],
        settings=settings,
        peak_config=peak_config,
    )[0]
    inferred_hold = replace(
        direct_hold,
        precursor_mz_basis="product_plus_neutral_loss",
    )
    candidates_by_sample = {
        "Support": (support,),
        "Hold": (inferred_hold,),
    }
    batch = build_untargeted_cid_nl_production_batch(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="Support",
                raw_stem="Support",
                injection_order=1,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="Hold",
                raw_stem="Hold",
                injection_order=2,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:issue329-production-hold",
    )
    _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "hold.runtime.json",
            "issue329-production-hold",
        ),
    )
    hold_observation_ids = tuple(
        peak.content.peak_observation_content_id
        for result in runtime_result.sample_results
        for peak in result.effective_assessment.candidate_peaks
        if result.effective_assessment.content.method_result == "HOLD"
    )
    assert len(hold_observation_ids) == 1

    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert hold_observation_ids[0] not in (
        completed.application_result.physical_observation_ids
    )
    writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
    assert writer_inputs.matrix.sample_order == ("Support", "Hold")
    assert len(writer_inputs.matrix.clusters) == 1
    assert tuple(cell.sample_stem for cell in writer_inputs.matrix.cells) == (
        "Support",
        "Hold",
    )
    decisions = writer_inputs.production_decisions
    cluster_id = writer_inputs.matrix.cells[0].cluster_id
    assert decisions.cell(cluster_id, "Support").write_matrix_value is True
    assert decisions.cell(cluster_id, "Hold").write_matrix_value is False
    assert decisions.cell(cluster_id, "Hold").blank_reason != ""


def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sample_stems = ("Support1", "Support2", "Support3", "Hold")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"issue329-production-recovery:{raw_path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {
        raw_path: _FakeRaw((apex_rt,))
        for raw_path, apex_rt in zip(
            raw_paths,
            (7.97, 7.99, 8.01, 8.03),
            strict=True,
        )
    }
    candidates_by_sample = {
        raw_path.stem: (
            _current_candidates(
                source_raws[raw_path],
                raw_file=raw_path,
                settings=settings,
                peak_config=peak_config,
            )[0],
        )
        for raw_path in raw_paths[:-1]
    }
    candidates_by_sample["Hold"] = ()
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:issue329-production-recovery",
    )
    _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "recovery.runtime.json",
            "issue329-production-recovery",
        ),
    )
    recovered = tuple(
        result
        for result in runtime_result.sample_results
        if result.job.sample.metadata.raw_stem == "Hold"
    )
    assert len(recovered) == 1
    assert recovered[0].recovery_attempt is not None
    assert len(recovered[0].effective_assessment.candidate_peaks) == 1

    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.children
    writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
    assert writer_inputs.matrix.sample_order == sample_stems
    assert len(writer_inputs.matrix.clusters) == 1
    assert (
        tuple(cell.sample_stem for cell in writer_inputs.matrix.cells) == sample_stems
    )
    cells_by_sample = {cell.sample_stem: cell for cell in writer_inputs.matrix.cells}
    assert all(
        cells_by_sample[sample_stem].source_candidate_id is not None
        for sample_stem in sample_stems[:-1]
    )
    assert cells_by_sample["Hold"].source_candidate_id is None
    row = writer_inputs.matrix.clusters[0]
    assert row.public_family_id == "FAM000001"
    expected_presentation_facts: dict[str, tuple[float, float]] = {}
    expected_cell_apexes: dict[str, float] = {}
    for child in completed.application_result.children:
        for projected in child.completed_projection.rows:
            for decision in projected.decisions:
                direct_observations = decision.evidence.direct_observations
                selected_repairs = tuple(
                    observation
                    for observation in decision.evidence.repaired_observations
                    if observation.selected_for_output
                )
                if direct_observations and decision.value is not None:
                    assert len(direct_observations) == 1
                    expected_cell_apexes[
                        decision.subject.canonical_sample.metadata.raw_stem
                    ] = direct_observations[0].content.integration.rt_apex_min
                elif selected_repairs and decision.value is not None:
                    assert len(selected_repairs) == 1
                    assert selected_repairs[0].facts is not None
                    expected_cell_apexes[
                        decision.subject.canonical_sample.metadata.raw_stem
                    ] = selected_repairs[0].facts.integration.rt_apex_min
                for direct in decision.evidence.direct_observations:
                    expected_presentation_facts[
                        direct.content.peak_observation_content_id
                    ] = (
                        direct.content.mz,
                        direct.content.integration.rt_apex_min,
                    )
                for repaired_observation in decision.evidence.repaired_observations:
                    if repaired_observation.selected_for_output:
                        assert repaired_observation.facts is not None
                        assert (
                            repaired_observation.facts.physical_evaluation is not None
                        )
                        assert (
                            repaired_observation.facts.physical_evaluation.facts
                            is not None
                        )
                        physical_facts = (
                            repaired_observation.facts.physical_evaluation.facts
                        )
                        expected_presentation_facts[physical_facts.fact_content_id] = (
                            repaired_observation.cell.backfill_seed_mz,
                            repaired_observation.cell.apex_rt,
                        )
    assert len(expected_presentation_facts) == len(sample_stems)
    assert row.physical_observation_ids == tuple(sorted(expected_presentation_facts))
    assert row.family_center_mz == pytest.approx(
        sum(value[0] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts)
    )
    assert row.family_center_rt == pytest.approx(
        sum(value[1] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts)
    )
    assert len(set(expected_cell_apexes.values())) > 1
    for sample_stem in sample_stems:
        cell_apex = cells_by_sample[sample_stem].apex_rt
        if sample_stem in expected_cell_apexes:
            assert cell_apex == pytest.approx(expected_cell_apexes[sample_stem])
        else:
            assert cell_apex is None
    assert row.center_mz_basis == "cross_sample_owner_arithmetic_mean_v1"
    assert row.center_rt_basis == "cross_sample_owner_arithmetic_mean_v1"

    base_cells = writer_inputs.production_decisions.cells
    weighted_values = dict(zip(sample_stems, (1.0, 9.0, 1.0, 9.0), strict=True))
    for variant, no_write_sample in (("unequal", None), ("no-write", "Hold")):
        decisions = ProductionDecisionSet(
            cells={
                key: replace(
                    decision,
                    production_status=(
                        "blank"
                        if decision.sample_stem == no_write_sample
                        else decision.production_status
                    ),
                    rescue_tier=(
                        ""
                        if decision.sample_stem == no_write_sample
                        else decision.rescue_tier
                    ),
                    write_matrix_value=decision.sample_stem != no_write_sample,
                    matrix_value=(
                        None
                        if decision.sample_stem == no_write_sample
                        else weighted_values[decision.sample_stem]
                    ),
                    blank_reason=(
                        "regression_no_write"
                        if decision.sample_stem == no_write_sample
                        else decision.blank_reason
                    ),
                )
                for key, decision in base_cells.items()
            },
            rows=writer_inputs.production_decisions.rows,
        )
        accepted = tuple(
            (
                cells_by_sample[stem],
                decisions.cell(cells_by_sample[stem].cluster_id, stem),
            )
            for stem in sample_stems
            if stem != no_write_sample
        )
        legacy_rt = sum(
            cell.apex_rt * decision.matrix_value
            for cell, decision in accepted
            if cell.apex_rt is not None and decision.matrix_value is not None
        ) / sum(
            decision.matrix_value
            for _cell, decision in accepted
            if decision.matrix_value is not None
        )
        assert legacy_rt != pytest.approx(row.family_center_rt)

        writer_dir = tmp_path / f"writer-{variant}"
        outputs = output_paths(
            writer_dir,
            output_level="machine",
            emit_alignment_cells=False,
            emit_alignment_status_matrix=False,
        )
        write_outputs_atomic(
            outputs,
            writer_inputs.matrix,
            metadata=alignment_metadata(
                discovery_batch_index=tmp_path / "discovery_batch_index.csv",
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                owner_build_xic_backend="raw",
                owner_backfill_xic_backend="raw",
                output_level="machine",
                peak_config=peak_config,
                audit_evidence_mode="none",
                heavy_audit_enabled=False,
                require_discovery_owner_closure=True,
                owner_backfill_peak_fact_provenance_row_count=1,
            ),
            ownership=OwnershipBuildResult(
                owners=(),
                assignments=(),
                ambiguous_records=(),
            ),
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            production_decisions=decisions,
        )

        assert outputs.matrix_tsv is not None
        with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
            matrix_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(matrix_row["RT"]) == pytest.approx(row.family_center_rt)

        assert outputs.matrix_identity_tsv is not None
        with outputs.matrix_identity_tsv.open(encoding="utf-8", newline="") as handle:
            identity_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(identity_row["RT"]) == pytest.approx(row.family_center_rt)
        assert identity_row["center_rt_basis"] == row.center_rt_basis
        assert identity_row["center_weight_basis"] == row.center_weight_basis

        assert outputs.workbook is not None
        workbook = load_workbook(outputs.workbook, read_only=True, data_only=True)
        try:
            workbook_rows = list(workbook["Matrix"].iter_rows(values_only=True))
        finally:
            workbook.close()
        workbook_row = dict(zip(workbook_rows[0], workbook_rows[1], strict=True))
        assert workbook_row["RT"] == pytest.approx(row.family_center_rt)


@pytest.mark.parametrize("exclude_all", (False, True))
def test_production_composition_retains_typed_non_acquired_candidate_exclusion(
    tmp_path: Path,
    monkeypatch,
    exclude_all: bool,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"issue329-production-orphan:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw, initial_candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    excluded_source_ids = {
        candidate.candidate_id
        for candidate in (initial_candidates if exclude_all else initial_candidates[1:])
    }
    candidates = tuple(
        replace(
            candidate,
            ms1_peak_found=False,
            ms1_apex_rt=None,
            ms1_area=None,
            ms1_peak_rt_start=None,
            ms1_peak_rt_end=None,
            ms1_height=None,
            ms1_trace_quality="not_found",
            ms1_scan_support_score=None,
            discovery_candidate_state="review_only_orphan_nl",
            ms1_feature_row_id="",
        )
        if candidate.candidate_id in excluded_source_ids
        else candidate
        for candidate in initial_candidates
    )
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:issue329-production-orphan",
    )
    _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    runtime_result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json",
            "issue329-production-orphan",
        ),
    )
    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.candidate_ids == tuple(
        candidate_id
        for subject in batch.subject_batch.subjects
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
        if method_subject_id == subject.method_subject_id
    )
    exclusions = completed.application_result.excluded_candidates
    assert {item.candidate_id for item in exclusions} == excluded_source_ids
    assert all(
        item.method_subject_id
        == dict(batch.subject_batch.candidate_memberships)[item.candidate_id]
        and item.discovery_candidate_state == "review_only_orphan_nl"
        and item.reason_code == "discovery_candidate_not_search_eligible"
        for item in exclusions
    )
    if exclude_all:
        assert completed.application_result.children == ()
        assert completed.application_result.subject_ids == tuple(
            subject.method_subject_id for subject in batch.subject_batch.subjects
        )
        assert completed.application_result.canonical_samples == tuple(
            sorted(
                batch.context.samples,
                key=lambda sample: sample.identity.sample_identity_id,
            )
        )
