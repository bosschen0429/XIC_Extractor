from __future__ import annotations

import csv
import math
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

import xic_extractor.alignment.result.application_result as application_result_owner
import xic_extractor.alignment.untargeted_finalized_matrix as finalized_matrix
import xic_extractor.extraction.untargeted_cid_nl_alignment as alignment_adapter
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _multi_tag_settings,
    _peak_config,
    _settings,
)
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
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeError,
    UntargetedCidNlRealDataRuntimeJob,
    build_untargeted_cid_nl_sample_runtime_jobs,
)
from xic_extractor.peak_detection.evidence_facts import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.sample_metadata import SampleMetadata


def _forbid_retained_production_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    from xic_extractor.alignment import workflow

    def unexpected_repair(*args, **kwargs):
        raise AssertionError("closed production coverage must not authorize repair")

    monkeypatch.setattr(workflow, "execute_observation_repair", unexpected_repair)
    monkeypatch.setattr(
        workflow, "consume_observation_repair_terminal", unexpected_repair
    )


def _assert_jobs_match_direct_construction(batch) -> None:
    from xic_extractor.extraction import (
        untargeted_cid_nl_real_data_runtime as runtime_owner,
    )

    context = batch.context
    expected = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=batch.jobs[0].dll_dir,
            work_unit_key=(
                "untargeted-cid-nl:"
                f"{sample.metadata.raw_stem}:{subject.method_subject_id}"
            ),
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                record
                for _candidate_id, record in sorted(
                    context.physical_records_by_candidate.items()
                )
                if record.work_item.sample_stem == sample.metadata.raw_stem
            ),
        )
        for sample in context.samples
        for subject in batch.subject_batch.subjects
    )
    assert batch.jobs == expected
    assert tuple(job._request_subjects for job in batch.jobs) == tuple(
        job._request_subjects for job in expected
    )
    memberships = dict(batch.subject_batch.candidate_memberships)
    for job, direct_job in zip(batch.jobs, expected, strict=True):
        records = tuple(
            record
            for record in job.physical_records
            if memberships.get(record.candidate_id)
            == job.method_subject.method_subject_id
        )
        selected = runtime_owner._request_subject_physical_records(job)
        assert selected == records
        assert all(left is right for left, right in zip(selected, records, strict=True))
        assert runtime_owner._request_subject_physical_records(direct_job) == records


def _assert_outside_group_cells(scientific, batch, tmp_path: Path) -> None:
    import json
    from dataclasses import asdict

    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.alignment import cell_observations as cell_owner

    source_cells = tuple(
        source
        for final_cell in scientific.cell_observations
        for source in final_cell.predecessor_cells
    )
    outside_cells = tuple(
        source for source in source_cells if source.content.outside_group is not None
    )
    local_cells = tuple(
        source for source in source_cells if source.content.outside_group is None
    )
    assert {source.group_context.group.public_family_id for source in source_cells} == {
        "FAM000001",
        "FAM000002",
    }
    for source in local_cells:
        legacy_content = asdict(source.content)
        legacy_content.pop("outside_group")
        expected = (
            json.dumps(
                {
                    "schema_version": "cell_observation_v4",
                    "validation_status": cell_owner.VALIDATION_STATUS,
                    "content": legacy_content,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        assert (
            cell_owner.canonical_cell_observation_bytes(source.content)
            == expected.encode()
        )
        assert source.artifact.semantic_contract.schema_version == "cell_observation_v4"
    outside = outside_cells[0]
    relation = outside.content.outside_group
    assert relation is not None
    supporting = outside.outside_group_context
    assert supporting is not None
    support = next(
        item
        for item in supporting.supports
        if item.assessment is outside.method_assessment
    )
    job = next(
        job
        for job in batch.jobs
        if job.sample.identity.sample_identity_id
        == outside.content.canonical_sample_identity_id
    )
    mint_args = dict(
        processing_owners=job.processing_owners,
        sample=job.sample,
        group_context=outside.group_context,
        method_assessment=outside.method_assessment,
        outside_group_context=supporting,
    )
    assert cell_owner.mint_cell_observation_ref(**mint_args) == outside
    for source in outside_cells:
        payload = json.loads(
            cell_owner.canonical_cell_observation_bytes(source.content)
        )
        assert payload["schema_version"] == "cell_observation_v5"
        assert payload["content"]["outside_group"] == asdict(
            source.content.outside_group
        )
        assert source.artifact.semantic_contract.schema_version == "cell_observation_v5"
        assert source.peak_observation is None and source.peak_hypothesis is None
        assert source.content.method_result == "SUPPORT"
        assert (
            source.content.reason_codes == source.method_assessment.content.reason_codes
        )
    (tmp_path / "other-request").mkdir()
    other_request = build_processing_identity_no_raw_fixture(
        tmp_path / "other-request", sample_stems=("different",)
    )
    other_job = next(item for item in batch.jobs if item.sample != job.sample)
    for substitution in (
        {"outside_group_context": outside.group_context},
        {"peak_hypothesis": support.hypothesis},
        {
            "peak_observation": next(
                source.peak_observation
                for source in local_cells
                if source.method_assessment is support.assessment
            )
        },
        {"sample": other_job.sample},
        {"processing_owners": other_request.owners},
    ):
        with pytest.raises(cell_owner.CellObservationContractError):
            cell_owner.mint_cell_observation_ref(**(mint_args | substitution))
    with pytest.raises(cell_owner.CellObservationContractError):
        replace(
            outside,
            content=replace(
                outside.content,
                outside_group=replace(
                    relation,
                    supporting_peak_hypothesis_id=local_cells[
                        0
                    ].group_context.group.owner_ids[0],
                ),
            ),
        )
    with pytest.raises(cell_owner.CellObservationContractError):
        replace(outside, outside_group_context=outside.group_context)


def _assert_single_unsupported_exclusion(
    completed, runtime_result, batch, tmp_path: Path, *, populated: bool
) -> None:
    from copy import copy

    from xic_extractor.alignment.result.application_result import (
        ApplicationResultContractError,
        UntargetBatchApplicationResultRequest,
    )
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        mint_cid_nl_method_assessment_ref,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        issue_processing_attempt,
    )

    result = completed.application_result
    exclusions = result.excluded_candidates
    assert len(exclusions) == 2
    subject_ids = {item.method_subject_id for item in exclusions}
    assert len(subject_ids) == 1
    retained = tuple(
        sorted(
            (
                item.effective_assessment
                for item in runtime_result.sample_results
                if item.job.method_subject.method_subject_id in subject_ids
            ),
            key=lambda ref: ref.content.canonical_sample_identity_id,
        )
    )
    assert len(retained) == 2
    assert all(
        item.reason_code == "cid_nl_requirement_not_supported"
        and not item.composite_assessments
        and item.method_assessments == retained
        and all(
            left is right
            for left, right in zip(item.method_assessments, retained, strict=True)
        )
        for item in exclusions
    )
    assert {item.candidate_id for item in exclusions} == {
        candidate_id
        for candidate_id, subject_id in batch.subject_batch.candidate_memberships
        if subject_id in subject_ids
    }
    assert len(result.children) == int(populated)
    writer = project_completed_untarget_batch_writer_inputs(completed)
    destination = tmp_path / "single-unsupported-matrix.tsv"
    write_alignment_matrix_tsv(
        destination, writer.matrix, production_decisions=writer.production_decisions
    )
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert {"Support", "Hold"}.issubset(reader.fieldnames)
        rows = list(reader)
    assert len(rows) == int(populated)
    if populated:
        assert all(float(rows[0][stem]) > 0 for stem in ("Support", "Hold"))
    else:
        assert not writer.matrix.clusters and not writer.matrix.cells

    def validate(exclusion):
        return UntargetBatchApplicationResultRequest(
            scope=result.scope,
            processing_request=result.processing_request,
            processing_attempt=result.processing_attempt,
            children=result.children,
            excluded_candidates=(exclusion, *exclusions[1:]),
        )

    first = exclusions[0]
    for proof in (retained[:1], (retained[0], retained[0])):
        with pytest.raises(
            ApplicationResultContractError, match="complete sample coverage"
        ):
            validate(replace(first, method_assessments=proof))
    for substitution in (
        {"method_assessments": ()},
        {"composite_assessments": retained},
        {"method_assessments": (), "composite_assessments": retained},
        {"reason_code": "composite_requirement_not_supported"},
        {"reason_code": "discovery_candidate_not_search_eligible"},
    ):
        with pytest.raises(ApplicationResultContractError):
            replace(first, **substitution)
    crossed = copy(first)
    object.__setattr__(crossed, "composite_assessments", retained)
    with pytest.raises(ApplicationResultContractError):
        validate(crossed)
    if populated:
        supported = next(
            item.effective_assessment
            for item in runtime_result.sample_results
            if item.effective_assessment.content.method_result == "SUPPORT"
        )
        with pytest.raises(ApplicationResultContractError):
            replace(first, method_assessments=(supported, retained[1]))
        with pytest.raises(ApplicationResultContractError):
            replace(
                first,
                method_subject_id=supported.content.method_subject.method_subject_id,
                method_assessments=(supported,),
            )

    sample = next(
        sample
        for sample in batch.context.samples
        if sample.identity.sample_identity_id
        == retained[0].content.canonical_sample_identity_id
    )
    receipt = replace(retained[0].content.evaluation_receipt, scanned_event_count=0)
    for state in ("failed", "not_evaluated", "unsupported"):
        technical = mint_cid_nl_method_assessment_ref(
            processing_owners=batch.context.processing_owners,
            sample=sample,
            method_subject=retained[0].content.method_subject,
            detector_invocation_id=f"test:{state}",
            evaluation_receipt=replace(
                receipt,
                search_completed=False,
                reason_code=f"test_{state}",
                acquisition_supported=state != "unsupported",
            ),
            technical_state=state,
        )
        with pytest.raises(ApplicationResultContractError):
            replace(first, method_assessments=(technical, retained[1]))
    retry_owners = replace(
        batch.context.processing_owners,
        attempt=issue_processing_attempt(
            result.processing_request, retry_of=result.processing_attempt
        ),
    )
    retry_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=retry_owners,
        sample=sample,
        method_subject=retained[0].content.method_subject,
        detector_invocation_id="test:retry",
        evaluation_receipt=receipt,
    )
    with pytest.raises(ApplicationResultContractError, match="owner binding"):
        validate(replace(first, method_assessments=(retry_assessment, retained[1])))
    import json

    request = result.processing_request
    other_request = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version=request.processing_algorithm_version + ":other",
        processing_contract_version=request.processing_contract_version,
        scientific_inputs=json.loads(request.normalized_scientific_inputs_json),
    )
    other_owners = replace(
        batch.context.processing_owners,
        request=other_request,
        attempt=issue_processing_attempt(other_request),
    )
    foreign_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=other_owners,
        sample=sample,
        method_subject=retained[0].content.method_subject,
        detector_invocation_id="test:foreign-request",
        evaluation_receipt=receipt,
    )
    with pytest.raises(ApplicationResultContractError, match="owner binding"):
        validate(replace(first, method_assessments=(foreign_assessment, retained[1])))


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
    ("group_sizes", "method_mode", "nearby", "family_context"),
    (
        ((2,), "single", False, False),
        ((1, 1), "single", False, False),
        ((2, 2), "single", False, False),
        pytest.param((2,), "single", True, False, id="nearby-single"),
        pytest.param((2,), "union", True, False, id="nearby-or-different-tags"),
        pytest.param((2,), "intersection", True, False, id="nearby-and-incomplete"),
        pytest.param((2,), "single", False, True, id="overlapping-family-one-peak"),
    ),
)
def test_profile_replay_closes_persisted_discovery_through_runtime_and_writer(
    tmp_path: Path,
    monkeypatch,
    group_sizes: tuple[int, ...],
    method_mode,
    nearby,
    family_context,
) -> None:
    from xic_extractor.alignment.csv_io import read_owner_bound_discovery_candidates_csv
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.discovery.physical_fact_persistence import (
        write_discovery_physical_fact_sidecar,
    )
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )
    from xic_extractor.peak_detection.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.processing_identity import IdentityContractError

    separate_groups = len(group_sizes) == 2
    sample_stems = tuple(f"S{index + 1}" for index in range(sum(group_sizes)))
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    # Observed RAW coordinates: acquisition bounds do not land on scan RTs.
    center_rt = 13.263299286133334
    precursor_mz = 307.1023254394531
    settings = replace(
        _settings() if method_mode == "single" else _multi_tag_settings(),
        neutral_loss_profile=None,
        tag_combine_mode=method_mode,
        rt_min=13.1,
        rt_max=17.4 if separate_groups else 13.4,
    )
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    if family_context:
        settings = replace(
            settings, neutral_loss_profile=None, seed_rt_gap_min=0.01, rt_min=12.8
        )
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=profile
    )
    from xic_extractor.extraction import (
        untargeted_cid_nl_real_data_runtime as runtime_module,
    )

    source_raws = {}
    opened_raws = _install_runtime_raws(
        monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path
    )
    receipts = {}
    csvs = {}
    candidates = {}
    physical_facts = {}
    source_records = {}
    family_evaluations = {}
    for path, source in zip(paths, spec.acquisition_inputs, strict=True):
        import numpy as np

        sample_offset = (
            4.0
            if separate_groups and sample_stems.index(path.stem) >= group_sizes[0]
            else 0.0
        )
        sample_center_rt = center_rt + sample_offset
        sample_index = sample_stems.index(path.stem)
        sample_mz = precursor_mz + (sample_index * 0.00030517578125 if nearby else 0.0)
        loss_index = sample_index if method_mode != "single" else 0
        raw = _FakeRaw(
            (8.0,),
            scan_precursor_mz=sample_mz,
            neutral_loss_das=(
                settings.neutral_loss_profiles[loss_index].neutral_loss_da,
            ),
            acquisition_source=source.acquisition_source,
        )
        raw.rt = np.linspace(12.517828061866668, 13.92044642215, 521) + sample_offset
        raw.rt[int(np.argmin(np.abs(raw.rt - sample_center_rt)))] = sample_center_rt
        raw.intensity = (
            1000.0
            * np.exp(
                -0.5
                * ((raw.rt - sample_center_rt) / (0.15 if family_context else 0.035))
                ** 2
            )
            + 250.0
        ) * (1.0 + sample_index * 0.4 if nearby else 1.0)
        raw.scans = tuple(
            replace(
                scan,
                rt=sample_center_rt,
                scan_number=int(np.argmin(np.abs(raw.rt - sample_center_rt))) + 1,
            )
            for scan in raw.scans
        )
        if family_context:
            raw.scans = tuple(
                replace(
                    raw.scans[0],
                    rt=query_rt,
                    scan_number=int(np.argmin(np.abs(raw.rt - query_rt))) + 1,
                )
                for query_rt in (sample_center_rt - 0.25, sample_center_rt + 0.05)
            )
        source_raws[path] = raw
        with runtime_module.open_raw(
            path, tmp_path, expected_acquisition_source=source.acquisition_source
        ) as discovery_raw:
            seeds = collect_strict_nl_seeds(
                discovery_raw, raw_file=path, settings=settings
            )
            discovery = _backfill_ms1_candidates_with_acquisition(
                discovery_raw,
                group_discovery_seeds(seeds, settings=settings),
                acquisition_source=source.acquisition_source,
                run_spec_id=spec.run_spec_id,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=profile,
            )
        folder = tmp_path / path.stem
        folder.mkdir()
        receipts.update(
            _discovery_receipts(folder, (path,), {path.stem: discovery.candidates})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.v1.json",
            run_spec=spec,
            acquisition_input=source,
            work_manifest=discovery.physical_work_manifest,
            records=discovery.physical_evaluations,
        )
        candidates[path.stem] = read_owner_bound_discovery_candidates_csv(
            csvs[path.stem], receipt=receipts[path.stem], require_successor_columns=True
        )
        source_records.update(
            (record.candidate_id, record) for record in discovery.physical_evaluations
        )
        if family_context:
            assert len(candidates[path.stem]) == 2
            assert all(candidate.ms1_peak_found for candidate in candidates[path.stem])
            left, right = discovery.physical_evaluations
            assert left.work_item.xic_rt_max > right.work_item.xic_rt_min
            rt_min = min(
                record.work_item.xic_rt_min for record in discovery.physical_evaluations
            )
            rt_max = max(
                record.work_item.xic_rt_max for record in discovery.physical_evaluations
            )
            # The vendor boundary includes the end scan returned for the request,
            # which may lie just beyond the requested RT upper bound.
            vendor = opened_raws[path][0]
            start = vendor.ScanNumberFromRetentionTime(rt_min) - 1
            end = vendor.ScanNumberFromRetentionTime(rt_max)
            prepared = prepare_physical_signal(
                raw.rt[start:end], raw.intensity[start:end], profile=profile
            )
            for record in discovery.physical_evaluations:
                family_evaluations[record.candidate_id] = evaluate_physical_peak(
                    prepared,
                    selection_subject_id=record.evaluation.question.selection_subject_id,
                    query_rt_min=record.evaluation.question.query_rt_min,
                    request_context_rt_start_min=rt_min,
                    request_context_rt_end_min=rt_max,
                )
            assert (
                len(
                    {
                        family_evaluations[
                            record.candidate_id
                        ].facts.physical_segment_id
                        for record in discovery.physical_evaluations
                        if family_evaluations[record.candidate_id].facts is not None
                    }
                )
                == 1
            )
            assert all(
                family_evaluations[record.candidate_id].facts is not None
                for record in discovery.physical_evaluations
            )
            continue
        (record,) = discovery.physical_evaluations
        assert record.evaluation.facts is not None
        assert (
            record.evaluation.question.context_rt_start_min
            != record.work_item.xic_rt_min
        )
        assert (
            record.evaluation.question.context_rt_end_min != record.work_item.xic_rt_max
        )
        physical_facts[path.stem] = record.evaluation.facts
    discovery_batch = DiscoveryBatchInput(
        sample_order=sample_stems,
        candidate_csvs=csvs,
        raw_files={path.stem: path for path in paths},
        review_csvs={path.stem: None for path in paths},
        candidate_receipts=receipts,
    )
    captured_batches = []
    captured_peaks = []
    from xic_extractor.peak_detection import capture as capture_owner

    capture_peak = capture_owner.capture_peak_hypotheses
    capture_reacquired = capture_owner.capture_reacquired_physical_peak

    def retain_capture(**kwargs):
        capture = capture_peak(**kwargs)
        captured_peaks.append(capture)
        return capture

    monkeypatch.setattr(capture_owner, "capture_peak_hypotheses", retain_capture)

    def retain_reacquired_capture(**kwargs):
        capture = capture_reacquired(**kwargs)
        captured_peaks.append(capture)
        return capture

    monkeypatch.setattr(
        capture_owner, "capture_reacquired_physical_peak", retain_reacquired_capture
    )
    build_batch = alignment_adapter.build_untargeted_cid_nl_production_batch

    def retain_batch(**kwargs):
        batch = build_batch(**kwargs)
        captured_batches.append(batch)
        return batch

    monkeypatch.setattr(
        alignment_adapter, "build_untargeted_cid_nl_production_batch", retain_batch
    )
    from xic_extractor.alignment import cross_sample_reconciliation
    from xic_extractor.alignment import workflow as workflow_owner
    from xic_extractor.alignment.mode_track import producer as producer_owner
    from xic_extractor.alignment.mode_track import refinement as refinement_owner
    from xic_extractor.extraction import (
        untargeted_cid_nl_production_batch as production,
    )
    from xic_extractor.peak_detection import observations as observation_owner

    bind_capture = capture_owner.bind_owner_backed_capture_hypothesis
    evaluate_physical = observation_owner.evaluate_physical_peak
    binding_rechecks = []
    bound_captures = []

    def record_binding(capture, **kwargs):
        rechecks = []

        def record_evaluation(*args, **evaluation_kwargs):
            rechecks.append(True)
            return evaluate_physical(*args, **evaluation_kwargs)

        with monkeypatch.context() as binding_patch:
            for owner in (capture_owner, observation_owner):
                binding_patch.setattr(
                    owner, "evaluate_physical_peak", record_evaluation
                )
            bound = bind_capture(capture, **kwargs)
        binding_rechecks.append(len(rechecks))
        bound_captures.append((capture, kwargs))
        return bound

    for owner in (production, workflow_owner):
        monkeypatch.setattr(
            owner, "bind_owner_backed_capture_hypothesis", record_binding
        )

    def unexpected_mt0(*args, **kwargs):
        raise AssertionError("retained production must not execute MT0 refinement")

    _forbid_retained_production_repair(monkeypatch)

    for model_operation in (
        "fit_mode_track_model_set",
        "assign_mode_tracks",
        "mint_mode_track_fit_once_sample_source",
        "mint_mode_track_fit_once_source",
    ):
        monkeypatch.setattr(producer_owner, model_operation, unexpected_mt0)

    monkeypatch.setattr(
        refinement_owner, "mint_mode_track_trajectory_input", unexpected_mt0
    )
    monkeypatch.setattr(
        refinement_owner, "_propose_mode_track_refinement", unexpected_mt0
    )
    monkeypatch.setattr(
        cross_sample_reconciliation,
        "apply_accepted_mode_track_refinement",
        unexpected_mt0,
    )

    retained_contexts = []
    retained_initial_edges = []
    consumed_contexts = []
    build_initial_context = production.build_initial_cid_nl_cross_sample_context
    close_without_repair = workflow_owner.close_cross_sample_without_observation_repair

    def retain_initial_context(**kwargs):
        family, context, edges = build_initial_context(**kwargs)
        retained_contexts.append(context)
        retained_initial_edges.extend(edges)
        return family, context, edges

    def retain_consumed_context(context):
        consumed_contexts.append(context)
        return close_without_repair(context)

    monkeypatch.setattr(
        workflow_owner,
        "bootstrap_mode_track_refinement_from_cid_nl_support",
        unexpected_mt0,
    )
    monkeypatch.setattr(
        production,
        "build_initial_cid_nl_cross_sample_context",
        retain_initial_context,
    )
    monkeypatch.setattr(
        workflow_owner, "apply_mode_track_refinement_run_for_workflow", unexpected_mt0
    )
    monkeypatch.setattr(
        workflow_owner,
        "close_cross_sample_without_observation_repair",
        retain_consumed_context,
    )

    runtime_results = []
    runtime_execute = production.execute_untargeted_cid_nl_real_data_runtime

    def checked_runtime(*args, **kwargs):
        result = runtime_execute(*args, **kwargs)
        assert result.succeeded, tuple(
            settlement.failure_detail.message
            for settlement in result.finalization.coordinator.state.runtime.settlements
            if settlement.failure_detail is not None
        )
        runtime_results.append(result)
        if family_context:
            for sample_result in result.sample_results:
                adapter = sample_result.adapter_result
                stem = sample_result.job.sample.metadata.raw_stem
                assert set(adapter.source_candidate_ids) == {
                    candidate.candidate_id for candidate in candidates[stem]
                }
                assert (
                    captured_batches[0].context.physical_records_by_candidate
                    == source_records
                )
                assert adapter.acquisition is not None
                assert len(adapter.acquisition.traces) == 1
                assert opened_raws[tmp_path / f"{stem}.synthetic"][
                    -1
                ].chromatogram_request_counts == [1]
                assert len(adapter.assessment_input.assessment.candidate_peaks) == 1
                from xic_extractor.alignment.hypothesis_owner_adapter import (
                    compose_peak_hypotheses_from_assessment_universe,
                )

                hypotheses = compose_peak_hypotheses_from_assessment_universe(
                    processing_owners=sample_result.job.processing_owners,
                    sample=sample_result.job.sample,
                    assessment=adapter.assessment_input.assessment,
                )
                assert len(hypotheses) == 1
                assert {capture.candidate_id for capture in adapter.captures} == set(
                    adapter.source_candidate_ids
                )
                for capture in adapter.captures:
                    expected = family_evaluations[capture.candidate_id]
                    assert capture.physical_evaluation.facts == expected.facts
        return result

    monkeypatch.setattr(
        production, "execute_untargeted_cid_nl_real_data_runtime", checked_runtime
    )
    completed = alignment_adapter.complete_untargeted_cid_nl_alignment(
        discovery_batch=discovery_batch,
        candidates_by_sample=candidates,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "alignment",
        settings=settings,
        peak_config=peak_config,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        signal_processing_profile=profile,
    )
    _assert_jobs_match_direct_construction(captured_batches[0])
    writer = project_completed_untarget_batch_writer_inputs(completed)
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix_path = tmp_path / "canonical-matrix.tsv"
    write_alignment_matrix_tsv(
        matrix_path, writer.matrix, production_decisions=writer.production_decisions
    )
    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    assert len(retained_contexts) == len(completed.application_result.children)
    assert len(retained_contexts) == len(consumed_contexts)
    assert all(
        retained is consumed
        for retained, consumed in zip(retained_contexts, consumed_contexts, strict=True)
    )
    for context, child in zip(
        retained_contexts, completed.application_result.children, strict=True
    ):
        assert context.fit_once_source_id is None
        assert child.scientific_core.final_population == context.population
        assert child.scientific_core.lineage == ()
        assert child.scientific_core.final_cross_sample_receipt.repair_terminal is None
    assert completed.initial_edges == tuple(retained_initial_edges)
    # Binding consumes the closed capture; it must not repeat physical selection.
    assert not any(binding_rechecks)
    if method_mode != "intersection":
        assert binding_rechecks
    if family_context:
        assert len(matrix_rows) == 1
        assert len(writer.matrix.cells) == len(sample_stems)
        return
    if nearby:
        assert len(matrix_rows) == (0 if method_mode == "intersection" else 1)
        batch = captured_batches[0]
        assert len(batch.subject_batch.subjects) == 1
        for result in runtime_results[0].sample_results:
            stem = result.job.sample.metadata.raw_stem
            (candidate,) = batch.context.candidates_by_sample[stem]
            record = batch.context.physical_records_by_candidate[candidate.candidate_id]
            assert record.work_item.precursor_mz == candidate.precursor_mz
            assert record.acquired_trace.coordinate.request.mz == candidate.precursor_mz
            assert record.evaluation.facts == physical_facts[stem]
            assert result.effective_assessment.content.method_result == (
                "HOLD" if method_mode == "intersection" else "SUPPORT"
            )
        if method_mode == "intersection":
            assert writer.matrix.cells == ()
            assert writer.matrix.clusters == ()
            assert all(
                "incomplete_composite_requirement"
                in result.effective_assessment.content.reason_codes
                for result in runtime_results[0].sample_results
            )
            return
    assert len(writer.matrix.cells) == len(group_sizes) * len(sample_stems)
    for cell in writer.matrix.cells:
        if cell.selected_integration is None:
            assert separate_groups
            assert not writer.production_decisions.cell(
                cell.cluster_id, cell.sample_stem
            ).write_matrix_value
            continue
        facts = physical_facts[cell.sample_stem]
        assert cell.area == facts.area_counts_seconds
        assert cell.selected_integration is not None
        assert cell.selected_integration.area_raw_counts_seconds != cell.area
        assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
        assert cell.selected_integration.height_smoothed == facts.height
        assert facts.physical_segment_id in cell.selected_integration.boundary_sources
    expected_row_count = sum(size > 1 for size in group_sizes)
    assert len(matrix_rows) == expected_row_count
    for sample_stem, facts in physical_facts.items():
        sample_values = [
            float(row[sample_stem]) for row in matrix_rows if row[sample_stem]
        ]
        assert sample_values == pytest.approx(
            [facts.area_counts_seconds] if expected_row_count else []
        )
    if nearby:
        assert len({facts.fact_content_id for facts in physical_facts.values()}) == 2
        assert len({cell.source_candidate_id for cell in writer.matrix.cells}) == 2
        assert len(matrix_rows) == 1
        return
    if separate_groups:
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert len(scientific.final_population) == 2
        assert len(scientific.cell_observations) == 2 * len(sample_stems)
        assert sum(
            cell.evidence_states == ("not_searched",)
            for cell in scientific.cell_observations
        ) == len(sample_stems)
        assert all(
            sum(bool(row[stem]) for stem in sample_stems) == 2 for row in matrix_rows
        )
        assert all(
            result.effective_assessment.content.method_result == "SUPPORT"
            for result in runtime_results[0].sample_results
        )
        _assert_outside_group_cells(scientific, captured_batches[0], tmp_path)
        return

    from xic_extractor.peak_detection.observations import (
        PeakObservationContractError,
        mint_peak_observation_ref,
        physical_peak_observation_integration,
    )

    capture = captured_peaks[0]
    evaluation = capture.physical_evaluation
    assert evaluation is not None
    integration = physical_peak_observation_integration(evaluation)
    prepared = prepare_physical_signal(
        capture.trace.rt, capture.trace.intensity, profile=profile
    )
    assert prepared.prepared is not None
    observation_args = dict(
        processing_owners=captured_batches[0].jobs[0].processing_owners,
        sample=captured_batches[0].jobs[0].sample,
        trace=capture.trace,
        integration=integration,
        selection_intensity=prepared.prepared.positive_signal,
        resolver_mode=peak_config.resolver_mode,
        proposal_sources=("canonical_physical_segment_v1",),
        physical_evaluation=evaluation,
        signal_processing_profile=profile,
    )
    for substitution in (
        {"signal_processing_profile": SignalProcessingProfile(window_points=21)},
        {"trace": replace(capture.trace, intensity=capture.trace.intensity + 1.0)},
        {
            "integration": replace(
                integration,
                area_baseline_corrected=integration.area_baseline_corrected + 1.0,
            )
        },
    ):
        with pytest.raises((PeakObservationContractError, ValueError)):
            mint_peak_observation_ref(**(observation_args | substitution))

    bound_capture, binding_args = bound_captures[0]
    changed_source = capture_peak(
        scope=bound_capture.scope,
        candidate_id=bound_capture.candidate_id,
        trace=replace(bound_capture.trace, source="different-capture-source"),
    )
    with pytest.raises(capture_owner.SampleLocalCaptureError):
        bind_capture(changed_source, **binding_args)
    mutable_capture = capture_peak(
        scope=bound_capture.scope,
        candidate_id=bound_capture.candidate_id,
        trace=replace(
            bound_capture.trace, intensity=bound_capture.trace.intensity.copy()
        ),
    )
    mutable_capture.trace.intensity[-1] += 1.0
    with pytest.raises(capture_owner.SampleLocalCaptureError):
        bind_capture(mutable_capture, **binding_args)
    from xic_extractor.peak_detection.search_scope import SearchScopeContractError

    changed_answer = evaluate_physical_peak(
        prepared,
        selection_subject_id="substituted-selection-subject",
        query_rt_min=evaluation.question.query_rt_min,
        request_context_rt_start_min=capture.trace.rt_min,
        request_context_rt_end_min=capture.trace.rt_max,
    )
    with pytest.raises(SearchScopeContractError, match="answer is not bound"):
        replace(
            capture.scope,
            capture_bindings=tuple(
                replace(binding, physical_evaluation=changed_answer)
                if binding.candidate_id == capture.candidate_id
                else binding
                for binding in capture.scope.capture_bindings
            ),
        )
    batch = captured_batches[0]
    from xic_extractor.extraction import (
        untargeted_cid_nl_real_data_runtime as runtime_module,
    )
    from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
        UntargetedCidNlRealDataAdapterError,
        recover_untargeted_cid_nl_sample_subject,
    )
    from xic_extractor.peak_detection.cid_nl_peak_support import (
        DdaCidNlPeakSupportPolicy,
    )
    from xic_extractor.processing_identity import resolve_request_acquisition_source

    job = batch.jobs[0]
    recovery_raw = runtime_module.open_raw(
        paths[0],
        tmp_path,
        expected_acquisition_source=resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        ),
    )
    recovery_args = dict(
        processing_owners=job.processing_owners,
        sample=job.sample,
        method_subject=job.method_subject,
        group_center_rt=center_rt,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=10.0
        ),
        peak_config=peak_config,
        raw=recovery_raw,
        settings=settings,
        signal_processing_profile=profile,
    )
    recovered = recover_untargeted_cid_nl_sample_subject(**recovery_args)
    (observation,) = recovered.assessment.candidate_peaks
    acquired = recovered.acquisition.traces[0]
    recovery_prepared = prepare_physical_signal(
        acquired.trace.rt, acquired.trace.intensity, profile=profile
    )
    expected_recovery = evaluate_physical_peak(
        recovery_prepared,
        selection_subject_id=job.method_subject.method_subject_id,
        query_rt_min=center_rt,
        request_context_rt_start_min=center_rt
        - peak_config.nl_rt_anchor_half_window_min,
        request_context_rt_end_min=center_rt + peak_config.nl_rt_anchor_half_window_min,
    )
    assert expected_recovery.facts is not None
    assert (
        observation.content.integration.area_baseline_corrected
        == expected_recovery.facts.area_counts_seconds
    )
    assert (
        observation.content.integration.height_smoothed
        == expected_recovery.facts.height
    )
    assert recovered.assessment.content.method_result == "HOLD"
    with pytest.raises(
        capture_owner.SampleLocalCaptureError, match="question is not request-bound"
    ):
        recover_untargeted_cid_nl_sample_subject(
            **(
                recovery_args
                | {
                    "signal_processing_profile": SignalProcessingProfile(
                        window_points=21
                    )
                }
            )
        )
    with pytest.raises(
        UntargetedCidNlRealDataAdapterError, match="lost its request-bound profile"
    ):
        recover_untargeted_cid_nl_sample_subject(
            **(recovery_args | {"signal_processing_profile": None})
        )
    original_intensity = source_raws[paths[0]].intensity
    try:
        source_raws[paths[0]].intensity = original_intensity * 0.0
        unavailable = recover_untargeted_cid_nl_sample_subject(**recovery_args)
        assert unavailable.assessment.candidate_peaks == ()
        assert unavailable.assessment.content.method_result == "HOLD"
        assert unavailable.recovery_input is None
    finally:
        source_raws[paths[0]].intensity = original_intensity
        recovery_raw.__exit__(None, None, None)
    from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
        UntargetedCidNlRealDataRuntimeError,
    )

    with pytest.raises(
        UntargetedCidNlRealDataRuntimeError, match="profile is not request-bound"
    ):
        replace(
            batch.jobs[0],
            signal_processing_profile=SignalProcessingProfile(window_points=21),
        )
    first_job = batch.jobs[0]
    sample_inputs = {
        item.name: getattr(first_job, item.name)
        for item in fields(first_job)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    subject_work_units = tuple(
        (job.method_subject, job.work_unit_key)
        for job in batch.jobs
        if job.sample == first_job.sample
    )
    for changed, message in (
        ({"signal_processing_profile": None}, "lost its profile"),
        (
            {"signal_processing_profile": SignalProcessingProfile(window_points=21)},
            "profile is not request-bound",
        ),
        ({"physical_records": ()}, "candidate coverage changed"),
    ):
        with pytest.raises(UntargetedCidNlRealDataRuntimeError, match=message):
            build_untargeted_cid_nl_sample_runtime_jobs(
                **(sample_inputs | changed), subject_work_units=subject_work_units
            )
    wrong_records = dict(batch.context.physical_records_by_candidate)
    keys = tuple(wrong_records)
    wrong_records[keys[0]] = wrong_records[keys[1]]
    with pytest.raises(IdentityContractError, match="crossed candidate"):
        build_batch(
            raw_paths=paths,
            sample_metadata=tuple(sample.metadata for sample in batch.context.samples),
            settings=settings,
            peak_config=peak_config,
            candidates_by_sample=batch.context.candidates_by_sample,
            discovery_receipts_by_sample=receipts,
            dll_dir=tmp_path,
            sample_identity_namespace="test:physical-replay",
            signal_processing_profile=profile,
            physical_records_by_candidate=wrong_records,
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
        group=SimpleNamespace(member_subjects=()),
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


@pytest.mark.parametrize(
    ("masses", "losses", "ppm", "centers"),
    (
        ((500.0, 500.004), (116.0474, 116.0474), 20.0, (500.0, 500.0)),
        ((500.0, 500.02), (116.0474, 116.0474), 20.0, (500.0, 500.02)),
        (
            (500.0, 500.008, 500.016),
            (116.0474,) * 3,
            20.0,
            (500.0, 500.0, 500.016),
        ),
        (
            (512.0, 512.0078125),
            (116.0474, 116.0474),
            15.2587890625,
            (512.0, 512.0),
        ),
        ((500.0, 500.0), (116.0474, 132.0423), 20.0, (500.0, 500.0)),
    ),
    ids=("nearby", "outside", "fixed-anchor", "inclusive-bound", "scalar-losses"),
)
def test_production_batch_preserves_bounded_subject_membership(
    tmp_path: Path, masses, losses, ppm, centers
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=ppm
    )
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )

    candidates = tuple(
        replace(
            candidates[0],
            candidate_id=f"{raw_path.stem}:membership-{index}",
            best_ms2_scan_id=index + 100,
            precursor_mz=mz,
            product_mz=mz - loss,
            configured_neutral_loss_da=loss,
        )
        for index, (mz, loss) in enumerate(zip(masses, losses, strict=True))
    )
    arguments = dict(
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
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )

    batch = build_untargeted_cid_nl_production_batch(
        **arguments, candidates_by_sample={"S1": candidates}
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    expected_memberships = tuple(
        sorted(
            (
                candidate.candidate_id,
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=center,
                    precursor_tolerance_ppm=ppm,
                    neutral_loss_da=loss,
                ).method_subject_id,
            )
            for candidate, center, loss in zip(candidates, centers, losses, strict=True)
        )
    )
    assert batch.subject_batch.candidate_memberships == expected_memberships
    assert len(batch.subject_batch.subjects) == len(
        set(zip(centers, losses, strict=True))
    )
    permuted = build_untargeted_cid_nl_production_batch(
        **arguments, candidates_by_sample={"S1": tuple(reversed(candidates))}
    )
    assert (
        permuted.subject_batch.request_payload()
        == batch.subject_batch.request_payload()
    )
    assert tuple(candidate.precursor_mz for candidate in candidates) == masses
    assert tuple(job.method_subject for job in batch.jobs) == (
        batch.subject_batch.subjects
    )
    assert all(
        job.candidates is batch.context.candidates_by_sample["S1"] for job in batch.jobs
    )
    _assert_jobs_match_direct_construction(batch)


def test_sample_job_builder_rejects_changed_request_bound_inputs(
    tmp_path: Path,
) -> None:
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=peak_config
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
            tmp_path, (raw_path,), {"S1": candidates}
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )
    first = batch.jobs[0]
    shared = {
        item.name: getattr(first, item.name)
        for item in fields(first)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    work_units = tuple((job.method_subject, job.work_unit_key) for job in batch.jobs)
    assert len(work_units) == 2
    assert (
        build_untargeted_cid_nl_sample_runtime_jobs(
            **shared, subject_work_units=work_units
        )
        == batch.jobs
    )
    other_dir = tmp_path / "other-request"
    other_dir.mkdir()
    other = build_processing_identity_no_raw_fixture(other_dir, sample_stems=("Other",))
    changed_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=600.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    for subject, key, message in (
        (changed_subject, "changed-subject", "not an exact member"),
        (None, "changed-subject", "wrong owner type"),
        (work_units[1][0], "", "work_unit_key"),
    ):
        with pytest.raises(UntargetedCidNlRealDataRuntimeError, match=message):
            build_untargeted_cid_nl_sample_runtime_jobs(
                **shared, subject_work_units=(work_units[0], (subject, key))
            )
    for changed, message in (
        ({"processing_owners": other.owners}, "supported real-data runtime contract"),
        ({"sample": other.sample("Other")}, "candidate universe"),
        ({"settings": replace(settings, rt_min=7.0)}, "Discovery settings"),
        ({"peak_config": replace(peak_config, smooth_window=17)}, "peak settings"),
        (
            {"candidates": (replace(candidates[0], reason="changed"), *candidates[1:])},
            "candidate universe",
        ),
    ):
        with pytest.raises(UntargetedCidNlRealDataRuntimeError, match=message):
            build_untargeted_cid_nl_sample_runtime_jobs(
                **(shared | changed), subject_work_units=work_units
            )


def test_alignment_adapter_builds_executes_and_composes_once(
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
    raw_path.write_bytes(b"production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
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
            sample_identity_namespace="study:production-batch",
        )
    except ValueError as exc:
        assert "single-provider" in str(exc)
    else:
        raise AssertionError("duplicate tag settings must be rejected")


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
        sample_identity_namespace="study:production-batch",
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
            "production-batch",
        ),
    )

    assert result.succeeded
    assert len(result.sample_results) == len(batch.jobs) == 2
    assert vendor_raw.chromatogram_request_counts == [2]

    validated_batches = []
    validate_batch = application_result_owner._validate_untarget_batch_request

    def record_batch_validation(request):
        validate_batch(request)
        validated_batches.append(request)

    monkeypatch.setattr(
        application_result_owner,
        "_validate_untarget_batch_request",
        record_batch_validation,
    )
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
    # Full owner validation is cohort-sized; sealing the same batch must reuse it.
    assert len(validated_batches) == 1


@pytest.mark.parametrize(
    "scenario",
    (
        "mixed",
        "all_hold",
        "all_reject",
        "hold_reject",
        "other_support",
        "ambiguous_hold_below_threshold",
        "ambiguous_hold_above_threshold",
    ),
)
def test_production_composition_closes_observation_only_hold_without_membership(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    _forbid_retained_production_repair(monkeypatch)
    from xic_extractor.extraction import (
        untargeted_cid_nl_production_batch as production,
    )

    execute_runtime = production.execute_untargeted_cid_nl_real_data_runtime

    def checked_runtime(*args, **kwargs):
        result = execute_runtime(*args, **kwargs)
        assert result.succeeded, tuple(
            settlement.failure_detail.message
            for settlement in result.finalization.coordinator.state.runtime.settlements
            if settlement.failure_detail is not None
        )
        return result

    monkeypatch.setattr(
        production, "execute_untargeted_cid_nl_real_data_runtime", checked_runtime
    )
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-hold:{raw_path.stem}".encode())
    # The other-support case must keep its independently rejected workset.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)

    def observation_only_raw(peak_rts: tuple[float, ...]) -> _FakeRaw:
        # MS1 stays usable, but each NL scan precedes its integration interval.
        # The narrow peak fits inside the scan-centered discovery query.
        raw = _FakeRaw(tuple(rt - 0.15 for rt in peak_rts))
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
    if scenario in {"all_hold", "hold_reject"}:
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
    support = _current_candidates(
        source_raws[raw_paths[0]],
        raw_file=raw_paths[0],
        settings=settings,
        peak_config=peak_config,
    )[0]
    direct_hold_candidates = _current_candidates(
        source_raws[raw_paths[1]],
        raw_file=raw_paths[1],
        settings=settings,
        peak_config=peak_config,
    )
    direct_hold = direct_hold_candidates[0]
    candidates_by_sample = {
        "Support": (support,),
        "Hold": (direct_hold,),
    }
    if ambiguous:
        candidates_by_sample["Hold"] = direct_hold_candidates
    if scenario in {"all_reject", "hold_reject"}:
        for stem in rejected_samples:
            candidate = support if stem == "Support" else direct_hold
            candidates_by_sample[stem] = (candidate,)
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
                    peak_config=peak_config,
                )[0]
                for raw in (first_raw, second_raw)
            )
            source_raw = second_raw
            source_raw.scans = (first_raw.scans[0], second_raw.scans[0])
            source_raws[raw_path] = source_raw
            first, second = sorted(nearby, key=lambda item: item.precursor_mz)
            candidates_by_sample[raw_path.stem] = (first, second)
    if above_threshold:
        candidates_by_sample["Support2"] = _current_candidates(
            source_raws[raw_paths[2]],
            raw_file=raw_paths[2],
            settings=settings,
            peak_config=peak_config,
        )
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-hold",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "hold.runtime.json",
            "production-hold",
        ),
    )
    hold_observation_ids = tuple(
        peak.content.peak_observation_content_id
        for result in runtime_result.sample_results
        for peak in result.effective_assessment.candidate_peaks
        if result.effective_assessment.content.method_result == "HOLD"
    )
    assert runtime_result.succeeded
    assert (
        len(hold_observation_ids)
        == {
            "mixed": 1,
            "all_hold": 2,
            "all_reject": 0,
            "hold_reject": 1,
            "other_support": 2,
            "ambiguous_hold_below_threshold": 2,
            "ambiguous_hold_above_threshold": 2,
        }[scenario]
    )

    opens_before_composition = sum(map(len, opened.values()))
    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert not set(hold_observation_ids) & set(
        completed.application_result.physical_observation_ids
    )
    writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
    assert writer_inputs.matrix.sample_order == stems
    if scenario == "mixed":
        (child,) = completed.application_result.children
        (group,) = child.scientific_core.final_population
        member_ids = {
            physical_id
            for member in group.member_subjects
            for physical_id in member.physical_observation_ids
        }
        retained_observations = {
            observation.content.peak_observation_content_id: observation
            for cell in child.scientific_core.cell_observations
            for observation in cell.direct_observations
        }
        assert set(hold_observation_ids) <= set(retained_observations)
        assert not set(hold_observation_ids) & member_ids
        (row,) = writer_inputs.matrix.clusters
        assert row.physical_observation_ids == tuple(sorted(member_ids))
        member_observations = tuple(
            retained_observations[physical_id] for physical_id in sorted(member_ids)
        )
        assert row.family_center_mz == pytest.approx(
            sum(observation.content.mz for observation in member_observations)
            / len(member_observations)
        )
        assert row.family_center_rt == pytest.approx(
            sum(
                observation.content.integration.rt_apex_min
                for observation in member_observations
            )
            / len(member_observations)
        )
    if above_threshold:
        from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert {subject.candidate_id for subject in scientific.terminal_subjects} == {
            candidate.candidate_id
            for values in candidates_by_sample.values()
            for candidate in values
        }
        hold_cell = next(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Hold"
        )
        (source,) = hold_cell.predecessor_cells
        assessment = next(
            result.effective_assessment
            for result in runtime_result.sample_results
            if result.job.sample.metadata.raw_stem == "Hold"
        )
        assert source.method_assessment is assessment
        assert assessment.content.method_result == "HOLD"
        assert len(assessment.candidate_peaks) == 2
        assert source.peak_observation is source.peak_hypothesis is None
        assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
        position = next(
            item
            for item in scientific.cell_observation_handoff.coverage.epoch.positions
            if item.sample.metadata.raw_stem == "Hold"
        )
        assert position.conservative_no_request_proof is None
        assert position.primary_logical_requests == ()
        assert all(
            not result.recovery_attempts for result in runtime_result.sample_results
        )
        assert sum(map(len, opened.values())) == opens_before_composition == 3
        cells = {cell.sample_stem: cell for cell in writer_inputs.matrix.cells}
        assert cells["Hold"].status == "ambiguous_ms1_owner"
        for stem in stems:
            decision = writer_inputs.production_decisions.cell(
                cells[stem].cluster_id, stem
            )
            assert decision.write_matrix_value is (stem != "Hold")
            assert (decision.matrix_value is None) is (stem == "Hold")
        matrix_path = tmp_path / "qualified-ambiguous-hold.tsv"
        write_alignment_matrix_tsv(
            matrix_path,
            writer_inputs.matrix,
            production_decisions=writer_inputs.production_decisions,
        )
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            (row,) = tuple(csv.DictReader(handle, delimiter="\t"))
        assert row["Hold"] == ""
        assert float(row["Support"]) > 0 and float(row["Support2"]) > 0
        return
    if scenario == "ambiguous_hold_below_threshold":
        from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

        assert all(
            not result.recovery_attempts for result in runtime_result.sample_results
        )
        assert sum(map(len, opened.values())) == opens_before_composition == 2
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        handoff = scientific.cell_observation_handoff
        coverage = handoff.coverage
        position = next(
            item
            for item in coverage.epoch.positions
            if item.sample.metadata.raw_stem == "Hold"
        )
        proof = position.conservative_no_request_proof
        assert proof.variant == "threshold_not_met"
        assert proof.completion is None
        assert proof.decision.group_facts.direct_support_count_s == 1
        assert proof.decision.group_facts.minimum_support_k == 2
        assert not proof.decision.group_facts.may_attempt_recovery
        assert proof.decision.reason_codes == ("direct_support_threshold_not_met",)
        assert position.primary_logical_requests == coverage.terminals == ()
        hold_cell = next(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Hold"
        )
        assert hold_cell.evidence_states == ("not_searched",)
        assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
        (source,) = hold_cell.predecessor_cells
        assert source is proof.decision.target_cell
        assert source.content.method_result == "HOLD"
        assert len(source.method_assessment.candidate_peaks) == 2
        assert source.method_assessment is next(
            result.adapter_result.assessment_input.assessment
            for result in runtime_result.sample_results
            if result.job.sample.metadata.raw_stem == "Hold"
        )
        assert source.peak_observation is source.peak_hypothesis is None
        assert all(
            not item.write_matrix_value and item.matrix_value is None
            for item in writer_inputs.production_decisions.cells.values()
            if item.sample_stem == "Hold"
        )
        matrix_path = tmp_path / "ambiguous-hold-matrix.tsv"
        write_alignment_matrix_tsv(
            matrix_path,
            writer_inputs.matrix,
            production_decisions=writer_inputs.production_decisions,
        )
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            assert all(
                row["Hold"] == "" for row in csv.DictReader(handle, delimiter="\t")
            )
        _assert_conservative_proof_seals(
            proof, position, coverage, handoff, hold_cell, batch, runtime_result
        )
        return
    if scenario != "mixed":
        _assert_single_unsupported_exclusion(
            completed,
            runtime_result,
            batch,
            tmp_path,
            populated=scenario == "other_support",
        )
        return
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


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_production_initial_groups_use_complete_canonical_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    from xic_extractor.alignment.cell_observations import CellObservationContractError
    from xic_extractor.extraction import (
        untargeted_cid_nl_production_batch as production,
    )

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
            raws[path], raw_file=path, settings=settings, peak_config=peak_config
        )
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:canonical-batch",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "canonical-batch.runtime.json", "canonical-batch"
        ),
    )
    assert runtime.succeeded
    initial_contexts = []
    initial_edges = []
    cell_contexts = []
    build_context = production.build_initial_cid_nl_cross_sample_context
    compose_cells = production.compose_cid_nl_predecessor_cells

    def retain_context(**kwargs):
        result = build_context(**kwargs)
        initial_contexts.append(result[0])
        initial_edges.extend(result[2])
        return result

    monkeypatch.setattr(
        production, "build_initial_cid_nl_cross_sample_context", retain_context
    )

    def retain_cells(**kwargs):
        cells = compose_cells(**kwargs)
        cell_contexts.extend(cell.group_context for cell in cells)
        return cells

    monkeypatch.setattr(production, "compose_cid_nl_predecessor_cells", retain_cells)
    compose_untargeted_cid_nl_production_result(
        batch, runtime, alignment_config=AlignmentConfig()
    )
    (context,) = initial_contexts
    (group,) = context.mode_tracks
    assert {owner.sample_stem for owner in group.owners} == set(
        stems[:supported_samples]
    )
    policy = (
        "owner_full_batch_continuity"
        if supported_samples == len(stems)
        else "owner_injection_trajectory"
    )
    assert group.evidence.startswith(f"{policy};")
    assert initial_edges
    assert all(edge.disposition == "eligible" for edge in initial_edges)
    assert all(
        edge.initial_evidence is not None
        and edge.initial_evidence.seed_support_level == "moderate"
        and edge.initial_evidence.score == 45
        for edge in initial_edges
    )
    assert cell_contexts
    for cell_context in cell_contexts:
        assert cell_context.sample_universe == frozenset(stems)
        assert dict(cell_context.injection_order) == dict(zip(stems, (1, 2, 3)))
        if supported_samples == 2:
            # Missing or duplicate order cannot reproduce an injection-trajectory
            # group; the owner falls back to its different bounded policy.
            for order in (None, tuple((stem, 1) for stem in stems)):
                with pytest.raises(CellObservationContractError, match="exact SUPPORT"):
                    replace(cell_context, injection_order=order)
        else:
            # The full-batch policy does not require an injection trajectory.
            replace(cell_context, injection_order=None)


def test_known_istd_reference_closes_drifting_group_and_cell_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpyxl import Workbook

    from xic_extractor.alignment.drift_evidence import read_known_istd_reference

    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(2))
    settings = replace(_settings(), neutral_loss_profile=None, rt_max=10)
    peak_config = _peak_config(tmp_path)
    raws = {path: _FakeRaw((7.98, 8.0, 8.02)) for path in paths}
    for i, raw in enumerate(raws.values()):
        raw.rt += i * 1.067
        raw.scans = tuple(replace(scan, rt=scan.rt + i * 1.067) for scan in raw.scans)
    candidates = {}
    for path, raw in raws.items():
        path.write_bytes(path.stem.encode())
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            peak_config=peak_config,
        )
    batch = build_untargeted_cid_nl_production_batch(
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
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:known-istd",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "runtime.json", "reference"),
    )
    assert runtime.succeeded
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
    targets.append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50, ""])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    for i, path in enumerate(paths):
        results.append([path.stem, "known", "ISTD", 8.0 + i * 1.067, -999])
    reference_path = tmp_path / "reference.xlsx"
    workbook.save(reference_path)
    reference = read_known_istd_reference(reference_path)
    plain = compose_untargeted_cid_nl_production_result(
        batch,
        runtime,
        alignment_config=AlignmentConfig(),
    )
    corrected = compose_untargeted_cid_nl_production_result(
        batch,
        runtime,
        alignment_config=AlignmentConfig(),
        known_istd_reference=reference,
    )
    plain_matrix = project_completed_untarget_batch_writer_inputs(plain).matrix
    corrected_matrix = project_completed_untarget_batch_writer_inputs(corrected).matrix
    assert len(plain_matrix.clusters) == 2
    assert len(corrected_matrix.clusters) == 1
    assert {(cell.sample_stem, cell.apex_rt, cell.area)
            for cell in corrected_matrix.cells} == {
        (cell.sample_stem, cell.apex_rt, cell.area)
        for cell in plain_matrix.cells if cell.area is not None
    }
    assert all(cell.area is not None and cell.area > 0
               for cell in corrected_matrix.cells)
    from xic_extractor.alignment.cell_observations import CellObservationContractError

    (child,) = corrected.application_result.children
    contexts = tuple(source.group_context
                     for cell in child.scientific_core.cell_observations
                     for source in cell.predecessor_cells)
    assert contexts
    assert all(context.drift_lookup is contexts[0].drift_lookup for context in contexts)
    with pytest.raises(CellObservationContractError, match="exact SUPPORT"):
        replace(contexts[0], drift_lookup=None)


def _assert_conservative_coverage_handoff(
    completed, writer_inputs, batch, runtime_result, tmp_path, *, threshold: bool
) -> None:
    from xic_extractor.alignment.backfill.observation_repair import (
        authorize_observation_repair,
    )
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    (child,) = completed.application_result.children
    scientific = child.scientific_core
    handoff = scientific.cell_observation_handoff
    assert handoff is not None
    coverage = handoff.coverage
    (position,) = tuple(
        position
        for position in coverage.epoch.positions
        if position.sample.metadata.raw_stem == "Hold"
    )
    proof = position.conservative_no_request_proof
    assert proof is not None
    assert proof.variant == (
        "threshold_not_met" if threshold else "completed_recovery_empty"
    )
    assert proof.decision.group_facts.direct_support_count_s == (1 if threshold else 3)
    assert proof.decision.group_facts.minimum_support_k == 2
    assert position.planning_state == "not_planned"
    assert position.primary_logical_requests == ()
    assert coverage.terminals == ()
    (hold_cell,) = tuple(
        cell
        for cell in scientific.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Hold"
    )
    assert hold_cell.evidence_states == (
        ("not_searched",) if threshold else ("unavailable",)
    )
    assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
    (source,) = hold_cell.predecessor_cells
    assert source is proof.decision.target_cell
    assert source.method_assessment is next(
        result.effective_assessment
        for result in runtime_result.sample_results
        if result.job.sample is position.sample
    )
    assert source.content.method_result == "HOLD"
    assert source.peak_observation is source.peak_hypothesis is None
    authorization = authorize_observation_repair(
        processing_owners=batch.context.processing_owners,
        coverage=coverage,
        cell_observations=tuple(
            cell
            for final in scientific.cell_observations
            for cell in final.predecessor_cells
        ),
    )
    assert authorization.state == "not_run"
    assert authorization.eligible_cell_ids == ()
    hold_decisions = tuple(
        decision
        for decision in writer_inputs.production_decisions.cells.values()
        if decision.sample_stem == "Hold"
    )
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in hold_decisions
    )
    matrix_path = tmp_path / "conservative-matrix.tsv"
    write_alignment_matrix_tsv(
        matrix_path,
        writer_inputs.matrix,
        production_decisions=writer_inputs.production_decisions,
    )
    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    assert len(matrix_rows) == (0 if threshold else 1)
    assert all(row["Hold"] == "" for row in matrix_rows)
    if not threshold:
        assert all(
            float(matrix_rows[0][stem]) > 0
            for stem in ("Support1", "Support2", "Support3")
        )
    _assert_conservative_proof_seals(
        proof, position, coverage, handoff, hold_cell, batch, runtime_result
    )


def _assert_conservative_proof_seals(
    proof, position, coverage, handoff, final_cell, batch, runtime_result
) -> None:
    import hashlib
    import json
    from copy import copy, deepcopy
    from dataclasses import fields

    from xic_extractor.alignment.backfill import evaluation_coverage as owner
    from xic_extractor.alignment.backfill.observation_repair import (
        authorize_observation_repair,
    )
    from xic_extractor.alignment.cell_observations import (
        _derive_lane_evidence_state,
        canonical_cell_observation_bytes,
    )
    from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
        canonical_untargeted_cid_nl_sample_result_bytes,
        project_untargeted_cid_nl_completed_recovery,
    )
    from xic_extractor.processing_identity import issue_processing_attempt

    epoch = coverage.epoch
    epoch_args = {
        field.name: getattr(epoch, field.name)
        for field in fields(epoch)
        if field.name != "epoch_id"
    }
    basis_args = {
        key: value
        for key, value in epoch_args.items()
        if key not in {"processing_owners", "group_identities"}
    } | {
        "processing_request_id": epoch.processing_owners.request.processing_request_id,
        "groups": epoch.group_identities,
    }
    epoch_basis = owner._epoch_basis(**basis_args)
    assert epoch_basis["schema"] == "owner_backfill_planning_epoch_v2"
    assert proof.proof_id in {
        row["conservative_no_request_proof_id"] for row in epoch_basis["positions"]
    }
    assert epoch.epoch_id == owner._typed_id(owner._EPOCH_PREFIX, epoch_basis)
    assert coverage.coverage_id == owner._typed_id(
        owner._BATCH_PREFIX,
        {
            "schema": "owner_backfill_evaluation_coverage_v1",
            "epoch_id": epoch.epoch_id,
            "terminal_ids": [],
            "terminal_evaluations": [],
        },
    )
    assert json.loads(handoff.basis_bytes)["coverage_id"] == coverage.coverage_id
    assert handoff.handoff_id.endswith(hashlib.sha256(handoff.basis_bytes).hexdigest())
    assert json.loads(final_cell.basis_bytes)["handoff_id"] == handoff.handoff_id
    assert final_cell.cell_observation_id.endswith(
        hashlib.sha256(final_cell.basis_bytes).hexdigest()
    )
    assert (
        json.loads(
            canonical_cell_observation_bytes(proof.decision.target_cell.content)
        )["schema_version"]
        == "cell_observation_v4"
    )

    # The old schema has precisely these position fields and no absent-proof key.
    legacy_positions = tuple(
        replace(
            item,
            conservative_no_request_proof=None,
            planning_reason="retained_local_owner",
        )
        for item in epoch.positions
    )
    legacy_basis = owner._epoch_basis(**(basis_args | {"positions": legacy_positions}))
    expected_legacy = deepcopy(epoch_basis)
    expected_legacy["schema"] = "owner_backfill_planning_epoch_v1"
    for row in expected_legacy["positions"]:
        row.pop("conservative_no_request_proof_id")
        row["planning_reason"] = "retained_local_owner"

    def canonical(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    assert canonical(legacy_basis) == canonical(expected_legacy)
    legacy_epoch = owner.mint_owner_backfill_planning_epoch(
        **(epoch_args | {"positions": legacy_positions})
    )
    assert (
        legacy_epoch.epoch_id
        == owner._EPOCH_PREFIX + hashlib.sha256(canonical(expected_legacy)).hexdigest()
    )
    assert legacy_epoch.epoch_id != epoch.epoch_id

    other_sample = next(
        sample for sample in epoch.samples if sample is not proof.sample
    )
    for substitution in (
        {"sample": other_sample},
        {"proof_id": "xic.owner-backfill-conservative-no-request.v1:" + "0" * 64},
        {
            "processing_owners": replace(
                proof.processing_owners,
                attempt=issue_processing_attempt(proof.processing_owners.request),
            )
        },
    ):
        with pytest.raises(ValueError):
            replace(proof, **substitution)
    other_group = copy(proof.group_identity)
    object.__setattr__(other_group, "group_hypothesis_id", "crossed-group")
    with pytest.raises(ValueError):
        replace(proof, group_identity=other_group)
    from xic_extractor.alignment.conservative_backfill import (
        map_conservative_backfill_candidates,
    )

    (support_decision,) = map_conservative_backfill_candidates(
        (proof.decision.group_facts,), sample=other_sample, candidate_universes=()
    )
    with pytest.raises(ValueError):
        replace(proof, decision=support_decision)
    for substitution in (
        {"planning_state": "planned", "planning_reason": "planned"},
        {"planning_reason": "retained_local_owner"},
        {"conservative_no_request_proof": None},
    ):
        with pytest.raises(ValueError):
            replace(position, **substitution)
    with pytest.raises(ValueError):
        owner.validate_owner_backfill_conservative_no_request_position(
            position, source_population=(proof.decision.target_cell,)
        )
    with pytest.raises(ValueError):
        owner.validate_owner_backfill_conservative_no_request_position(
            position,
            source_cell=next(
                cell
                for cell in proof.decision.group_facts.source_cells
                if cell is not proof.decision.target_cell
            ),
        )

    from xic_extractor.alignment import conservative_backfill as conservative_owner

    def unexpected_reevaluation(*args, **kwargs):
        raise AssertionError("completed Backfill decision was reevaluated")

    with pytest.MonkeyPatch.context() as retained:
        retained.setattr(
            conservative_owner,
            "assess_conservative_backfill_group",
            unexpected_reevaluation,
        )
        retained.setattr(
            conservative_owner,
            "map_conservative_backfill_candidates",
            unexpected_reevaluation,
        )
        owner.validate_owner_backfill_conservative_no_request_position(
            position,
            source_cell=proof.decision.target_cell,
            source_population=proof.decision.group_facts.source_cells,
        )
        authorize_observation_repair(
            processing_owners=batch.context.processing_owners,
            coverage=coverage,
            cell_observations=proof.decision.group_facts.source_cells,
        )
    lane_args = dict(
        association=final_cell.position_associations[0],
        source_cell=proof.decision.target_cell,
        coverage_terminals=(),
        repair_receipts=(),
        repaired_observations=(),
        final_group=final_cell.group,
        canonical_sample=proof.sample,
        processing_request_id=epoch.processing_owners.request.processing_request_id,
    )
    for field in ("coverage_terminals", "repair_receipts", "repaired_observations"):
        with pytest.raises(ValueError, match="physical or repair evidence"):
            _derive_lane_evidence_state(**(lane_args | {field: (object(),)}))

    result = next(
        item
        for item in runtime_result.sample_results
        if item.job.sample is proof.sample
    )
    context = proof.decision.group_facts.group_context
    original_result_bytes = canonical_untargeted_cid_nl_sample_result_bytes(
        result.result_ref
    )
    with pytest.raises(ValueError, match="not a recovery"):
        project_untargeted_cid_nl_completed_recovery(
            replace(result, recovery_attempts=()), group_context=context
        )
    if proof.completion is not None:
        completion = proof.completion
        with pytest.raises(ValueError):
            replace(proof, completion=None)
        assert (
            project_untargeted_cid_nl_completed_recovery(result, group_context=context)
            == completion
        )
        other_coordinate = next(
            item.adapter_result.acquisition.traces[0].coordinate
            for item in runtime_result.sample_results
            if item.job.sample is other_sample
        )
        for substitution in (
            {"acquisition_sha256": "0" * 64},
            {"source_result_artifact": completion.initial_assessment_artifact},
            {"trace_coordinate": other_coordinate},
        ):
            with pytest.raises(ValueError):
                replace(completion, **substitution)
        recovery = result.recovery_attempt
        with pytest.raises(ValueError):
            project_untargeted_cid_nl_completed_recovery(
                replace(
                    result,
                    recovery_attempts=(
                        replace(
                            recovery,
                            assessment=result.adapter_result.assessment_input.assessment,
                        ),
                    ),
                ),
                group_context=context,
            )
        for traces in ((), recovery.acquisition.traces * 2):
            wrong_acquisition = copy(recovery.acquisition)
            object.__setattr__(wrong_acquisition, "traces", traces)
            with pytest.raises(ValueError, match="sealed runtime result"):
                project_untargeted_cid_nl_completed_recovery(
                    replace(
                        result,
                        recovery_attempts=(
                            replace(recovery, acquisition=wrong_acquisition),
                        ),
                    ),
                    group_context=context,
                )
    assert (
        canonical_untargeted_cid_nl_sample_result_bytes(result.result_ref)
        == original_result_bytes
    )


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch,
    recovery_case: str,
) -> None:
    _forbid_retained_production_repair(monkeypatch)
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
        sample_identity_namespace="study:production-recovery",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "recovery.runtime.json",
            "production-recovery",
        ),
    )
    recovered = tuple(
        result
        for result in runtime_result.sample_results
        if result.job.sample.metadata.raw_stem == "Hold"
    )
    assert len(recovered) == 1
    assert (recovered[0].recovery_attempt is not None) == (recovery_case != "threshold")
    assert len(recovered[0].effective_assessment.candidate_peaks) == (
        recovery_case == "positive"
    )
    opens_before_composition = sum(map(len, opened.values()))

    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.children
    writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
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
    from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
        project_untargeted_cid_nl_completed_recovery,
    )

    (child,) = completed.application_result.children
    recovered_source = next(
        source
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
        if source.method_assessment is recovered[0].effective_assessment
    )
    with pytest.raises(ValueError, match="completed empty epoch"):
        project_untargeted_cid_nl_completed_recovery(
            recovered[0], group_context=recovered_source.group_context
        )
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
    diagnostic_observation_ids: set[str] = set()
    for child in completed.application_result.children:
        for projected in child.completed_projection.rows:
            group = next(
                group
                for group in child.scientific_core.final_population
                if group.group_hypothesis_id == projected.row_key.group_hypothesis_id
            )
            member_ids = {
                physical_id
                for member in group.member_subjects
                for physical_id in member.physical_observation_ids
            }
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
                    physical_id = direct.content.peak_observation_content_id
                    if physical_id not in member_ids:
                        assert decision.subject.canonical_sample.metadata.raw_stem == (
                            "Hold"
                        )
                        assert decision.value is None
                        diagnostic_observation_ids.add(physical_id)
                        continue
                    expected_presentation_facts[physical_id] = (
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
    assert len(diagnostic_observation_ids) == 1
    assert not diagnostic_observation_ids & set(row.physical_observation_ids)
    assert len(expected_presentation_facts) == len(sample_stems) - 1
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
    weighted_values = dict(zip(sample_stems, (1.0, 9.0, 2.0, 9.0), strict=True))
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
    raw_path.write_bytes(b"production-orphan:S1")
    # Isolate the orphan's method rejection from the acquired candidate.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
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
        sample_identity_namespace="study:production-orphan",
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
            "production-orphan",
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
        and item.reason_code == "cid_nl_requirement_not_supported"
        and len(item.method_assessments) == 1
        and item.method_assessments[0].content.method_result == "REJECT"
        for item in exclusions
    )
    from xic_extractor.alignment.result.application_result import (
        ApplicationResultContractError,
    )

    for item in exclusions:
        legacy = replace(
            item,
            reason_code="discovery_candidate_not_search_eligible",
            method_assessments=(),
        )
        assert legacy.discovery_candidate_state == item.discovery_candidate_state
        for field in ("method_assessments", "composite_assessments"):
            with pytest.raises(ApplicationResultContractError):
                replace(legacy, **{field: item.method_assessments})
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
