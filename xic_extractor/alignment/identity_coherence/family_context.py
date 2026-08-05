"""Owner-minted internal identity for one wide chromatographic Family context."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import InitVar, dataclass, field
from typing import final

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)

SCHEMA_VERSION = "family_context_ref_v1"
VALIDATION_STATUS = "shadow_ready"
PRODUCT_AUTHORITY = "none"
FAMILY_CONTEXT_ID_PREFIX = "family-context:"

_FAMILY_CONTEXT_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class FamilyContextRef:
    """Content-addressed snapshot of one exact internal search universe.

    The identity deliberately excludes method subjects and legacy/public Family
    labels. ModeTracks retain their existing ``group_hypothesis_id`` identity.
    """

    family_context_id: str
    mz_interval: tuple[float, float]
    rt_interval: tuple[float, float]
    context_policy_version: str
    source_lineage_refs: tuple[str, ...]
    mode_tracks: tuple[CrossSamplePeakGroupHypothesis, ...] = field(
        compare=False,
        repr=False,
    )
    group_hypothesis_ids: tuple[str, ...]
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FAMILY_CONTEXT_MINT_TOKEN:
            raise ValueError("FamilyContextRef must be minted by the M3 owner")
        normalized_mz = _validated_interval(self.mz_interval, field="mz_interval")
        normalized_rt = _validated_interval(self.rt_interval, field="rt_interval")
        if self.mz_interval != normalized_mz or self.rt_interval != normalized_rt:
            raise ValueError("Family context intervals must use canonical floats")
        _required_text(self.context_policy_version, field="context_policy_version")
        _require_canonical_refs(
            self.source_lineage_refs,
            field="source_lineage_refs",
        )
        _validate_tracks(
            self.mode_tracks,
            group_hypothesis_ids=self.group_hypothesis_ids,
            mz_interval=self.mz_interval,
            rt_interval=self.rt_interval,
        )
        if self.validation_status != VALIDATION_STATUS:
            raise ValueError(
                "FamilyContextRef validation_status must remain shadow_ready"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise ValueError("FamilyContextRef cannot grant product authority")
        expected_id = _family_context_id(
            mz_interval=self.mz_interval,
            rt_interval=self.rt_interval,
            context_policy_version=self.context_policy_version,
            source_lineage_refs=self.source_lineage_refs,
            group_hypothesis_ids=self.group_hypothesis_ids,
        )
        if self.family_context_id != expected_id:
            raise ValueError("family_context_id mismatches canonical owner content")


def mint_family_context_ref(
    *,
    mz_interval: tuple[float, float],
    rt_interval: tuple[float, float],
    context_policy_version: str,
    source_lineage_refs: tuple[str, ...],
    mode_tracks: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> FamilyContextRef:
    """Mint one internal Family identity without legacy/public label authority."""

    canonical_mz = _validated_interval(mz_interval, field="mz_interval")
    canonical_rt = _validated_interval(rt_interval, field="rt_interval")
    policy_version = _required_text(
        context_policy_version,
        field="context_policy_version",
    )
    canonical_lineage = _canonical_refs(
        source_lineage_refs,
        field="source_lineage_refs",
    )
    canonical_tracks = _canonical_tracks(mode_tracks)
    group_hypothesis_ids = tuple(
        track.group_hypothesis_id for track in canonical_tracks
    )
    _validate_tracks(
        canonical_tracks,
        group_hypothesis_ids=group_hypothesis_ids,
        mz_interval=canonical_mz,
        rt_interval=canonical_rt,
    )
    return FamilyContextRef(
        family_context_id=_family_context_id(
            mz_interval=canonical_mz,
            rt_interval=canonical_rt,
            context_policy_version=policy_version,
            source_lineage_refs=canonical_lineage,
            group_hypothesis_ids=group_hypothesis_ids,
        ),
        mz_interval=canonical_mz,
        rt_interval=canonical_rt,
        context_policy_version=policy_version,
        source_lineage_refs=canonical_lineage,
        mode_tracks=canonical_tracks,
        group_hypothesis_ids=group_hypothesis_ids,
        _mint_token=_FAMILY_CONTEXT_MINT_TOKEN,
    )


def canonical_family_context_ref_bytes(reference: FamilyContextRef) -> bytes:
    """Serialize the internal context identity and ModeTrack membership facts.

    The referenced group objects remain owned by ``CrossSamplePeakGroupHypothesis``;
    this ref certifies their immutable ``group_hypothesis_id`` membership rather
    than duplicating the complete group payload.
    """

    if not isinstance(reference, FamilyContextRef):
        raise TypeError("canonical serialization requires FamilyContextRef")
    expected_id = _family_context_id(
        mz_interval=reference.mz_interval,
        rt_interval=reference.rt_interval,
        context_policy_version=reference.context_policy_version,
        source_lineage_refs=reference.source_lineage_refs,
        group_hypothesis_ids=reference.group_hypothesis_ids,
    )
    if reference.family_context_id != expected_id:
        raise ValueError("family_context_id mismatches canonical owner content")
    return _canonical_json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "validation_status": reference.validation_status,
            "product_authority": reference.product_authority,
            "family_context_id": reference.family_context_id,
            "mz_interval": list(reference.mz_interval),
            "rt_interval": list(reference.rt_interval),
            "context_policy_version": reference.context_policy_version,
            "source_lineage_refs": list(reference.source_lineage_refs),
            "group_hypothesis_ids": list(reference.group_hypothesis_ids),
        }
    )


def validate_family_context_ref_for_consumption(
    reference: FamilyContextRef,
) -> None:
    """Revalidate the complete current M3-owned reference contract."""

    if type(reference) is not FamilyContextRef:
        raise TypeError("consumer validation requires exact FamilyContextRef")
    reference.__post_init__(_FAMILY_CONTEXT_MINT_TOKEN)


def _family_context_id(
    *,
    mz_interval: tuple[float, float],
    rt_interval: tuple[float, float],
    context_policy_version: str,
    source_lineage_refs: tuple[str, ...],
    group_hypothesis_ids: tuple[str, ...],
) -> str:
    basis = {
        "schema": "family_context_identity_v1",
        "mz_interval": list(mz_interval),
        "rt_interval": list(rt_interval),
        "context_policy_version": context_policy_version,
        "source_lineage_refs": list(source_lineage_refs),
        "group_hypothesis_ids": list(group_hypothesis_ids),
    }
    digest = hashlib.sha256(_canonical_json_bytes(basis)).hexdigest()
    return f"{FAMILY_CONTEXT_ID_PREFIX}{digest}"


def _canonical_tracks(
    mode_tracks: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    if not isinstance(mode_tracks, tuple):
        raise ValueError("mode_tracks must be a tuple")
    if any(
        not isinstance(track, CrossSamplePeakGroupHypothesis) for track in mode_tracks
    ):
        raise ValueError("mode_tracks must contain existing group hypotheses")
    ids = tuple(track.group_hypothesis_id for track in mode_tracks)
    if any(not isinstance(group_id, str) or not group_id.strip() for group_id in ids):
        raise ValueError("group_hypothesis_id must be non-empty text")
    if len(ids) != len(set(ids)):
        raise ValueError("mode_tracks contain duplicate group_hypothesis_id")
    return tuple(sorted(mode_tracks, key=lambda track: track.group_hypothesis_id))


def _validate_tracks(
    mode_tracks: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    group_hypothesis_ids: tuple[str, ...],
    mz_interval: tuple[float, float],
    rt_interval: tuple[float, float],
) -> None:
    canonical_tracks = _canonical_tracks(mode_tracks)
    if mode_tracks != canonical_tracks:
        raise ValueError("mode_tracks must be canonical-sorted by group_hypothesis_id")
    expected_ids = tuple(track.group_hypothesis_id for track in mode_tracks)
    if group_hypothesis_ids != expected_ids:
        raise ValueError("group_hypothesis_ids must exactly match mode_tracks")
    owner_id_groups = tuple(track.owner_ids for track in mode_tracks)
    if any(not isinstance(owner_ids, tuple) for owner_ids in owner_id_groups):
        raise ValueError("ModeTrack owner_ids must be a tuple")
    owner_ids = tuple(
        _required_text(owner_id, field="ModeTrack owner_ids")
        for group_owner_ids in owner_id_groups
        for owner_id in group_owner_ids
    )
    if len(owner_ids) != len(set(owner_ids)):
        raise ValueError("ModeTracks must not share owner ids")
    for track in mode_tracks:
        center_mz = _finite_float(track.group_center_mz, field="group_center_mz")
        center_rt = _finite_float(track.group_center_rt, field="group_center_rt")
        if not mz_interval[0] <= center_mz <= mz_interval[1]:
            raise ValueError(
                f"ModeTrack {track.group_hypothesis_id!r} is outside the "
                "Family context m/z interval"
            )
        if not rt_interval[0] <= center_rt <= rt_interval[1]:
            raise ValueError(
                f"ModeTrack {track.group_hypothesis_id!r} is outside the "
                "Family context RT interval"
            )


def _validated_interval(
    value: tuple[float, float],
    *,
    field: str,
) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{field} must contain exactly two bounds")
    lower = _finite_float(value[0], field=f"{field}[0]")
    upper = _finite_float(value[1], field=f"{field}[1]")
    if lower >= upper:
        raise ValueError(f"{field} lower bound must be less than upper bound")
    return (lower, upper)


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _canonical_refs(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{field} must be a non-empty tuple")
    normalized = tuple(_required_text(value, field=field) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must be unique")
    return tuple(sorted(normalized))


def _require_canonical_refs(values: tuple[str, ...], *, field: str) -> None:
    if values != _canonical_refs(values, field=field):
        raise ValueError(f"{field} must be canonical-sorted")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return (serialized + "\n").encode("utf-8")


__all__ = (
    "FAMILY_CONTEXT_ID_PREFIX",
    "PRODUCT_AUTHORITY",
    "SCHEMA_VERSION",
    "VALIDATION_STATUS",
    "FamilyContextRef",
    "canonical_family_context_ref_bytes",
    "mint_family_context_ref",
    "validate_family_context_ref_for_consumption",
)
