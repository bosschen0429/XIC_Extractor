"""Review an injection timeline: the short-method pre-flag and SLD comparison."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.injection_timeline import Injection, acquisition_key

if TYPE_CHECKING:
    from xic_extractor.raw_sequence import ScheduledInjection


@dataclass(frozen=True)
class PerturbationPolicy:
    max_previous_run_min: float = 20.0
    max_previous_run_fraction: float = 0.5
    max_idle_min: float = 5.0


def validate_perturbation_policy(model: PerturbationPolicy) -> None:
    values = (
        model.max_previous_run_min,
        model.max_previous_run_fraction,
        model.max_idle_min,
    )
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError("perturbation thresholds must be finite and nonnegative")
    if not 0 < model.max_previous_run_fraction < 1:
        raise ValueError("max_previous_run_fraction must be between 0 and 1")


def possibly_perturbed(row: Injection, policy: PerturbationPolicy) -> bool:
    """A short run shortly before a long one may leave the column unsettled."""
    previous_run = row.previous_run_length_min
    idle = row.idle_min
    return (
        previous_run is not None
        and idle is not None
        and 0 <= idle <= policy.max_idle_min
        and previous_run <= policy.max_previous_run_min
        and previous_run <= row.run_length_min * policy.max_previous_run_fraction
    )


@dataclass(frozen=True)
class SequenceComparison:
    scheduled: ScheduledInjection
    status: str
    injection_orders: tuple[int, ...]


def compare_sequence(
    scheduled: Sequence[ScheduledInjection],
    timeline: Sequence[Injection],
) -> tuple[SequenceComparison, ...]:
    """Compare exact vendor filenames; ambiguous repeat acquisitions stay visible.

    Sequence segments remain separate provenance records and never determine
    chronology. No fuzzy basename matching across acquisition directories.
    """
    by_source: dict[str, list[int]] = {}
    for row in timeline:
        by_source.setdefault(acquisition_key(row.header.acquisition_source), []).append(
            row.injection_order
        )
    comparisons = []
    for entry in scheduled:
        orders = tuple(by_source.get(acquisition_key(entry.acquisition_source), ()))
        status = (
            "unconfigured"
            if not entry.acquisition_source
            else "missing_raw"
            if not orders
            else "matched"
            if len(orders) == 1
            else "ambiguous"
        )
        comparisons.append(SequenceComparison(entry, status, orders))
    return tuple(comparisons)
