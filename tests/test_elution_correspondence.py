"""Independent elution-order oracles, not chemical-identity labels."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.elution_correspondence import (
    resolve_elution_correspondence,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)


def _owner(sample: str, label: str, rt: float) -> SampleLocalMS1Owner:
    return SampleLocalMS1Owner(
        owner_id=f"{sample}:{label}",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        precursor_mz=500.0,
        owner_apex_rt=rt,
        owner_peak_start_rt=rt - 0.1,
        owner_peak_end_rt=rt + 0.1,
        owner_area=1000.0,
        owner_height=100.0,
        primary_identity_event=None,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="same_apex_window",
    )


def _context(
    sample: str, peaks: tuple[tuple[float, float], ...]
) -> SampleElutionContext:
    return SampleElutionContext(
        sample_stem=sample,
        mz=500.0,
        rt_start=8.0,
        rt_end=15.0,
        landmarks=tuple(ElutionLandmark(rt, height, 0.25) for rt, height in peaks),
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_order_survives_height_reversal_and_overlapping_absolute_rts(
    reverse: bool,
) -> None:
    contexts = [
        _context("a", ((10.0, 10.0), (10.6, 100.0))),
        _context("b", ((10.1, 100.0), (10.7, 10.0))),
        _context("c", ((10.6, 40.0), (11.2, 100.0))),
    ]
    owners = [
        _owner("a", "early", 10.0),
        _owner("a", "late", 10.6),
        _owner("b", "early", 10.1),
        _owner("b", "late", 10.7),
        _owner("c", "early", 10.6),
        _owner("c", "late", 11.2),
    ]
    if reverse:
        contexts.reverse()
        owners.reverse()
    result = resolve_elution_correspondence(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "c": 20},
    )
    assert {track.owner_ids for track in result.tracks} == {
        ("a:early", "b:early", "c:early"),
        ("a:late", "b:late", "c:late"),
    }


def test_missing_and_extra_neighbors_provide_locations_not_new_owners() -> None:
    contexts = [
        _context("a", ((10.0, 20.0), (10.7, 100.0), (11.3, 60.0))),
        _context("b", ((10.8, 25.0), (11.2, 100.0), (12.0, 60.0))),
        _context("c", ((10.9, 30.0), (11.3, 100.0), (12.1, 65.0), (12.8, 7.0))),
        _context("d", ((11.4, 100.0), (12.2, 60.0))),
    ]
    owners = [
        _owner("a", "late", 11.3),
        _owner("c", "late", 12.1),
        _owner("d", "central", 11.4),
    ]
    result = resolve_elution_correspondence(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 15, "c": 16, "d": 17},
    )
    assert {track.owner_ids for track in result.tracks} == {
        ("a:late", "c:late"),
        ("d:central",),
    }
    assert result.locations_for(("a:late", "c:late")).get("b") == 12.0
    assert result.locations_for(("a:late", "c:late")).get("d") == 12.2
    assert result.locations_for(("d:central",)).get("a") == 10.7
    assert result.locations_for(("a:late", "d:central")).get("c") is None


def test_single_peak_continuation_cannot_overrule_an_ordered_pair() -> None:
    contexts = [
        _context("a", ((10.0, 10.0), (10.6, 100.0))),
        _context("b", ((10.05, 100.0), (10.65, 60.0))),
        _context("c", ((10.7, 100.0),)),
        _context("d", ((10.65, 50.0), (11.25, 100.0))),
    ]
    owners = [
        _owner("a", "early", 10.0),
        _owner("b", "early", 10.05),
        _owner("c", "late", 10.7),
        _owner("d", "early", 10.65),
    ]
    result = resolve_elution_correspondence(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "c": 3, "d": 30},
    )
    assert result.locations_for(("c:late",)).get("a") == 10.6
    assert result.locations_for(("c:late",)).get("d") == 11.25
    assert {track.owner_ids for track in result.tracks} == {
        ("a:early", "b:early", "d:early"),
        ("c:late",),
    }


def test_repeated_indistinguishable_pattern_does_not_invent_an_ordinal() -> None:
    contexts = [
        _context("a", ((10.0, 100.0), (10.6, 100.0), (11.2, 100.0))),
        _context("b", ((10.1, 100.0), (10.7, 100.0))),
    ]
    result = resolve_elution_correspondence(
        [_owner("a", "unknown", 10.6), _owner("b", "unknown", 10.1)],
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
    )
    assert result.tracks == ()


def test_isolated_peaks_and_other_mass_have_no_pattern_authority() -> None:
    owners = [_owner("a", "only", 10.0), _owner("b", "only", 10.1)]
    contexts = [_context("a", ((10.0, 100.0),)), _context("b", ((10.1, 100.0),))]
    result = resolve_elution_correspondence(
        owners, contexts, config=AlignmentConfig(), injection_order={"a": 1, "b": 2}
    )
    assert result.tracks == ()
    unrelated = [replace(context, mz=501.0) for context in contexts]
    assert (
        resolve_elution_correspondence(
            owners, unrelated, config=AlignmentConfig(), injection_order={}
        ).tracks
        == ()
    )


def test_landmarks_exclude_clipped_edge_and_keep_interior_maxima() -> None:
    rt = np.linspace(0.0, 3.0, 601)
    raw = sum(
        height * np.exp(-0.5 * ((rt - apex) / 0.06) ** 2)
        for apex, height in [(0.01, 1000.0), (1.0, 300.0), (1.6, 700.0)]
    )
    prepared = prepare_physical_signal(rt, raw)
    before = prepared.prepared
    context = project_elution_context(prepared, sample_stem="sample", mz=500.0)
    assert context is not None
    assert [landmark.apex_rt for landmark in context.landmarks] == pytest.approx(
        [1.0, 1.6], abs=0.01
    )
    assert all(landmark.width > 0.0 for landmark in context.landmarks)
    assert prepared.prepared == before
