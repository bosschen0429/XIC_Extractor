from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.package2_no_raw_fixture import build_package2_targeted_raw_fixture
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.target_identity import (
    mint_target_list_snapshot,
    resolve_target_definition,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.ms1_backfill import backfill_ms1_candidates
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.extraction.targeted_real_data_adapter import (
    TargetedRealDataAdapterError,
)
from xic_extractor.extraction.targeted_real_data_adapter import (
    _adapt_current_discovery_sample_target as adapt_current_discovery_sample_target,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    Ms2Scan,
    Ms2ScanEvent,
    RawFileHandle,
    open_raw,
    reset_reader_state,
)
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474
SECONDARY_NEUTRAL_LOSS_DA = 132.0423
PRECURSOR_MZ = 500.0
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


def test_adapter_reacquires_one_current_candidate_into_targeted_lifecycle(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.validation_status == "diagnostic_only"
    assert result.source_candidate_ids == (candidates[0].candidate_id,)
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert result.acquisition.xic_batch_count == 1
    assert result.acquisition.adapter_chromatogram_call_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]
    assert result.alignment_input.selection.status == "selected"
    assert len(result.alignment_input.candidate_set.candidates) == 1
    assert result.alignment_input.candidate_set.lineage_artifacts


def test_adapter_preserves_repeated_strict_scans_as_one_support_event(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.98, 8.02))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 1
    assert len(set(candidates[0].seed_scan_ids)) == 2
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    hypothesis = result.alignment_input.candidate_set.candidate_hypotheses[0]
    support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    assert support is not None
    assert support.content.strict_nl_scan_count == 2
    assert support.content.chromatographic_support_event_count == 1
    assert tuple(
        member.dda_scan_number for member in support.content.scan_supports
    ) == candidates[0].seed_scan_ids
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_reconstructs_each_union_tag_scan_from_its_own_provenance(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw(
        (7.98, 8.02),
        neutral_loss_das=(NEUTRAL_LOSS_DA, SECONDARY_NEUTRAL_LOSS_DA),
    )
    settings = _multi_tag_settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 1
    assert candidates[0].matched_tag_names == ("DNA_dR", "RNA_R")
    assert len(candidates[0].seed_scan_ids) == 2
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    hypothesis = result.alignment_input.candidate_set.candidate_hypotheses[0]
    support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    assert support is not None
    assert support.content.strict_nl_scan_count == 2
    assert tuple(
        member.dda_scan_number for member in support.content.scan_supports
    ) == candidates[0].seed_scan_ids
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_selects_one_seed_when_one_scan_matches_multiple_union_tags(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.98, 8.02))
    first_scan, shared_scan = discovery_raw.scans
    discovery_raw.scans = (
        first_scan,
        replace(
            shared_scan,
            masses=np.asarray(
                (
                    PRODUCT_MZ,
                    PRECURSOR_MZ - SECONDARY_NEUTRAL_LOSS_DA,
                )
            ),
            intensities=np.asarray((700.0, 600.0)),
        ),
    )
    settings = _multi_tag_settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.best_ms2_scan_id == first_scan.scan_number
    evidence = json.loads(candidate.tag_evidence_json)
    assert shared_scan.scan_number in evidence["DNA_dR"]["scan_ids"]
    assert shared_scan.scan_number in evidence["RNA_R"]["scan_ids"]
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    hypothesis = result.alignment_input.candidate_set.candidate_hypotheses[0]
    support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    assert support is not None
    assert support.content.strict_nl_scan_count == 2
    assert tuple(
        member.dda_scan_number for member in support.content.scan_supports
    ) == candidate.seed_scan_ids
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_does_not_collapse_strict_scans_from_separated_peaks(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.7, 8.3))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    separated_candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(separated_candidates) == 2
    ordered = tuple(
        sorted(separated_candidates, key=lambda candidate: candidate.best_seed_rt)
    )
    seed_scan_ids = tuple(candidate.best_ms2_scan_id for candidate in ordered)
    tag_evidence = json.loads(ordered[0].tag_evidence_json)
    tag_receipt = tag_evidence[ordered[0].neutral_loss_tag]
    tag_receipt["scan_count"] = len(seed_scan_ids)
    tag_receipt["scan_ids"] = list(seed_scan_ids)
    tag_receipt["rt_min"] = ordered[0].best_seed_rt
    tag_receipt["rt_max"] = ordered[-1].best_seed_rt
    candidates = (
        replace(
            ordered[0],
            seed_event_count=2,
            seed_scan_ids=seed_scan_ids,
            rt_seed_min=ordered[0].best_seed_rt,
            rt_seed_max=ordered[-1].best_seed_rt,
            tag_evidence_json=json.dumps(tag_evidence),
        ),
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="does not link to exactly one candidate physical MS1 peak",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )

    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_uses_targeted_gaussian15_boundary_owner(tmp_path: Path) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    integration = (
        result.alignment_input.candidate_set.candidate_hypotheses[0].integration
    )
    assert result.acquisition is not None
    request = result.acquisition.traces[0].coordinate.request
    target_value = fixture.target.definition.target
    assert (request.mz, request.rt_min, request.rt_max, request.ppm_tol) == (
        target_value.mz,
        pytest.approx(7.0),
        pytest.approx(9.0),
        target_value.ppm_tol,
    )
    assert integration.integration_method == "chrom_peak_segment_gaussian15"
    assert integration.ms1_morphology_trace_method == "gaussian_15"
    assert integration.ms1_morphology_trace_window_points == 15
    assert "chrom_peak_segment" in integration.boundary_sources


def test_adapter_preserves_multiple_owner_backed_candidates_as_ambiguous(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.8, 8.3))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.source_candidate_ids == tuple(
        sorted(item.candidate_id for item in candidates)
    )
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]
    assert len(result.alignment_input.candidate_set.candidates) == 2
    assert result.alignment_input.selection.status == "ambiguous"
    assert result.alignment_input.selection.selected_hypothesis_id is None


def test_adapter_acquires_every_disjoint_candidate_receipt_window(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.55, 8.45))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2
    assert candidates[1].ms1_search_rt_max - candidates[0].ms1_search_rt_min > (
        peak_config.nl_rt_anchor_half_window_min
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.acquisition is not None
    assert len(result.acquisition.traces) == 1
    request = result.acquisition.traces[0].coordinate.request
    assert all(
        request.rt_min <= candidate.ms1_search_rt_min
        and request.rt_max >= candidate.ms1_search_rt_max
        for candidate in candidates
    )
    assert result.acquisition.logical_xic_request_count == 1
    assert result.source_candidate_ids == tuple(
        sorted(item.candidate_id for item in candidates)
    )
    assert result.alignment_input.selection.status == "ambiguous"
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_builds_each_observation_from_its_candidate_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import capture as capture_owner

    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((7.7, 8.3))
    settings = _settings()
    peak_config = replace(
        _peak_config(tmp_path),
        nl_rt_anchor_half_window_min=0.05,
    )
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )
    detection_windows: list[tuple[float, float]] = []
    detect_ms1_trace_peak = capture_owner.detect_ms1_trace_peak

    def _capture_detection_window(rt, intensity, **kwargs):
        detection_windows.append((float(rt[0]), float(rt[-1])))
        return detect_ms1_trace_peak(rt, intensity, **kwargs)

    monkeypatch.setattr(
        capture_owner,
        "detect_ms1_trace_peak",
        _capture_detection_window,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.acquisition is not None
    acquisition_request = result.acquisition.traces[0].coordinate.request
    assert all(
        acquisition_request.rt_min <= candidate.ms1_search_rt_min
        and acquisition_request.rt_max >= candidate.ms1_search_rt_max
        for candidate in candidates
    )
    assert len(detection_windows) == 2
    for candidate, detection_window in zip(
        candidates,
        detection_windows,
        strict=True,
    ):
        assert candidate.ms1_search_rt_min <= detection_window[0]
        assert detection_window[1] <= candidate.ms1_search_rt_max
        assert detection_window[0] <= candidate.ms1_apex_rt <= detection_window[1]
    hypotheses = tuple(
        sorted(
            result.alignment_input.candidate_set.candidate_hypotheses,
            key=lambda hypothesis: hypothesis.integration.rt_apex_min,
        )
    )
    candidates = tuple(sorted(candidates, key=lambda candidate: candidate.ms1_apex_rt))
    assert len(hypotheses) == 2
    assert tuple(
        hypothesis.integration.rt_apex_min for hypothesis in hypotheses
    ) == pytest.approx(tuple(candidate.ms1_apex_rt for candidate in candidates))
    observation_windows = tuple(
        (
            hypothesis.peak_observation_refs[0].generation_provenance.query_rt_min,
            hypothesis.peak_observation_refs[0].generation_provenance.query_rt_max,
        )
        for hypothesis in hypotheses
    )
    assert observation_windows == tuple(
        (
            pytest.approx(candidate.ms1_search_rt_min),
            pytest.approx(candidate.ms1_search_rt_max),
        )
        for candidate in candidates
    )
    support_scan_sets = tuple(
        tuple(
            member.dda_scan_number
            for member in (
                hypothesis.evidence.cid_nl_chromatographic_support_ref.content.scan_supports
            )
        )
        for hypothesis in hypotheses
    )
    assert support_scan_sets == tuple(
        (candidate.best_ms2_scan_id,) for candidate in candidates
    )
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_rejects_raw_handle_bound_to_another_acquisition_source(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1", "S2"),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S2",
        discovery_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="not owner-bound to the resolved acquisition source",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )

    assert vendor_raw.chromatogram_request_counts == []


def test_adapter_rejects_adapter_that_only_claims_the_expected_source(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    forged_raw = _FakeRaw(
        (8.0,),
        acquisition_source=_source_for(fixture, "S1"),
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        forged_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="not owner-bound to the resolved acquisition source",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=forged_raw,
        )

    assert forged_raw.batch_calls == 0


def test_adapter_rejects_candidate_receipt_drift(tmp_path: Path) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )[0]
    assert candidate.ms1_area is not None
    drifted = replace(candidate, ms1_area=candidate.ms1_area * 1.1)
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="area_raw_counts_seconds mismatches current receipt",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=(drifted,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_adapter_rejects_inferred_discovery_evidence(tmp_path: Path) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    discovery_raw = _FakeRaw((8.0,), scan_precursor_mz=500.5)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        discovery_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 1
    assert candidates[0].precursor_mz_basis == "product_plus_neutral_loss"
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        discovery_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="exact canonical Discovery seed is not unique",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_adapter_rejects_target_without_cid_nl_definition(tmp_path: Path) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    raw = _FakeRaw(
        (8.0,),
        acquisition_source=_source_for(fixture, "S1"),
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    no_nl_target_value = replace(
        fixture.target.definition.target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    no_nl_snapshot = mint_target_list_snapshot(
        (no_nl_target_value,),
        identity_namespace="target-list:adapter-no-nl",
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="target lacks a CID-NL definition",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=resolve_target_definition(no_nl_snapshot, "Analyte"),
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_adapter_rejects_noncanonical_baseline_integration(tmp_path: Path) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    raw = _FakeRaw(
        (8.0,),
        acquisition_source=_source_for(fixture, "S1"),
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="requires canonical asls baseline integration",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=replace(
                peak_config,
                baseline_integration_method="linear_edge",
            ),
            raw=raw,
        )


def test_adapter_seals_empty_target_universe_without_raw_acquisition(
    tmp_path: Path,
) -> None:
    fixture = build_package2_targeted_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    raw = _FakeRaw((8.0,))

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assert result.acquisition is None
    assert result.source_candidate_ids == ()
    assert result.alignment_input.selection.status == "no_candidate"
    assert raw.batch_calls == 0


def _current_candidates(
    raw: _FakeRaw,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
):
    seeds = collect_strict_nl_seeds(raw, raw_file=raw_file, settings=settings)
    groups = group_discovery_seeds(seeds, settings=settings)
    return backfill_ms1_candidates(
        raw,
        groups,
        settings=settings,
        peak_config=peak_config,
    )


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        rt_min=7.5,
        rt_max=8.6,
        seed_rt_gap_min=0.20,
        ms1_search_padding_min=0.20,
        resolver_mode="local_minimum",
    )


def _multi_tag_settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
            NeutralLossProfile("RNA_R", SECONDARY_NEUTRAL_LOSS_DA),
        ),
        selected_tag_names=("DNA_dR", "RNA_R"),
        tag_combine_mode="union",
        rt_min=7.5,
        rt_max=8.6,
        seed_rt_gap_min=0.20,
        ms1_search_padding_min=0.20,
        resolver_mode="local_minimum",
    )


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


def _source_for(fixture, sample_stem: str) -> AcquisitionSourceIdentity:
    return resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
    )


def _open_bound_raw(
    fixture,
    sample_stem: str,
    source_raw: _FakeRaw,
    *,
    tmp_path: Path,
) -> tuple[RawFileHandle, _FakeThermoRaw]:
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)
    handle = open_raw(
        tmp_path / f"{sample_stem}.synthetic",
        tmp_path,
        expected_acquisition_source=_source_for(fixture, sample_stem),
        _import_module=_working_imports,
        _thermo=SimpleNamespace(
            raw_file_reader_adapter=SimpleNamespace(
                FileFactory=lambda _path: vendor_raw
            ),
            device_ms="MS",
            mass_range_factory=lambda low, high: (low, high),
            trace_settings_factory=lambda *_args: SimpleNamespace(
                MassRanges=None,
                Filter=None,
            ),
            mass_range_trace_type="MassRange",
            ms2_order="Ms2",
        ),
    )
    return handle, vendor_raw


def _working_imports(name: str):
    if name == "pythonnet":
        return SimpleNamespace(get_runtime_info=lambda: "ok")
    if name == "clr":
        return SimpleNamespace(AddReference=lambda _path: None)
    return SimpleNamespace()


class _FakeThermoRaw:
    def __init__(self, source: _FakeRaw) -> None:
        self.source = source
        self.disposed = False
        self.chromatogram_request_counts: list[int] = []
        self.scans = {scan.scan_number: scan for scan in source.scans}

    def Dispose(self) -> None:
        self.disposed = True

    def SelectInstrument(self, *_args: object) -> None:
        return None

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        index = int(np.searchsorted(self.source.rt, rt, side="left"))
        return min(max(index + 1, 1), len(self.source.rt))

    def RetentionTimeFromScanNumber(self, scan_number: int) -> float:
        return float(self.source.rt[scan_number - 1])

    def GetChromatogramData(self, settings, start_scan: int, end_scan: int):
        self.chromatogram_request_counts.append(len(settings))
        rt = self.source.rt[start_scan - 1 : end_scan]
        intensity = self.source.intensity[start_scan - 1 : end_scan]
        return SimpleNamespace(
            PositionsArray=[rt.tolist() for _setting in settings],
            IntensitiesArray=[intensity.tolist() for _setting in settings],
        )

    def GetFilterForScanNumber(self, scan_number: int):
        scan = self.scans.get(scan_number)
        if scan is None:
            return SimpleNamespace(MSOrder="Ms1")
        return SimpleNamespace(
            MSOrder="Ms2",
            Filter=SimpleNamespace(
                Reactions=[
                    SimpleNamespace(PrecursorMass=scan.precursor_mz),
                ]
            ),
        )

    def GetSimplifiedScan(self, scan_number: int):
        scan = self.scans[scan_number]
        return SimpleNamespace(
            Masses=scan.masses,
            Intensities=scan.intensities,
        )


class _FakeRaw:
    def __init__(
        self,
        scan_rts: tuple[float, ...],
        *,
        scan_precursor_mz: float = PRECURSOR_MZ,
        neutral_loss_das: tuple[float, ...] | None = None,
        acquisition_source: AcquisitionSourceIdentity | None = None,
    ) -> None:
        if neutral_loss_das is None:
            product_mzs = (PRODUCT_MZ,) * len(scan_rts)
        else:
            if len(neutral_loss_das) != len(scan_rts):
                raise ValueError("neutral_loss_das must match scan_rts")
            product_mzs = tuple(
                scan_precursor_mz - neutral_loss_da
                for neutral_loss_da in neutral_loss_das
            )
        self.acquisition_source = acquisition_source
        self.rt = np.linspace(7.4, 8.7, 521)
        self.intensity = sum(
            (
                1000.0 * np.exp(-0.5 * ((self.rt - center) / 0.035) ** 2)
                for center in scan_rts
            ),
            np.zeros_like(self.rt),
        )
        self.scans = tuple(
            Ms2Scan(
                scan_number=int(np.argmin(np.abs(self.rt - rt))) + 1,
                rt=rt,
                precursor_mz=scan_precursor_mz,
                masses=np.asarray(
                    (
                        product_mz,
                        product_mz + 10.0,
                    )
                ),
                intensities=np.asarray((800.0, 200.0)),
                base_peak=1000.0,
            )
            for rt, product_mz in zip(scan_rts, product_mzs, strict=True)
        )
        self.batch_calls = 0

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self.batch_calls

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        request = XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol)
        trace = self._trace(request)
        return trace.rt, trace.intensity

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_calls += 1
        return tuple(self._trace(request) for request in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        indices = np.flatnonzero(mask)
        return int(indices[0]), int(indices[-1])

    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ):
        for scan in self.scans:
            if rt_min <= scan.rt <= rt_max:
                yield Ms2ScanEvent(
                    scan=scan,
                    parse_error=None,
                    scan_number=scan.scan_number,
                )

    def _trace(self, request: XICRequest) -> XICTrace:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        return XICTrace(rt=self.rt[mask], intensity=self.intensity[mask])
