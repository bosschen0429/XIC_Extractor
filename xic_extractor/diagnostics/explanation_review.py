"""Small, presentation-only cell facts; no artifact graph or signal arrays."""

from __future__ import annotations

import json
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

REVIEW_SCHEMA_VERSION = "alignment_explanation_review"

# Per-sample retained review facts. These bits summarize recorded decisions;
# they do not choose peaks, assign groups, or authorize Matrix values.
OBSERVED, ATTENTION, EXCLUDED, NULL_CELL, ZERO_CELL = 1, 2, 4, 8, 16
ORIGIN_FLAGS = {
    "backfill_support": 32,
    "reacquired_support": 64,
    "discovery_support": 128,
}
SELECTION_FLAGS = (
    OBSERVED,
    ATTENTION,
    EXCLUDED,
    NULL_CELL,
    ZERO_CELL,
    *ORIGIN_FLAGS.values(),
)


def attention_reason(kind: str, fact: dict[str, Any]) -> str | None:
    """One recorded-fact predicate for selection and its displayed explanation."""
    if kind == "cell_decisions":
        if (
            fact.get("variant") == "unresolved"
            and fact.get("value", fact.get("quantitative_value")) is None
        ):
            return "unresolved"
    elif kind == "method_assessments" and fact.get("method_result") == "HOLD":
        return "HOLD"
    elif kind == "initial_edges":
        evidence = fact.get("initial_evidence", {})
        failure = evidence.get("failure", evidence.get("reason"))
        if failure in ("ambiguous_owner", "identity_conflict"):
            return failure
        if fact.get("disposition") == "review_only":
            return "review_only"
    return None


def selection_sample_facts(
    kind, identifier, gid, keys, candidates, records, links, cells, observed
):
    """Reduce recorded facts once, preserving sample attribution for later scopes."""
    group = records.get("final_groups", {}).get(gid, {})
    initial = (
        ATTENTION if group.get("disposition") in ("provisional", "unresolved") else 0
    )
    flags = {sid: initial for _, sid in keys}
    for cid, sid in keys:
        if candidates[cid, sid].get("discovery_candidate_state") == "excluded":
            flags[sid] |= EXCLUDED
        for record_kind, rid in links.get((cid, sid), ()):
            if record_kind not in ("method_assessments", "initial_edges", "exclusions"):
                continue
            fact = records.get(record_kind, {}).get(rid, {})
            if fact.get("canonical_sample_identity_id", fact.get("sample_id")) not in (
                None,
                sid,
            ):
                continue
            if fact.get("group_hypothesis_id") not in (None, gid):
                continue
            if kind == "row" and fact.get("row_key_id") not in (None, identifier):
                continue
            if attention_reason(record_kind, fact) is not None:
                flags[sid] |= ATTENTION
            if record_kind == "exclusions":
                flags[sid] |= EXCLUDED
    for cell in cells:
        sid = cell["canonical_sample_identity_id"]
        flag = flags.setdefault(sid, initial)
        value = cell.get("value")
        flag |= NULL_CELL if value is None else ZERO_CELL if value == 0 else 0
        if attention_reason("cell_decisions", cell) is not None:
            flag |= ATTENTION
        if cell.get("observation_ids"):
            flag |= OBSERVED
        flag = (flag & ~sum(ORIGIN_FLAGS.values())) | ORIGIN_FLAGS.get(
            cell.get("support_origin"), 0
        )
        flags[sid] = flag
    observed = set(observed)
    observed.update(
        member["canonical_sample_identity_id"]
        for member in group.get("member_subjects", [])
        if member.get("physical_observation_ids")
    )
    for sid in observed & flags.keys():
        flags[sid] |= OBSERVED
    return flags


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
