"""Small, presentation-only cell facts; no artifact graph or signal arrays."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
    from xic_extractor.alignment.cell_observation.observations import (
        FinalGroupCellObservation,
    )

SUPPORT_ORIGINS = frozenset(("detected", "backfilled", "unavailable"))


def competition_peers(
    risks: Iterable[Mapping[str, Any]], sample_key: str
) -> list[list[list[Any]]]:
    """The peers each recorded alternative would give one cell's peak.

    The peak is its full owner key (sample, measured m/z, native apex RT), so
    another peak of the same sample is not mistaken for it. An empty entry
    means the alternative leaves the peak out of this group. Alternatives are
    compared configurations, not published features.
    """
    groups: list[list[list[Any]]] = []
    for risk in risks:
        if not {"accepted_competition", "backfill_competition"}.intersection(
            risk["risk_flags"]
        ):
            continue
        selected = [list(owner) for owner in risk["selected_owners"]]
        own = [owner for owner in selected if owner[0] == sample_key]
        if len(own) != 1:
            continue
        peak = own[0]
        alternative = risk.get("alternative_partition")
        for partition in [
            *([alternative] if alternative is not None else []),
            *risk.get("equivalent_alternatives", []),
        ]:
            members = [[list(owner) for owner in group] for group in partition]
            group = next((group for group in members if peak in group), [peak])
            if sorted(group) == sorted(selected):
                continue
            peers = [owner for owner in group if owner != peak]
            if peers not in groups:
                groups.append(peers)
    return groups


def group_attention(disposition: Any) -> bool:
    """A group whose recorded disposition is not settled needs attention."""
    return disposition in ("provisional", "unresolved")


def cell_attention(variant: Any, value: Any, risk_flagged: bool) -> str | None:
    """Why a recorded cell needs attention, from its variant, value and risks."""
    if risk_flagged:
        return "accepted_membership_risk"
    if variant == "unresolved" and value is None:
        return "unresolved"
    return None


def review_cell(decision: ProductionCellDecision) -> dict[str, Any]:
    """Project the decision owner and its effective observations without hashing."""
    evidence = cast("FinalGroupCellObservation", decision.evidence)
    cells = evidence.effective_cells
    qualities = {}
    for cell in cells:
        observation = cell.peak_observation
        if observation is None:
            continue
        integration = observation.integration
        quality = integration.signal_quality if integration is not None else None
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
        "support_origin": (
            decision.origin
            if decision.value is not None and decision.origin is not None
            else "unavailable"
        ),
        "signal_quality": [qualities[key] for key in sorted(qualities)],
        "observation_ids": sorted(
            {
                cell.physical_peak_key
                for cell in cells
                if cell.physical_peak_key is not None
            }
        ),
    }
