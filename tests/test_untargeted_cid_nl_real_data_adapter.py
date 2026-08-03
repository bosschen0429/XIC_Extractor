from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlRealDataAdapterError,
    adapt_current_discovery_sample_subject,
    recover_untargeted_cid_nl_sample_subject,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)


def test_adapter_preserves_complete_multiscan_evidence_without_target_identity(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.02))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_subject.subject_kind == "untargeted_precursor"
    assert assessment.content.target_definition_id is None
    assert assessment.content.method_result == "SUPPORT"
    assert tuple(
        sorted(
            evidence.observation.scan_number
            for evidence in assessment.evidence_refs
        )
    ) == tuple(sorted(candidates[0].seed_scan_ids))
    assert len(result.assessment_input.discovery_projections) == 2
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert result.acquisition.xic_batch_count == 1
    assert result.acquisition.adapter_chromatogram_call_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_closes_qualifying_ms2_without_ms1_peak_as_reject(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )[0]
    orphan = _without_ms1_peak(candidate)
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(orphan,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "REJECT"
    assert assessment.candidate_peaks == ()
    assert len(assessment.evidence_refs) == 1
    assert len(result.assessment_input.discovery_projections) == 1
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_adapter_closes_empty_subject_universe_as_hold_without_xic(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "HOLD"
    assert assessment.content.reason_codes == ("insufficient_method_evidence",)
    assert result.assessment_input.discovery_projections == ()
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_adapter_rejects_discovery_candidate_receipt_drift(tmp_path: Path) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )[0]
    assert candidate.ms1_area is not None
    drifted = replace(candidate, ms1_area=candidate.ms1_area * 1.1)
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        UntargetedCidNlRealDataAdapterError,
        match="area_raw_counts_seconds mismatches current receipt",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(drifted,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_recovery_revisits_one_hold_cell_without_minting_support(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = recover_untargeted_cid_nl_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        group_center_rt=8.0,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assert result.assessment.content.method_result == "HOLD"
    assert len(result.assessment.candidate_peaks) == 1
    assert result.assessment.evidence_refs == ()
    assert result.recovery_input is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]


def _without_ms1_peak(candidate: DiscoveryCandidate) -> DiscoveryCandidate:
    return replace(
        candidate,
        ms1_peak_found=False,
        ms1_apex_rt=None,
        ms1_area=None,
        ms1_peak_rt_start=None,
        ms1_peak_rt_end=None,
        ms1_height=None,
        ms1_seed_delta_min=None,
        discovery_candidate_state="review_only_orphan_nl",
        ms1_feature_row_id="",
    )
