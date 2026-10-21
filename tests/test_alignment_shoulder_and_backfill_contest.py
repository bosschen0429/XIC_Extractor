"""A shoulder split from its peak and a Backfill peak already held elsewhere."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.alignment_pipeline_helpers import _owner
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.grouping_workflow import (
    contested_backfill_peaks,
    group_cid_nl_owners,
    reject_shoulder_groups,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)

_CONFIG = AlignmentConfig()
_MZ = 261.1273


def test_ms1_patterns_prevent_remerging_sample_disjoint_direct_rows() -> None:
    owners = []
    contexts = []
    for sample, shift, selected in (
        ("A", 0.0, 0),
        ("B", 0.05, 1),
        ("C", 0.6, 0),
        ("D", 0.65, 1),
    ):
        apex = 9.0 + shift + selected * 0.6
        owners.append(
            _peak(
                sample,
                str(selected),
                apex=apex,
                start=apex - 0.1,
                end=apex + 0.1,
                area=1000.0,
            )
        )
        contexts.append(
            SampleElutionContext(
                sample,
                _MZ,
                8.0,
                11.0,
                (
                    ElutionLandmark(9.0 + shift, 100.0, 0.2),
                    ElutionLandmark(9.6 + shift, 100.0, 0.2),
                ),
            )
        )
    orders = {sample: index for index, sample in enumerate(("A", "B", "C", "D"))}
    correspondence = resolve_peak_trajectories(
        owners, contexts, config=_CONFIG, injection_order=orders
    )
    result = group_cid_nl_owners(
        owners,
        config=_CONFIG,
        sample_universe=frozenset(orders),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        peak_trajectories=correspondence,
    )
    assert {frozenset(group.owner_ids) for group in result} == {
        frozenset((owners[0].owner_id, owners[2].owner_id)),
        frozenset((owners[1].owner_id, owners[3].owner_id)),
    }
    assert all(group.disposition == "established" for group in result)


def _peak(
    sample: str,
    suffix: str,
    *,
    apex: float,
    start: float,
    end: float,
    area: float,
    mz: float = _MZ,
) -> SampleLocalMS1Owner:
    owner = _owner(sample, suffix, apex_rt=apex, precursor_mz=mz)
    assert owner.selected_integration is not None
    return replace(
        owner,
        owner_peak_start_rt=start,
        owner_peak_end_rt=end,
        owner_area=area,
        selected_integration=replace(
            owner.selected_integration,
            rt_left_min=start,
            rt_right_min=end,
            rt_width_min=end - start,
            area_raw_counts_seconds=area,
        ),
    )


def _main(sample: str) -> SampleLocalMS1Owner:
    return _peak(sample, "main", apex=9.0, start=8.65, end=9.4, area=10_000.0)


def _shoulder(sample: str, *, end: float = 8.65) -> SampleLocalMS1Owner:
    return _peak(sample, "shoulder", apex=8.55, start=8.3, end=end, area=800.0)


def _groups(owners: tuple[SampleLocalMS1Owner, ...]):
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=_CONFIG,
        sample_universe=frozenset(owner.sample_stem for owner in owners),
        rt_frame=SampleRtFrame(),
        rules=CID_NL_GROUPING_RULES,
    )
    by_rt = {round(group.family_center_rt, 1): group for group in groups}
    assert set(by_rt) == {8.6, 9.0}
    return groups, by_rt[8.6], by_rt[9.0]


def test_shoulder_split_from_most_of_its_peaks_leaves_the_matrix() -> None:
    # S7's main peak was not detected, so its shoulder has no neighbour.
    owners = (
        *(_main(f"S{index}") for index in range(1, 7)),
        *(_shoulder(f"S{index}") for index in (1, 2, 3)),
        _shoulder("S7"),
    )
    groups, shoulder, main = _groups(owners)
    assert shoulder.disposition == main.disposition == "established"

    result = {
        group.group_hypothesis_id: group
        for group in reject_shoulder_groups(groups, config=_CONFIG)
    }

    assert result[main.group_hypothesis_id].disposition == "established"
    rejected = result[shoulder.group_hypothesis_id]
    assert rejected.disposition == "rejected"
    assert rejected.owners == shoulder.owners
    assert rejected.evidence.endswith(f";shoulder_of:{main.group_hypothesis_id}")


def test_shoulder_split_from_half_of_its_peaks_stays_established() -> None:
    owners = (
        *(_main(f"S{index}") for index in range(1, 7)),
        *(_shoulder(f"S{index}") for index in (1, 2)),
        _shoulder("S7"),
        _shoulder("S8"),
    )
    groups, shoulder, _ = _groups(owners)

    result = reject_shoulder_groups(groups, config=_CONFIG)

    assert result == groups
    assert shoulder.disposition == "established"


def test_a_peak_larger_than_its_neighbour_in_one_sample_is_not_a_shoulder() -> None:
    owners = (
        *(_main(f"S{index}") for index in range(1, 7)),
        *(_shoulder(f"S{index}") for index in (1, 2)),
        _peak("S3", "shoulder", apex=8.55, start=8.3, end=8.65, area=20_000.0),
    )
    groups, _, _ = _groups(owners)

    assert reject_shoulder_groups(groups, config=_CONFIG) == groups


def test_a_shoulder_separated_by_a_gap_is_not_split_from_the_peak() -> None:
    owners = (
        *(_main(f"S{index}") for index in range(1, 7)),
        *(_shoulder(f"S{index}", end=8.6) for index in (1, 2, 3)),
    )
    groups, _, _ = _groups(owners)

    assert reject_shoulder_groups(groups, config=_CONFIG) == groups


def _candidate(
    content_id: str, *, apex: float, mz: float = _MZ, query_ppm: float = 20.0
):
    return SimpleNamespace(
        content=SimpleNamespace(
            peak_observation_content_id=content_id,
            mz=mz,
            integration=SimpleNamespace(rt_apex_min=apex),
        ),
        generation_provenance=SimpleNamespace(query_ppm_tol=query_ppm),
    )


def _backfill(*peaks):
    return (None, SimpleNamespace(candidate_peaks=peaks))


def _sample_id(owner: SampleLocalMS1Owner) -> str:
    assert owner.cross_sample_member_subject is not None
    return owner.cross_sample_member_subject.canonical_sample_identity_id


def test_backfill_peak_of_another_rows_member_is_contested() -> None:
    """A Backfill trace and a Discovery trace of one peak differ in content ID."""

    owners = tuple(_main(f"S{index}") for index in range(1, 4))
    (group,) = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=_CONFIG,
        sample_universe=frozenset({"S1", "S2", "S3", "S4"}),
        rt_frame=SampleRtFrame(),
        rules=CID_NL_GROUPING_RULES,
    )
    s1 = _sample_id(owners[0])
    s4 = "test-sample:S4"
    backfills = {
        # Another row's window found S1's detected main peak.
        ("other-row", s1, "subject"): _backfill(_candidate("bf-s1", apex=9.0)),
        # Two rows' windows found S4's peak with different bounds.
        ("other-row", s4, "subject"): _backfill(_candidate("bf-s4-a", apex=9.0)),
        (group.group_hypothesis_id, s4, "subject"): _backfill(
            _candidate("bf-s4-b", apex=9.0)
        ),
    }

    contested = contested_backfill_peaks(
        groups=(group,), backfills=backfills, alignment_config=_CONFIG
    )

    assert contested == frozenset({(s1, "bf-s1"), (s4, "bf-s4-a"), (s4, "bf-s4-b")})


def test_backfill_peak_held_only_by_an_archived_group_is_not_contested() -> None:
    """An archive row writes no value, so its member cannot take the peak away."""

    owners = tuple(_main(f"S{index}") for index in range(1, 4))
    (group,) = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=_CONFIG,
        sample_universe=frozenset({"S1", "S2", "S3"}),
        rt_frame=SampleRtFrame(),
        rules=CID_NL_GROUPING_RULES,
    )
    s1 = _sample_id(owners[0])
    backfills = {
        ("matrix-row", s1, "subject"): _backfill(_candidate("bf-s1", apex=9.0)),
    }

    assert (
        contested_backfill_peaks(
            groups=(replace(group, disposition="unresolved"),),
            backfills=backfills,
            alignment_config=_CONFIG,
        )
        == frozenset()
    )
    assert contested_backfill_peaks(
        groups=(group,), backfills=backfills, alignment_config=_CONFIG
    ) == frozenset({(s1, "bf-s1")})


def test_backfill_peak_at_another_apex_or_mass_is_not_contested() -> None:
    owners = tuple(_main(f"S{index}") for index in range(1, 4))
    (group,) = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=_CONFIG,
        sample_universe=frozenset({"S1", "S2", "S3"}),
        rt_frame=SampleRtFrame(),
        rules=CID_NL_GROUPING_RULES,
    )
    s1 = _sample_id(owners[0])
    backfills = {
        ("shoulder-row", s1, "subject"): _backfill(_candidate("bf-early", apex=8.55)),
        ("other-mass-row", s1, "subject"): _backfill(
            _candidate("bf-mass", apex=9.0, mz=_MZ * (1 + 50e-6))
        ),
    }

    assert (
        contested_backfill_peaks(
            groups=(group,), backfills=backfills, alignment_config=_CONFIG
        )
        == frozenset()
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("first_mz", "first_ppm", "second_mz", "second_ppm", "competing"),
    [
        (500.0, 20.0, 500.01055, 20.0, True),
        (500.0, 5.0, 500.0075, 5.0, False),
        (500.0, 5.0, 500.01055, 20.0, True),
        (500.0, 20.0, 500.025, 20.0, False),
        # Exact windows [999, 1001] and [1001, 1003] share their endpoint.
        (1000.0, 1000.0, 1002.0, 1e6 / 1002.0, True),
        (1000.0, 1000.0, 1002.0, 0.5e6 / 1002.0, False),
    ],
    ids=["overlap", "narrow-disjoint", "unequal-widths", "disjoint", "touch", "gap"],
)
def test_backfill_contest_uses_each_extraction_window(
    first_mz, first_ppm, second_mz, second_ppm, competing, reverse
) -> None:
    backfills = {
        ("row-a", "sample", "subject-a"): _backfill(
            _candidate("a", apex=9.0, mz=first_mz, query_ppm=first_ppm)
        ),
        ("row-b", "sample", "subject-b"): _backfill(
            _candidate("b", apex=9.0, mz=second_mz, query_ppm=second_ppm)
        ),
    }
    if reverse:
        backfills = dict(reversed(tuple(backfills.items())))
    assert contested_backfill_peaks(
        groups=(), backfills=backfills, alignment_config=_CONFIG
    ) == (frozenset({("sample", "a"), ("sample", "b")}) if competing else frozenset())


@pytest.mark.parametrize(
    ("second_group", "second_sample", "second_apex"),
    [
        ("row-b", "other-sample", 9.0),
        ("row-b", "sample", 9.1),
        ("row-a", "sample", 9.0),
    ],
    ids=["different-sample", "different-apex", "same-group"],
)
def test_backfill_contest_keeps_sample_apex_and_group_boundaries(
    second_group, second_sample, second_apex
) -> None:
    backfills = {
        ("row-a", "sample", "subject-a"): _backfill(_candidate("a", apex=9.0)),
        (second_group, second_sample, "subject-b"): _backfill(
            _candidate("b", apex=second_apex)
        ),
    }
    assert (
        contested_backfill_peaks(
            groups=(), backfills=backfills, alignment_config=_CONFIG
        )
        == frozenset()
    )


def _feature(sample: str, apex: float, suffix: str = "f") -> SampleLocalMS1Owner:
    return _peak(
        sample, suffix, apex=apex, start=apex - 0.2, end=apex + 0.2, area=5_000.0
    )


def _two_bands(early: tuple[str, ...], late: tuple[str, ...], *, gap: float):
    owners = (
        *(_feature(sample, 9.0) for sample in early),
        *(_feature(sample, 9.0 + gap, "late") for sample in late),
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=_CONFIG,
        sample_universe=frozenset(owner.sample_stem for owner in owners),
        rt_frame=SampleRtFrame(),
        rules=CID_NL_GROUPING_RULES,
    )
    assert len(groups) == 2
    assert all(group.disposition == "established" for group in groups)
    return groups


@pytest.mark.parametrize("calibrated", [False, True])
def test_grouping_uses_corrected_coordinates_for_separated_bands(
    calibrated: bool,
) -> None:
    # Alternating injections at 9.0 and 10.8 min contradict one local band,
    # despite disjoint samples and overlapping Backfill windows.
    groups = _two_bands(
        tuple(f"S{i}" for i in range(0, 12, 2)),
        tuple(f"S{i}" for i in range(1, 12, 2)),
        gap=1.8,
    )

    class CorrectedCoordinates:
        def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
            return 9.0

    result = group_cid_nl_owners(
        tuple(owner for group in groups for owner in group.owners),
        config=_CONFIG,
        sample_universe=frozenset(f"S{i}" for i in range(12)),
        rt_frame=SampleRtFrame(
            injection_order=({f"S{i}": i for i in range(12)})
        ).with_warp(CorrectedCoordinates() if calibrated else None),
    )
    if calibrated:
        assert len(result) == 1
        assert set(result[0].owner_ids) == {
            owner_id for group in groups for owner_id in group.owner_ids
        }
    else:
        assert {g.group_hypothesis_id for g in result} == {
            g.group_hypothesis_id for g in groups
        }
