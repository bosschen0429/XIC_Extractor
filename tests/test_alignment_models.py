import math
from dataclasses import dataclass

import pytest

from xic_extractor.alignment.models import (
    AlignmentCluster,
    build_alignment_cluster,
    calculate_alignment_cluster_center,
)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    best_seed_rt: float = 1.0
    ms1_apex_rt: float | None = 1.0
    ms1_area: float | None = 1.0


def test_anchor_cluster_center_uses_anchor_members_only():
    low_area_anchor = _candidate(
        candidate_id="anchor-low-area",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=50.0,
        ms1_apex_rt=1.0,
        ms1_area=1.0,
    )
    high_area_anchor = _candidate(
        candidate_id="anchor-high-area",
        precursor_mz=104.0,
        product_mz=54.0,
        observed_neutral_loss_da=50.2,
        ms1_apex_rt=1.4,
        ms1_area=10_000.0,
    )
    non_anchor = _candidate(
        candidate_id="non-anchor",
        precursor_mz=500.0,
        product_mz=450.0,
        observed_neutral_loss_da=60.0,
        ms1_apex_rt=9.0,
        ms1_area=50_000.0,
    )

    cluster = build_alignment_cluster(
        cluster_id="cluster-1",
        neutral_loss_tag="NL50",
        members=(low_area_anchor, high_area_anchor, non_anchor),
        anchor_members=(low_area_anchor, high_area_anchor),
    )

    assert cluster == AlignmentCluster(
        cluster_id="cluster-1",
        neutral_loss_tag="NL50",
        cluster_center_mz=102.0,
        cluster_center_rt=1.2,
        cluster_product_mz=52.0,
        cluster_observed_neutral_loss_da=50.1,
        has_anchor=True,
        members=(low_area_anchor, high_area_anchor, non_anchor),
        anchor_members=(low_area_anchor, high_area_anchor),
    )


def test_non_anchor_cluster_center_uses_all_members():
    first = _candidate(
        candidate_id="first",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=49.8,
        ms1_apex_rt=1.0,
    )
    second = _candidate(
        candidate_id="second",
        precursor_mz=104.0,
        product_mz=54.0,
        observed_neutral_loss_da=50.2,
        ms1_apex_rt=1.4,
    )
    third = _candidate(
        candidate_id="third",
        precursor_mz=500.0,
        product_mz=450.0,
        observed_neutral_loss_da=60.0,
        ms1_apex_rt=9.0,
    )

    cluster = build_alignment_cluster(
        cluster_id="cluster-2",
        neutral_loss_tag="NL50",
        members=(first, second, third),
    )

    assert cluster.cluster_center_mz == 104.0
    assert cluster.cluster_center_rt == 1.4
    assert cluster.cluster_product_mz == 54.0
    assert cluster.cluster_observed_neutral_loss_da == 50.2
    assert cluster.has_anchor is False
    assert cluster.anchor_members == ()


def test_alignment_cluster_fold_metadata_defaults_to_empty():
    candidate = _candidate(
        candidate_id="sample-a",
        precursor_mz=242.114,
        product_mz=126.066,
        observed_neutral_loss_da=116.048,
        best_seed_rt=12.5927,
        ms1_apex_rt=12.5927,
    )

    cluster = build_alignment_cluster(
        cluster_id="ALN000001",
        neutral_loss_tag="DNA_dR",
        members=(candidate,),
    )

    assert cluster.folded_cluster_ids == ()
    assert cluster.folded_member_count == 0
    assert cluster.folded_sample_fill_count == 0
    assert cluster.fold_evidence == ""


def test_center_uses_best_seed_rt_when_ms1_apex_rt_is_missing():
    fallback_rt = _candidate(
        candidate_id="fallback-rt",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=50.0,
        best_seed_rt=2.5,
        ms1_apex_rt=None,
    )

    center = calculate_alignment_cluster_center((fallback_rt,))

    assert center == (100.0, 2.5, 50.0, 50.0, False)


def test_anchor_members_must_belong_to_cluster_members():
    member = _candidate(
        candidate_id="member",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=50.0,
    )
    outsider = _candidate(
        candidate_id="outsider",
        precursor_mz=110.0,
        product_mz=60.0,
        observed_neutral_loss_da=50.0,
    )

    with pytest.raises(ValueError, match="anchor_members must be members"):
        calculate_alignment_cluster_center((member,), anchor_members=(outsider,))


@pytest.mark.parametrize(
    "ms1_apex_rt,best_seed_rt",
    [
        (math.nan, 1.0),
        (math.inf, 1.0),
        (-math.inf, 1.0),
        (None, None),
        (None, math.nan),
        (None, math.inf),
        (None, -math.inf),
    ],
)
def test_center_rejects_invalid_rt_values(ms1_apex_rt, best_seed_rt):
    candidate = _candidate(
        candidate_id="invalid-rt",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=50.0,
        best_seed_rt=best_seed_rt,
        ms1_apex_rt=ms1_apex_rt,
    )

    with pytest.raises(ValueError, match="finite retention time"):
        calculate_alignment_cluster_center((candidate,))


def test_center_accepts_production_discovery_candidate_shape():
    candidate = _discovery_candidate(
        candidate_id="discovery-candidate",
        precursor_mz=100.0,
        product_mz=50.0,
        observed_neutral_loss_da=50.0,
        best_seed_rt=1.2,
        ms1_apex_rt=None,
    )

    cluster = build_alignment_cluster(
        cluster_id="cluster-discovery",
        neutral_loss_tag="NL50",
        members=(candidate,),
    )

    assert cluster.cluster_center_mz == 100.0
    assert cluster.cluster_center_rt == 1.2
    assert cluster.cluster_product_mz == 50.0
    assert cluster.cluster_observed_neutral_loss_da == 50.0


def test_empty_center_input_raises_clear_value_error():
    with pytest.raises(ValueError, match="requires at least one member"):
        calculate_alignment_cluster_center(())


def _candidate(
    *,
    candidate_id: str,
    precursor_mz: float,
    product_mz: float,
    observed_neutral_loss_da: float,
    best_seed_rt: float = 1.0,
    ms1_apex_rt: float | None = 1.0,
    ms1_area: float | None = 1.0,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_neutral_loss_da,
        best_seed_rt=best_seed_rt,
        ms1_apex_rt=ms1_apex_rt,
        ms1_area=ms1_area,
    )


def _discovery_candidate(
    *,
    candidate_id: str,
    precursor_mz: float,
    product_mz: float,
    observed_neutral_loss_da: float,
    best_seed_rt: float,
    ms1_apex_rt: float | None,
):
    from pathlib import Path

    from xic_extractor.discovery.models import DiscoveryCandidate

    return DiscoveryCandidate(
        review_priority="LOW",
        evidence_score=0,
        evidence_tier="E",
        ms2_support="weak",
        ms1_support="missing",
        rt_alignment="missing",
        candidate_id=candidate_id,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_neutral_loss_da,
        best_seed_rt=best_seed_rt,
        seed_event_count=1,
        ms1_peak_found=ms1_apex_rt is not None,
        ms1_apex_rt=ms1_apex_rt,
        ms1_area=1.0,
        ms2_product_max_intensity=10.0,
        reason="test",
        raw_file=Path(f"{candidate_id}.raw"),
        sample_stem=candidate_id,
        best_ms2_scan_id=1,
        seed_scan_ids=(1,),
        neutral_loss_tag="NL50",
        configured_neutral_loss_da=50.0,
        neutral_loss_mass_error_ppm=0.0,
        rt_seed_min=best_seed_rt,
        rt_seed_max=best_seed_rt,
        ms1_search_rt_min=best_seed_rt - 0.1,
        ms1_search_rt_max=best_seed_rt + 0.1,
        ms1_seed_delta_min=None,
        ms1_peak_rt_start=None,
        ms1_peak_rt_end=None,
        ms1_height=None,
        ms1_trace_quality="missing",
    )
