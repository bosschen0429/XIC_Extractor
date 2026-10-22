from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_real_data_adapter import _without_ms1_peak
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
)
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.neighborhood_graph import (
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.feature_membership_inference import (
    supports_existing_feature,
)
from xic_extractor.alignment.grouping_workflow import (
    admit_cid_nl_group_backfills,
    group_cid_nl_owners,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.execution.attempt import start_runtime_attempt
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    is_ms1_only_cid_nl_backfill_assessment,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition import cid_nl_acquisition
from xic_extractor.untargeted_execution.partition import models as partition_models
from xic_extractor.untargeted_execution.partition.execution import execute_attempt
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionJob,
    project_completed_backfill,
)
from xic_extractor.untargeted_execution.partition.request import (
    build_partition_context,
)


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("with_order", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_backfill_batch_preserves_shape_compatibility_without_arbitrary_winners(
    tmp_path,
    conflicting,
    with_order,
    reverse,
):
    widths = {
        "reference1": 0.07,
        "reference2": 0.07,
        "narrow": 0.03,
        "other": 0.25 if conflicting else 0.05,
        "compatible": 0.07,
    }
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=tuple(widths),
    )
    owners, assessments = {}, {}
    offsets = np.linspace(-0.6, 0.6, 121)
    for name, width in widths.items():
        sample = fixture.sample(name)
        values = 1000 * np.exp(-0.5 * (offsets / width) ** 2)
        source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=sample,
        )
        trace = Trace.from_arrays(
            sample_name=name,
            mz=500.0,
            rt=offsets + 8,
            intensity=values,
            rt_min=7.4,
            rt_max=8.6,
            ppm_tol=10.0,
            source="synthetic_gaussian",
            acquisition_source_id=source.acquisition_source_id,
        )
        peak = mint_peak_observation_ref(
            processing_owners=fixture.owners,
            sample=sample,
            trace=trace,
            integration=IntegrationResult(
                rt_left_min=7.4,
                rt_apex_min=8.0,
                rt_right_min=8.6,
                raw_apex_rt_min=8.0,
                rt_width_min=1.2,
                height_raw=1000.0,
                height_smoothed=1000.0,
                area_raw_counts_seconds=float(np.trapezoid(values, offsets) * 60),
                integration_method="raw_trapezoid",
                boundary_sources=("candidate_interval", "gaussian15"),
                raw_scan_indices=tuple(range(len(offsets))),
            ),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum", "gaussian15"),
        )
        evidence = cid_nl_evidence(fixture, name, scan_number=101, scan_rt_min=8.0)
        support = cid_nl_unique_support(fixture, name, peak=peak, evidence=evidence)
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id=f"gaussian15:{name}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=7.4,
                query_rt_max=8.6,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                scanned_event_count=1,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        assert assessment.content.method_result == "SUPPORT"
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=fixture.owners,
            sample=sample,
            assessment=assessment,
        )
        owners[name] = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        assessments[name] = assessment

    config = AlignmentConfig()
    frame = SampleRtFrame(
        injection_order={name: index for index, name in enumerate(widths, 1)}
        if with_order
        else {}
    )
    references = (owners["reference1"], owners["reference2"])
    (group,) = group_cid_nl_owners(
        references,
        config=config,
        sample_universe=frozenset(widths),
        rt_frame=frame,
    )
    assert group.disposition == "established"
    identity = validate_cross_sample_peak_group_identity(group, require_canonical=True)
    initial = CrossSampleGroupingResult(
        processing_owners=fixture.owners,
        rt_frame=frame,
        partition_transition=CrossSamplePartitionTransition(
            status="assessable",
            predecessors=(identity,),
            successors=(identity,),
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
    )
    backfills = {}
    for name in ("narrow", "other", "compatible"):
        # Both conflicting shapes pass individually against the references:
        # r(narrow, reference)=.898, r(wide, reference)=.818, but r(narrow, wide)=.597.
        assert supports_existing_feature(
            owners[name],
            group.owners,
            config=config,
            rt_frame=frame,
            rules=CID_NL_GROUPING_RULES,
        )
        backfills[
            (
                group.group_hypothesis_id,
                fixture.sample(name).identity.sample_identity_id,
                assessments[name].content.method_subject.method_subject_id,
            )
        ] = (fixture.sample(name), assessments[name])
    if reverse:
        backfills = dict(reversed(tuple(backfills.items())))
    grouping, (successor,), successor_ids, retained, _ = admit_cid_nl_group_backfills(
        result=initial,
        groups=(group,),
        backfills=backfills,
        alignment_config=config,
        member_observations={
            peak.content.peak_observation_content_id: peak
            for assessment in assessments.values()
            for peak in assessment.candidate_peaks
        },
    )
    accepted = {"compatible"} if conflicting else {"narrow", "other", "compatible"}
    assert {owner.sample_stem for owner in successor.owners} == (
        {"reference1", "reference2"} | accepted
    )
    assert set(references).issubset(successor.owners)
    assert successor_ids == {group.group_hypothesis_id: successor.group_hypothesis_id}
    assert set(grouping.partition_transition.admitted_members) == {
        owners[name].cross_sample_member_subject for name in accepted
    }
    assert set(retained) == {
        (
            fixture.sample(name).identity.sample_identity_id,
            assessments[name].artifact.content_identity.artifact_content_id,
        )
        for name in accepted
    }
    assert {
        hypothesis.hypothesis_id
        for values in retained.values()
        for hypothesis in values
    } == {owners[name].owner_id for name in accepted}


def test_backfill_window_does_not_change_initial_owner_partition(tmp_path, monkeypatch):
    physical_records = {}
    from xic_extractor.untargeted_execution.partition_composition import (
        finalize_untargeted_partition_grouping,
        prepare_untargeted_partition_grouping,
    )

    settings = replace(_settings(), rt_min=8.4, rt_max=11.5)
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(6))
    raws, candidates = {}, {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw((8.0,))
        shift = 1.0 if index < 3 else 2.8
        raw.rt += shift
        raw.scans = tuple(replace(scan, rt=scan.rt + shift) for scan in raw.scans)
        raws[path] = raw
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(sample_name=path.stem, raw_stem=path.stem, injection_order=i)
            for i, path in enumerate(paths, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:grouping-window-invariance",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    config = AlignmentConfig(anchor_min_seed_events=1)
    observed = []

    def prepare(initial):
        bootstrap = prepare_untargeted_partition_grouping(
            batch, initial, alignment_config=config, bootstrap_only=True
        )
        # Reuse identical captures, owners and RT frame; the shared acquisition
        # setting must not change detection inputs in this grouping comparison.
        for half_window in (1.0, 2.0):
            variant = replace(
                batch,
                context=replace(
                    batch.context,
                    peak_config=replace(
                        peak_config, nl_rt_anchor_half_window_min=half_window
                    ),
                ),
            )
            final = finalize_untargeted_partition_grouping(
                variant, bootstrap, rt_frame=SampleRtFrame()
            )
            observed.append(
                tuple(
                    group
                    for workset in final.worksets
                    for group in workset.grouping_scope.groups
                )
            )
        return final

    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="grouping-window-invariance",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        alignment_config=config,
        prepare_grouping=prepare,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "runtime.json", "window"),
    )
    assert result.succeeded
    assert len(observed) == 2

    def partition(groups):
        return {(frozenset(group.owner_ids), group.disposition) for group in groups}

    assert partition(observed[0]) == partition(observed[1])
    for groups in observed:
        assert {
            frozenset(owner.sample_stem for owner in group.owners) for group in groups
        } == {
            frozenset(("S0", "S1", "S2")),
            frozenset(("S3", "S4", "S5")),
        }
        assert all(group.disposition == "established" for group in groups)
        assert all(
            len(group.owners) == len({owner.sample_stem for owner in group.owners})
            for group in groups
        )
    assert {group.group_hypothesis_id for group in observed[0]} == {
        group.group_hypothesis_id for group in observed[1]
    }


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("half_window", [0.10, 0.15])
def test_backfill_query_and_empty_completion_keep_sample_rt_separate_from_group(
    tmp_path, monkeypatch, empty, half_window
):
    physical_records = {}
    import csv

    from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
    from xic_extractor.diagnostics.backfill_evidence.tsv import (
        iter_backfill_evidence_tsv,
    )
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_backfill_cell_evidence_tsv,
    )
    from xic_extractor.untargeted_execution.partition_composition import (
        finalize_untargeted_partition_grouping,
        prepare_untargeted_partition_grouping,
    )

    shifts = {"S1": 0.0, "S2": 0.0, "Late": 0.4, "Early": -0.4}
    settings = _settings()
    peak_config = replace(
        _peak_config(tmp_path), nl_rt_anchor_half_window_min=half_window
    )
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in shifts)
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw((8.0 + shifts[path.stem],))
        candidates[path.stem] = (
            _current_candidates(
                raw,
                raw_file=path,
                settings=settings,
                physical_records=physical_records,
            )
            if path.stem.startswith("S")
            else ()
        )
        if not path.stem.startswith("S"):
            raw.scans = ()
            if empty:
                raw.intensity[:] = 0.0
        raws[path] = raw
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=i,
                sample_role="study_sample",
            )
            for i, path in enumerate(paths, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:sample-query-rt",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    config = AlignmentConfig(anchor_min_seed_events=1)
    warp = BatchRtWarp(
        tuple(
            (stem, ((7.0, offset), (20.0, offset))) for stem, offset in shifts.items()
        ),
        12,
    )

    def prepare(initial):
        bootstrap = prepare_untargeted_partition_grouping(
            batch,
            initial,
            alignment_config=config,
            bootstrap_only=True,
        )
        return finalize_untargeted_partition_grouping(
            batch, bootstrap, rt_frame=SampleRtFrame().with_warp(warp)
        )

    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="sample-query-rt",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        alignment_config=config,
        prepare_grouping=prepare,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "query-runtime.json", "query-rt"
        ),
    )
    assert result.succeeded
    for sample_result in result.sample_results:
        stem = sample_result.job.sample.metadata.raw_stem
        if stem.startswith("S"):
            continue
        (attempt,) = sample_result.backfill_attempts
        expected = 8.0 + shifts[stem]
        assert attempt.query_rt == pytest.approx(expected)
        assert attempt.query_rt_basis == "calibrated_trajectory_fit"
        receipt = attempt.assessment.content.evaluation_receipt
        assert (receipt.query_rt_min, receipt.query_rt_max) == pytest.approx(
            (expected - half_window, expected + half_window)
        )
        assert not receipt.query_rt_min <= 8.0 <= receipt.query_rt_max
        assert len(attempt.assessment.candidate_peaks) == (0 if empty else 1)
        if not empty:
            assert attempt.assessment.candidate_peaks[
                0
            ].content.integration.rt_apex_min == pytest.approx(expected)
    completed = complete_dense_partition(batch, result, alignment_config=config)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    for cell in writer.matrix.cells:
        if cell.sample_stem in {"Early", "Late"}:
            assert cell.backfill_request_rt_min == pytest.approx(
                8.0 + shifts[cell.sample_stem] - half_window
            )
            assert cell.backfill_request_rt_max == pytest.approx(
                8.0 + shifts[cell.sample_stem] + half_window
            )
    path = write_alignment_backfill_cell_evidence_tsv(
        tmp_path / "queries.tsv",
        writer.matrix,
        production_decisions=writer.production_decisions,
    )
    with path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if not empty:
        for row in rows:
            if row["sample_stem"] in {"Early", "Late"}:
                assert float(row["backfill_query_rt_min"]) == pytest.approx(
                    8.0 + shifts[row["sample_stem"]] - half_window
                )
        assert len(tuple(iter_backfill_evidence_tsv(path))) == len(rows)


@pytest.mark.parametrize(
    "outcome", ["support", "empty", "below-quorum", "blank", "failed"]
)
def test_rejected_initial_evidence_gets_one_independent_bounded_assessment(
    tmp_path, monkeypatch, outcome
):
    physical_records = {}
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        project_completed_untarget_batch_writer_inputs,
    )

    stems = ("S1", "S2", "Rejected")
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    settings, peak_config = _settings(), _peak_config(tmp_path)
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw((8.0,))
        raws[path] = raw
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
        if path.stem == "Rejected":
            candidates[path.stem] = tuple(
                _without_ms1_peak(candidate) for candidate in candidates[path.stem]
            )
            if outcome == "empty":
                raw.intensity[:] = 0.0
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="blank"
                if (
                    (outcome == "blank" and stem == "Rejected")
                    or (outcome == "below-quorum" and stem == "S2")
                )
                else "study_sample",
            )
            for i, stem in enumerate(stems)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:rejected-bounded-recovery",
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    recovery_calls = []
    acquire = cid_nl_acquisition.acquire_untargeted_cid_nl_backfill_trace_batch

    def acquire_recovery(**kwargs):
        recovery_calls.append(kwargs["sample"].metadata.raw_stem)
        if outcome == "failed":
            raise OSError("recovery acquisition failed")
        return acquire(**kwargs)

    monkeypatch.setattr(
        cid_nl_acquisition,
        "acquire_untargeted_cid_nl_backfill_trace_batch",
        acquire_recovery,
    )
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="rejected-bounded-recovery",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "reject-recovery"
        ),
    )
    if outcome == "failed":
        assert not result.succeeded
        assert recovery_calls == ["Rejected"]
        assert any(
            settlement.failure_detail is not None
            and "recovery acquisition failed" in settlement.failure_detail.message
            for settlement in result.finalization.coordinator.state.runtime.settlements
        )
        return
    assert result.succeeded, tuple(
        s.failure_detail.message
        for s in result.finalization.coordinator.state.runtime.settlements
        if s.failure_detail is not None
    )
    rejected = next(
        r for r in result.sample_results if r.job.sample.metadata.raw_stem == "Rejected"
    )
    initial = rejected.adapter_result.assessment_input.assessment
    assert initial.content.method_result == "REJECT"
    assert initial.content.reason_codes == (
        "qualifying_cid_nl_without_usable_ms1_peak",
    )
    completed = complete_dense_partition(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    assert initial in completed.method_assessments
    assert any(
        exclusion.candidate_id == candidates["Rejected"][0].candidate_id
        for exclusion in completed.application_result.excluded_candidates
    )
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    (decision,) = tuple(
        d
        for d in writer.production_decisions.cells.values()
        if d.sample_stem == "Rejected"
    )
    if outcome == "support":
        assert decision.write_matrix_value and decision.matrix_value > 0
        (recovery,) = rejected.backfill_attempts
        assert recovery.assessment.content.method_result == "SUPPORT"
        assert recovery.assessment in completed.method_assessments
        assert recovery.assessment is not initial
        assert recovery.assessment.evidence_refs == initial.evidence_refs
        assert recovery.assessment.individual_support_refs
        assert (
            next(
                c for c in writer.matrix.cells if c.sample_stem == "Rejected"
            ).source_candidate_id
            is None
        )

        from xic_extractor.diagnostics.alignment_explanation import (
            read_cid_nl_explanation,
        )
        from xic_extractor.diagnostics.alignment_explanation_projection import (
            write_cid_nl_explanation,
        )
        from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

        matrix_path = tmp_path / "matrix.tsv"
        config = AlignmentConfig(anchor_min_seed_events=1)
        write_alignment_matrix_tsv(
            matrix_path,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        report = write_cid_nl_explanation(
            tmp_path / "explanation.sqlite",
            full_audit=True,
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix_path,
            alignment_config=config,
        )
        records = read_cid_nl_explanation(
            report, candidate_id=candidates["Rejected"][0].candidate_id
        )["records"]
        assert {r["method_result"] for r in records["method_assessments"]} == {
            "REJECT",
            "SUPPORT",
        }
        assert {
            r["artifact"]["evidence_artifact_id"] for r in records["method_assessments"]
        } == {
            initial.artifact.evidence_artifact_id,
            recovery.assessment.artifact.evidence_artifact_id,
        }
        assert records["exclusions"]
        assert (
            next(
                record["value"]
                for record in records["cell_decisions"]
                if record["canonical_sample_identity_id"]
                == rejected.job.sample.identity.sample_identity_id
            )
            == decision.matrix_value
        )

        from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
            backfill_untargeted_cid_nl_sample_subject,
        )

        # An immutable assessment from another sample/source cannot authorize
        # the new relation, even when its method and coordinates look identical.
        with pytest.raises(ValueError, match="sample|acquisition"):
            backfill_untargeted_cid_nl_sample_subject(
                processing_owners=batch.context.processing_owners,
                sample=batch.context.sample("S1"),
                method_subject=initial.content.method_subject,
                group_center_rt=8.0,
                group_center_mz=initial.content.method_subject.precursor_mz,
                support_policy=initial.content.evaluation_receipt.support_policy,
                peak_config=peak_config,
                raw=None,
                initial_assessment=initial,
                signal_processing_profile=TEST_PROFILE,
            )
        with pytest.raises(ValueError, match="support policy"):
            backfill_untargeted_cid_nl_sample_subject(
                processing_owners=batch.context.processing_owners,
                sample=rejected.job.sample,
                method_subject=initial.content.method_subject,
                group_center_rt=8.0,
                group_center_mz=initial.content.method_subject.precursor_mz,
                support_policy=replace(
                    initial.content.evaluation_receipt.support_policy,
                    precursor_tolerance_da=0.25,
                ),
                peak_config=peak_config,
                raw=None,
                initial_assessment=initial,
                signal_processing_profile=TEST_PROFILE,
            )
    elif outcome == "empty":
        (recovery,) = rejected.backfill_attempts
        assert recovery.assessment.content.method_result == "HOLD"
        assert not recovery.assessment.candidate_peaks
        assert not decision.write_matrix_value and decision.matrix_value is None
    else:
        assert rejected.backfill_attempts == ()
        assert not decision.write_matrix_value and decision.matrix_value is None
    assert recovery_calls == (
        [] if outcome in {"blank", "below-quorum"} else ["Rejected"]
    )
    assert sum(sum(raw.chromatogram_request_counts) for raw in opened[paths[-1]]) == (
        0 if outcome in {"blank", "below-quorum"} else 2
    )


@pytest.mark.parametrize("batched", [False, True], ids=["direct", "sample-batched"])
@pytest.mark.parametrize(
    "recovery_peaks",
    [(8.0, 12.0), (8.0,), ()],
    ids=["positive-positive", "positive-empty", "empty-empty"],
)
@pytest.mark.parametrize(
    "shared_support",
    ["separate", "shared", "below-quorum"],
    ids=["separate-support", "multi-hypothesis-support", "one-unauthorized-group"],
)
def test_runtime_recovers_every_authorized_rt_group(
    tmp_path, monkeypatch, batched, recovery_peaks, shared_support
):
    physical_records = {}
    support = {"S1": (8.0,), "S2": (8.0,), "S3": (12.0,), "S4": (12.0,)}
    if shared_support == "configured":
        support = {"S1": (8.0,), "S2": (8.0,), "S3": (8.12,)}
    if shared_support == "shared":
        support = {"S1": (8.0, 12.0), "S2": (8.0, 12.0)}
    if shared_support == "below-quorum":
        support.pop("S4")
    authorized_centers = (8.0,) if shared_support == "below-quorum" else (8.0, 12.0)
    centers = {**support, "Missing": recovery_peaks}
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in centers)
    settings, peak_config = (
        replace(_settings(), neutral_loss_profile=None, rt_max=12.6),
        _peak_config(tmp_path),
    )
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw(centers[path.stem])
        raw.rt = np.linspace(7.4, 12.7, 2121)
        raw.intensity = sum(
            (
                1000 * np.exp(-0.5 * ((raw.rt - center) / 0.035) ** 2)
                for center in centers[path.stem]
            ),
            np.zeros_like(raw.rt),
        )
        raw.scans = tuple(
            replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
            for scan in raw.scans
        )
        if shared_support == "configured" and path.stem in {"S1", "S2"}:
            raw.scans = tuple(
                replace(
                    raw.scans[0],
                    rt=rt,
                    scan_number=int(np.argmin(np.abs(raw.rt - rt))) + 1,
                )
                for rt in (7.99, 8.01)
            )
        raws[path] = raw
        current_candidates = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
        candidates[path.stem] = (
            current_candidates
            if path.stem in support
            else (
                tuple(_without_ms1_peak(candidate) for candidate in current_candidates)
                if recovery_peaks == (8.0, 12.0) and shared_support == "separate"
                else ()
            )
        )
    kwargs = dict(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, stem in enumerate(centers)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:multi-group-recovery",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in candidates.values()
            for c in values
        },
    )
    if shared_support == "configured":
        _assert_alignment_uses_configured_recovery_groups(
            tmp_path,
            monkeypatch,
            paths,
            raws,
            candidates,
            kwargs,
            settings,
            peak_config,
        )
        return
    if batched:
        batch = build_dense_partition(**kwargs, dll_dir=tmp_path)
        context, jobs = batch.context, batch.jobs
        assert len(batch.subject_batch.subjects) == 1
    else:
        subject = mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            neutral_loss_da=116.0474,
        )
        context = build_partition_context(**kwargs, method_subject=subject)
        jobs = tuple(
            UntargetedPartitionJob(
                processing_owners=context.processing_owners,
                sample=sample,
                method_subject=subject,
                candidates=candidates[sample.metadata.raw_stem],
                discovery_receipt=context.discovery_receipts_by_sample[
                    sample.metadata.raw_stem
                ],
                settings=settings,
                peak_config=peak_config,
                raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
                dll_dir=tmp_path,
                work_unit_key=sample.metadata.raw_stem,
                signal_processing_profile=context.signal_processing_profile,
                physical_records=tuple(
                    context.physical_records_by_candidate[c.candidate_id]
                    for c in (candidates[sample.metadata.raw_stem])
                ),
            )
            for sample in context.samples
        )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="multi-group-recovery",
    )
    canonical_calls = []
    canonical_bytes = partition_models._canonical_acquisition_bytes

    def count_canonical(acquisition):
        canonical_calls.append(id(acquisition))
        return canonical_bytes(acquisition)

    monkeypatch.setattr(
        partition_models, "_canonical_acquisition_bytes", count_canonical
    )
    result = execute_attempt(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "multi-group-recovery"
        ),
    )
    assert result.succeeded, tuple(
        s.failure_detail.message
        for s in result.finalization.coordinator.state.runtime.settlements
        if s.failure_detail is not None
    )
    assert len(canonical_calls) == len(set(canonical_calls))
    for sample_result in result.sample_results:
        acquisition = (
            sample_result.backfill_attempts[0].acquisition
            if sample_result.backfill_attempts
            else sample_result.adapter_result.acquisition
        )
        assert sample_result.result_ref == (
            partition_models.mint_sample_result_ref(
                job=sample_result.job,
                result=sample_result.adapter_result,
                backfill_input=sample_result.backfill_input,
                backfill_attempt=None,
                backfill_attempts=sample_result.backfill_attempts,
                acquisition=acquisition,
            )
        )
    missing = next(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    assert sum(
        sum(raw.chromatogram_request_counts) for raw in opened.get(paths[-1], ())
    ) == 2 * len(authorized_centers)
    assert len(missing.backfill_attempts) == len(authorized_centers)
    assert sorted(
        len(attempt.assessment.candidate_peaks) for attempt in missing.backfill_attempts
    ) == sorted(int(center in recovery_peaks) for center in authorized_centers)
    assert missing.result_ref.backfill_candidate_count == sum(
        center in recovery_peaks for center in authorized_centers
    )
    assert missing.result_ref.backfill_assessment_artifact_ids == tuple(
        attempt.assessment.artifact.evidence_artifact_id
        for attempt in missing.backfill_attempts
    )
    for center, attempt in zip(
        authorized_centers, missing.backfill_attempts, strict=True
    ):
        assert attempt.group_hypothesis_id is not None
        assert (
            missing.backfill_for_group(
                center, group_hypothesis_id=attempt.group_hypothesis_id
            )
            is attempt
        )
        receipt = attempt.assessment.content.evaluation_receipt
        assert (receipt.query_rt_min, receipt.query_rt_max) == (
            center - peak_config.nl_rt_anchor_half_window_min,
            center + peak_config.nl_rt_anchor_half_window_min,
        )
    if len(authorized_centers) > 1:
        with pytest.raises(ValueError, match="multiple backfill attempts"):
            _ = missing.backfill_attempt
    if batched:
        canonical_calls.clear()
        completed = complete_dense_partition(
            batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
        )
        assert len(canonical_calls) == len(set(canonical_calls))
        assert len(completed.initial_groups) == 2
        projection_context = partition_models._SampleResultContext(jobs)

        (child,) = completed.application_result.children
        scientific = child.scientific_core
        transition = scientific.partition_transition
        assert len(transition.predecessors) == len(transition.successors) == 2
        assert len(completed.initial_groups) == len(transition.predecessors)
        for predecessor in transition.predecessors:
            successors = tuple(
                successor
                for successor in transition.successors
                if set(predecessor.member_subjects).issubset(successor.member_subjects)
            )
            assert len(successors) == 1
        recovery_is_support = all(
            attempt.assessment.content.method_result == "SUPPORT"
            for attempt in missing.backfill_attempts
        )
        if recovery_peaks == (8.0, 12.0) and shared_support == "separate":
            assert recovery_is_support, tuple(
                attempt.assessment.content.method_result
                for attempt in missing.backfill_attempts
            )
        if (
            recovery_peaks == (8.0, 12.0)
            and shared_support == "separate"
            and recovery_is_support
        ):
            assert len(transition.admitted_members) == 2
            assert all(
                member.canonical_sample_identity_id
                == missing.job.sample.identity.sample_identity_id
                for member in transition.admitted_members
            )
        missing_cells = tuple(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Missing"
        )
        assert len(missing_cells) == 2
        if recovery_peaks == (8.0, 12.0) and recovery_is_support:
            assert all(cell.backfill_cell is not None for cell in missing_cells)
            assert all(
                cell.backfill_cell.content.method_result == "SUPPORT"
                for cell in missing_cells
            )
        if (
            recovery_peaks == (8.0, 12.0)
            and shared_support == "separate"
            and recovery_is_support
        ):
            initial_groups = tuple(completed.initial_groups)
            member_observations = {
                source.peak_observation.content.peak_observation_content_id: (
                    source.peak_observation
                )
                for cell in scientific.cell_observations
                for source in cell.predecessor_cells
                if source.peak_observation is not None
            }
            initial_transition = CrossSamplePartitionTransition(
                status="assessable",
                predecessors=transition.predecessors,
                successors=transition.predecessors,
                lineage=(),
                reason="unchanged_membership_no_lineage",
            )
            initial_result = CrossSampleGroupingResult(
                processing_owners=scientific.grouping_result.processing_owners,
                partition_transition=initial_transition,
                rt_frame=replace(
                    scientific.grouping_result.rt_frame,
                    injection_order={},
                    sample_cohorts={},
                ).with_warp(None),
            )
            bound_recoveries = {
                (
                    group.group_hypothesis_id,
                    missing.job.sample.identity.sample_identity_id,
                    missing.job.method_subject.method_subject_id,
                ): (
                    missing.job.sample,
                    missing.backfill_for_group(
                        next(
                            source.group_context.group.group_center_rt
                            for cell in missing_cells
                            for source in cell.predecessor_cells
                            if (
                                source.group_context.group.group_hypothesis_id
                                == group.group_hypothesis_id
                            )
                        ),
                        group_hypothesis_id=group.group_hypothesis_id,
                    ).assessment,
                )
                for group in transition.predecessors
            }
            forward = admit_cid_nl_group_backfills(
                result=initial_result,
                groups=initial_groups,
                backfills=bound_recoveries,
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                member_observations=member_observations,
            )
            reverse = admit_cid_nl_group_backfills(
                result=initial_result,
                groups=tuple(reversed(initial_groups)),
                backfills=dict(reversed(tuple(bound_recoveries.items()))),
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                member_observations=member_observations,
            )

            def normalized_admission(admission):
                grouping, groups, successor_ids, hypotheses, _ = admission
                return (
                    tuple(
                        sorted(
                            (
                                group.group_hypothesis_id,
                                tuple(
                                    sorted(
                                        member.member_subject_id
                                        for member in group.member_subjects
                                    )
                                ),
                            )
                            for group in groups
                        )
                    ),
                    tuple(sorted(successor_ids.items())),
                    tuple(
                        sorted(
                            (
                                key,
                                tuple(
                                    sorted(
                                        hypothesis.hypothesis_id
                                        for hypothesis in values
                                    )
                                ),
                            )
                            for key, values in hypotheses.items()
                        )
                    ),
                    tuple(
                        sorted(
                            member.member_subject_id
                            for member in grouping.partition_transition.admitted_members
                        )
                    ),
                )

            assert normalized_admission(forward) == normalized_admission(reverse)

            first_group, second_group = initial_groups
            shared_recovery = next(iter(bound_recoveries.values()))
            ambiguous = {
                (
                    first_group.group_hypothesis_id,
                    missing.job.sample.identity.sample_identity_id,
                    missing.job.method_subject.method_subject_id,
                ): shared_recovery,
                (
                    second_group.group_hypothesis_id,
                    missing.job.sample.identity.sample_identity_id,
                    missing.job.method_subject.method_subject_id,
                ): shared_recovery,
            }
            rejected = admit_cid_nl_group_backfills(
                result=initial_result,
                groups=initial_groups,
                backfills=ambiguous,
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                member_observations=member_observations,
            )
            rejected_reverse = admit_cid_nl_group_backfills(
                result=initial_result,
                groups=tuple(reversed(initial_groups)),
                backfills=dict(reversed(tuple(ambiguous.items()))),
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                member_observations=member_observations,
            )
            assert len(rejected[0].partition_transition.admitted_members) == 1
            assert normalized_admission(rejected) == normalized_admission(
                rejected_reverse
            )
            assert (
                sum(
                    group_id != successor_id
                    for group_id, successor_id in rejected[2].items()
                )
                == 1
            )
        initial_groups_by_id = {
            group.group_hypothesis_id: group for group in completed.initial_groups
        }
        has_initial_evidence = bool(missing.job.candidates)
        allocated = []
        for cell in missing_cells:
            predecessor_group_ids = {
                association.position.group_identity.group_hypothesis_id
                for association in cell.position_associations
            }
            assert len(predecessor_group_ids) <= 1
            source = cell.predecessor_cells[0] if cell.predecessor_cells else None
            if has_initial_evidence:
                assert source is not None
                assert len(predecessor_group_ids) == 1
                predecessor_group_id = next(iter(predecessor_group_ids))
                initial_group = initial_groups_by_id[predecessor_group_id]
                assert source.group_context.group is initial_group
                center = initial_group.group_center_rt
            else:
                center = (
                    initial_groups_by_id[
                        next(iter(predecessor_group_ids))
                    ].group_center_rt
                    if predecessor_group_ids
                    else None
                )
            recovery = (
                missing.backfill_for_group(
                    center,
                    group_hypothesis_id=next(iter(predecessor_group_ids)),
                )
                if center is not None
                else None
            )
            if recovery is None:
                assert cell.evidence_states == ("not_searched",)
                assert cell.backfill_cell is None
                if not has_initial_evidence:
                    assert cell.predecessor_cells == ()
                if source is not None:
                    assert (
                        source.method_assessment
                        is missing.adapter_result.assessment_input.assessment
                    )
                    assert source.peak_observation is None
                    assert source.peak_hypothesis is None
                assert cell.integrations == ()
            elif not recovery.assessment.candidate_peaks:
                assert cell.evidence_states == ("unavailable",)
                if source is not None:
                    assert source.method_assessment is recovery.assessment
                    assert source.peak_observation is None
                    assert source.peak_hypothesis is None
                    recovery_context = source.group_context
                else:
                    assert cell.backfill_cell is not None
                    assert cell.backfill_cell.method_assessment is recovery.assessment
                    recovery_context = cell.backfill_cell.group_context
                completion = project_completed_backfill(
                    missing, group_context=recovery_context
                )
                assert (
                    projection_context.project_completed_backfill(
                        missing, group_context=recovery_context
                    )
                    == completion
                )
                with pytest.raises(ValueError, match="exact runtime batch job"):
                    projection_context.project_completed_backfill(
                        replace(missing, job=replace(missing.job)),
                        group_context=recovery_context,
                    )
                changed_acquisition = deepcopy(recovery.acquisition)
                changed_trace = changed_acquisition.traces[0].trace
                changed_trace.intensity.flags.writeable = True
                changed_trace.intensity[0] += 1.0
                changed_recoveries = tuple(
                    replace(attempt, acquisition=changed_acquisition)
                    for attempt in missing.backfill_attempts
                )
                with pytest.raises(ValueError, match="sealed runtime result"):
                    project_completed_backfill(
                        replace(missing, backfill_attempts=changed_recoveries),
                        group_context=recovery_context,
                    )
                assert completion.backfill_assessment is recovery.assessment
                if len(missing.backfill_attempts) > 1:
                    with pytest.raises(ValueError, match="sealed runtime result"):
                        project_completed_backfill(
                            replace(missing, backfill_attempts=(recovery,)),
                            group_context=recovery_context,
                        )
                    with pytest.raises(ValueError):
                        replace(
                            missing.result_ref,
                            backfill_assessment_artifact_ids=(
                                recovery.assessment.artifact.evidence_artifact_id,
                            ),
                        )
            else:
                if recovery_is_support:
                    if source is not None:
                        assert source.method_assessment is (
                            missing.adapter_result.assessment_input.assessment
                        )
                    assert cell.backfill_cell is not None
                    assert cell.backfill_cell.method_assessment is recovery.assessment
                    allocated.append(
                        cell.backfill_cell.peak_observation.content.peak_observation_content_id
                    )
                elif source is not None:
                    assert source.method_assessment is recovery.assessment
                    allocated.append(
                        source.peak_observation.content.peak_observation_content_id
                    )
                else:
                    assert cell.backfill_cell is not None
                    assert cell.backfill_cell.method_assessment is recovery.assessment
                    allocated.append(
                        cell.backfill_cell.peak_observation.content.peak_observation_content_id
                    )
        assert len(set(allocated)) == len(allocated)
        expected_assessments = {
            item.adapter_result.assessment_input.assessment
            for item in result.sample_results
            if item.job.candidates
        }
        expected_assessments.update(
            attempt.assessment
            for item in result.sample_results
            for attempt in item.backfill_attempts
        )
        assert set(completed.method_assessments) == expected_assessments

        from xic_extractor.alignment.untargeted_finalized_matrix import (
            project_completed_untarget_batch_writer_inputs,
        )

        writer = project_completed_untarget_batch_writer_inputs(
            completed.application_result
        )
        decisions = tuple(
            decision
            for decision in writer.production_decisions.cells.values()
            if decision.sample_stem == "Missing"
        )
        if recovery_is_support:
            assert all(
                decision.write_matrix_value and decision.matrix_value is not None
                for decision in decisions
            )
            assert all(decision.blank_reason == "" for decision in decisions)
        else:
            # One group-bounded MS1 peak without CID-NL evidence fills the
            # Matrix as MS1-only Backfill; it never counts as a detection.
            ms1_only_cells = tuple(
                cell
                for cell in missing_cells
                if cell.evidence_states == ("ms1_only_backfill_observation",)
            )
            assert len(ms1_only_cells) == sum(
                center in recovery_peaks for center in authorized_centers
            )
            for cell in ms1_only_cells:
                (source,) = cell.predecessor_cells
                assessment = source.method_assessment
                assert assessment.content.reason_codes == (
                    "no_unique_qualifying_relation",
                )
                assert len(assessment.candidate_peaks) == 1
                assert assessment.evidence_refs == ()
                assert cell.integrations == (
                    assessment.candidate_peaks[0].content.integration,
                )
            written = tuple(
                decision for decision in decisions if decision.write_matrix_value
            )
            assert len(written) == len(ms1_only_cells)
            assert all(
                decision.matrix_value is not None
                and decision.raw_status == "backfilled"
                and decision.production_status == "accepted_backfill"
                and decision.backfill_tier == "accepted_backfill"
                and decision.blank_reason == ""
                for decision in written
            )
            assert sorted(
                decision.blank_reason
                for decision in decisions
                if not decision.write_matrix_value
            ) == sorted(
                cell.evidence_state
                for cell in missing_cells
                if cell not in ms1_only_cells
            )
            assert not any(decision.raw_status == "detected" for decision in decisions)
        if shared_support == "shared" and recovery_peaks == (8.0, 12.0):
            from xic_extractor.alignment.grouping_bootstrap import (
                bootstrap_cid_nl_initial_population,
            )

            inputs = tuple(
                (item.job.sample, assessment)
                for item in result.sample_results
                for assessment in item.effective_assessments
            )
            options = dict(
                processing_owners=context.processing_owners,
                rt_frame=SampleRtFrame(),
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                retain_observation_only_candidates=True,
            )
            baseline = bootstrap_cid_nl_initial_population(
                sample_assessments=inputs, **options
            )
            replayed = bootstrap_cid_nl_initial_population(
                sample_assessments=tuple(pair for pair in inputs for _ in range(2)),
                **options,
            )
            assert replayed[:2] == baseline[:2]


@pytest.mark.parametrize("mz_separation_ppm", [0.0, 12.0])
def test_shared_physical_recovery_claim_is_not_assigned_to_two_groups(
    tmp_path, monkeypatch, mz_separation_ppm
):
    physical_records = {}
    """A shared physical peak must not be assigned to both groups."""

    centers = {"S1": (8.0, 8.12), "S2": (8.0, 8.12), "Missing": (8.06,)}
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in centers)
    # Keep the two synthetic discovery peaks as separate fixture groups; this
    # changes only this test input, never the production/default threshold.
    settings, peak_config = (
        replace(
            _settings(), neutral_loss_profile=None, rt_max=8.6, seed_rt_gap_min=0.1
        ),
        _peak_config(tmp_path),
    )
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw(centers[path.stem])
        raw.rt = np.linspace(7.4, 8.7, 521)
        raw.intensity = sum(
            (
                1000 * np.exp(-0.5 * ((raw.rt - center) / 0.018) ** 2)
                for center in centers[path.stem]
            ),
            np.zeros_like(raw.rt),
        )
        raw.scans = tuple(
            replace(
                scan,
                scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1,
                precursor_mz=scan.precursor_mz
                + 500.0
                * mz_separation_ppm
                * 1e-6
                * (0.5 if path.stem == "Missing" else float(scan.rt > 8.06)),
                masses=scan.masses
                + 500.0
                * mz_separation_ppm
                * 1e-6
                * (0.5 if path.stem == "Missing" else float(scan.rt > 8.06)),
            )
            for scan in raw.scans
        )
        raws[path] = raw
        current = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
        candidates[path.stem] = (
            current
            if path.stem != "Missing"
            else tuple(_without_ms1_peak(candidate) for candidate in current)
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, stem in enumerate(centers)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:shared-recovery-claim",
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="shared-recovery-claim",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "shared-recovery-claim"
        ),
    )
    assert result.succeeded
    missing = next(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    completed = complete_dense_partition(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    assert len(missing.backfill_attempts) == 2, (
        missing.result_ref.backfill_candidate_count,
        len(completed.initial_groups),
        tuple(group.group_center_rt for group in completed.initial_groups),
    )
    assert all(
        attempt.assessment.content.method_result == "SUPPORT"
        for attempt in missing.backfill_attempts
    )
    physical_ids = tuple(
        attempt.assessment.candidate_peaks[0].content.peak_observation_content_id
        for attempt in missing.backfill_attempts
    )
    assert len(set(physical_ids)) == (1 if mz_separation_ppm == 0.0 else 2)

    (child,) = completed.application_result.children
    transition = child.scientific_core.partition_transition
    assert len(transition.predecessors) == len(transition.successors) == 2
    assert transition.admitted_members == ()
    missing_cells = tuple(
        cell
        for cell in child.scientific_core.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Missing"
    )
    assert len(missing_cells) == 2
    assert all(cell.backfill_cell is not None for cell in missing_cells)
    assert all(cell.evidence_states == ("unresolved",) for cell in missing_cells)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    missing_decisions = tuple(
        decision
        for decision in writer.production_decisions.cells.values()
        if decision.sample_stem == "Missing"
    )
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in missing_decisions
    )


@pytest.mark.parametrize("mz_separation_ppm", [0.0, 12.0, 21.1])
@pytest.mark.parametrize("reverse", [False, True])
def test_shared_ms1_only_backfill_peak_is_not_written_to_two_rows(
    tmp_path, monkeypatch, mz_separation_ppm, reverse
):
    physical_records = {}
    """Overlapping extraction windows cannot supply one peak to two rows."""

    ms1_centers = {"S1": (8.0, 8.12), "S2": (8.0, 8.12), "Missing": (8.06,)}
    nl_centers = {"S1": (8.0, 8.12), "S2": (8.0, 8.12), "Missing": ()}
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in ms1_centers)
    if reverse:
        paths = tuple(reversed(paths))
    first_mz = 500.0
    second_mz = first_mz * (1 + mz_separation_ppm * 1e-6)
    physical_mz = (first_mz + second_mz) / 2

    class MassAwareRaw(_FakeRaw):
        def __init__(self, centers):
            super().__init__(centers)
            self.scans = tuple(
                replace(
                    scan,
                    precursor_mz=second_mz if scan.rt > 8.06 else first_mz,
                    masses=scan.masses
                    + (second_mz - first_mz if scan.rt > 8.06 else 0.0),
                )
                for scan in self.scans
            )

        def _trace(self, request):
            trace = super()._trace(request)
            # The fixture contains one ion, not one independent signal per query.
            if abs(physical_mz - request.mz) > request.mz * request.ppm_tol * 1e-6:
                return replace(trace, intensity=np.zeros_like(trace.intensity))
            return trace

    settings, peak_config = (
        replace(
            _settings(), neutral_loss_profile=None, rt_max=8.6, seed_rt_gap_min=0.1
        ),
        _peak_config(tmp_path),
    )
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = MassAwareRaw(nl_centers[path.stem])
        raw.rt = np.linspace(7.4, 8.7, 521)
        raw.intensity = sum(
            (
                1000 * np.exp(-0.5 * ((raw.rt - center) / 0.018) ** 2)
                for center in ms1_centers[path.stem]
            ),
            np.zeros_like(raw.rt),
        )
        raw.scans = tuple(
            replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
            for scan in raw.scans
        )
        raws[path] = raw
        candidates[path.stem] = (
            _current_candidates(
                raw,
                raw_file=path,
                settings=settings,
                physical_records=physical_records,
            )
            if path.stem != "Missing"
            else ()
        )
        if reverse:
            candidates[path.stem] = tuple(reversed(candidates[path.stem]))
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, stem in enumerate(ms1_centers)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:shared-ms1-only-backfill",
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="shared-ms1-only-backfill",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "shared-ms1-only-backfill"
        ),
    )
    assert result.succeeded
    missing_attempts = tuple(
        attempt
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
        for attempt in item.backfill_attempts
    )
    assert len(missing_attempts) == 2
    assert all(
        is_ms1_only_cid_nl_backfill_assessment(attempt.assessment)
        for attempt in missing_attempts
    )
    first, second = (
        attempt.assessment.candidate_peaks[0] for attempt in missing_attempts
    )
    assert first.content.rt == second.content.rt
    assert first.content.intensity == second.content.intensity
    assert first.content.integration == second.content.integration
    assert first.generation_provenance.acquisition_source_id == (
        second.generation_provenance.acquisition_source_id
    )
    assert len(
        {
            first.content.peak_observation_content_id,
            second.content.peak_observation_content_id,
        }
    ) == (1 if mz_separation_ppm == 0.0 else 2)

    completed = complete_dense_partition(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    (child,) = completed.application_result.children
    missing_cells = tuple(
        cell
        for cell in child.scientific_core.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Missing"
    )
    assert len(missing_cells) == 2
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    missing_decisions = tuple(
        decision
        for decision in writer.production_decisions.cells.values()
        if decision.sample_stem == "Missing"
    )
    assert len(missing_decisions) == 2
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in missing_decisions
    )
    assert all(
        row.detected_count == 2 for row in writer.production_decisions.rows.values()
    )
    assert all(cell.evidence_states == ("unresolved",) for cell in missing_cells)


@pytest.mark.parametrize(
    ("missing_peaks", "expected_state"),
    [
        ((8.0,), "ms1_only_backfill_observation"),
        ((7.8, 8.2), "unavailable"),
    ],
    ids=["one-ms1-peak", "two-ms1-peaks"],
)
def test_ms1_only_backfill_fills_only_one_unique_group_window_peak(
    tmp_path, monkeypatch, missing_peaks, expected_state
):
    physical_records = {}
    centers = {"S1": (8.0,), "S2": (8.0,), "Missing": missing_peaks}
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in centers)
    settings, peak_config = (
        replace(_settings(), neutral_loss_profile=None, rt_max=8.6),
        _peak_config(tmp_path),
    )
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw(centers[path.stem])
        raw.rt = np.linspace(7.4, 8.7, 521)
        raw.intensity = sum(
            (
                1000 * np.exp(-0.5 * ((raw.rt - center) / 0.035) ** 2)
                for center in centers[path.stem]
            ),
            np.zeros_like(raw.rt),
        )
        raw.scans = tuple(
            replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
            for scan in raw.scans
        )
        raws[path] = raw
        candidates[path.stem] = (
            _current_candidates(
                raw,
                raw_file=path,
                settings=settings,
                physical_records=physical_records,
            )
            if path.stem != "Missing"
            else ()
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, stem in enumerate(centers)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:ms1-only-backfill",
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="ms1-only-backfill",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "ms1-only-backfill"
        ),
    )
    assert result.succeeded
    missing = next(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    (attempt,) = missing.backfill_attempts
    if len(missing_peaks) == 1:
        assert attempt.assessment.content.reason_codes == (
            "no_unique_qualifying_relation",
        )
        assert len(attempt.assessment.candidate_peaks) == 1
    else:
        # No physical peak occupies the predicted position between the two peaks.
        assert attempt.assessment.content.reason_codes == (
            "insufficient_method_evidence",
        )
        assert attempt.assessment.candidate_peaks == ()
    completed = complete_dense_partition(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    (child,) = completed.application_result.children
    (missing_cell,) = tuple(
        cell
        for cell in child.scientific_core.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Missing"
    )
    assert missing_cell.evidence_states == (expected_state,)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    (decision,) = tuple(
        decision
        for decision in writer.production_decisions.cells.values()
        if decision.sample_stem == "Missing"
    )
    if expected_state == "unavailable":
        assert not decision.write_matrix_value
        assert decision.matrix_value is None
    else:
        assert decision.write_matrix_value
        assert decision.raw_status == "backfilled"
        assert decision.production_status == "accepted_backfill"
        from tools.diagnostics.output_population_census import main as population_main
        from xic_extractor.output.alignment.pipeline_outputs import (
            output_paths,
            write_outputs_atomic,
        )

        writer_dir = tmp_path / "writer"
        writer_dir.mkdir()
        write_outputs_atomic(
            output_paths(
                writer_dir,
                output_level="validation-minimal",
                emit_alignment_cells=True,
                emit_alignment_status_matrix=False,
            ),
            writer.matrix,
            metadata={},
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            production_decisions=writer.production_decisions,
        )
        required = (
            "alignment_backfill_cell_evidence.tsv",
            "alignment_review.tsv:has_anchor",
            "alignment_review.tsv:neutral_loss_tag",
            "alignment_review.tsv:family_product_mz",
        )
        requirements = [
            part for item in required for part in ("--require-populated", item)
        ]
        backfill_tsv = writer_dir / "alignment_backfill_cell_evidence.tsv"
        populated = backfill_tsv.read_text(encoding="utf-8")
        backfill_tsv.write_text(populated.splitlines()[0] + "\n", encoding="utf-8")
        assert population_main([str(writer_dir), *requirements]) == 1
        backfill_tsv.write_text(populated, encoding="utf-8")
        assert population_main([str(writer_dir), *requirements]) == 0
    (row,) = writer.production_decisions.rows.values()
    assert row.detected_count == 2
    # The finalized row carries the group's own tag, fragment and anchor.
    (cluster,) = writer.matrix.clusters
    assert cluster.has_anchor
    assert cluster.neutral_loss_tag == "cid_nl:116.0474"
    assert cluster.family_product_mz == pytest.approx(383.9526, abs=1e-3)
    assert cluster.family_observed_neutral_loss_da == pytest.approx(116.0474, abs=1e-3)


def test_backfill_reaches_a_sample_whose_support_belongs_to_another_group(
    tmp_path, monkeypatch
):
    # Missing has CID-NL support only for its own peak at 9.6 min; its peak in
    # the S1/S2 group at 8.0 min carries no MS2. Support elsewhere must not
    # stop the group-bounded revisit.
    physical_records = {}
    ms1_centers = {"S1": (8.0,), "S2": (8.0,), "Missing": (8.0, 9.6)}
    nl_centers = {"S1": (8.0,), "S2": (8.0,), "Missing": (9.6,)}
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in ms1_centers)
    settings, peak_config = (
        replace(_settings(), neutral_loss_profile=None, rt_max=10.1),
        _peak_config(tmp_path),
    )
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw(nl_centers[path.stem])
        raw.rt = np.linspace(7.4, 10.2, 1121)
        raw.intensity = sum(
            (
                1000 * np.exp(-0.5 * ((raw.rt - center) / 0.035) ** 2)
                for center in ms1_centers[path.stem]
            ),
            np.zeros_like(raw.rt),
        )
        raw.scans = tuple(
            replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
            for scan in raw.scans
        )
        raws[path] = raw
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
                sample_name=stem,
                raw_stem=stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, stem in enumerate(ms1_centers)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        sample_identity_namespace="study:support-elsewhere",
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="support-elsewhere",
    )
    result = execute_attempt(
        runtime,
        batch.jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "support-elsewhere"
        ),
    )
    assert result.succeeded
    missing = next(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    assert missing.adapter_result.assessment_input.assessment.content.method_result == (
        "SUPPORT"
    )
    completed = complete_dense_partition(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    sample_stems = writer.matrix.sample_order
    group_rows = {
        round(cluster.family_center_rt, 1): cluster
        for cluster in writer.matrix.clusters
    }
    group_8 = group_rows[8.0]
    (missing_decision,) = tuple(
        decision
        for decision in writer.production_decisions.cells.values()
        if decision.feature_family_id == group_8.feature_family_id
        and decision.sample_stem == "Missing"
    )
    assert "Missing" in sample_stems
    assert missing_decision.write_matrix_value
    assert missing_decision.raw_status == "backfilled"


def test_alignment_uses_configured_recovery_groups(tmp_path, monkeypatch):
    test_runtime_recovers_every_authorized_rt_group(
        tmp_path, monkeypatch, True, (), "configured"
    )


def _assert_alignment_uses_configured_recovery_groups(
    tmp_path, monkeypatch, paths, raws, candidates, kwargs, settings, peak_config
):
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        project_completed_untarget_batch_writer_inputs,
    )
    from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
    from xic_extractor.untargeted_execution import execution as bounded_runtime
    from xic_extractor.untargeted_execution.alignment import (
        complete_untargeted_alignment,
    )

    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime_results = []
    execute = bounded_runtime.execute_partition

    def retain_runtime(*args, **options):
        result = execute(*args, **options)
        assert result.succeeded
        if options.get("initial_only"):
            return result
        missing = next(
            item
            for item in result.sample_results
            if item.job.sample.metadata.raw_stem == "Missing"
        )
        (recovery,) = missing.backfill_attempts
        receipt = recovery.assessment.content.evaluation_receipt
        half_window = peak_config.nl_rt_anchor_half_window_min
        assert (receipt.query_rt_min, receipt.query_rt_max) == (
            8.0 - half_window,
            8.0 + half_window,
        )
        runtime_results.append(result)
        return result

    monkeypatch.setattr(bounded_runtime, "execute_partition", retain_runtime)
    monkeypatch.setattr(
        "xic_extractor.untargeted_execution.alignment._load_physical_replay_inputs",
        lambda **options: (
            options["candidates_by_sample"],
            kwargs["physical_records_by_candidate"],
            None,
        ),
    )
    completed = complete_untargeted_alignment(
        signal_processing_profile=TEST_PROFILE,
        discovery_batch=DiscoveryBatchInput(
            sample_order=tuple(candidates),
            candidate_csvs={
                stem: tmp_path / f"{stem}.owner-candidates.csv" for stem in candidates
            },
            raw_files={path.stem: path for path in paths},
            review_csvs={stem: None for stem in candidates},
            candidate_receipts=kwargs["discovery_receipts_by_sample"],
        ),
        candidates_by_sample=candidates,
        sample_metadata=kwargs["sample_metadata"],
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "alignment",
        settings=settings,
        peak_config=peak_config,
        # Keep the 7.2-second S3 offset outside the configured RT group;
        # default continuity tolerances would legitimately include S3.
        alignment_config=AlignmentConfig(
            anchor_min_seed_events=2, preferred_rt_sec=2.0, max_rt_sec=5.0
        ),
    )
    assert sorted(len(group.owners) for group in completed.initial_groups) == [1, 2]
    missing = next(
        item
        for item in runtime_results[0].sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    (recovery,) = missing.backfill_attempts
    assert recovery.assessment.candidate_peaks == ()
    (child,) = completed.application_result.children
    missing_cells = tuple(
        cell
        for cell in child.scientific_core.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Missing"
    )
    initial_groups_by_id = {
        group.group_hypothesis_id: group for group in completed.initial_groups
    }

    def predecessor_center(cell):
        predecessor_group_ids = {
            association.position.group_identity.group_hypothesis_id
            for association in cell.position_associations
        }
        assert len(predecessor_group_ids) == 1
        return initial_groups_by_id[next(iter(predecessor_group_ids))].group_center_rt

    recovered = next(cell for cell in missing_cells if predecessor_center(cell) == 8.0)
    no_input = next(cell for cell in missing_cells if predecessor_center(cell) != 8.0)
    # The partition store restores the empty recovery assessment from the
    # completed runtime and leaves the other no-input position unsearched.
    assert all(
        cell.peak_observation is None and cell.peak_hypothesis is None
        for cell in recovered.predecessor_cells
    )
    if recovered.backfill_cell is not None:
        assert recovered.backfill_cell.method_assessment is recovery.assessment
    else:
        assert len(recovered.predecessor_cells) == 1
        assert recovered.predecessor_cells[0].method_assessment == recovery.assessment
        assert recovered.predecessor_cells[0].peak_observation is None
        assert recovered.predecessor_cells[0].peak_hypothesis is None
    assert recovered.evidence_states == ("unavailable",)
    assert no_input.predecessor_cells == ()
    assert no_input.backfill_cell is None
    assert no_input.evidence_states == ("not_searched",)
    assert no_input.integrations == ()
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in writer.production_decisions.cells.values()
        if decision.sample_stem == "Missing"
    )
    assert sum(sum(raw.chromatogram_request_counts) for raw in opened[paths[-1]]) == 2
