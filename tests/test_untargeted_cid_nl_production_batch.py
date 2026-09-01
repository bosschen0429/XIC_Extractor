from __future__ import annotations

from pathlib import Path

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raw,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
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
