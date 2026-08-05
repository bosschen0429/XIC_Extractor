from __future__ import annotations

from collections.abc import Callable

import pytest

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.identity_coherence import family_context
from xic_extractor.alignment.identity_coherence.family_context import (
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
) -> FamilyContextRef:
    return mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc", "trace-query:def"),
        mode_tracks=tracks,
    )


def _context(*, with_tracks: bool = True) -> FamilyContextRef:
    tracks = (_track("track-a"), _track("track-b", center_rt=8.4))
    return _mint(tracks if with_tracks else ())


@pytest.mark.parametrize("with_tracks", (False, True))
def test_consumer_validation_accepts_exact_replay_without_mutation(
    with_tracks: bool,
) -> None:
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


Mutation = Callable[[FamilyContextRef], object]


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
    mutation: Mutation,
) -> None:
    reference = _context()
    object.__setattr__(reference, field, mutation(reference))

    with pytest.raises(ValueError):
        validate_family_context_ref_for_consumption(reference)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("owner_ids", ("owner:track-a",), "share owner ids"),
        ("owner_ids", ["owner:track-b"], "owner_ids must be a tuple"),
        ("owner_ids", 1, "owner_ids must be a tuple"),
        ("owner_ids", (1,), "owner_ids must contain canonical text"),
        ("owner_ids", (" owner:track-b",), "owner_ids must contain canonical text"),
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


def test_family_mint_rejects_malformed_consumed_owner_ids() -> None:
    track = _track("track-a")
    object.__setattr__(track, "owner_ids", ["owner:track-a"])

    with pytest.raises(ValueError, match="owner_ids must be a tuple"):
        _mint((track,))


def test_consumer_validation_is_exported_by_the_family_owner() -> None:
    assert "validate_family_context_ref_for_consumption" in family_context.__all__
