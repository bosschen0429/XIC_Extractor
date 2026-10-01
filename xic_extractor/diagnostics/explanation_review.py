"""Small, presentation-only cell facts; no artifact graph or signal arrays."""

from __future__ import annotations

import json
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

REVIEW_SCHEMA_VERSION = "alignment_explanation_review"


def pack_cells(cells: Sequence[Mapping[str, Any]], samples: Mapping[str, int]) -> bytes:
    """Store a row's values once, with sample ordinals and shared field names."""
    values = [
        [
            samples[cell["canonical_sample_identity_id"]],
            cell["variant"],
            cell.get("value"),
            cell.get("source"),
            cell.get("evidence_states", []),
            cell.get("reason_codes", []),
            cell.get("support_origin", "unavailable"),
            cell.get("observation_ids", []),
        ]
        for cell in cells
    ]
    return zlib.compress(
        json.dumps(values, separators=(",", ":"), allow_nan=False).encode(), 1
    )


def unpack_cells(
    payload: bytes,
    *,
    row_id: str,
    group_id: str,
    sample_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Diagnostic row positions identify display facts, not scientific decisions."""
    values = json.loads(zlib.decompress(payload))
    result = {}
    for position, cell in enumerate(values):
        sample, variant, value, source, states, reasons, origin, observations = cell
        if (
            isinstance(sample, bool)
            or not isinstance(sample, int)
            or not 0 <= sample < len(sample_ids)
        ):
            raise ValueError("review cell has an invalid sample ordinal")
        identity = f"review-cell:{row_id}:{position}"
        result[identity] = {
            "review_cell_id": identity,
            "row_key_id": row_id,
            "group_hypothesis_id": group_id,
            "canonical_sample_identity_id": sample_ids[sample],
            "variant": variant,
            "value": value,
            "source": source,
            "evidence_states": states,
            "reason_codes": reasons,
            "support_origin": origin,
            "observation_ids": observations,
        }
    return result


def review_cell(decision: Any) -> dict[str, Any]:
    """Project the decision owner and its effective observations without hashing."""
    evidence = decision.evidence
    cells = evidence.effective_cells
    stages = {
        cell.peak_observation.capture_stage
        for cell in cells
        if cell.peak_observation is not None and cell.content.method_result == "SUPPORT"
    }
    origin = (
        "backfill_support"
        if "backfill_observation" in stages or "method_backfill" in stages
        else "reacquired_support"
        if "reacquired_capture" in stages
        else "discovery_support"
        if "initial_capture" in stages
        else "unavailable"
    )
    return {
        "canonical_sample_identity_id": (
            decision.subject.canonical_sample.identity.sample_identity_id
        ),
        "variant": decision.variant,
        "value": getattr(decision, "value", None),
        "source": getattr(decision, "source", None),
        "evidence_states": evidence.evidence_states,
        "reason_codes": sorted(
            {reason for cell in cells for reason in cell.content.reason_codes}
        ),
        "support_origin": origin,
        "observation_ids": sorted(
            {
                cell.content.peak_observation_content_id
                for cell in cells
                if cell.content.peak_observation_content_id is not None
            }
        ),
    }


def review_record(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The retained review facts also define conversion of existing full reports."""
    if kind == "method_assessments":
        names = (
            "canonical_sample_identity_id",
            "provider_id",
            "method_id",
            "method_version",
            "detector_invocation_id",
            "technical_state",
            "method_result",
            "reason_codes",
            "candidate_peak_content_ids",
            "method_subject_id",
        )
        return {name: payload[name] for name in names if name in payload}
    return {
        key: value for key, value in payload.items() if key != "processing_request_id"
    }
