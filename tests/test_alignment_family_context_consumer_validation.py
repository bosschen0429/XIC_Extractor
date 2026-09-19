from __future__ import annotations

from collections.abc import Callable

import pytest

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.mode_track import family_context
from xic_extractor.alignment.mode_track.family_context import (
    FamilyContextRef,
    canonical_family_context_ref_bytes,
    mint_family_context_ref,
    validate_family_context_ref_for_consumption,
)


def _track(
    group_id: str,
    *,
    center_rt: float = 8.0,
) -> CrossSamplePeakGroupHypothesis:
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=group_id,
        public_family_id="display-family",
        owner_ids=(f"owner:{group_id}",),
        event_ids=(f"event:{group_id}",),
        event_member_count=1,
        group_center_mz=500.0,
        group_center_rt=center_rt,
    )


def _mint(
    tracks: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    mz_interval: tuple[float, float] = (499.9, 500.1),
    rt_interval: tuple[float, float] = (7.5, 8.8),
) -> FamilyContextRef:
    return mint_family_context_ref(
        mz_interval=mz_interval,
        rt_interval=rt_interval,
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("source-packet:abc", "trace-query:def"),
        mode_tracks=tracks,
    )


def _context(*, with_tracks: bool = True) -> FamilyContextRef:
    tracks = (_track("track-a"), _track("track-b", center_rt=8.4))
    return _mint(tracks if with_tracks else ())


def _refresh_family_context_id(reference: FamilyContextRef) -> None:
    refreshed_id = family_context._family_context_id(  # type: ignore[attr-defined]
        mz_interval=reference.mz_interval,
        rt_interval=reference.rt_interval,
        context_policy_version=reference.context_policy_version,
        source_lineage_refs=reference.source_lineage_refs,
        group_hypothesis_ids=reference.group_hypothesis_ids,
    )
    object.__setattr__(reference, "family_context_id", refreshed_id)


@pytest.mark.parametrize("with_tracks", (False, True))
def test_consumer_accepts_exact_replay_without_mutation(with_tracks: bool) -> None:
    reference = _context(with_tracks=with_tracks)
    before = canonical_family_context_ref_bytes(reference)

    assert validate_family_context_ref_for_consumption(reference) is None  # type: ignore[func-returns-value]
    assert validate_family_context_ref_for_consumption(reference) is None  # type: ignore[func-returns-value]
    assert canonical_family_context_ref_bytes(reference) == before


class _FamilyContextRefSubclass(FamilyContextRef):  # type: ignore[misc]
    pass


@pytest.mark.parametrize(
    "value",
    (object(), object.__new__(_FamilyContextRefSubclass)),
)
def test_consumer_validation_rejects_non_exact_reference_type(value: object) -> None:
    with pytest.raises(TypeError, match="exact FamilyContextRef"):
        validate_family_context_ref_for_consumption(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "mutation"),
    (
        ("mz_interval", lambda ref: list(ref.mz_interval)),
        ("rt_interval", lambda ref: list(ref.rt_interval)),
        ("source_lineage_refs", lambda ref: list(ref.source_lineage_refs)),
        ("mode_tracks", lambda ref: list(ref.mode_tracks)),
        ("group_hypothesis_ids", lambda ref: list(ref.group_hypothesis_ids)),
        ("context_policy_version", lambda _: ""),
        ("validation_status", lambda _: "production_ready"),
        ("product_authority", lambda _: "matrix"),
        ("family_context_id", lambda _: "family-context:forged"),
        ("source_lineage_refs", lambda ref: ref.source_lineage_refs[:1] * 2),
        ("source_lineage_refs", lambda ref: tuple(reversed(ref.source_lineage_refs))),
        ("group_hypothesis_ids", lambda ref: ref.group_hypothesis_ids[:1] * 2),
        ("mode_tracks", lambda ref: tuple(reversed(ref.mode_tracks))),
    ),
)
def test_consumer_validation_rejects_top_level_mutation(
    field: str,
    mutation: Callable[[FamilyContextRef], object],
) -> None:
    reference = _context()
    object.__setattr__(reference, field, mutation(reference))

    with pytest.raises(ValueError):
        validate_family_context_ref_for_consumption(reference)
    with pytest.raises(ValueError):
        canonical_family_context_ref_bytes(reference)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("owner_ids", ("owner:track-a",), "share owner ids"),
        ("group_center_mz", 501.0, "outside the Family context m/z"),
        ("group_center_rt", 9.0, "outside the Family context RT"),
    ),
)
def test_consumer_validation_rejects_mutated_track_facts(
    field: str,
    value: object,
    message: str,
) -> None:
    reference = _context()
    object.__setattr__(reference.mode_tracks[1], field, value)

    with pytest.raises(ValueError, match=message):
        validate_family_context_ref_for_consumption(reference)


@pytest.mark.parametrize(
    "owner_ids",
    (
        ["owner:track-b"],
        1,
        (1,),
        ("",),
        (" owner:track-b",),
        ("owner:track-b", "owner:track-b"),
    ),
)
def test_mint_and_consumer_reject_malformed_consumed_owner_ids(
    owner_ids: object,
) -> None:
    mint_track = _track("track-b")
    object.__setattr__(mint_track, "owner_ids", owner_ids)
    with pytest.raises(ValueError, match="owner.?ids"):
        _mint((mint_track,))

    reference = _context()
    object.__setattr__(reference.mode_tracks[1], "owner_ids", owner_ids)
    with pytest.raises(ValueError, match="owner_ids|share owner ids"):
        validate_family_context_ref_for_consumption(reference)


def test_empty_owner_tuple_remains_valid_at_both_identity_ingresses() -> None:
    track = _track("track-a")
    object.__setattr__(track, "owner_ids", ())
    reference = _mint((track,))

    assert validate_family_context_ref_for_consumption(reference) is None  # type: ignore[func-returns-value]


@pytest.mark.parametrize("group_id", ("", " track-a", "track-a "))
def test_mint_and_consumer_reject_noncanonical_consumed_group_id(
    group_id: str,
) -> None:
    mint_track = _track(group_id)
    object.__setattr__(mint_track, "owner_ids", ("owner:canonical",))
    with pytest.raises(ValueError, match="group_hypothesis_id"):
        _mint((mint_track,))

    reference = _context()
    object.__setattr__(reference.mode_tracks[0], "group_hypothesis_id", group_id)
    ids = tuple(track.group_hypothesis_id for track in reference.mode_tracks)
    object.__setattr__(reference, "group_hypothesis_ids", ids)
    _refresh_family_context_id(reference)
    with pytest.raises(ValueError, match="group_hypothesis_id"):
        validate_family_context_ref_for_consumption(reference)


@pytest.mark.parametrize(
    ("field", "interval"),
    (("mz_interval", (499, 501)), ("rt_interval", (-0.0, 8.8))),
)
def test_consumer_rejects_noncanonical_interval_identity_bytes(
    field: str,
    interval: tuple[float, float],
) -> None:
    reference = _context()
    object.__setattr__(reference, field, interval)
    _refresh_family_context_id(reference)

    with pytest.raises(ValueError, match="canonical floats"):
        validate_family_context_ref_for_consumption(reference)


def test_mint_normalizes_interval_identity_bytes() -> None:
    reference = _mint(
        (_track("track-a"),),
        mz_interval=(499, 501),
        rt_interval=(-0.0, 8.8),
    )

    assert reference.mz_interval == (499.0, 501.0)
    assert all(type(bound) is float for bound in reference.mz_interval)
    assert reference.rt_interval[0].hex() == "0x0.0p+0"


def test_consumer_validation_is_exported_by_the_family_owner() -> None:
    assert "validate_family_context_ref_for_consumption" in family_context.__all__
