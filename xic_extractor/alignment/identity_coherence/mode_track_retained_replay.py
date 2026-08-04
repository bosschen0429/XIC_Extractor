"""Fail-closed no-RAW replay for retained M8 ModeTrack evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from typing import Literal, final

RETAINED_REPLAY_SCHEMA_VERSION = "mode_track_retained_replay_fixture_v1"
RETAINED_REPLAY_SUMMARY_SCHEMA_VERSION = "mode_track_retained_replay_summary_v1"

ReplayValidationStatus = Literal["inconclusive"]
ReplayVerdict = Literal["inconclusive_no_write"]

_REPLAY_MINT_TOKEN = object()


class ModeTrackRetainedReplayError(ValueError):
    """Raised when retained M8 evidence is malformed or overclaims authority."""


@final
@dataclass(frozen=True, slots=True)
class ModeTrackRetainedReplaySummary:
    """One deterministic no-RAW replay result with no assignment authority."""

    fixture_sha256: str
    family_count: int
    sample_count: int
    peak_hypothesis_count: int
    track_count: int
    anchor_count: int
    landmark_count: int
    assignment_state_counts: tuple[tuple[str, int], ...]
    ambiguous_assignment_count: int
    raw_open_count: int
    chromatogram_call_count: int
    seed_oracle_reproduced: bool
    reason_codes: tuple[str, ...]
    validation_status: ReplayValidationStatus = "inconclusive"
    verdict: ReplayVerdict = "inconclusive_no_write"
    product_authority: Literal["none"] = "none"
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _REPLAY_MINT_TOKEN:
            raise ModeTrackRetainedReplayError(
                "retained replay summary must be minted by the M8 replay owner"
            )
        _sha256(self.fixture_sha256, field="fixture_sha256")
        for name in (
            "family_count",
            "sample_count",
            "peak_hypothesis_count",
            "track_count",
            "anchor_count",
            "landmark_count",
            "ambiguous_assignment_count",
            "raw_open_count",
            "chromatogram_call_count",
        ):
            _nonnegative_int(getattr(self, name), field=name)
        if self.family_count != 1:
            raise ModeTrackRetainedReplayError(
                "retained M8 replay must describe exactly one Family context"
            )
        if self.seed_oracle_reproduced:
            raise ModeTrackRetainedReplayError(
                "inconclusive replay cannot claim the seed oracle was reproduced"
            )
        if self.raw_open_count or self.chromatogram_call_count:
            raise ModeTrackRetainedReplayError("Stage B replay must read no RAW")
        if (
            self.validation_status != "inconclusive"
            or self.verdict != "inconclusive_no_write"
            or self.product_authority != "none"
        ):
            raise ModeTrackRetainedReplayError(
                "retained replay cannot grant product authority"
            )
        if self.assignment_state_counts != tuple(
            sorted(self.assignment_state_counts)
        ):
            raise ModeTrackRetainedReplayError(
                "assignment_state_counts must be canonical-sorted"
            )
        if any(
            not isinstance(state, str)
            or not state
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for state, count in self.assignment_state_counts
        ):
            raise ModeTrackRetainedReplayError(
                "assignment_state_counts must contain nonnegative integer counts"
            )
        _canonical_texts(self.reason_codes, field="reason_codes")


def replay_retained_mode_track_fixture(
    fixture_bytes: bytes,
) -> ModeTrackRetainedReplaySummary:
    """Replay one compact fixture without resolving paths or opening RAW files.

    The v1 fixture intentionally represents the authorized reconstruction's
    fail-closed outcome. It records the historical diagnostic census, but the
    absent owner-bound source packet and direct-SUPPORT refs prevent it from
    minting ModeTracks, anchors, or assignments.
    """

    if not isinstance(fixture_bytes, bytes) or not fixture_bytes:
        raise ModeTrackRetainedReplayError("fixture_bytes must be non-empty bytes")
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    try:
        payload = json.loads(fixture_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModeTrackRetainedReplayError(
            "retained replay fixture must be UTF-8 JSON"
        ) from exc
    root = _mapping(payload, field="fixture")
    if root.get("schema_version") != RETAINED_REPLAY_SCHEMA_VERSION:
        raise ModeTrackRetainedReplayError("unsupported retained replay schema")

    authority = _mapping(root.get("authority"), field="authority")
    if (
        authority.get("validation_status") != "inconclusive"
        or authority.get("verdict") != "inconclusive_no_write"
        or authority.get("product_authority") != "none"
        or authority.get("assignment_facts_emitted") is not False
    ):
        raise ModeTrackRetainedReplayError(
            "retained fixture must remain inconclusive with no assignment authority"
        )

    _text(root.get("fixture_id"), field="fixture_id")
    family = _mapping(root.get("family_context"), field="family_context")
    _text(
        family.get("diagnostic_family_label"),
        field="diagnostic_family_label",
    )
    if family.get("label_is_identity") is not False:
        raise ModeTrackRetainedReplayError(
            "diagnostic Family label cannot become retained identity"
        )
    _positive_float(family.get("mz"), field="family m/z")
    windows = _mapping(family.get("mode_windows_min"), field="mode_windows_min")
    if len(windows) < 2:
        raise ModeTrackRetainedReplayError(
            "historical multiple-track packet requires at least two mode windows"
        )
    canonical_windows = tuple(
        sorted(
            (
                _text(name, field="mode window name"),
                _interval(value, field=f"mode window {name}"),
            )
            for name, value in windows.items()
        )
    )
    by_start = tuple(sorted(interval for _name, interval in canonical_windows))
    if any(left[1] >= right[0] for left, right in zip(by_start, by_start[1:])):
        raise ModeTrackRetainedReplayError(
            "historical mode windows must be disjoint and ordered"
        )

    source = _mapping(root.get("source_qualification"), field="source_qualification")
    _git_sha(source.get("producing_code_state"), field="producing_code_state")
    _git_sha(source.get("implementation_base"), field="implementation_base")
    required = _mapping(
        source.get("required_seed_oracle"),
        field="required_seed_oracle",
    )
    historical = _mapping(
        source.get("retained_historical_packet"),
        field="retained_historical_packet",
    )
    _sha256(required.get("discovery_index_sha256"), field="required discovery hash")
    _sha256(required.get("sample_metadata_sha256"), field="required metadata hash")
    _sha256(required.get("trace_packet_sha256"), field="required trace hash")
    required_sample_count = _positive_int(
        required.get("sample_count"),
        field="required sample count",
    )
    if (
        required.get("raw_open_count") != required_sample_count
        or required.get("chromatogram_call_count") != required_sample_count
    ):
        raise ModeTrackRetainedReplayError(
            "required seed source budget must be one RAW open and call per sample"
        )
    _positive_float(required.get("elapsed_seconds"), field="seed elapsed_seconds")
    if (
        required.get("independent_verdict")
        != "multiple_tracks_supported_diagnostic_only"
    ):
        raise ModeTrackRetainedReplayError(
            "required seed verdict must remain diagnostic_only"
        )
    _sha256(
        historical.get("discovery_index_sha256"),
        field="historical discovery hash",
    )
    _sha256(historical.get("trace_packet_sha256"), field="historical trace hash")
    if (
        historical.get("discovery_index_schema") != "legacy_v1_unowned"
        or historical.get("raw_open_count_evidence") is not None
        or historical.get("chromatogram_call_count_evidence") is not None
        or historical.get("source_packet_bytes_retained_in_repo") is not False
    ):
        raise ModeTrackRetainedReplayError(
            "historical packet must preserve its unowned and unclosed provenance"
        )
    if source.get("required_seed_bytes_retained") is not False:
        raise ModeTrackRetainedReplayError(
            "v1 replay requires explicit missing seed bytes"
        )
    if (
        required["discovery_index_sha256"]
        == historical["discovery_index_sha256"]
        or required["trace_packet_sha256"] == historical["trace_packet_sha256"]
    ):
        raise ModeTrackRetainedReplayError(
            "historical packet cannot impersonate the required seed oracle"
        )

    reconstruction = _mapping(root.get("reconstruction"), field="reconstruction")
    if (
        reconstruction.get("status") != "not_launched_missing_source"
        or reconstruction.get("raw_open_count") != 0
        or reconstruction.get("chromatogram_call_count") != 0
    ):
        raise ModeTrackRetainedReplayError(
            "fixture must preserve the no-launch RAW stop condition"
        )

    census = _mapping(root.get("sample_census"), field="sample_census")
    sample_count = _nonnegative_int(census.get("total"), field="sample total")
    study_count = _nonnegative_int(census.get("study"), field="study count")
    pooled_qc_count = _nonnegative_int(
        census.get("pooled_qc"),
        field="pooled QC count",
    )
    if sample_count != study_count + pooled_qc_count:
        raise ModeTrackRetainedReplayError("sample census does not close")
    if sample_count != required_sample_count:
        raise ModeTrackRetainedReplayError(
            "sample census differs from the required seed oracle"
        )
    if historical.get("trace_count") != sample_count:
        raise ModeTrackRetainedReplayError(
            "historical trace count differs from the retained census"
        )
    type_counts = _mapping(
        census.get("historical_type_counts"),
        field="historical_type_counts",
    )
    canonical_type_counts = {
        _text(name, field="historical sample type"): _nonnegative_int(
            count,
            field=f"historical sample type {name}",
        )
        for name, count in type_counts.items()
    }
    if (
        sum(canonical_type_counts.values()) != sample_count
        or canonical_type_counts.get("QC") != pooled_qc_count
        or sum(
            count
            for name, count in canonical_type_counts.items()
            if name != "QC"
        )
        != study_count
    ):
        raise ModeTrackRetainedReplayError(
            "historical sample types do not close the 78 Study / 7 pooled-QC census"
        )

    diagnostic = _mapping(
        root.get("historical_diagnostic_counts"),
        field="historical_diagnostic_counts",
    )
    for count_field in ("trace_packet_assigned_mode", "irt_refined_label"):
        values = _mapping(diagnostic.get(count_field), field=count_field)
        if sum(
            _nonnegative_int(count, field=f"{count_field} {name}")
            for name, count in values.items()
        ) != sample_count:
            raise ModeTrackRetainedReplayError(
                f"{count_field} does not close the historical sample census"
            )
    cooccurrence_orders = tuple(
        _positive_int(value, field="cooccurrence injection order")
        for value in _sequence(
            diagnostic.get("same_raw_low_high_cooccurrence_expected_injection_orders"),
            field="same_raw_low_high_cooccurrence_expected_injection_orders",
        )
    )
    if (
        cooccurrence_orders != tuple(sorted(set(cooccurrence_orders)))
        or any(value > sample_count for value in cooccurrence_orders)
    ):
        raise ModeTrackRetainedReplayError(
            "cooccurrence injection orders must be unique within the census"
        )

    owner = _mapping(root.get("owner_evidence"), field="owner_evidence")
    if (
        owner.get("direct_support_peak_hypothesis_count") != 0
        or owner.get("owner_refs_complete") is not False
        or owner.get("canonical_track_seed_count") != 0
    ):
        raise ModeTrackRetainedReplayError(
            "unclosed historical evidence cannot seed or train ModeTracks"
        )

    sensitivity = _mapping(root.get("sensitivity"), field="sensitivity")
    if (
        sensitivity.get(
            "normalized_morphology_and_absolute_intensity_present_in_historical_packet"
        )
        is not True
        or sensitivity.get("canonical_segment_owner_refs_present") is not False
        or
        sensitivity.get("shoulder_exclusion_status") != "technical_unassessable"
        or sensitivity.get("low_absolute_intensity_exclusion_status")
        != "technical_unassessable"
    ):
        raise ModeTrackRetainedReplayError(
            "unfrozen sensitivity inputs must remain technical_unassessable"
        )

    counts = _mapping(
        root.get("expected_replay_counts"),
        field="expected_replay_counts",
    )
    zero_fields = (
        "peak_hypotheses",
        "tracks",
        "anchors",
        "landmarks",
        "ambiguous_assignments",
    )
    if any(counts.get(name) != 0 for name in zero_fields):
        raise ModeTrackRetainedReplayError(
            "inconclusive fixture cannot retain typed assignment counts"
        )
    if counts.get("families") != 1:
        raise ModeTrackRetainedReplayError(
            "retained replay counts must describe exactly one Family"
        )
    state_counts_mapping = _mapping(
        counts.get("assignment_states"),
        field="assignment_states",
    )
    state_counts = tuple(
        sorted(
            (
                _text(state, field="assignment state"),
                _nonnegative_int(count, field=f"assignment state {state}"),
            )
            for state, count in state_counts_mapping.items()
        )
    )
    if any(count for _state, count in state_counts):
        raise ModeTrackRetainedReplayError(
            "unclosed replay cannot emit ModeTrackAssignment facts"
        )

    reason_codes = tuple(
        _text(value, field="reason code")
        for value in _sequence(root.get("reason_codes"), field="reason_codes")
    )
    _canonical_texts(reason_codes, field="reason_codes")
    return ModeTrackRetainedReplaySummary(
        fixture_sha256=fixture_sha256,
        family_count=1,
        sample_count=sample_count,
        peak_hypothesis_count=0,
        track_count=0,
        anchor_count=0,
        landmark_count=0,
        assignment_state_counts=state_counts,
        ambiguous_assignment_count=0,
        raw_open_count=0,
        chromatogram_call_count=0,
        seed_oracle_reproduced=False,
        reason_codes=reason_codes,
        _mint_token=_REPLAY_MINT_TOKEN,
    )


def canonical_retained_mode_track_replay_summary_bytes(
    summary: ModeTrackRetainedReplaySummary,
) -> bytes:
    """Return deterministic bytes for an owner-minted replay summary."""

    if not isinstance(summary, ModeTrackRetainedReplaySummary):
        raise TypeError("summary must be ModeTrackRetainedReplaySummary")
    payload = {
        "schema_version": RETAINED_REPLAY_SUMMARY_SCHEMA_VERSION,
        "fixture_sha256": summary.fixture_sha256,
        "validation_status": summary.validation_status,
        "verdict": summary.verdict,
        "product_authority": summary.product_authority,
        "seed_oracle_reproduced": summary.seed_oracle_reproduced,
        "raw_open_count": summary.raw_open_count,
        "chromatogram_call_count": summary.chromatogram_call_count,
        "counts": {
            "families": summary.family_count,
            "samples": summary.sample_count,
            "peak_hypotheses": summary.peak_hypothesis_count,
            "tracks": summary.track_count,
            "anchors": summary.anchor_count,
            "landmarks": summary.landmark_count,
            "assignment_states": dict(summary.assignment_state_counts),
            "ambiguous_assignments": summary.ambiguous_assignment_count,
        },
        "reason_codes": list(summary.reason_codes),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ModeTrackRetainedReplayError(f"{field} must be a JSON object")
    return value


def _sequence(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise ModeTrackRetainedReplayError(f"{field} must be a JSON array")
    return tuple(value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModeTrackRetainedReplayError(f"{field} must be non-empty text")
    return value


def _sha256(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ModeTrackRetainedReplayError(f"{field} must be lowercase SHA-256")
    return text


def _git_sha(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if len(text) != 40 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ModeTrackRetainedReplayError(f"{field} must be a lowercase Git SHA")
    return text


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModeTrackRetainedReplayError(f"{field} must be numeric")
    result = float(value)
    if not result == result or result in {float("inf"), float("-inf")}:
        raise ModeTrackRetainedReplayError(f"{field} must be finite")
    return result


def _positive_float(value: object, *, field: str) -> float:
    result = _finite_float(value, field=field)
    if result <= 0.0:
        raise ModeTrackRetainedReplayError(f"{field} must be positive")
    return result


def _interval(value: object, *, field: str) -> tuple[float, float]:
    values = _sequence(value, field=field)
    if len(values) != 2:
        raise ModeTrackRetainedReplayError(f"{field} must contain two endpoints")
    start = _finite_float(values[0], field=f"{field} start")
    end = _finite_float(values[1], field=f"{field} end")
    if start >= end:
        raise ModeTrackRetainedReplayError(f"{field} must be increasing")
    return start, end


def _nonnegative_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ModeTrackRetainedReplayError(
            f"{field} must be a nonnegative integer"
        )
    return value


def _positive_int(value: object, *, field: str) -> int:
    result = _nonnegative_int(value, field=field)
    if result < 1:
        raise ModeTrackRetainedReplayError(f"{field} must be positive")
    return result


def _canonical_texts(values: tuple[str, ...], *, field: str) -> None:
    if not values or values != tuple(sorted(set(values))):
        raise ModeTrackRetainedReplayError(
            f"{field} must be non-empty, unique, and canonical-sorted"
        )


__all__ = (
    "RETAINED_REPLAY_SCHEMA_VERSION",
    "RETAINED_REPLAY_SUMMARY_SCHEMA_VERSION",
    "ModeTrackRetainedReplayError",
    "ModeTrackRetainedReplaySummary",
    "canonical_retained_mode_track_replay_summary_bytes",
    "replay_retained_mode_track_fixture",
)
