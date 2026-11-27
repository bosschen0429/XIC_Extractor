from dataclasses import replace
from pathlib import Path

import pytest

from tests.alignment_known_istd_in_memory_fixtures import (
    _make_target,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.applications.untargeted.istd_reference import istd_apex_rts
from xic_extractor.configuration.targets import read_targets
from xic_extractor.discovery.models import DiscoveryPeak


def test_mint_known_istd_reference_validates_inputs() -> None:
    with pytest.raises(ValueError, match="at least one ISTD target"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("Analyte1", is_istd=False)],
            coordinates={},
        )

    with pytest.raises(ValueError, match="unique ISTD target labels"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1"), _make_target("ISTD1")],
            coordinates={},
        )

    with pytest.raises(ValueError, match="undeclared ISTD target"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1")],
            coordinates={("UnknownTarget", "Sample1"): 2.5},
        )

    with pytest.raises(ValueError, match="reference RT must be nonnegative"):
        mint_known_istd_reference_from_measurements(
            targets=[_make_target("ISTD1")],
            coordinates={("ISTD1", "Sample1"): -1.0},
        )


def test_mint_known_istd_reference_computes_centers_and_deltas() -> None:
    targets = [_make_target("ISTD1"), _make_target("ISTD2")]
    coords = {
        ("ISTD1", "S1"): 2.0,
        ("ISTD1", "S2"): 2.2,
        ("ISTD1", "S3"): 2.4,
        ("ISTD2", "S1"): 10.0,
        ("ISTD2", "S2"): 10.2,
        ("ISTD2", "S3"): 10.4,
    }
    ref = mint_known_istd_reference_from_measurements(targets, coords)

    assert isinstance(ref, KnownIstdReference)
    assert ref.workbook_sha256 == ""
    assert len(ref.coordinates) == 6

    coord_map = {(t, s): (rt, delta) for t, s, rt, delta in ref.coordinates}
    assert coord_map[("ISTD1", "S1")][0] == pytest.approx(2.0)
    assert coord_map[("ISTD1", "S1")][1] == pytest.approx(-0.2)
    assert coord_map[("ISTD1", "S2")][1] == pytest.approx(0.0)
    assert coord_map[("ISTD1", "S3")][1] == pytest.approx(0.2)


def test_read_targets_from_bundled_example() -> None:
    targets = read_targets(Path("xic_extractor/configuration/data/targets.xlsx"))
    assert len(targets) == 14
    istd_targets = [t for t in targets if t.is_istd]
    assert len(istd_targets) == 7
    assert all(t.is_istd for t in istd_targets)


def test_istd_apex_is_the_discovery_peak_with_most_events_near_expected_rt():
    target = replace(
        _make_target("ISTD"), rt_min=10.0, rt_max=10.0, expected_rt_min=10.0
    )

    def peak(name, sample, mz, rt, events, *, tag="dR", status="admitted"):
        return DiscoveryPeak(
            peak_id=name,
            sample_stem=sample,
            peak_mz=mz,
            neutral_loss_tag=tag,
            best_ms2_scan_id=1,
            attached_event_count=events,
            ms1_apex_rt=rt,
            ms1_search_rt_min=rt - 1.0,
            ms1_search_rt_max=rt + 1.0,
            query_rt_min=rt,
            status=status,
            matched_tag_names=(tag,),
        )

    peaks = (
        peak("closer", "S1", 200.0005, 10.6, 2),
        peak("spiked", "S1", 200.001, 11.2, 6),
        peak("off_mass", "S1", 200.004, 10.0, 9),
        peak("off_window", "S1", 200.0, 12.5, 9),
        peak("other_loss", "S1", 200.0, 10.0, 9, tag="R"),
        peak("other_ion", "S1", 200.0, 10.0, 9, status="doubly_charged"),
        peak("unattached", "S2", 200.0, 9.5, 0, status="no_nl_event"),
        peak("tie_far", "S3", 200.0, 10.4, 3),
        peak("tie_near", "S3", 200.0, 9.8, 3),
    )

    assert istd_apex_rts(
        [target], peaks, neutral_loss_by_tag={"dR": 100.0, "R": 132.0423}
    ) == {("ISTD", "S1"): 11.2, ("ISTD", "S2"): 9.5, ("ISTD", "S3"): 9.8}
