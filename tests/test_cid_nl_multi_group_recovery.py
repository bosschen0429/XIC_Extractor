from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.test_untargeted_cid_nl_real_data_adapter import _without_ms1_peak
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import start_runtime_attempt
from xic_extractor.extraction import (
    untargeted_cid_nl_real_data_runtime as runtime_owner,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
    project_untargeted_cid_nl_completed_recovery,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.sample_metadata import SampleMetadata


@pytest.mark.parametrize(
    "outcome", ["support", "empty", "below-quorum", "blank", "failed"]
)
def test_rejected_initial_evidence_gets_one_independent_bounded_assessment(
    tmp_path, monkeypatch, outcome
):
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
            raw, raw_file=path, settings=settings, peak_config=peak_config
        )
        if path.stem == "Rejected":
            candidates[path.stem] = tuple(
                _without_ms1_peak(candidate) for candidate in candidates[path.stem]
            )
            if outcome == "empty":
                raw.intensity[:] = 0.0
    batch = build_untargeted_cid_nl_production_batch(
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
    )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    recovery_calls = []
    acquire = runtime_owner.acquire_untargeted_cid_nl_recovery_trace_batch

    def acquire_recovery(**kwargs):
        recovery_calls.append(kwargs["sample"].metadata.raw_stem)
        if outcome == "failed":
            raise OSError("recovery acquisition failed")
        return acquire(**kwargs)

    monkeypatch.setattr(
        runtime_owner,
        "acquire_untargeted_cid_nl_recovery_trace_batch",
        acquire_recovery,
    )
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation="rejected-bounded-recovery:v1",
    )
    result = execute_untargeted_cid_nl_real_data_runtime(
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
    completed = compose_untargeted_cid_nl_production_result(
        batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
    )
    assert initial in completed.method_assessments
    assert any(
        exclusion.candidate_id == candidates["Rejected"][0].candidate_id
        for exclusion in completed.application_result.excluded_candidates
    )
    writer = project_completed_untarget_batch_writer_inputs(completed)
    (decision,) = tuple(
        d
        for d in writer.production_decisions.cells.values()
        if d.sample_stem == "Rejected"
    )
    if outcome == "support":
        assert decision.write_matrix_value and decision.matrix_value > 0
        (recovery,) = rejected.recovery_attempts
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

        from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
        from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
        from xic_extractor.diagnostics.cid_nl_explanation_projection import (
            write_cid_nl_explanation,
        )

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

        from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
            recover_untargeted_cid_nl_sample_subject,
        )

        # An immutable assessment from another sample/source cannot authorize
        # the new relation, even when its method and coordinates look identical.
        with pytest.raises(ValueError, match="sample|acquisition"):
            recover_untargeted_cid_nl_sample_subject(
                processing_owners=batch.context.processing_owners,
                sample=batch.context.sample("S1"),
                method_subject=initial.content.method_subject,
                group_center_rt=8.0,
                support_policy=initial.content.evaluation_receipt.support_policy,
                peak_config=peak_config,
                raw=None,
                initial_assessment=initial,
            )
        with pytest.raises(ValueError, match="support policy"):
            recover_untargeted_cid_nl_sample_subject(
                processing_owners=batch.context.processing_owners,
                sample=rejected.job.sample,
                method_subject=initial.content.method_subject,
                group_center_rt=8.0,
                support_policy=replace(
                    initial.content.evaluation_receipt.support_policy,
                    precursor_tolerance_da=0.25,
                ),
                peak_config=peak_config,
                raw=None,
                initial_assessment=initial,
            )
    elif outcome == "empty":
        (recovery,) = rejected.recovery_attempts
        assert recovery.assessment.content.method_result == "HOLD"
        assert not recovery.assessment.candidate_peaks
        assert not decision.write_matrix_value and decision.matrix_value is None
    else:
        assert rejected.recovery_attempts == ()
        assert not decision.write_matrix_value and decision.matrix_value is None
    assert recovery_calls == (
        [] if outcome in {"blank", "below-quorum"} else ["Rejected"]
    )
    assert sum(sum(raw.chromatogram_request_counts) for raw in opened[paths[-1]]) == (
        0 if outcome in {"blank", "below-quorum"} else 1
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
        candidates[path.stem] = (
            _current_candidates(
                raw, raw_file=path, settings=settings, peak_config=peak_config
            )
            if path.stem in support
            else ()
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
        batch = build_untargeted_cid_nl_production_batch(**kwargs, dll_dir=tmp_path)
        context, jobs = batch.context, batch.jobs
        assert len(batch.subject_batch.subjects) == 1
    else:
        subject = mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            neutral_loss_da=116.0474,
        )
        context = build_untargeted_cid_nl_real_data_context(
            **kwargs, method_subject=subject
        )
        jobs = tuple(
            UntargetedCidNlRealDataRuntimeJob(
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
            )
            for sample in context.samples
        )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="multi-group-recovery:v1",
    )
    canonical_calls = []
    canonical_bytes = runtime_owner._canonical_acquisition_bytes

    def count_canonical(acquisition):
        canonical_calls.append(id(acquisition))
        return canonical_bytes(acquisition)

    monkeypatch.setattr(runtime_owner, "_canonical_acquisition_bytes", count_canonical)
    result = execute_untargeted_cid_nl_real_data_runtime(
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
            sample_result.recovery_attempts[0].acquisition
            if sample_result.recovery_attempts
            else sample_result.adapter_result.acquisition
        )
        assert sample_result.result_ref == (
            runtime_owner.mint_untargeted_cid_nl_sample_result_ref(
                job=sample_result.job,
                result=sample_result.adapter_result,
                recovery_input=sample_result.recovery_input,
                recovery_attempt=None,
                recovery_attempts=sample_result.recovery_attempts,
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
    ) == len(authorized_centers)
    assert len(missing.recovery_attempts) == len(authorized_centers)
    assert sorted(
        len(attempt.assessment.candidate_peaks) for attempt in missing.recovery_attempts
    ) == sorted(int(center in recovery_peaks) for center in authorized_centers)
    assert missing.result_ref.recovery_candidate_count == sum(
        center in recovery_peaks for center in authorized_centers
    )
    assert missing.result_ref.recovery_assessment_artifact_ids == tuple(
        attempt.assessment.artifact.evidence_artifact_id
        for attempt in missing.recovery_attempts
    )
    for center, attempt in zip(
        authorized_centers, missing.recovery_attempts, strict=True
    ):
        assert missing.recovery_for_group(center) is attempt
        receipt = attempt.assessment.content.evaluation_receipt
        assert (receipt.query_rt_min, receipt.query_rt_max) == (
            center - peak_config.nl_rt_anchor_half_window_min,
            center + peak_config.nl_rt_anchor_half_window_min,
        )
    if len(authorized_centers) > 1:
        with pytest.raises(ValueError, match="multiple recovery attempts"):
            _ = missing.recovery_attempt
    if batched:
        canonical_calls.clear()
        completed = compose_untargeted_cid_nl_production_result(
            batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
        )
        assert len(canonical_calls) == len(set(canonical_calls))
        assert len(completed.initial_groups) == 2
        projection_context = runtime_owner._RuntimeResultContext(jobs)

        (child,) = completed.application_result.children
        scientific = child.scientific_core
        missing_cells = tuple(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Missing"
        )
        assert len(missing_cells) == 2
        allocated = []
        for cell in missing_cells:
            (source,) = cell.predecessor_cells
            center = source.group_context.group.group_center_rt
            recovery = missing.recovery_for_group(center)
            if recovery is None:
                assert cell.evidence_states == ("not_searched",)
                assert (
                    source.method_assessment
                    is missing.adapter_result.assessment_input.assessment
                )
            elif not recovery.assessment.candidate_peaks:
                assert cell.evidence_states == ("unavailable",)
                assert source.method_assessment is recovery.assessment
                completion = project_untargeted_cid_nl_completed_recovery(
                    missing, group_context=source.group_context
                )
                assert (
                    projection_context.project_completed_recovery(
                        missing, group_context=source.group_context
                    )
                    == completion
                )
                with pytest.raises(ValueError, match="exact runtime batch job"):
                    projection_context.project_completed_recovery(
                        replace(missing, job=replace(missing.job)),
                        group_context=source.group_context,
                    )
                changed_acquisition = deepcopy(recovery.acquisition)
                changed_trace = changed_acquisition.traces[0].trace
                changed_trace.intensity.flags.writeable = True
                changed_trace.intensity[0] += 1.0
                changed_recoveries = tuple(
                    replace(attempt, acquisition=changed_acquisition)
                    for attempt in missing.recovery_attempts
                )
                with pytest.raises(ValueError, match="sealed runtime result"):
                    project_untargeted_cid_nl_completed_recovery(
                        replace(missing, recovery_attempts=changed_recoveries),
                        group_context=source.group_context,
                    )
                assert completion.recovery_assessment is recovery.assessment
                if len(missing.recovery_attempts) > 1:
                    with pytest.raises(ValueError, match="sealed runtime result"):
                        project_untargeted_cid_nl_completed_recovery(
                            replace(missing, recovery_attempts=(recovery,)),
                            group_context=source.group_context,
                        )
                    with pytest.raises(ValueError):
                        replace(
                            missing.result_ref,
                            recovery_assessment_artifact_ids=(
                                recovery.assessment.artifact.evidence_artifact_id,
                            ),
                        )
            else:
                assert source.method_assessment is recovery.assessment
                allocated.append(
                    source.peak_observation.content.peak_observation_content_id
                )
        assert len(set(allocated)) == len(allocated)
        assert set(completed.method_assessments) == {
            assessment
            for item in result.sample_results
            for assessment in item.effective_assessments
        }

        from xic_extractor.alignment.untargeted_finalized_matrix import (
            project_completed_untarget_batch_writer_inputs,
        )

        writer = project_completed_untarget_batch_writer_inputs(completed)
        decisions = tuple(
            decision
            for decision in writer.production_decisions.cells.values()
            if decision.sample_stem == "Missing"
        )
        assert all(
            not decision.write_matrix_value and decision.matrix_value is None
            for decision in decisions
        )
        assert sorted(decision.blank_reason for decision in decisions) == sorted(
            cell.evidence_state for cell in missing_cells
        )
        # Single CID-NL recovery retains MS1 candidates without direct SUPPORT.
        assert all(
            cell.evidence_states == ("unresolved",)
            for cell in missing_cells
            if cell.predecessor_cells[0].peak_observation is not None
        )
        if shared_support == "shared" and recovery_peaks == (8.0, 12.0):
            from xic_extractor.alignment.mode_track import (
                ModeTrackTrajectoryPolicy,
            )
            from xic_extractor.alignment.mode_track.producer import (
                bootstrap_mode_tracks_from_cid_nl_support,
            )

            inputs = tuple(
                (item.job.sample, assessment)
                for item in result.sample_results
                for assessment in item.effective_assessments
            )
            options = dict(
                processing_owners=context.processing_owners,
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                trajectory_policy=ModeTrackTrajectoryPolicy(
                    trajectory_policy_version="anchor-trajectory-v1",
                    band_policy_version="fixed-band-v1",
                    prediction_half_width_min=2 / 60,
                    max_interpolation_gap=10,
                    max_extrapolation_gap=10,
                ),
                retain_observation_only_candidates=True,
            )
            baseline = bootstrap_mode_tracks_from_cid_nl_support(
                sample_assessments=inputs, **options
            )
            replayed = bootstrap_mode_tracks_from_cid_nl_support(
                sample_assessments=tuple(pair for pair in inputs for _ in range(2)),
                **options,
            )
            assert replayed == baseline


def test_alignment_uses_configured_recovery_groups(tmp_path, monkeypatch):
    test_runtime_recovers_every_authorized_rt_group(
        tmp_path, monkeypatch, True, (), "configured"
    )


def _assert_alignment_uses_configured_recovery_groups(
    tmp_path, monkeypatch, paths, raws, candidates, kwargs, settings, peak_config
):
    from xic_extractor.alignment.csv_io import DiscoveryBatchInput
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        project_completed_untarget_batch_writer_inputs,
    )
    from xic_extractor.extraction import (
        untargeted_cid_nl_bounded_runtime as bounded_runtime,
    )
    from xic_extractor.extraction.untargeted_cid_nl_alignment import (
        complete_untargeted_cid_nl_alignment,
    )

    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime_results = []
    execute = bounded_runtime.execute_untargeted_cid_nl_runtime_partition

    def retain_runtime(*args, **options):
        result = execute(*args, **options)
        assert result.succeeded
        missing = next(
            item
            for item in result.sample_results
            if item.job.sample.metadata.raw_stem == "Missing"
        )
        (recovery,) = missing.recovery_attempts
        receipt = recovery.assessment.content.evaluation_receipt
        half_window = peak_config.nl_rt_anchor_half_window_min
        assert (receipt.query_rt_min, receipt.query_rt_max) == (
            8.0 - half_window,
            8.0 + half_window,
        )
        runtime_results.append(result)
        return result

    monkeypatch.setattr(
        bounded_runtime, "execute_untargeted_cid_nl_runtime_partition", retain_runtime
    )
    completed = complete_untargeted_cid_nl_alignment(
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
    (recovery,) = missing.recovery_attempts
    assert recovery.assessment.candidate_peaks == ()
    (child,) = completed.application_result.children
    missing_cells = tuple(
        cell
        for cell in child.scientific_core.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Missing"
    )
    recovered = next(
        cell
        for cell in missing_cells
        if cell.predecessor_cells[0].group_context.group.group_center_rt == 8.0
    )
    # The partition store restores assessment values from the completed runtime.
    assert recovered.predecessor_cells[0].method_assessment == recovery.assessment
    assert recovered.evidence_states == ("unavailable",)
    writer = project_completed_untarget_batch_writer_inputs(completed)
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in writer.production_decisions.cells.values()
        if decision.sample_stem == "Missing"
    )
    assert sum(sum(raw.chromatogram_request_counts) for raw in opened[paths[-1]]) == 1
