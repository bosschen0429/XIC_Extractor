"""Candidate participation belongs to the in-memory CID-NL grouping owner."""

from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _multi_tag_settings,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    execute_partition_inline,
)
from xic_extractor.alignment.cid_nl_initial_grouping import (
    prepare_cid_nl_grouping,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.sample_metadata import SampleMetadata


@pytest.mark.parametrize(
    ("scenario", "method_results", "retained_samples", "exclusion_reason"),
    [
        ("supported", ("SUPPORT", "SUPPORT"), {"S1", "S2"}, None),
        ("unsearched", ("SUPPORT", None), {"S1"}, None),
        (
            "unsupported_unsearched",
            ("HOLD", None),
            set(),
            "cid_nl_requirement_not_supported",
        ),
        ("unsupported", ("HOLD", "HOLD"), set(), "cid_nl_requirement_not_supported"),
        ("rejected", ("REJECT", "REJECT"), set(), "cid_nl_requirement_not_supported"),
        (
            "missing_capture",
            ("SUPPORT", "REJECT"),
            {"S1"},
            "discovery_candidate_not_search_eligible",
        ),
        ("composite", ("HOLD", "HOLD"), set(), "composite_requirement_not_supported"),
    ],
)
def test_owner_decides_membership_and_exact_exclusion_from_acquired_evidence(
    tmp_path, monkeypatch, scenario, method_results, retained_samples, exclusion_reason
):
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in (1, 2))
    settings = (
        replace(_multi_tag_settings(), tag_combine_mode="intersection")
        if scenario == "composite"
        else _settings()
    )
    raws, candidates = {}, {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw(
            (8.0,),
            neutral_loss_das=(
                132.0423 if scenario == "composite" and index == 1 else 116.0474,
            ),
        )
        raw.intensity *= index + 1
        candidates[path.stem] = (
            ()
            if index == 1 and "unsearched" in scenario
            else _current_candidates(raw, raw_file=path, settings=settings)
        )
        if scenario in {"unsupported", "unsupported_unsearched"}:
            raw.intensity = np.roll(raw.intensity, 60)
        if scenario == "rejected" or scenario == "missing_capture" and index == 1:
            raw.intensity[:] = 0.0
        raws[path] = raw
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i,
                sample_role="sample",
            )
            for i, p in enumerate(paths, 1)
        ),
        settings=settings,
        peaks_by_sample=candidates,
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    monkeypatch.setattr(
        "xic_extractor.untargeted_execution.partition.execution.ensure_assemblies_loaded",
        lambda *_: None,
    )
    initial = execute_partition_inline(
        batch.jobs,
        partition_key="initial",
        initial_only=True,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert initial.succeeded, initial.failure
    sample_assessments = tuple(
        (result.job.sample, result.adapter_result.assessment_input.assessment)
        for result in initial.sample_results
    )
    assert tuple(assessment.method_result for _, assessment in sample_assessments) == (
        method_results
    )
    observations = {
        candidate_id: observation
        for result in initial.sample_results
        for candidate_id, observation in result.adapter_result.candidate_observations
    }
    peaks = tuple(peak for values in candidates.values() for peak in values)
    arguments = dict(
        subject_batch=batch.subject_batch,
        request_subjects=batch.context.method_subject,
        samples=batch.context.samples,
        candidates=peaks,
        observation_by_candidate=observations,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        rt_frame=batch.context.rt_frame,
    )
    grouping = prepare_cid_nl_grouping(**arguments)
    groups = tuple(
        group
        for workset in grouping.worksets
        for group in workset.grouping_scope.groups
    )
    assert {owner.sample_stem for group in groups for owner in group.owners} == (
        retained_samples
    )
    assert len(groups) == bool(retained_samples)
    assert {
        binding.sample_key for binding in grouping.request.scope.capture_bindings
    } == retained_samples
    exclusions = grouping.request.excluded_candidates
    if exclusion_reason is None:
        assert exclusions == ()
    else:
        expected_peaks = tuple(
            peak for peak in peaks if peak.sample_stem not in retained_samples
        )
        assert {
            (item.candidate_id, item.sample_key, item.reason_code)
            for item in exclusions
        } == {
            (peak.peak_id, peak.sample_stem, exclusion_reason)
            for peak in expected_peaks
        }
        for item in exclusions:
            refs = (
                item.composite_assessments
                if scenario == "composite"
                else (item.method_assessments)
            )
            assert refs == (
                tuple(assessment for _, assessment in sample_assessments)
                if "requirement_not_supported" in exclusion_reason
                else ()
            )
    if scenario != "missing_capture":
        return

    renamed = {
        peak.peak_id: f"candidate-{len(peaks) - i}" for i, peak in enumerate(peaks)
    }
    subjects = replace(
        batch.subject_batch,
        candidate_memberships=tuple(
            reversed(
                tuple(
                    (renamed[cid], sid)
                    for cid, sid in batch.subject_batch.candidate_memberships
                )
            )
        ),
    )
    renamed_observations = {
        renamed[cid]: observation
        for cid, observation in reversed(tuple(observations.items()))
    }
    variant = prepare_cid_nl_grouping(
        **(
            arguments
            | dict(
                subject_batch=subjects,
                request_subjects=subjects,
                candidates=tuple(
                    replace(peak, peak_id=renamed[peak.peak_id])
                    for peak in reversed(peaks)
                ),
                observation_by_candidate=renamed_observations,
                sample_assessments=tuple(reversed(sample_assessments)),
                samples=tuple(reversed(batch.context.samples)),
            )
        )
    )
    assert {frozenset(owner.peak for owner in group.owners) for group in groups} == {
        frozenset(owner.peak for owner in group.owners)
        for workset in variant.worksets
        for group in workset.grouping_scope.groups
    }
    assert {
        (renamed[item.candidate_id], item.sample_key, item.reason_code)
        for item in exclusions
    } == {
        (item.candidate_id, item.sample_key, item.reason_code)
        for item in variant.request.excluded_candidates
    }
