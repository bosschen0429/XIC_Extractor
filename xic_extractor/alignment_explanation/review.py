"""Small, presentation-only cell facts; no artifact graph or signal arrays."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
    from xic_extractor.alignment.cell_observation.observations import (
        FinalGroupCellObservation,
    )

# Per-sample retained review facts. These bits summarize recorded decisions;
# they do not choose peaks, assign groups, or authorize Matrix values.
OBSERVED, ATTENTION, EXCLUDED, NULL_CELL, ZERO_CELL = 1, 2, 4, 8, 16
ORIGIN_FLAGS = {
    "backfill_support": 32,
    "discovery_support": 128,
}
SUPPORT_ORIGINS = frozenset((*ORIGIN_FLAGS, "unavailable"))


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


def cell_attention(variant: Any, value: Any, risk_flagged: bool) -> str | None:
    """Why a recorded cell needs attention, from its variant, value and risks."""
    if risk_flagged:
        return "accepted_membership_risk"
    if variant == "unresolved" and value is None:
        return "unresolved"
    return None


def attention_reason(kind: str, fact: dict[str, Any]) -> str | None:
    """One recorded-fact predicate for selection and its displayed explanation."""
    if kind == "cell_decisions":
        return cell_attention(
            fact.get("variant"),
            fact.get("value", fact.get("quantitative_value")),
            any(risk.get("risk_flags") for risk in fact.get("membership_risks", [])),
        )
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


def review_cell(decision: ProductionCellDecision) -> dict[str, Any]:
    """Project the decision owner and its effective observations without hashing."""
    # Review rows are built only for Untargeted final-group cells.
    evidence = cast("FinalGroupCellObservation", decision.evidence)
    cells = evidence.effective_cells
    stages = {
        cell.peak_observation.capture_stage
        for cell in cells
        if cell.peak_observation is not None and cell.method_result == "SUPPORT"
    }
    origin = (
        "backfill_support"
        if "backfill_observation" in stages or "method_backfill" in stages
        else "discovery_support"
        if "initial_capture" in stages
        else "unavailable"
    )
    qualities = {}
    for cell in cells:
        observation = cell.peak_observation
        if observation is None:
            continue
        quality = observation.integration.signal_quality
        qualities[observation.physical_peak_key] = {
            "observation_id": observation.physical_peak_key,
            **(asdict(quality) if quality is not None else {"status": "not_recorded"}),
        }
    return {
        "sample_key": (decision.subject.canonical_sample.sample_name),
        "variant": decision.variant,
        "value": decision.value,
        "apex_rt_min": (
            decision.selected_integration.rt_apex_min
            if decision.selected_integration is not None
            else None
        ),
        "source": decision.source,
        "evidence_states": evidence.evidence_states,
        "reason_codes": sorted(
            {reason for cell in cells for reason in cell.reason_codes}
        ),
        "support_origin": origin,
        "signal_quality": [qualities[key] for key in sorted(qualities)],
        "observation_ids": sorted(
            {
                cell.physical_peak_key
                for cell in cells
                if cell.physical_peak_key is not None
            }
        ),
    }
