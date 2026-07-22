"""Exact reviewed-packet contract for Pt oracle calibration and replay."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    AUTHORITY_SCOPE as PROMOTION_AUTHORITY_SCOPE,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    EVIDENCE_OWNER as PROMOTION_EVIDENCE_OWNER,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    ORACLE_DETECTION_STATUS,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    READINESS_TIER as PROMOTION_READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    SCHEMA_VERSION as PROMOTION_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_review_bundle import (
    load_verified_review_packet_collection,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER as REVIEW_PACKET_EVIDENCE_OWNER,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    READINESS_TIER as REVIEW_PACKET_READINESS_TIER,
)

PROMOTED_ORACLE_STATUS = "manual_oracle_promoted"
REVIEW_PACKET_ORACLE_STATUS = "pending_human_review"
EXACT_PROVENANCE_STATUS = "exact_review_evidence_bundle_matched"


@dataclass(frozen=True, slots=True)
class LoadedReviewPackets:
    rows: tuple[dict[str, str], ...]
    sha256_by_packet_row_id: Mapping[str, str]


def load_review_packets(paths: Sequence[Path]) -> LoadedReviewPackets:
    """Load manifest-verified current packets and bind rows to exact file hashes."""
    collection = load_verified_review_packet_collection(
        paths,
        require_nonempty=False,
    )
    return LoadedReviewPackets(
        rows=collection.rows,
        sha256_by_packet_row_id=collection.sha256_by_packet_row_id,
    )


def validate_oracle_promotion_binding(
    *,
    oracle_row: Mapping[str, str],
    review_row: Mapping[str, str],
    review_packet_csv_sha256: str,
) -> None:
    """Fail closed unless an oracle row matches the exact reviewed evidence."""
    packet_row_id = _required_text(oracle_row, "packet_row_id")
    expected_promotion_values = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "readiness_tier": PROMOTION_READINESS_TIER,
        "evidence_owner": PROMOTION_EVIDENCE_OWNER,
        "authority_scope": PROMOTION_AUTHORITY_SCOPE,
        "oracle_status": PROMOTED_ORACLE_STATUS,
        "oracle_detection_status": ORACLE_DETECTION_STATUS,
        "provenance_status": EXACT_PROVENANCE_STATUS,
    }
    for field, expected in expected_promotion_values.items():
        observed = _required_text(oracle_row, field)
        if observed != expected:
            raise ValueError(f"{packet_row_id}: oracle {field} must be {expected!r}")

    _required_text(oracle_row, "oracle_packet_id")
    _required_text(oracle_row, "adjudication_basis")
    oracle_call = _required_text(oracle_row, "oracle_call").lower()
    if oracle_call not in {"accept", "reject"}:
        raise ValueError(
            f"{packet_row_id}: oracle oracle_call must be accept or reject"
        )
    human_review_call = _required_text(oracle_row, "human_review_call").lower()
    if human_review_call != oracle_call:
        raise ValueError(
            f"{packet_row_id}: oracle human_review_call must equal oracle_call"
        )

    if _required_text(review_row, "readiness_tier") != REVIEW_PACKET_READINESS_TIER:
        raise ValueError(
            f"{packet_row_id}: review packet readiness_tier must be "
            f"{REVIEW_PACKET_READINESS_TIER!r}"
        )
    if _required_text(review_row, "evidence_owner") != REVIEW_PACKET_EVIDENCE_OWNER:
        raise ValueError(
            f"{packet_row_id}: review packet evidence_owner must be "
            f"{REVIEW_PACKET_EVIDENCE_OWNER!r}"
        )
    if _required_text(review_row, "oracle_status") != REVIEW_PACKET_ORACLE_STATUS:
        raise ValueError(
            f"{packet_row_id}: review packet oracle_status must be "
            f"{REVIEW_PACKET_ORACLE_STATUS!r}"
        )

    observed_packet_sha256 = _normalized_sha256(
        oracle_row.get("review_packet_csv_sha256"),
        label=f"{packet_row_id}: review_packet_csv_sha256",
    )
    expected_packet_sha256 = _normalized_sha256(
        review_packet_csv_sha256,
        label=f"{packet_row_id}: actual review packet SHA-256",
    )
    if observed_packet_sha256 != expected_packet_sha256:
        raise ValueError(
            f"{packet_row_id}: review_packet_csv_sha256 does not match the "
            "actual review packet"
        )
    _normalized_sha256(
        oracle_row.get("blind_overlay_summary_json_sha256"),
        label=f"{packet_row_id}: blind_overlay_summary_json_sha256",
    )

    matching_fields = {
        "candidate_id": "candidate_id",
        "sample_stem": "sample_stem",
        "review_packet_schema_version": "schema_version",
        "review_packet_readiness_tier": "readiness_tier",
        "review_packet_oracle_status": "oracle_status",
        "review_packet_combined_pt_pattern_status": ("combined_pt_pattern_status"),
    }
    for oracle_field, review_field in matching_fields.items():
        oracle_value = _required_text(oracle_row, oracle_field)
        review_value = _required_text(review_row, review_field)
        if oracle_value != review_value:
            raise ValueError(
                f"{packet_row_id}: oracle {oracle_field} does not match "
                f"review packet {review_field}"
            )
    for field in ("base_mz", "base_rt_apex_min"):
        oracle_coordinate = _required_finite_float(oracle_row, field)
        review_coordinate = _required_finite_float(review_row, field)
        if oracle_coordinate != review_coordinate:
            raise ValueError(
                f"{packet_row_id}: oracle {field} does not match review packet {field}"
            )


def _normalized_sha256(value: object, *, label: str) -> str:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 64 or any(
        character not in "0123456789ABCDEF" for character in normalized
    ):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return normalized


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value


def _required_finite_float(row: Mapping[str, str], key: str) -> float:
    value = _required_text(row, key)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{key} must be finite")
    return parsed
