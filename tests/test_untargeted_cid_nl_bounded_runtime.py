"""End-to-end bounded cohort closure through the retained scientific owners."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from time import perf_counter, sleep

import pytest
from openpyxl import Workbook

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _FakeRaw,
    _FakeThermoRaw,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import read_known_istd_reference
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
    materialize_untargeted_cid_nl_partition,
    prepare_untargeted_cid_nl_production_plan,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.sample_metadata import SampleMetadata


@pytest.fixture(autouse=True)
def _disable_real_dll_loading(monkeypatch) -> None:
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    monkeypatch.setattr(runtime_owner, "_ensure_assemblies_loaded", lambda *_: None)


def _plan(tmp_path, monkeypatch, *, scenario="positive", opened_sink=None):
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(4))
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        precursor_mz_tolerance_ppm=(
            5.0 if scenario == "cross_subject_recovery" else 1.0
        ),
    )
    peak_config = _peak_config(tmp_path)
    raws = {}
    candidates = {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw, sample_candidates = _nearby_subject_candidates(
            path, settings=settings, peak_config=peak_config
        )
        third_raw = _FakeRaw(
            (7.98,), scan_precursor_mz=500.008, neutral_loss_das=(116.0474,)
        )
        third_scan_number = raw.scans[-1].scan_number + 1
        third_raw.scans = (
            replace(
                third_raw.scans[0],
                scan_number=third_scan_number,
                rt=float(raw.rt[third_scan_number - 1]),
            ),
        )
        if scenario == "mixed_eligibility":
            third_raw.intensity[:] = 0.0
        third_candidates = _current_candidates(
            third_raw, raw_file=path, settings=settings, peak_config=peak_config
        )
        sample_candidates = tuple(
            sorted(
                (*sample_candidates, *third_candidates),
                key=lambda item: item.candidate_id,
            )
        )
        raw.scans = (*raw.scans, *third_raw.scans)
        if scenario == "ineligible":
            raw.intensity[:] = 0.0
            sample_candidates = _current_candidates(
                raw, raw_file=path, settings=settings, peak_config=peak_config
            )
        raws[path] = raw
        if scenario == "recovery" and path == paths[-1]:
            candidates[path.stem] = ()
        elif scenario == "below_threshold" and path != paths[0]:
            candidates[path.stem] = ()
        elif scenario == "blank_missing" and path == paths[-1]:
            candidates[path.stem] = ()
        else:
            candidates[path.stem] = sample_candidates
        if scenario == "partial_recovery" and path == paths[-1]:
            candidates[path.stem] = sample_candidates[:1]
        if scenario == "cross_subject_recovery":
            precursor = 500.0 if path == paths[0] else 500.004
            candidates[path.stem] = (
                tuple(c for c in sample_candidates if c.precursor_mz == precursor)
                if path in paths[:2]
                else ()
            )
    plan = prepare_untargeted_cid_nl_production_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role=(
                    "blank"
                    if scenario == "blank_missing" and path == paths[-1]
                    else "study_sample"
                ),
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:bounded-runtime",
    )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    if opened_sink is not None:
        opened_sink.append(opened)
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    monkeypatch.setattr(
        runtime_owner,
        "_ensure_assemblies_loaded",
        lambda *_args: None,
    )
    reference = None
    if scenario == "istd":
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
        for path in paths:
            results.append([path.stem, "known", "ISTD", 7.98, -999])
        reference_path = tmp_path / "reference.xlsx"
        workbook.save(reference_path)
        reference = read_known_istd_reference(reference_path)
    assert len(plan.subject_batch.subjects) == (
        2 if scenario == "cross_subject_recovery" else 3
    )
    return plan, reference


def test_bounded_partition_keeps_alignment_workset_atomic(
    tmp_path,
    monkeypatch,
) -> None:
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        CidNlPartitionPipelineAdapter,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=116.0474,
                )
                for mz in (500.0, 500.015, 500.05)
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    memberships = tuple(
        (f"candidate-{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
    )
    sample_count = len(plan.context.samples)

    adapter = CidNlPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=sample_count * 2,
    )

    assert {
        frozenset(subject.precursor_mz for subject in partition.payload)
        for partition in adapter.partitions
    } == {frozenset({500.0, 500.015}), frozenset({500.05})}
    with pytest.raises(
        ValueError,
        match="cannot contain one alignment subject workset",
    ):
        CidNlPartitionPipelineAdapter(
            packing_plan,
            alignment_config=AlignmentConfig(),
            max_partition_jobs=sample_count,
        )


def test_indexed_mass_partition_keeps_closed_worksets_and_budgets_neighbors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction import (
        untargeted_cid_nl_bounded_runtime as bounded_owner,
    )
    from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
        PreparedCidNlAlignmentSubjectWorksets,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=loss,
                )
                for mz, loss in (
                    (99.0, 116.0474),
                    (201.0, 116.0474),
                    (250.0, 114.0),
                    (251.0, 112.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    worksets = cid_nl_alignment_subject_worksets(
        subjects,
        comparison_mz_intervals=((90.0, 210.0),),
    )
    closed = next(workset for workset in worksets if len(workset) == 2)
    assert {subject.precursor_mz for subject in closed} == {99.0, 201.0}
    membership_counts = {
        subject.method_subject_id: (
            600 if subject.precursor_mz in {250.0, 251.0} else 1
        )
        for subject in subjects
    }
    memberships = tuple(
        sorted(
            (
                f"candidate-{subject.method_subject_id}-{index:04d}",
                subject.method_subject_id,
            )
            for subject in subjects
            for index in range(membership_counts[subject.method_subject_id])
        )
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
        partition_input_loader=lambda _candidate_ids: ({}, {}, {}),
    )
    memberships_by_subject = {
        subject.method_subject_id: tuple(
            membership
            for membership in memberships
            if membership[1] == subject.method_subject_id
        )
        for subject in subjects
    }
    prepared = PreparedCidNlAlignmentSubjectWorksets(
        plan=packing_plan,
        worksets=worksets,
        subjects_by_id={subject.method_subject_id: subject for subject in subjects},
        workset_ids_by_subject={
            subject.method_subject_id: frozenset(
                member.method_subject_id for member in workset
            )
            for workset in worksets
            for subject in workset
        },
        memberships_by_subject=memberships_by_subject,
    )
    monkeypatch.setattr(
        bounded_owner,
        "prepare_untargeted_cid_nl_alignment_subject_worksets",
        lambda active_plan, **_kwargs: (
            prepared
            if active_plan is packing_plan
            else pytest.fail("partition preparation crossed its indexed plan")
        ),
    )

    adapter = bounded_owner.CidNlPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples) * len(subjects),
    )
    payloads = tuple(
        adapter_partition.payload for adapter_partition in adapter.partitions
    )

    assert any(set(payload) == set(closed) for payload in payloads)
    assert all(
        not ({250.0, 251.0} <= {subject.precursor_mz for subject in payload})
        for payload in payloads
    )
    assert len(payloads) == 3


def test_indexed_finalization_orders_children_by_canonical_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.extraction import (
        untargeted_cid_nl_bounded_runtime as bounded_owner,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    indexed_plan = replace(
        plan,
        partition_input_loader=lambda _candidate_ids: ({}, {}, {}),
    )
    adapter = bounded_owner.CidNlPartitionPipelineAdapter(
        indexed_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples),
    )
    canonical_ids = tuple(
        subject.method_subject_id for subject in plan.subject_batch.subjects
    )
    mass_execution_ids = tuple(reversed(canonical_ids))

    class StoredChildren:
        def sequence(self, field_name: str):
            assert field_name == "children"
            return (None,) * len(mass_execution_ids)

        def read_selected(
            self,
            index: int,
            field_name: str,
            positions: tuple[int, ...],
        ):
            assert field_name == "children"
            assert positions == (0,)
            return (mass_execution_ids[index],)

    outputs = tuple(
        SimpleNamespace(
            result=SimpleNamespace(
                capture_bindings=(),
                excluded_candidates=(),
                child_order_keys=(subject_id,),
                sealed=SimpleNamespace(index=index),
            )
        )
        for index, subject_id in enumerate(mass_execution_ids)
    )
    captured = []

    def capture_finalization(request):
        captured.append(request)
        return request

    monkeypatch.setattr(
        bounded_owner,
        "finalize_alignment_result",
        capture_finalization,
    )
    monkeypatch.setattr(
        bounded_owner,
        "UntargetBatchApplicationResultRequest",
        lambda **values: SimpleNamespace(**values),
    )

    finalized = adapter.finalize_application(StoredChildren(), outputs)

    assert captured == [finalized]
    assert tuple(finalized.children) == canonical_ids


def test_bounded_partition_real_entry_mints_interleaved_worksets_canonically(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.extraction import (
        untargeted_cid_nl_bounded_runtime as bounded_owner,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=precursor_mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=neutral_loss_da,
                )
                for precursor_mz, neutral_loss_da in (
                    (300.0, 116.0474),
                    (300.005, 116.0474),
                    (300.003, 114.0),
                    (300.004, 114.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    worksets = cid_nl_alignment_subject_worksets(subjects)
    assert tuple(len(workset) for workset in worksets) == (2, 2)
    assert tuple(subject for workset in worksets for subject in workset) != subjects
    memberships = tuple(
        (f"candidate-{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
    )
    prepare_worksets = (
        bounded_owner.prepare_untargeted_cid_nl_alignment_subject_worksets
    )
    prepared = prepare_worksets(packing_plan)
    assert (
        materialize_untargeted_cid_nl_partition(
            packing_plan,
            packing_plan.subject_batch,
            prepared_worksets=prepared,
        ).jobs
        == materialize_untargeted_cid_nl_partition(
            packing_plan, packing_plan.subject_batch
        ).jobs
    )
    split = worksets[0][:1]
    split_ids = {subject.method_subject_id for subject in split}
    split_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=split,
        candidate_memberships=tuple(
            membership for membership in memberships if membership[1] in split_ids
        ),
    )
    with pytest.raises(ValueError, match="cannot split one alignment subject workset"):
        materialize_untargeted_cid_nl_partition(
            packing_plan,
            split_batch,
            prepared_worksets=prepared,
        )
    foreign = mint_untargeted_cid_nl_method_subject(
        precursor_mz=900.0,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    foreign_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(foreign,),
        candidate_memberships=(("foreign-candidate", foreign.method_subject_id),),
    )
    with pytest.raises(ValueError, match="foreign alignment subject"):
        materialize_untargeted_cid_nl_partition(
            packing_plan,
            foreign_batch,
            prepared_worksets=prepared,
        )
    crossed_memberships = tuple(
        (candidate_id, subjects[(index + 1) % len(subjects)].method_subject_id)
        for index, (candidate_id, _subject_id) in enumerate(memberships)
    )
    crossed_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=crossed_memberships,
    )
    with pytest.raises(ValueError, match="crossed prepared candidate memberships"):
        materialize_untargeted_cid_nl_partition(
            packing_plan,
            crossed_batch,
            prepared_worksets=prepared,
        )
    captured_batches = []
    prepare_calls = []
    original_materialize_partition = (
        bounded_owner.materialize_untargeted_cid_nl_partition
    )

    def capture_prepare(_plan, *, known_istd_reference=None):
        prepare_calls.append(_plan.runtime_subjects)
        return prepare_worksets(
            _plan,
            known_istd_reference=known_istd_reference,
        )

    def capture_materialized_partition(
        _plan,
        subject_batch,
        *,
        prepared_worksets,
        acquisition_cache_dir=None,
        sparse=False,
    ):
        assert prepared_worksets.plan is _plan
        assert sparse is True
        captured_batches.append(subject_batch)
        return original_materialize_partition(
            _plan,
            subject_batch,
            prepared_worksets=prepared_worksets,
            acquisition_cache_dir=acquisition_cache_dir,
            sparse=sparse,
        )

    def close_without_raw(*_args, **_kwargs):
        return SimpleNamespace(
            succeeded=True,
            worker_batch=SimpleNamespace(resource_settlements=()),
            acquisitions=(),
            prepared_grouping=None,
        )

    def forbid_raw_open(*_args, **_kwargs):
        pytest.fail("partition batch mint must complete before RAW acquisition")

    monkeypatch.setattr(
        bounded_owner,
        "materialize_untargeted_cid_nl_partition",
        capture_materialized_partition,
    )
    monkeypatch.setattr(
        bounded_owner,
        "prepare_untargeted_cid_nl_alignment_subject_worksets",
        capture_prepare,
    )
    monkeypatch.setattr(
        bounded_owner,
        "execute_untargeted_cid_nl_runtime_partition",
        close_without_raw,
    )
    monkeypatch.setattr(_FakeThermoRaw, "__init__", forbid_raw_open)
    adapter = bounded_owner.CidNlPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples) * 2,
    )

    assert len(adapter.partitions) == 2
    results = tuple(
        adapter.execute_acquisition(partition) for partition in adapter.partitions
    )

    assert all(result.succeeded for result in results)
    assert prepare_calls == [subjects]
    assert tuple(batch.subjects for batch in captured_batches) == tuple(
        partition.payload for partition in adapter.partitions
    )
    assert {
        subject.method_subject_id
        for batch in captured_batches
        for subject in batch.subjects
    } == {subject.method_subject_id for subject in subjects}


@pytest.mark.parametrize("scenario", ["mixed_eligibility", "ineligible"])
@pytest.mark.parametrize("raw_workers", [1, 4])
def test_bounded_runtime_skips_subjects_without_ms1_features(
    tmp_path, monkeypatch, scenario, raw_workers
):
    from xic_extractor.discovery.models import MS1_FEATURE_BACKED_STATES
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        CidNlPartitionPipelineAdapter,
        execute_bounded_untargeted_cid_nl,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    request_payload = plan.subject_batch.request_payload()
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in plan.context.candidates_by_sample.values()
        for candidate in candidates
    }
    eligible_subject_ids = {
        subject_id
        for candidate_id, subject_id in plan.subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].ms1_peak_found
        and candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
    }
    assert len(eligible_subject_ids) == (2 if scenario == "mixed_eligibility" else 0)
    config = AlignmentConfig(anchor_min_seed_events=1)
    adapter = CidNlPartitionPipelineAdapter(
        plan, alignment_config=config, max_partition_jobs=len(plan.context.samples)
    )
    assert {
        subject.method_subject_id
        for partition in adapter.partitions
        for subject in partition.payload
    } == eligible_subject_ids
    eager = None
    if eligible_subject_ids:
        batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
        runtime = execute_untargeted_cid_nl_production_batch(
            batch,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
            alignment_config=config,
            known_istd_reference=reference,
        )
        eager = compose_untargeted_cid_nl_production_result(
            batch,
            runtime,
            alignment_config=config,
            known_istd_reference=reference,
        )
    if not eligible_subject_ids:

        def forbid_raw_open(*args, **kwargs):
            pytest.fail("an ineligible cohort must close without opening RAW")

        monkeypatch.setattr(_FakeThermoRaw, "__init__", forbid_raw_open)
    result = execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "eligibility-output",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "eligibility.json", "eligibility"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=raw_workers,
        pipeline_overlap=True,
    )
    assert plan.subject_batch.request_payload() == request_payload
    assert set(result.application_result.candidate_ids) == set(candidate_by_id)
    assert {
        assessment.content.method_subject.method_subject_id
        for assessment in result.method_assessments
    } == eligible_subject_ids
    skipped = {
        candidate_id
        for candidate_id, subject_id in plan.subject_batch.candidate_memberships
        if subject_id not in eligible_subject_ids
    }
    exclusions = {
        exclusion.candidate_id: exclusion
        for exclusion in result.application_result.excluded_candidates
    }
    assert skipped <= exclusions.keys()
    for candidate_id in skipped:
        exclusion = exclusions[candidate_id]
        assert exclusion.reason_code == "discovery_candidate_not_search_eligible"
        assert not exclusion.method_assessments
        assert not exclusion.composite_assessments
    assert bool(result.application_result.children) is bool(eligible_subject_ids)
    writer = project_completed_untarget_batch_writer_inputs(result)
    if eager is not None:
        assert writer == project_completed_untarget_batch_writer_inputs(eager)
        assert {
            assessment.artifact.evidence_artifact_id: assessment
            for assessment in result.method_assessments
        } == {
            assessment.artifact.evidence_artifact_id: assessment
            for assessment in eager.method_assessments
            if assessment.content.method_subject.method_subject_id
            in eligible_subject_ids
        }
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    matrix_path = tmp_path / "eligibility.tsv"
    write_alignment_matrix_tsv(
        matrix_path,
        writer.matrix,
        alignment_config=config,
        production_decisions=writer.production_decisions,
    )
    report = write_cid_nl_explanation(
        tmp_path / "eligibility.sqlite",
        completed=result,
        writer_inputs=writer,
        candidates_by_sample=plan.context.candidates_by_sample,
        matrix_path=matrix_path,
        alignment_config=config,
    )
    with sqlite3.connect(report) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[
            0
        ] == len(candidate_by_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM records WHERE kind = 'method_assessments'"
        ).fetchone()[0] == len(result.method_assessments)


@pytest.mark.parametrize("subjects_per_partition", [1, 2])
@pytest.mark.parametrize("raw_workers", [1, 2, 4])
@pytest.mark.parametrize("scenario", ["positive", "recovery", "istd"])
def test_bounded_runtime_matches_eager_after_disk_owner_round_trip(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
    subjects_per_partition: int,
    raw_workers: int,
):
    import xic_extractor.diagnostics.timing as timing_owner
    from xic_extractor.diagnostics.timing import TimingRecorder
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        execute_bounded_untargeted_cid_nl,
        prepare_untargeted_cid_nl_alignment_subject_worksets,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    prepared_worksets = prepare_untargeted_cid_nl_alignment_subject_worksets(
        plan,
        known_istd_reference=reference,
    )
    if scenario == "istd":
        assert tuple(len(workset) for workset in prepared_worksets.worksets) == (3,)
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    coordinator_thread = get_ident()
    warm_threads = []
    monkeypatch.setattr(
        runtime_owner,
        "_ensure_assemblies_loaded",
        lambda *_args: warm_threads.append(get_ident()),
        raising=False,
    )
    config = AlignmentConfig(anchor_min_seed_events=1)
    batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    eager_runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
        alignment_config=config,
        known_istd_reference=reference,
    )
    eager = compose_untargeted_cid_nl_production_result(
        batch, eager_runtime, alignment_config=config, known_istd_reference=reference
    )
    warm_threads.clear()
    marker = OwnerPathRuntimeMarker(tmp_path / "bounded.json", "bounded")
    progress_events = []
    live_path = tmp_path / "alignment-live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)
    monkeypatch.setattr(timing_owner, "perf_counter", lambda: 1.0)
    writes = []
    write_live = recorder._write_live

    def record_write():
        writes.append(dict(recorder._heartbeat))
        write_live()

    monkeypatch.setattr(recorder, "_write_live", record_write)

    def report_progress(event):
        assert get_ident() == coordinator_thread
        recorder.heartbeat_event(event)
        progress_events.append(event)

    with recorder.stage("alignment.untargeted_cid_nl_runtime"):
        partition_subject_capacity = (
            len(prepared_worksets.worksets[0])
            if scenario == "istd"
            else subjects_per_partition
        )
        bounded = execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "bounded-output",
            alignment_config=config,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            known_istd_reference=reference,
            progress_callback=report_progress,
            max_partition_jobs=(partition_subject_capacity * len(plan.context.samples)),
            raw_workers=raw_workers,
        )
    assert type(bounded) is CompletedUntargetBatchWorkflowResult
    assert bool(warm_threads) is (raw_workers > 1)
    assert all(thread == coordinator_thread for thread in warm_threads)
    assert type(bounded.application_result.children) is ImmutableSequence
    bounded_writer = project_completed_untarget_batch_writer_inputs(bounded)
    eager_writer = project_completed_untarget_batch_writer_inputs(eager)
    assert bounded_writer.matrix == eager_writer.matrix
    if scenario == "istd":
        expected_areas = {
            (cell.cluster_id, cell.sample_stem): cell.area
            for cell in eager_writer.matrix.cells
            if cell.area is not None
        }
        actual_areas = {
            (cell.cluster_id, cell.sample_stem): cell.area
            for cell in bounded_writer.matrix.cells
            if cell.area is not None
        }
        assert expected_areas
        assert actual_areas == pytest.approx(expected_areas)
        assert all(area > 0 for area in actual_areas.values())
    actual = bounded.application_result
    expected = eager.application_result
    assert actual.candidate_ids == expected.candidate_ids
    assert actual.physical_observation_ids == expected.physical_observation_ids
    assert tuple(actual.excluded_candidates) == expected.excluded_candidates
    owners = plan.context.processing_owners
    assert actual.processing_request is owners.request
    assert actual.processing_attempt is owners.attempt
    assert bounded.runtime_receipt.supervisor.attempt is owners.attempt
    assert all(
        left is right
        for left, right in zip(
            actual.canonical_samples, expected.canonical_samples, strict=True
        )
    )
    for child in actual.children:
        assert child.processing_request is owners.request
        assert (
            child.scientific_core.cell_observation_handoff.coverage.epoch.processing_owners
            is owners
        )
        assert all(
            left is right
            for left, right in zip(
                child.canonical_samples, expected.canonical_samples, strict=True
            )
        )
    assert sorted(
        item.artifact.evidence_artifact_id for item in bounded.method_assessments
    ) == sorted(item.artifact.evidence_artifact_id for item in eager.method_assessments)
    _assert_explanation_parity(tmp_path, plan, config, eager, bounded)
    partition_stages = [
        (event["stage"], event["completed"])
        for event in progress_events
        if event.get("event") == "partition_progress"
        and event["stage"]
        in {
            "partition_start",
            "partition_compose",
            "partition_released",
            "global_closure",
            "batch_completed",
        }
    ]
    partition_count = (
        len(plan.subject_batch.subjects) + partition_subject_capacity - 1
    ) // partition_subject_capacity
    if scenario == "istd" and subjects_per_partition == 1:
        assert partition_count < len(plan.subject_batch.subjects)
    assert partition_stages == [
        event
        for index in range(partition_count)
        for event in (
            ("partition_start", index),
            ("partition_compose", index),
            ("partition_released", index + 1),
        )
    ] + [("global_closure", partition_count), ("batch_completed", partition_count)]
    seal_writes = [row for row in writes if row.get("stage") == "partition_seal"]
    assert len(seal_writes) <= 2 * partition_count
    heartbeat = json.loads(live_path.read_text(encoding="utf-8"))["heartbeat"]
    assert heartbeat["stage"] == "batch_completed"
    assert heartbeat["completed"] == heartbeat["total"] == partition_count
    eager_manifest = eager_runtime.finalization.coordinator.state.runtime.manifest
    assert len(eager_manifest.work_unit_keys) == len(batch.jobs)
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert len(snapshot.manifest_work_unit_keys) == (
        partition_count * len(plan.context.samples) * 2 + 1
    )
    assert actual.children
    if scenario == "recovery":
        assert any(result.recovery_attempts for result in eager_runtime.sample_results)


def _assert_explanation_parity(tmp_path, plan, config, eager, bounded):
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    candidate_id = plan.subject_batch.candidate_memberships[0][0]
    records = []
    for name, completed in (("eager", eager), ("bounded", bounded)):
        writer = project_completed_untarget_batch_writer_inputs(completed)
        matrix = tmp_path / f"{name}.tsv"
        write_alignment_matrix_tsv(
            matrix,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        report = write_cid_nl_explanation(
            tmp_path / f"{name}.sqlite",
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=plan.context.candidates_by_sample,
            matrix_path=matrix,
            alignment_config=config,
        )
        detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
        records.append(detail["records"])
    assert records[0] == records[1]


@pytest.mark.parametrize("failure_seam", ["later_partition", "later_spool"])
def test_bounded_runtime_failure_never_publishes_completed_batch(
    tmp_path: Path, monkeypatch, failure_seam: str
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as bounded_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    marker = OwnerPathRuntimeMarker(tmp_path / "failed.json", "failed")
    original_error = OSError("second partition storage unavailable")
    calls = []
    if failure_seam == "later_partition":
        seam_owner = bounded_owner
        seam_name = "execute_untargeted_cid_nl_runtime_partition"
    else:
        seam_owner = bounded_owner.PartitionResultStore
        seam_name = "append_children"
    original = getattr(seam_owner, seam_name)

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise original_error
        return original(*args, **kwargs)

    monkeypatch.setattr(seam_owner, seam_name, fail_second)

    def forbid_completed(*args, **kwargs):
        pytest.fail("An incomplete partition must not publish a completed result")

    monkeypatch.setattr(
        bounded_owner, "CompletedUntargetBatchWorkflowResult", forbid_completed
    )
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        bounded_owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
        )
    assert len(calls) == 2
    assert error.value.__cause__ is original_error
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.processing_attempt_id == (
        plan.context.processing_owners.attempt.processing_attempt_id
    )


def test_failed_attempt_retry_reuses_completed_raw_facts_and_recomposes(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner

    ms2_reads: dict[int, int] = {}
    original_filter = _FakeThermoRaw.GetFilterForScanNumber

    def count_filter(vendor, scan_number):
        key = id(vendor)
        ms2_reads[key] = ms2_reads.get(key, 0) + 1
        return original_filter(vendor, scan_number)

    monkeypatch.setattr(_FakeThermoRaw, "GetFilterForScanNumber", count_filter)
    baseline_plan, _ = _plan(tmp_path, monkeypatch)
    baseline = owner.execute_bounded_untargeted_cid_nl(
        baseline_plan,
        output_dir=tmp_path / "cold-output",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "cold.json", "cold"),
        max_partition_jobs=len(baseline_plan.context.samples),
        pipeline_overlap=False,
    )
    baseline_writer = project_completed_untarget_batch_writer_inputs(baseline)
    opened_runs = []
    plan, _ = _plan(tmp_path, monkeypatch, opened_sink=opened_runs)
    output_dir = tmp_path / "retry-output"
    marker = OwnerPathRuntimeMarker(
        tmp_path / "retry.json",
        "retry",
        allow_failed_attempt_retry=True,
    )
    original_append = owner.PartitionResultStore.append_children
    append_calls = 0

    def fail_second(store, *args, **kwargs):
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise OSError("second partition storage unavailable")
        return original_append(store, *args, **kwargs)

    monkeypatch.setattr(owner.PartitionResultStore, "append_children", fail_second)
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=output_dir,
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            pipeline_overlap=False,
        )
    failed_attempt_id = plan.context.processing_owners.attempt.processing_attempt_id
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED

    monkeypatch.setattr(owner.PartitionResultStore, "append_children", original_append)
    retry_plan, _ = _plan(tmp_path, monkeypatch, opened_sink=opened_runs)
    completed = owner.execute_bounded_untargeted_cid_nl(
        retry_plan,
        output_dir=output_dir,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        max_partition_jobs=len(retry_plan.context.samples),
        pipeline_overlap=False,
    )

    assert completed.runtime_receipt.supervisor.attempt is (
        retry_plan.context.processing_owners.attempt
    )
    assert (
        retry_plan.context.processing_owners.attempt.processing_attempt_id
        != failed_attempt_id
    )
    writer = project_completed_untarget_batch_writer_inputs(completed)
    assert writer.matrix.cells
    assert writer.matrix == baseline_writer.matrix
    assert completed.application_result.candidate_ids == (
        baseline.application_result.candidate_ids
    )
    assert completed.application_result.physical_observation_ids == (
        baseline.application_result.physical_observation_ids
    )
    completed_samples = tuple(
        sample.metadata for sample in completed.application_result.canonical_samples
    )
    baseline_samples = tuple(
        sample.metadata for sample in baseline.application_result.canonical_samples
    )
    assert completed_samples == baseline_samples
    assert all(cell.area is None or cell.area > 0 for cell in writer.matrix.cells)
    assert {
        sample.metadata.raw_stem
        for sample in completed.application_result.canonical_samples
    } == {path.stem for path in retry_plan.context.raw_paths_by_sample.values()}

    retry_vendors = tuple(
        vendor for vendors in opened_runs[1].values() for vendor in vendors
    )
    assert retry_vendors
    assert any(not vendor.chromatogram_request_counts for vendor in retry_vendors)
    assert any(vendor.chromatogram_request_counts for vendor in retry_vendors)
    assert any(ms2_reads.get(id(vendor), 0) == 0 for vendor in retry_vendors)


def test_partition_acquisitions_overlap_with_shared_worker_budget_and_ordered_output(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "_ensure_assemblies_loaded", lambda *_args: None)
    config = AlignmentConfig(anchor_min_seed_events=1)
    serial = owner.execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "serial-partitions",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "serial-partitions.json", "serial-partitions"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=4,
        pipeline_overlap=False,
    )

    original_leaf = owner.execute_untargeted_cid_nl_runtime_partition
    original_append = owner.PartitionResultStore.append_children
    first_two = {"partition-00000", "partition-00001"}
    barrier = Barrier(2)
    second_completed = Event()
    lock = Lock()
    active_budgets: dict[str, int] = {}
    observed_budgets: dict[str, int] = {}
    peak_budget = 0
    completion_order = []
    append_order = []
    composition_active = 0

    def leaf(*args, **kwargs):
        nonlocal peak_budget
        partition_key = kwargs["partition_key"]
        budget = kwargs["raw_workers"]
        assert kwargs["pool"].max_workers == budget
        with lock:
            active_budgets[partition_key] = budget
            observed_budgets[partition_key] = budget
            peak_budget = max(peak_budget, sum(active_budgets.values()))
        try:
            if partition_key in first_two:
                barrier.wait(timeout=10)
            result = original_leaf(*args, **kwargs)
            if partition_key == "partition-00001":
                second_completed.set()
            elif partition_key == "partition-00000":
                assert second_completed.wait(timeout=10)
            with lock:
                completion_order.append(partition_key)
            return result
        finally:
            with lock:
                active_budgets.pop(partition_key)

    def append(store, partition_key, *args, **kwargs):
        nonlocal composition_active
        with lock:
            assert composition_active == 0
            composition_active = 1
        try:
            sleep(0.01)
            result = original_append(store, partition_key, *args, **kwargs)
            append_order.append(partition_key)
            return result
        finally:
            with lock:
                composition_active = 0

    monkeypatch.setattr(owner, "execute_untargeted_cid_nl_runtime_partition", leaf)
    monkeypatch.setattr(owner.PartitionResultStore, "append_children", append)
    overlapped = owner.execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "overlapped-partitions",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "overlapped-partitions.json", "overlapped-partitions"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=4,
        pipeline_overlap=True,
    )

    assert completion_order[:2] == ["partition-00001", "partition-00000"]
    assert append_order == [
        f"partition-{index:05d}" for index in range(len(observed_budgets))
    ]
    assert set(observed_budgets.values()) == {2}
    assert peak_budget == 4
    assert composition_active == 0 and not active_budgets
    assert project_completed_untarget_batch_writer_inputs(overlapped) == (
        project_completed_untarget_batch_writer_inputs(serial)
    )
    assert overlapped.application_result.candidate_ids == (
        serial.application_result.candidate_ids
    )
    assert overlapped.application_result.physical_observation_ids == (
        serial.application_result.physical_observation_ids
    )


def test_overlapped_partition_acquisition_failure_propagates(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "_ensure_assemblies_loaded", lambda *_args: None)
    original_leaf = owner.execute_untargeted_cid_nl_runtime_partition
    barrier = Barrier(2)
    failure_released = Event()
    injected_error = OSError("injected concurrent acquisition failure")
    observed_budgets = {}

    def leaf(*args, **kwargs):
        partition_key = kwargs["partition_key"]
        observed_budgets[partition_key] = kwargs["raw_workers"]
        if partition_key in {"partition-00000", "partition-00001"}:
            barrier.wait(timeout=10)
        result = original_leaf(*args, **kwargs)
        if partition_key == "partition-00001":
            failure_released.set()
            raise injected_error
        if partition_key == "partition-00000":
            assert failure_released.wait(timeout=10)
        return result

    monkeypatch.setattr(owner, "execute_untargeted_cid_nl_runtime_partition", leaf)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "overlapped-failure.json", "overlapped-failure"
    )
    with pytest.raises(
        RuntimeError, match="injected concurrent acquisition failure"
    ) as error:
        owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "overlapped-failure",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            raw_workers=4,
            pipeline_overlap=True,
        )
    assert error.value.__cause__ is injected_error
    assert observed_budgets == {
        "partition-00000": 2,
        "partition-00001": 2,
    }
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED
    assert list((tmp_path / "overlapped-failure").glob(".cid_nl_partitions-*")) == []


@pytest.mark.parametrize("raw_workers", [1, 2, 4])
@pytest.mark.parametrize("managed_call", [False, True])
def test_sample_parallelism_keeps_partition_and_acquisition_counts_fixed(
    tmp_path: Path, monkeypatch, managed_call: bool, raw_workers: int
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    if managed_call:
        pytest.importorskip("clr")
        from System.Threading import Thread

        def pause():
            Thread.Sleep(100)
    else:

        def pause():
            sleep(0.1)

    plan, _ = _plan(tmp_path, monkeypatch, scenario="recovery")
    monkeypatch.setattr(runtime_owner, "_ensure_assemblies_loaded", lambda *_args: None)
    overlap_count = min(raw_workers, 3)  # Three samples have initial candidates.
    barrier = Barrier(overlap_count)
    lock = Lock()
    coordinator = get_ident()
    intervals = []
    selected_calls = 0
    handle_threads = {}
    active_handles = set()
    disposed = set()
    peak_handles = 0
    live_jobs = 0
    job_counts = []
    append_order = []
    barrier_checks = []
    acquisitions = []
    original_materialize = owner.materialize_untargeted_cid_nl_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_append = owner.PartitionResultStore.append_children
    original_prepare = runtime_owner._prepare_group_authorized_recoveries
    original_execute = owner.execute_runtime_attempt

    def materialize(*args, **kwargs):
        nonlocal live_jobs
        assert get_ident() == coordinator and live_jobs == 0
        batch = original_materialize(*args, **kwargs)
        from xic_extractor.extraction.untargeted_real_data_models import (
            _request_subject_candidates,
        )

        expected_initial_sample_ids = {
            sample.identity.sample_identity_id
            for sample in plan.context.samples
            if plan.context.candidates_by_sample[sample.metadata.raw_stem]
        }
        assert {
            job.sample.identity.sample_identity_id for job in batch.jobs
        } == expected_initial_sample_ids
        assert all(_request_subject_candidates(job) for job in batch.jobs)
        assert all(
            job.processing_owners is plan.context.processing_owners
            for job in batch.jobs
        )
        live_jobs = len(batch.jobs)
        job_counts.append(live_jobs)
        return batch

    def initialize(handle, *args):
        nonlocal peak_handles
        original_init(handle, *args)
        with lock:
            handle_threads[handle] = get_ident()
            active_handles.add(handle)
            peak_handles = max(peak_handles, len(active_handles))

    def dispose(handle):
        assert handle_threads[handle] == get_ident()
        original_dispose(handle)
        with lock:
            disposed.add(handle)
            active_handles.remove(handle)

    def chromatogram(handle, *args):
        nonlocal selected_calls
        assert handle_threads[handle] == get_ident()
        with lock:
            selected = selected_calls < overlap_count
            selected_calls += 1
        if selected:
            barrier.wait(timeout=10)
            start = perf_counter()
            pause()
            intervals.append((start, perf_counter()))
        return original_chromatogram(handle, *args)

    def prepare(*args, **kwargs):
        assert get_ident() == coordinator and not active_handles
        barrier_checks.append(len(disposed))
        return original_prepare(*args, **kwargs)

    def append(store, partition_key, *args, **kwargs):
        assert get_ident() == coordinator and not active_handles
        append_order.append(partition_key)
        return original_append(store, partition_key, *args, **kwargs)

    def progress(event):
        nonlocal live_jobs
        assert get_ident() == coordinator
        if event["stage"] == "partition_released":
            live_jobs = 0

    def execute(*args, worker_batch_provider, **kwargs):
        def observe(aggregate):
            batch = worker_batch_provider(aggregate)
            acquisitions.extend(batch.acquisitions)
            return batch

        return original_execute(*args, worker_batch_provider=observe, **kwargs)

    monkeypatch.setattr(owner, "materialize_untargeted_cid_nl_partition", materialize)
    monkeypatch.setattr(owner, "execute_runtime_attempt", execute)
    monkeypatch.setattr(runtime_owner, "_prepare_group_authorized_recoveries", prepare)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    monkeypatch.setattr(owner.PartitionResultStore, "append_children", append)
    result = owner.execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "parallel",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "parallel.json", "parallel"),
        max_partition_jobs=8,
        raw_workers=raw_workers,
        progress_callback=progress,
    )
    assert type(result) is CompletedUntargetBatchWorkflowResult
    initial_sample_count = sum(
        bool(plan.context.candidates_by_sample[sample.metadata.raw_stem])
        for sample in plan.context.samples
    )
    assert job_counts == [initial_sample_count * 2, initial_sample_count]
    assert live_jobs == 0
    assert append_order == ["partition-00000", "partition-00001"]
    assert len(handle_threads) == len(acquisitions) == 8
    assert barrier_checks == [3, 7]
    assert overlap_count <= peak_handles <= raw_workers
    assert len(intervals) == overlap_count
    if raw_workers > 1:
        assert min(end for _, end in intervals) > max(start for start, _ in intervals)
        assert coordinator not in handle_threads.values()
    else:
        assert set(handle_threads.values()) == {coordinator}
    assert set(handle_threads) == disposed and not active_handles


@pytest.mark.parametrize(
    "failure_mode", ["cancel", "vendor_failure", "cleanup_failure", "spool_failure"]
)
def test_sample_failure_drains_without_more_samples_or_partition_publication(
    tmp_path: Path, monkeypatch, failure_mode: str
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner
    import xic_extractor.extraction.untargeted_cid_nl_real_data_runtime as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "_ensure_assemblies_loaded", lambda *_args: None)
    barrier = Barrier(2)
    lock = Lock()
    caller_cancel = Event()
    caller_observed_cancel = Event()
    failed_sample_observed = Event()
    entered = set()
    opened = {}
    closed = set()
    batches = []
    original_leaf = owner.execute_untargeted_cid_nl_runtime_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_execute = owner.execute_runtime_attempt

    def leaf(*args, **kwargs):
        entered.add(kwargs["partition_key"])
        return original_leaf(*args, **kwargs)

    def initialize(handle, *args):
        original_init(handle, *args)
        with lock:
            opened[handle] = (get_ident(), len(opened))

    def dispose(handle):
        thread, ordinal = opened[handle]
        assert thread == get_ident()
        original_dispose(handle)
        closed.add(handle)
        if failure_mode == "cleanup_failure" and ordinal == 1:
            raise OSError("injected cleanup failure")

    def chromatogram(handle, *args):
        thread, ordinal = opened[handle]
        assert thread == get_ident()
        if ordinal < 2:
            barrier.wait(timeout=10)
            if failure_mode == "cancel":
                caller_cancel.set()
                assert caller_observed_cancel.wait(timeout=10)
            elif failure_mode in {"vendor_failure", "cleanup_failure"}:
                if ordinal == 1 and failure_mode == "vendor_failure":
                    raise OSError("injected vendor failure")
                if ordinal == 0:
                    assert failed_sample_observed.wait(timeout=10)
        return original_chromatogram(handle, *args)

    def progress(event):
        if event["stage"] == "partition_initial" and event["completed"]:
            failed_sample_observed.set()

    def should_cancel():
        if caller_cancel.is_set():
            caller_observed_cancel.set()
            return True
        return False

    def execute(*args, worker_batch_provider, **kwargs):
        def observe(aggregate):
            batch = worker_batch_provider(aggregate)
            assert set(opened) == closed
            batches.append(batch)
            return batch

        return original_execute(*args, worker_batch_provider=observe, **kwargs)

    def fail_spool(*_args):
        raise OSError("injected spool failure")

    monkeypatch.setattr(owner, "execute_untargeted_cid_nl_runtime_partition", leaf)
    monkeypatch.setattr(owner, "execute_runtime_attempt", execute)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    if failure_mode == "spool_failure":
        monkeypatch.setattr(owner.PartitionResultStore, "append_children", fail_spool)
    marker = OwnerPathRuntimeMarker(tmp_path / "failure.json", "parallel-failure")
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "failed",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            raw_workers=2,
            max_partition_jobs=8,
            should_cancel=should_cancel,
            progress_callback=progress,
        )
    if failure_mode in {"vendor_failure", "cleanup_failure"}:
        assert "injected " + failure_mode.replace("_", " ") in str(error.value)
    assert entered == {"partition-00000"}
    assert set(opened) == closed
    assert len(opened) == (4 if failure_mode == "spool_failure" else 2)
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED
    assert len(batches) == 1
    resources = batches[0].resource_settlements
    assert sum(
        receipt.cleanup_outcome is RuntimeResourceCleanupOutcome.FAILED
        for receipt in resources
    ) == (1 if failure_mode == "cleanup_failure" else 0)
    assert sum(
        receipt.open_outcome is RuntimeResourceOpenOutcome.OPENED
        for receipt in resources
    ) == len(opened)
    assert all(
        receipt.open_outcome is RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
        for receipt in resources
        if "partition-00001:" in receipt.resource_key
    )
    assert list((tmp_path / "failed").glob(".cid_nl_partitions-*")) == []


def test_sample_worker_transport_references_global_request_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from xic_extractor.execution_engine.owner_pickle import (
        owner_pickle_dumps,
        owner_pickle_loads,
    )
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        _sample_worker_shared_objects,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    shared = _sample_worker_shared_objects(
        plan.context.processing_owners,
        (batch.jobs[0]._request_subjects, batch.subject_batch),
    )
    source = batch.jobs[0]

    restored = owner_pickle_loads(owner_pickle_dumps(source, shared), shared)

    assert restored.processing_owners is source.processing_owners
    assert restored.processing_owners.request is source.processing_owners.request
    assert restored.processing_owners.attempt is source.processing_owners.attempt
    assert restored._request_subjects is source._request_subjects
    assert restored.sample == source.sample
    assert restored.candidates == source.candidates


def test_process_recovery_dispatches_only_missing_subjects(tmp_path, monkeypatch):
    from xic_extractor.execution_engine.worker_pool import WorkerAdmissionPool
    from xic_extractor.extraction import (
        untargeted_real_data_runtime as runtime,
    )
    from xic_extractor.extraction import (
        untargeted_real_data_worker as worker,
    )

    # Exercise process message construction/consumption with synthetic RAW I/O.
    # Native spawning and transport identity have their own existing coverage.
    class LocalProcessMessages(WorkerAdmissionPool):
        @property
        def backend(self):
            return "process"

    plan, _ = _plan(tmp_path, monkeypatch, scenario="partial_recovery")
    batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    monkeypatch.setattr(worker, "_ensure_assemblies_loaded", lambda *_: None)
    requests = []
    execute_recovery = runtime._execute_sample_recovery_worker

    def observe_request(request):
        requests.append(request)
        keys = {job.work_unit_key for job in request.jobs}
        assert keys == set(request.recovery_group_center_rts_by_key)
        assert set(request.adapter_results_by_key) == keys
        assert len(keys) < len(plan.subject_batch.subjects)
        return execute_recovery(request)

    monkeypatch.setattr(runtime, "_execute_sample_recovery_worker", observe_request)
    with LocalProcessMessages(max_workers=1) as pool:
        result = runtime.execute_untargeted_cid_nl_runtime_partition(
            batch.jobs,
            partition_key="sparse-recovery",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            pool=pool,
        )
    assert result.succeeded
    assert len(requests) == 1
    assert len(result.sample_results) == len(batch.jobs)
    selected = {job.work_unit_key for job in requests[0].jobs}
    for sample_result in result.sample_results:
        assert bool(sample_result.recovery_attempts) is (
            sample_result.job.work_unit_key in selected
        )


def test_sparse_partition_materializes_only_samples_with_request_candidates(
    tmp_path, monkeypatch
):
    from xic_extractor.extraction.untargeted_real_data_models import (
        _request_subject_candidates,
    )

    plan, _ = _plan(tmp_path, monkeypatch, scenario="recovery")
    batch = materialize_untargeted_cid_nl_partition(
        plan,
        plan.subject_batch,
        sparse=True,
    )

    assert len(batch.sample_job_templates) == len(plan.context.samples)
    assert batch.jobs
    assert all(_request_subject_candidates(job) for job in batch.jobs)
    initial_sample_ids = {job.sample.identity.sample_identity_id for job in batch.jobs}
    assert len(initial_sample_ids) >= 2
    missing_sample_id = plan.context.samples[-1].identity.sample_identity_id
    assert missing_sample_id not in initial_sample_ids


@pytest.mark.parametrize(
    "scenario,expected_sparse_jobs,expected_sparse_results",
    [("recovery", 9, 12), ("below_threshold", 3, 3), ("blank_missing", 9, 9)],
)
def test_sparse_recovery_partition_matches_dense_composition(
    tmp_path, monkeypatch, scenario, expected_sparse_jobs, expected_sparse_results
):
    from xic_extractor.extraction.untargeted_production_batch import (
        compose_untargeted_cid_nl_partition,
        execute_untargeted_cid_nl_production_batch,
    )
    from xic_extractor.extraction.untargeted_real_data_runtime import (
        execute_untargeted_cid_nl_runtime_partition,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    config = AlignmentConfig(anchor_min_seed_events=1)
    dense_batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    assert len(dense_batch.jobs) == 12
    dense_runtime = execute_untargeted_cid_nl_production_batch(
        dense_batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "dense.json", "dense"),
        alignment_config=config,
    )
    assert dense_runtime.succeeded
    dense_composition = compose_untargeted_cid_nl_partition(
        dense_batch,
        dense_runtime.sample_results,
        alignment_config=config,
        known_istd_reference=reference,
    )

    sparse_batch = materialize_untargeted_cid_nl_partition(
        plan, plan.subject_batch, sparse=True
    )
    assert len(sparse_batch.jobs) == expected_sparse_jobs
    sparse_runtime = execute_untargeted_cid_nl_runtime_partition(
        sparse_batch.jobs,
        recovery_templates=sparse_batch.sample_job_templates,
        partition_key="sparse-recovery",
        alignment_config=config,
    )
    assert sparse_runtime.succeeded
    assert len(sparse_runtime.sample_results) == expected_sparse_results
    sparse_composition = compose_untargeted_cid_nl_partition(
        sparse_batch,
        sparse_runtime.sample_results,
        alignment_config=config,
        known_istd_reference=reference,
    )

    dense_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in dense_runtime.sample_results
    }
    sparse_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in sparse_runtime.sample_results
    }
    missing_stem = plan.context.samples[-1].metadata.raw_stem
    if scenario == "recovery":
        missing_results = [
            result
            for result in sparse_runtime.sample_results
            if result.job.sample.metadata.raw_stem == missing_stem
        ]
        assert missing_results
        assert all(result.recovery_attempts for result in missing_results)
        assert set(sparse_by_key) == set(dense_by_key)
    else:
        assert missing_stem not in {
            result.job.sample.metadata.raw_stem
            for result in sparse_runtime.sample_results
        }
    assert dense_composition.children == sparse_composition.children
    assert dense_composition.initial_groups == sparse_composition.initial_groups
    assert dense_composition.initial_edges == sparse_composition.initial_edges
    assert (
        dense_composition.excluded_candidates == sparse_composition.excluded_candidates
    )


def test_formal_partition_reuses_prepared_group_ids_for_recovery_and_composition(
    tmp_path, monkeypatch
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as bounded_owner
    import xic_extractor.extraction.untargeted_production_batch as production_owner
    from xic_extractor.execution_engine.streamed_result_store import StreamedResultStore

    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    calls = []
    original_group_builder = production_owner.build_initial_cid_nl_cross_sample_groups

    def observe_group_build(*args, **kwargs):
        calls.append(1)
        return original_group_builder(*args, **kwargs)

    monkeypatch.setattr(
        production_owner,
        "build_initial_cid_nl_cross_sample_groups",
        observe_group_build,
    )
    adapter = bounded_owner.CidNlPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    assert len(adapter.partitions) == 1
    partition = adapter.partitions[0]
    acquisition = adapter.execute_acquisition(partition)
    assert acquisition.succeeded
    batch, leaf = acquisition.result
    prepared = leaf.prepared_grouping
    assert prepared is not None
    assert len(prepared.worksets) == 1
    # N=4 and K=2: neither subject qualifies alone, but their one SUPPORT
    # sample each belongs to the same compatible group.
    assert len(batch.jobs) == 2
    assert len({job.method_subject.method_subject_id for job in batch.jobs}) == 2
    assert len(leaf.sample_results) == 4
    assert calls == [1] * len(prepared.worksets)
    prepared_group_ids = {
        group.group_hypothesis_id
        for workset in prepared.worksets
        for group in workset.assignment_scope.groups
    }
    assert len(prepared_group_ids) == 1
    recovery_group_ids = {
        attempt.group_hypothesis_id
        for result in leaf.sample_results
        for attempt in result.recovery_attempts
        if attempt.group_hypothesis_id is not None
    }
    assert recovery_group_ids
    assert recovery_group_ids == prepared_group_ids
    assert {
        result.job.sample.metadata.raw_stem
        for result in leaf.sample_results
        if result.recovery_attempts
    } == {"S2", "S3"}
    recovery_result = next(
        result for result in leaf.sample_results if result.recovery_attempts
    )
    recovery_attempt = recovery_result.recovery_attempts[0]
    same_rt_different_groups = replace(
        recovery_result,
        recovery_attempts=(
            replace(recovery_attempt, group_hypothesis_id="group-a"),
            replace(recovery_attempt, group_hypothesis_id="group-b"),
        ),
    )
    assert (
        same_rt_different_groups.recovery_for_group(
            0.0, group_hypothesis_id="group-b"
        ).group_hypothesis_id
        == "group-b"
    )
    assert (
        same_rt_different_groups.recovery_for_group(
            0.0, group_hypothesis_id="wrong-group"
        )
        is None
    )
    calls_before_composition = len(calls)
    store = StreamedResultStore(tmp_path / "formal-spool", adapter.get_shared_objects())
    composed = adapter.execute_composition(
        partition,
        acquisition,
        store,
        retain_memory=True,
    )
    assert composed.result.child_count == 1
    assert len(calls) == calls_before_composition
    assert batch.prepared_grouping is prepared
    persisted = store.read(composed.sealed.index)
    assert {
        group.group_hypothesis_id for group in persisted.initial_groups
    } == prepared_group_ids
    assert {
        group.group_hypothesis_id
        for child in persisted.children
        for group in child.scientific_core.grouping_result.final_population
    } == prepared_group_ids


def test_process_backend_reuses_one_pool_per_acquisition_lane_and_closes_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner

    created = []

    class RecordingPool:
        def __init__(self, **kwargs):
            self.max_workers = kwargs["max_workers"]
            self.backend = kwargs["backend"]
            self.initializer = kwargs["initializer"]
            self.shared_objects = kwargs["shared_objects"]
            self.stopped = False
            self.first_error = None
            self.transport_metrics = None
            self.exit_calls = 0
            created.append(self)

        def __exit__(self, *_args):
            self.exit_calls += 1

    monkeypatch.setattr(owner, "WorkerAdmissionPool", RecordingPool)
    plan, _reference = _plan(tmp_path, monkeypatch)
    adapter = owner.CidNlPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=4,
        raw_worker_backend="process",
        acquisition_workers=2,
    )

    assert adapter.acquisition_workers == 2
    assert adapter.partition_raw_workers == 2
    assert len(created) == 2
    assert sum(pool.max_workers for pool in created) == 4
    assert {id(pool.shared_objects) for pool in created} == {
        id(created[0].shared_objects)
    }
    shared = created[0].shared_objects
    for partition in adapter.partitions:
        subject_batch = adapter._partition_subject_batches[partition.partition_key]
        assert any(value is subject_batch for value in shared)

    adapter._process_pools_failed.set()
    with pytest.raises(RuntimeError, match="failure stopped admission"):
        adapter.execute_acquisition(adapter.partitions[0])
    adapter._process_pools_failed.clear()

    adapter.close_process_worker_pools()
    adapter.close_process_worker_pools()
    assert [pool.exit_calls for pool in created] == [1, 1]

    with pytest.raises(RuntimeError, match="already closed"):
        adapter.execute_acquisition(adapter.partitions[0])
