from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .models import SeedGateResult
from .row_evaluator import IdentityCoherenceRowResult


@dataclass(frozen=True)
class IdentityCoherenceOutputRecord:
    seed_gate: SeedGateResult
    row_result: IdentityCoherenceRowResult


@dataclass(frozen=True)
class IdentityCoherenceOutputContext:
    command: str
    mode: str
    input_source: str
    input_hashes: tuple[tuple[str, str], ...] = ()
    control_manifest_path: str = "not_provided"
    raw_xic_request_count: int | None = None
    xic_point_count: int | None = None
    projected_full_cohort_identity_request_count: int | None = None
    max_projected_full_cohort_identity_xic_requests: int | None = None
    max_infrastructure_blocked_fraction: float = 0.05
    firewall_fixture_status: str = "not_assessed"
    spawn_payload_smoke_status: str = "not_assessed"

    def __post_init__(self) -> None:
        blocked_fraction = _validate_nonnegative_float(
            self.max_infrastructure_blocked_fraction,
            "max_infrastructure_blocked_fraction",
        )
        if blocked_fraction > 1.0:
            raise ValueError("max_infrastructure_blocked_fraction must be <= 1")
        object.__setattr__(
            self,
            "max_infrastructure_blocked_fraction",
            blocked_fraction,
        )
        for field_name in (
            "raw_xic_request_count",
            "xic_point_count",
            "projected_full_cohort_identity_request_count",
            "max_projected_full_cohort_identity_xic_requests",
        ):
            object.__setattr__(
                self,
                field_name,
                _validate_optional_nonnegative_int(
                    getattr(self, field_name),
                    field_name,
                ),
            )


@dataclass(frozen=True)
class IdentityCoherenceOutputPaths:
    requests_tsv: Path
    decisions_tsv: Path
    cell_evidence_tsv: Path
    controls_tsv: Path
    summary_md: Path


def _validate_nonnegative_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be nonnegative")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return numeric


def _validate_optional_nonnegative_int(
    value: object,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return value
