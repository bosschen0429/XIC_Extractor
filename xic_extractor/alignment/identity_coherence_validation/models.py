from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

IDENTITY_COHERENCE_FILES = {
    "requests_tsv": "untargeted_identity_coherence_requests.tsv",
    "decisions_tsv": "untargeted_identity_coherence_decisions.tsv",
    "cell_evidence_tsv": "untargeted_identity_coherence_cell_evidence.tsv",
    "controls_tsv": "untargeted_identity_coherence_controls.tsv",
    "summary_md": "untargeted_identity_coherence_summary.md",
}

VALIDATION_SUMMARY_COLUMNS = (
    "check_name",
    "status",
    "serial_value",
    "process_value",
    "details",
)

# Keep this in sync with controls.py:REQUIRED_MANIFEST_FIELDS plus the optional
# fields read by _entry_from_row(); tests must round-trip proposals through the
# real manifest reader to catch drift.
CONTROL_MANIFEST_COLUMNS = (
    "control_id",
    "control_type",
    "control_name",
    "expected_mapping_status",
    "control_expected_behavior",
    "fragment_observation_mode",
    "precursor_tolerance_ppm",
    "product_tolerance_ppm",
    "cid_observed_loss_tolerance_ppm",
    "rt_tolerance_sec",
    "required_failure_reason_when_missed",
    "decision_id",
    "identity_family_id",
    "seed_candidate_id",
    "decoy_generation_method",
    "decoy_source_request_id",
    "decoy_fragment_tags",
    "positive_control_target_name",
    "positive_control_target_mz",
    "positive_control_target_rt_sec",
    "positive_control_mapping_error_ppm",
    "positive_control_mapping_delta_rt_sec",
    "control_notes",
)

ACCEPTANCE_SUMMARY_COLUMNS = (
    "criterion",
    "status",
    "evidence",
    "details",
)

V04_ACCEPTANCE_PASS_PREFIX = (
    "PASS identity_coherence_v04_acceptance "
    "scope=method_review_only not_full_cohort_ready"
)

SIDECAR_PARITY_CHECKS = (
    "requests_tsv_exact",
    "decisions_tsv_exact",
    "cell_evidence_tsv_exact",
    "controls_tsv_parity_only",
    "summary_md_presence",
)


@dataclass(frozen=True)
class DiagnosticBundle:
    requests_tsv: Path
    decisions_tsv: Path
    cell_evidence_tsv: Path
    controls_tsv: Path
    summary_md: Path


@dataclass(frozen=True)
class TsvRows:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ValidationRow:
    check_name: str
    status: str
    serial_value: str
    process_value: str
    details: str


@dataclass(frozen=True)
class RunMetadata:
    mode: str
    command_line: str
    output_dir: Path
    returncode: int


@dataclass(frozen=True)
class ValidationResult:
    rows: tuple[ValidationRow, ...]
    run_metadata: tuple[RunMetadata, ...] = ()

    @property
    def failed_count(self) -> int:
        return sum(1 for row in self.rows if row.status == "fail")


@dataclass(frozen=True)
class AcceptanceRow:
    criterion: str
    status: str
    evidence: str
    details: str


@dataclass(frozen=True)
class AcceptanceReport:
    rows: tuple[AcceptanceRow, ...]

    @property
    def accepted(self) -> bool:
        rows = {row.criterion: row for row in self.rows}
        final = rows.get("v04_acceptance")
        return final is not None and final.status == "pass"


class CommandRunner(Protocol):
    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        ...
