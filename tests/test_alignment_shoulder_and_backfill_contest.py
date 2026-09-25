"""A shoulder split from its peak and a Backfill peak already held elsewhere."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from tests.test_alignment_owner_clustering import _owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.grouping_workflow import (
    contested_backfill_peaks,
    reject_shoulder_groups,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

_CONFIG = AlignmentConfig()
_MZ = 261.1273


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


def _candidate(content_id: str, *, apex: float, mz: float = _MZ):
    return SimpleNamespace(
        content=SimpleNamespace(
            peak_observation_content_id=content_id,
            mz=mz,
            integration=SimpleNamespace(rt_apex_min=apex),
        )
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
        owners, config=_CONFIG, sample_universe=frozenset({"S1", "S2", "S3", "S4"})
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
        owners, config=_CONFIG, sample_universe=frozenset({"S1", "S2", "S3"})
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
        owners, config=_CONFIG, sample_universe=frozenset({"S1", "S2", "S3"})
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
