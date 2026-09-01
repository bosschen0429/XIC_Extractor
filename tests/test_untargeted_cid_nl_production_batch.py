from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

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
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
)
from xic_extractor.sample_metadata import SampleMetadata


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
        job.candidates is batch.context.candidates_by_sample["S1"]
        for job in batch.jobs
    )


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

    completed = compose_untargeted_cid_nl_production_result(
        batch,
        result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
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
    source_raws = {raw_path: _FakeRaw((8.0,)) for raw_path in raw_paths}
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
        for candidate in (
            initial_candidates if exclude_all else initial_candidates[1:]
        )
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
