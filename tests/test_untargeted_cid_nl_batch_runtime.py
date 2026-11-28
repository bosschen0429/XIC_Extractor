from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _FakeRaw,
    _settings,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.untargeted_cid_nl_batch_runtime_fixtures import (
    _nearby_subject_candidates,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    execute_dense_partition,
)
from xic_extractor.alignment.cid_nl_inputs import CID_NL_ALIGNMENT_CONFIG
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.sample_metadata import SampleMetadata


@pytest.mark.parametrize("missing_second_ion", [False, True])
def test_runtime_batch_request_bounds_initial_and_recovery_by_sample_phase(
    tmp_path: Path,
    monkeypatch,
    missing_second_ion: bool,
) -> None:
    stems = ("S1", "S2", "Missing")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(
            f"real-data-runtime-batch-recovery:{raw_path.stem}".encode()
        )
    settings = replace(_settings(), precursor_mz_tolerance_ppm=1.0)
    source_raws: dict[Path, _FakeRaw] = {}
    peaks_by_sample: dict[str, tuple[DiscoveryPeak, ...]] = {}
    for raw_path in raw_paths[:2]:
        source_raw, candidates = _nearby_subject_candidates(
            raw_path,
            settings=settings,
        )
        source_raws[raw_path] = source_raw
        peaks_by_sample[raw_path.stem] = candidates
    missing_raw, _ = _nearby_subject_candidates(raw_paths[2], settings=settings)
    missing_raw.scans = ()
    if missing_second_ion:
        missing_raw.additional_ms1_ions = {}
    source_raws[raw_paths[2]] = missing_raw
    peaks_by_sample["Missing"] = ()
    batch = build_dense_partition(
        raw_paths=raw_paths,
        settings=settings,
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        peaks_by_sample=peaks_by_sample,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
    )
    assert len(batch.subject_batch.subjects) == 2
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    events = []
    result = execute_dense_partition(
        batch,
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        progress_callback=events.append,
    )
    for stage in ("partition_initial", "partition_backfill", "partition_seal"):
        progress = [event for event in events if event.get("stage") == stage]
        assert progress[-1]["completed"] == progress[-1]["total"]

    assert result.succeeded is True, result.failure
    assert result.prepared_grouping is not None
    selected = {
        owner.peak.physical_peak_key
        for workset in result.prepared_grouping.worksets
        for group in workset.grouping_scope.groups
        if group.disposition == "established"
        for owner in group.owners
        if owner.sample_stem == "Missing"
    }
    missing_results = tuple(
        item for item in result.sample_results if item.job.sample.raw_stem == "Missing"
    )
    lighter, heavier = sorted(
        missing_results, key=lambda item: item.job.method_subject.precursor_mz
    )
    for item in (lighter, heavier):
        assessment = item.adapter_result.assessment_input.assessment
        assert assessment.method_result is None
        assert not assessment.individual_support_refs
        if item is heavier and missing_second_ion:
            assert not assessment.candidate_peaks
            (attempt,) = item.backfill_attempts
            assert not attempt.assessment.candidate_peaks
        else:
            (peak,) = assessment.candidate_peaks
            assert peak.mz == pytest.approx(item.job.method_subject.precursor_mz)
            assert peak.integration.rt_apex_min == pytest.approx(7.98)
            assert peak.physical_peak_key in selected
            assert item.backfill_attempts == ()
    for path in raw_paths[:2]:
        (initial,) = opened[path]
        assert initial.chromatogram_request_counts == [2]
    initial, *revisits = opened[raw_paths[2]]
    assert initial.chromatogram_request_counts == [2]
    assert len(revisits) == int(missing_second_ion)
