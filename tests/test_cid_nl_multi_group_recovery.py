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
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import start_runtime_attempt
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
        completed = compose_untargeted_cid_nl_production_result(
            batch, result, alignment_config=AlignmentConfig(anchor_min_seed_events=1)
        )
        assert len(completed.initial_groups) == 2

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
