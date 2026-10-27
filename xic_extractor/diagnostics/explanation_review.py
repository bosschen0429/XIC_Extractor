"""Small, presentation-only cell facts; no artifact graph or signal arrays."""

from __future__ import annotations

import json
import math
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict
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


def validate_membership_risks(risks: object) -> None:
    """Validate declared optional facts at the persisted JSON boundary."""

    def strings(value: object) -> bool:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)

    def owner(value: object) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 3
            and isinstance(value[0], str)
            and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value[1:]
            )
        )

    def owners(value: object) -> bool:
        return isinstance(value, list) and all(owner(item) for item in value)

    def partition(value: object) -> bool:
        return isinstance(value, list) and all(owners(group) for group in value)

    if not isinstance(risks, list):
        raise ValueError("membership risks must be a list")
    for risk in risks:
        if not isinstance(risk, dict):
            raise ValueError("membership risk must be an object")
        valid = (
            all(
                isinstance(risk.get(key), str)
                for key in ("status", "phase", "rt_basis", "evidence_basis")
            )
            and owners(risk.get("selected_owners"))
            and all(
                strings(risk.get(key)) for key in ("soft_contradictions", "risk_flags")
            )
        )
        alternative = risk.get("alternative_partition")
        equivalents = risk.get("equivalent_alternatives")
        valid = (
            valid
            and (alternative is None or partition(alternative))
            and isinstance(equivalents, list)
            and all(partition(value) for value in equivalents)
        )
        for key in (
            "selected_cost",
            "alternative_cost",
            "cost_gap",
            "selected_peer_fit",
            "alternative_peer_fit",
            "peer_fit_gap",
        ):
            value = risk.get(key)
            if value is None and key != "selected_cost":
                continue
            valid = (
                valid
                and not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
            )
        estimate = risk.get("rt_estimate")
        if estimate is not None:
            if not isinstance(estimate, dict):
                raise ValueError("membership risk RT estimate must be an object")
            valid = valid and isinstance(estimate.get("method"), str)
            for key in (
                "rt_min",
                "residual_spread_min",
                "nearest_anchor_distance_min",
                "outside_anchor_range_min",
            ):
                value = estimate.get(key)
                if value is None and key != "rt_min":
                    continue
                valid = valid and (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(value)
                )
            count = estimate.get("anchor_count")
            valid = valid and (count is None or (type(count) is int and count >= 0))
            bounds = estimate.get("anchor_range_min")
            valid = valid and (
                bounds is None
                or (
                    isinstance(bounds, list)
                    and len(bounds) == 2
                    and all(
                        not isinstance(v, bool)
                        and isinstance(v, (int, float))
                        and math.isfinite(v)
                        for v in bounds
                    )
                    and bounds[0] <= bounds[1]
                )
            )
            valid = valid and (
                estimate.get("fit_status") is None
                or isinstance(estimate["fit_status"], str)
            )
        if not valid:
            raise ValueError("malformed membership risk fields")


def attention_reason(kind: str, fact: dict[str, Any]) -> str | None:
    """One recorded-fact predicate for selection and its displayed explanation."""
    if kind == "cell_decisions":
        if any(risk.get("risk_flags") for risk in fact.get("membership_risks", [])):
            return "accepted_membership_risk"
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
            if fact.get("sample_key", fact.get("sample_id")) not in (
                None,
                sid,
            ):
                continue
            if fact.get("feature_id") not in (None, gid):
                continue
            if kind == "row" and fact.get("feature_id") not in (None, identifier):
                continue
            if attention_reason(record_kind, fact) is not None:
                flags[sid] |= ATTENTION
            if record_kind == "exclusions":
                flags[sid] |= EXCLUDED
    for cell in cells:
        sid = cell["sample_key"]
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
        member["sample_key"]
        for member in group.get("member_subjects", [])
        if member.get("physical_peak_keys")
    )
    for sid in observed & flags.keys():
        flags[sid] |= OBSERVED
    return flags


def pack_cells(cells: Sequence[Mapping[str, Any]], samples: Mapping[str, int]) -> bytes:
    """Store a row's values once, with sample ordinals and shared field names."""
    values = [
        [
            samples[cell["sample_key"]],
            cell["variant"],
            cell.get("value"),
            cell.get("source"),
            cell.get("evidence_states", []),
            cell.get("reason_codes", []),
            cell.get("support_origin", "unavailable"),
            cell.get("observation_ids", []),
            cell.get("signal_quality", []),
            cell.get("membership_risks", []),
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
) -> dict[tuple[str, str], dict[str, Any]]:
    """Locate each cell by its group and canonical sample."""
    values = json.loads(zlib.decompress(payload))
    if not isinstance(values, list):
        raise ValueError("review cells must be an array; rerun Alignment")
    result = {}
    for cell in values:
        if not isinstance(cell, list) or len(cell) != 10:
            raise ValueError(
                "review cell fields do not match the schema; rerun Alignment"
            )
        (
            sample,
            variant,
            value,
            source,
            states,
            reasons,
            origin,
            observations,
            quality,
            risks,
        ) = cell
        validate_membership_risks(risks)
        if (
            isinstance(sample, bool)
            or not isinstance(sample, int)
            or not 0 <= sample < len(sample_ids)
        ):
            raise ValueError("review cell has an invalid sample ordinal")
        key = (group_id, sample_ids[sample])
        if key in result:
            raise ValueError(
                "duplicate review cell for group and sample; rerun Alignment"
            )
        result[key] = {
            "feature_id": group_id,
            "sample_key": sample_ids[sample],
            "variant": variant,
            "value": value,
            "source": source,
            "evidence_states": states,
            "reason_codes": reasons,
            "support_origin": origin,
            "observation_ids": observations,
            "signal_quality": quality,
            "membership_risks": risks,
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
    qualities = {}
    for cell in cells:
        observation = cell.peak_observation
        if observation is None:
            continue
        content = observation.content
        quality = content.integration.signal_quality
        qualities[content.physical_peak_key] = {
            "observation_id": content.physical_peak_key,
            **(asdict(quality) if quality is not None else {"status": "not_recorded"}),
        }
    return {
        "sample_key": (decision.subject.canonical_sample.identity.sample_key),
        "variant": decision.variant,
        "value": getattr(decision, "value", None),
        "source": getattr(decision, "source", None),
        "evidence_states": evidence.evidence_states,
        "reason_codes": sorted(
            {reason for cell in cells for reason in cell.content.reason_codes}
        ),
        "support_origin": origin,
        "signal_quality": [qualities[key] for key in sorted(qualities)],
        "observation_ids": sorted(
            {
                cell.content.physical_peak_key
                for cell in cells
                if cell.content.physical_peak_key is not None
            }
        ),
    }


def review_record(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The retained review facts also define conversion of existing full reports."""
    if kind == "method_assessments":
        names = (
            "sample_key",
            "provider_id",
            "method_id",
            "method_version",
            "detector_invocation_id",
            "technical_state",
            "method_result",
            "reason_codes",
            "candidate_physical_peak_keys",
            "method_subject_id",
        )
        return {name: payload[name] for name in names if name in payload}
    return dict(payload)
