from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import (
    read_tsv_required,
    split_semicolon_labels,
)

from .oracle import ManualOracleRow

CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "apex_rt",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    "reason",
)
REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "identity_decision",
    "identity_reason",
    "row_flags",
)
CANDIDATE_GATE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "candidate_gate_status",
    "recommended_action",
    "challenge_blockers",
    "dependent_context",
)


@dataclass(frozen=True)
class MachineMatch:
    evidence_source: str
    source_role: str
    source_artifact: Path
    source_artifact_sha256: str
    source_row_id: str
    row: Mapping[str, str]
    sample_level: bool

    @property
    def machine_current_label(self) -> str:
        if self.evidence_source == "alignment_cells":
            return self.row.get("status", "not_available") or "not_available"
        if self.evidence_source == "candidate_gate_sidecar":
            return (
                self.row.get("candidate_gate_status", "not_available")
                or "not_available"
            )
        return self.row.get("identity_decision", "not_available") or "not_available"

    @property
    def machine_reason(self) -> str:
        if self.evidence_source == "alignment_cells":
            return self.row.get("reason", "")
        if self.evidence_source == "candidate_gate_sidecar":
            return self.row.get("recommended_action", "")
        return self.row.get("identity_reason", "")

    @property
    def machine_blockers(self) -> tuple[str, ...]:
        if self.evidence_source == "candidate_gate_sidecar":
            return tuple(split_semicolon_labels(self.row.get("challenge_blockers", "")))
        if self.evidence_source == "alignment_review":
            return tuple(split_semicolon_labels(self.row.get("row_flags", "")))
        reason = self.row.get("reason", "")
        return (reason.replace(" ", "_"),) if reason else ()


def load_machine_matches(
    *,
    oracle_rows: Sequence[ManualOracleRow],
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    candidate_gate_tsv: Path | None = None,
) -> dict[str, tuple[MachineMatch, ...]]:
    review_hash = _sha256_file(alignment_review_tsv)
    cell_hash = _sha256_file(alignment_cells_tsv)
    review_rows = read_tsv_required(alignment_review_tsv, REVIEW_REQUIRED_COLUMNS)
    cell_rows = read_tsv_required(alignment_cells_tsv, CELL_REQUIRED_COLUMNS)
    review_by_family = _index_family_context(
        rows=review_rows,
        path=alignment_review_tsv,
        sha256=review_hash,
        evidence_source="alignment_review",
        source_role="selected_peak",
    )
    cells_by_key = _index_cells(
        rows=cell_rows,
        path=alignment_cells_tsv,
        sha256=cell_hash,
    )
    candidate_by_family: dict[str, tuple[MachineMatch, ...]] = {}
    if candidate_gate_tsv is not None:
        gate_hash = _sha256_file(candidate_gate_tsv)
        candidate_rows = read_tsv_required(
            candidate_gate_tsv,
            CANDIDATE_GATE_REQUIRED_COLUMNS,
        )
        candidate_by_family = _index_family_context(
            rows=candidate_rows,
            path=candidate_gate_tsv,
            sha256=gate_hash,
            evidence_source="candidate_gate_sidecar",
            source_role="candidate_gate_family_context",
        )

    matches_by_oracle: dict[str, tuple[MachineMatch, ...]] = {}
    for oracle in oracle_rows:
        if oracle.is_sentinel:
            matches_by_oracle[oracle.oracle_row_id] = ()
            continue
        matches: list[MachineMatch] = []
        matches.extend(
            review_by_family.get(oracle.feature_family_id, ()),
        )
        matches.extend(
            cells_by_key.get((oracle.feature_family_id, oracle.sample_id), ()),
        )
        matches.extend(
            candidate_by_family.get(oracle.feature_family_id, ()),
        )
        matches_by_oracle[oracle.oracle_row_id] = tuple(matches)
    return matches_by_oracle


def sample_level_match_status(matches: Sequence[MachineMatch]) -> str:
    sample_matches = [match for match in matches if match.sample_level]
    if not sample_matches:
        return "no_match"
    if len(sample_matches) == 1:
        return "single_match"
    return "ambiguous_multiple_matches"


def _index_cells(
    *,
    rows: Sequence[Mapping[str, str]],
    path: Path,
    sha256: str,
) -> dict[tuple[str, str], tuple[MachineMatch, ...]]:
    grouped: dict[tuple[str, str], list[MachineMatch]] = {}
    for index, row in enumerate(rows, start=1):
        key = (row["feature_family_id"], row["sample_stem"])
        grouped.setdefault(key, []).append(
            MachineMatch(
                evidence_source="alignment_cells",
                source_role="rescued_cell",
                source_artifact=path,
                source_artifact_sha256=sha256,
                source_row_id=f"{path.name}:{index}",
                row=row,
                sample_level=True,
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _index_family_context(
    *,
    rows: Sequence[Mapping[str, str]],
    path: Path,
    sha256: str,
    evidence_source: str,
    source_role: str,
) -> dict[str, tuple[MachineMatch, ...]]:
    grouped: dict[str, list[MachineMatch]] = {}
    for index, row in enumerate(rows, start=1):
        grouped.setdefault(row["feature_family_id"], []).append(
            MachineMatch(
                evidence_source=evidence_source,
                source_role=source_role,
                source_artifact=path,
                source_artifact_sha256=sha256,
                source_row_id=f"{path.name}:{index}",
                row=row,
                sample_level=False,
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
