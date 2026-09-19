from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    BackfillEvidenceOwnerBindings,
)
from xic_extractor.alignment.backfill_evidence_tsv import (
    iter_owner_bound_backfill_evidence_tsv,
)
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.provisional_matrix import (
    PROVISIONAL_PROVENANCE_COLUMNS,
    PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.tsv_writer import ALIGNMENT_REVIEW_COLUMNS
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
)
from xic_extractor.extraction.provisional_run_manifest import (
    PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
    validate_provisional_run_manifest,
)
from xic_extractor.tabular_io import validate_delimited_header, write_tsv

LEDGER_SCHEMA_VERSION: Final = "backfill_decision_ledger"
SUMMARY_SCHEMA_VERSION: Final = "backfill_decision_summary"
VALIDATION_STATUS: Final = "diagnostic_only"

_LEDGER_COLUMNS: Final = (
    "schema_version",
    "record_type",
    "diagnostic_layers",
    "claim_status",
    "sample_stem",
    "physical_ms1_feature_id",
    "physical_ms1_identity_status",
    "candidate_id",
    "best_ms2_scan_id",
    "seed_scan_ids",
    "scientific_hypothesis_status",
    "owner_ids",
    "owner_projection_refs",
    "assignment_role",
    "owner_assignment_status",
    "group_hypothesis_id",
    "group_status",
    "consolidation_state",
    "consolidation_winner_group_hypothesis_id",
    "earliest_settlement_stage",
    "logical_backfill_request_id",
    "request_equivalence_class_id",
    "request_identity_status",
    "display_request_proxy_count",
    "display_request_proxy_duplicate_count",
    "batch_cost",
    "raw_call_cost",
    "point_cost",
    "cost_status",
)

_CONSOLIDATION_STATES: Final = frozenset(
    {"not_consolidated", "primary_loser", "primary_winner"}
)

_REVIEW_COLUMNS: Final = ALIGNMENT_REVIEW_COLUMNS
_BACKFILL_COLUMNS: Final = ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS
_PROVENANCE_COLUMNS: Final = PROVISIONAL_PROVENANCE_COLUMNS


@dataclass(frozen=True, slots=True)
class BackfillDecisionLedgerOutputs:
    ledger_tsv: Path
    summary_json: Path
    summary_markdown: Path
    summary: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _ReviewGroup:
    feature_family_id: str
    group_hypothesis_id: str
    public_family_id: str
    owner_projection_refs: str
    event_member_count: int
    consolidation_state: str
    consolidation_winner_group_hypothesis_id: str
    quantifiable_rescue_count: int
    review_rescue_count: int


@dataclass(frozen=True, slots=True)
class _ReviewCensus:
    groups: Mapping[str, _ReviewGroup]
    consolidation_counts: Mapping[str, int]
    loser_row_with_rescue_count: int
    loser_quantifiable_rescue_count: int
    loser_review_rescue_count: int


@dataclass(frozen=True, slots=True)
class _BackfillCensus:
    row_count: int
    candidate_group_ids: Mapping[tuple[str, str], frozenset[str]]


@dataclass(frozen=True, slots=True)
class _RequestProxyCensus:
    row_count: int
    projected_request_count: int
    projected_duplicate_count: int
    per_group_count: Mapping[str, int]
    per_group_duplicate_count: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _TimingCensus:
    schema_status: str
    assignment_count: int
    owner_count: int
    backfill_scope: str
    input_feature_count: int
    backfill_feature_count: int
    skipped_feature_count: int
    backfill_request_target_count: int
    backfill_extract_request_count: int
    backfill_extract_xic_count: int
    backfill_batch_count: int
    backfill_raw_call_count: int
    backfill_point_count: int
    backfill_elapsed_sec: float
    pre_backfill_consolidation_observed: bool


@dataclass(frozen=True, slots=True)
class _ManifestEvidence:
    schema_version: str
    rehydration_status: str


@dataclass(frozen=True, slots=True)
class _CandidateCensus:
    summary: Mapping[str, int | float | str]
    candidate_samples: Mapping[str, str]
    sample_stems: tuple[str, ...]


def build_backfill_decision_ledger(
    *,
    artifact_root: Path,
    output_dir: Path,
    expected_code_revision: str,
) -> BackfillDecisionLedgerOutputs:
    artifact_root = artifact_root.resolve()
    output_dir = output_dir.resolve()
    manifest_evidence = _read_manifest_evidence(
        _required_file(artifact_root / "run_manifest.json"),
        artifact_root=artifact_root,
        expected_code_revision=expected_code_revision,
    )

    batch_index, batch = _read_discovery_batch(artifact_root)
    candidate_census = _read_candidate_census(
        artifact_root=artifact_root,
        batch_index=batch_index,
        batch=batch,
    )

    alignment_dir = artifact_root / "workflow" / "alignment"
    review = _read_review_groups(_required_file(alignment_dir / "alignment_review.tsv"))
    backfill = _read_backfill_evidence(
        _required_file(alignment_dir / "alignment_backfill_cell_evidence.tsv"),
        known_group_ids=frozenset(review.groups),
        group_feature_family_ids={
            group_id: group.feature_family_id
            for group_id, group in review.groups.items()
        },
        group_public_family_ids={
            group_id: group.public_family_id
            for group_id, group in review.groups.items()
        },
        candidate_samples=candidate_census.candidate_samples,
        known_sample_stems=frozenset(candidate_census.sample_stems),
    )
    request_proxy = _read_request_proxy(
        _required_file(alignment_dir / "provisional_cell_provenance.tsv"),
        known_group_ids=frozenset(review.groups),
        known_sample_stems=frozenset(candidate_census.sample_stems),
    )
    heartbeat = _read_json_object(_required_file(artifact_root / "heartbeat.json"))
    timing = _read_timing(
        _required_file(artifact_root / "timing.json"),
        expected_sample_stems=candidate_census.sample_stems,
    )
    if timing.backfill_feature_count != len(review.groups):
        raise ValueError(
            "Backfill full-audit feature count does not match alignment review "
            f"group universe: timing={timing.backfill_feature_count}; "
            f"review={len(review.groups)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_tsv = output_dir / "backfill_decision_ledger.tsv"
    _write_ledger(
        ledger_tsv,
        artifact_root=artifact_root,
        batch_index=batch_index,
        batch=batch,
        review=review,
        backfill=backfill,
        request_proxy=request_proxy,
        timing=timing,
    )
    summary = _summary(
        artifact_root=artifact_root,
        expected_code_revision=expected_code_revision,
        manifest_evidence=manifest_evidence,
        heartbeat=heartbeat,
        candidate_census=candidate_census.summary,
        review=review,
        backfill=backfill,
        request_proxy=request_proxy,
        timing=timing,
    )
    summary_json = output_dir / "backfill_decision_summary.json"
    _write_text_atomic(
        summary_json,
        json.dumps(summary, allow_nan=False, indent=2, sort_keys=True) + "\n",
    )
    summary_markdown = output_dir / "backfill_decision_summary.md"
    _write_text_atomic(summary_markdown, _summary_markdown(summary))
    return BackfillDecisionLedgerOutputs(
        ledger_tsv=ledger_tsv,
        summary_json=summary_json,
        summary_markdown=summary_markdown,
        summary=summary,
    )


def _read_discovery_batch(
    artifact_root: Path,
) -> tuple[Path, DiscoveryBatchInput]:
    batch_index = _required_file(
        artifact_root / "workflow" / "discovery" / "discovery_batch_index.csv"
    )
    return batch_index, read_discovery_batch_index(batch_index)


def _iter_owner_bound_candidates(
    *,
    artifact_root: Path,
    batch_index: Path,
    batch: DiscoveryBatchInput,
) -> Iterator[DiscoveryCandidate]:
    for sample_stem in batch.sample_order:
        receipt = batch.candidate_receipts.get(sample_stem)
        if receipt is None:
            raise ValueError(
                f"{batch_index}: sample {sample_stem!r} has no owner receipt"
            )
        candidate_path = _candidate_artifact_path(
            artifact_root=artifact_root,
            batch=batch,
            sample_stem=sample_stem,
        )
        yield from read_owner_bound_discovery_candidates_csv(
            candidate_path,
            receipt=receipt,
        )


def _candidate_artifact_path(
    *,
    artifact_root: Path,
    batch: DiscoveryBatchInput,
    sample_stem: str,
) -> Path:
    declared_path = batch.candidate_csvs[sample_stem]
    if declared_path.is_file():
        return declared_path

    # The retained Backfill bundle was atomically renamed from its exact
    # ``<artifact>.attempt`` staging directory after the batch index was
    # written. Relocate only that mechanically exact stale locator; the
    # Discovery receipt still closes the candidate bytes and typed payload.
    attempt_root = artifact_root.with_name(f"{artifact_root.name}.attempt")
    attempt_path = (
        attempt_root
        / "workflow"
        / "discovery"
        / sample_stem
        / "discovery_candidates.csv"
    )
    relocated_path = (
        artifact_root
        / "workflow"
        / "discovery"
        / sample_stem
        / "discovery_candidates.csv"
    )
    if declared_path.resolve() == attempt_path.resolve() and relocated_path.is_file():
        return relocated_path
    return _required_file(declared_path)


def _read_candidate_census(
    *,
    artifact_root: Path,
    batch_index: Path,
    batch: DiscoveryBatchInput,
) -> _CandidateCensus:
    candidate_count = 0
    populated_physical_feature_count = 0
    excluded_non_feature_backed_populated_identity_count = 0
    multi_seed_candidate_count = 0
    display_family_counts: Counter[tuple[str, str]] = Counter()
    display_family_best_scans: dict[tuple[str, str], set[int]] = defaultdict(set)
    candidate_samples: dict[str, str] = {}

    for candidate in _iter_owner_bound_candidates(
        artifact_root=artifact_root,
        batch_index=batch_index,
        batch=batch,
    ):
        previous_sample = candidate_samples.get(candidate.candidate_id)
        if previous_sample is not None:
            raise ValueError(
                f"{batch_index}: duplicate candidate_id {candidate.candidate_id!r} "
                f"across samples {previous_sample!r} and "
                f"{candidate.sample_stem!r}"
            )
        candidate_count += 1
        candidate_samples[candidate.candidate_id] = candidate.sample_stem
        if len(candidate.seed_scan_ids) > 1:
            multi_seed_candidate_count += 1
        family_key = (candidate.sample_stem, candidate.feature_family_id)
        display_family_counts[family_key] += 1
        display_family_best_scans[family_key].add(candidate.best_ms2_scan_id)

        physical_id = candidate.ms1_feature_row_id
        if (
            physical_id
            and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
        ):
            populated_physical_feature_count += 1
        elif physical_id:
            excluded_non_feature_backed_populated_identity_count += 1

    multi_display_families = sum(count > 1 for count in display_family_counts.values())
    display_family_excess_candidate_count = sum(
        count - 1 for count in display_family_counts.values() if count > 1
    )
    multi_scan_display_families = sum(
        len(scans) > 1 for scans in display_family_best_scans.values()
    )
    return _CandidateCensus(
        summary={
            "sample_count": len(batch.sample_order),
            "candidate_count": candidate_count,
            "populated_physical_feature_count": populated_physical_feature_count,
            "physical_feature_identity_scope": "owner_feature_backed_states_only",
            "excluded_non_feature_backed_populated_identity_count": (
                excluded_non_feature_backed_populated_identity_count
            ),
            # The owner parser rejects duplicate sample/tag/feature identities
            # for feature-backed states before yielding candidates. Reaching
            # this summary proves that accepted invariant domain is unique;
            # this is not a second, independently variable diagnostic counter.
            "unique_physical_feature_count": populated_physical_feature_count,
            "duplicate_physical_feature_count": 0,
            "multi_seed_candidate_count": multi_seed_candidate_count,
            "display_family_multi_candidate_group_count": multi_display_families,
            "display_family_excess_candidate_count": (
                display_family_excess_candidate_count
            ),
            "display_family_multi_best_scan_group_count": (multi_scan_display_families),
        },
        candidate_samples=candidate_samples,
        sample_stems=batch.sample_order,
    )


def _write_ledger(
    path: Path,
    *,
    artifact_root: Path,
    batch_index: Path,
    batch: DiscoveryBatchInput,
    review: _ReviewCensus,
    backfill: _BackfillCensus,
    request_proxy: _RequestProxyCensus,
    timing: _TimingCensus,
) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    write_tsv(
        temporary_path,
        _iter_ledger_rows(
            artifact_root=artifact_root,
            batch_index=batch_index,
            batch=batch,
            review=review,
            backfill=backfill,
            request_proxy=request_proxy,
            timing=timing,
        ),
        _LEDGER_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    temporary_path.replace(path)


def _iter_ledger_rows(
    *,
    artifact_root: Path,
    batch_index: Path,
    batch: DiscoveryBatchInput,
    review: _ReviewCensus,
    backfill: _BackfillCensus,
    request_proxy: _RequestProxyCensus,
    timing: _TimingCensus,
) -> Iterator[Mapping[str, object]]:
    for candidate in _iter_owner_bound_candidates(
        artifact_root=artifact_root,
        batch_index=batch_index,
        batch=batch,
    ):
        projected_groups = backfill.candidate_group_ids.get(
            (candidate.sample_stem, candidate.candidate_id),
            frozenset(),
        )
        group_ids = ";".join(sorted(projected_groups))
        group_status = (
            "unavailable_missing_owner_assignment_payload"
            if not projected_groups
            else "projection_only_source_candidate_link"
            if len(projected_groups) == 1
            else "projection_only_ambiguous_multiple_groups"
        )
        physical_id = candidate.ms1_feature_row_id
        yield {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "record_type": "candidate",
            "diagnostic_layers": "A;B",
            "claim_status": "discovery_owner_bound_candidate",
            "sample_stem": candidate.sample_stem,
            "physical_ms1_feature_id": physical_id,
            "physical_ms1_identity_status": (
                "discovery_owner_rehydrated_declared_feature_identity"
                if physical_id
                else "unavailable_review_only_orphan"
            ),
            "candidate_id": candidate.candidate_id,
            "best_ms2_scan_id": candidate.best_ms2_scan_id,
            "seed_scan_ids": ";".join(str(value) for value in candidate.seed_scan_ids),
            "scientific_hypothesis_status": (
                "candidate_declared_precursor_product_hypothesis"
            ),
            "owner_ids": "",
            "owner_projection_refs": "",
            "assignment_role": "",
            "owner_assignment_status": ("unavailable_missing_owner_assignment_payload"),
            "group_hypothesis_id": group_ids,
            "group_status": group_status,
            "consolidation_state": "",
            "consolidation_winner_group_hypothesis_id": "",
            "earliest_settlement_stage": "",
            "logical_backfill_request_id": "",
            "request_equivalence_class_id": "",
            "request_identity_status": "unavailable_no_owner_request_identity",
            "display_request_proxy_count": "",
            "display_request_proxy_duplicate_count": "",
            "batch_cost": "",
            "raw_call_cost": "",
            "point_cost": "",
            "cost_status": "aggregate_only_no_request_correlation",
        }

    for group_id in sorted(review.groups):
        group = review.groups[group_id]
        yield {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "record_type": "group_settlement",
            "diagnostic_layers": "C;D;E",
            "claim_status": "persisted_projection_only",
            "sample_stem": "",
            "physical_ms1_feature_id": "",
            "physical_ms1_identity_status": (
                "unavailable_missing_complete_owner_payload"
            ),
            "candidate_id": "",
            "best_ms2_scan_id": "",
            "seed_scan_ids": "",
            "scientific_hypothesis_status": (
                "cross_sample_group_projection_not_rehydrated"
            ),
            "owner_ids": "",
            "owner_projection_refs": group.owner_projection_refs,
            "assignment_role": "",
            "owner_assignment_status": (
                "projection_only_refs_without_owner_assignments"
            ),
            "group_hypothesis_id": group_id,
            "group_status": "projection_only_not_owner_rehydrated",
            "consolidation_state": group.consolidation_state,
            "consolidation_winner_group_hypothesis_id": (
                group.consolidation_winner_group_hypothesis_id
            ),
            "earliest_settlement_stage": _earliest_settlement_stage(
                group.consolidation_state,
                timing=timing,
            ),
            "logical_backfill_request_id": "",
            "request_equivalence_class_id": "",
            "request_identity_status": "unavailable_no_owner_request_identity",
            "display_request_proxy_count": request_proxy.per_group_count.get(
                group_id, 0
            ),
            "display_request_proxy_duplicate_count": (
                request_proxy.per_group_duplicate_count.get(group_id, 0)
            ),
            "batch_cost": "",
            "raw_call_cost": "",
            "point_cost": "",
            "cost_status": "aggregate_only_no_request_correlation",
        }


def _read_review_groups(path: Path) -> _ReviewCensus:
    groups: dict[str, _ReviewGroup] = {}
    states: Counter[str] = Counter()
    loser_row_with_rescue_count = 0
    loser_quantifiable_rescue_count = 0
    loser_review_rescue_count = 0
    for row_number, row in _iter_delimited_rows(
        path,
        delimiter="\t",
        required_columns=_REVIEW_COLUMNS,
    ):
        group_id = _required_text(path, row_number, row, "group_hypothesis_id")
        if group_id in groups:
            raise ValueError(f"{path}: row {row_number}: duplicate group {group_id!r}")
        state = _required_text(path, row_number, row, "consolidation_state")
        if state not in _CONSOLIDATION_STATES:
            raise ValueError(
                f"{path}: row {row_number}: unsupported consolidation_state {state!r}"
            )
        quantifiable_rescue_count = _nonnegative_int(
            path,
            row_number,
            row,
            "quantifiable_rescue_count",
        )
        review_rescue_count = _nonnegative_int(
            path,
            row_number,
            row,
            "review_rescue_count",
        )
        groups[group_id] = _ReviewGroup(
            feature_family_id=_required_text(
                path,
                row_number,
                row,
                "feature_family_id",
            ),
            group_hypothesis_id=group_id,
            public_family_id=row.get("public_family_id", "").strip(),
            owner_projection_refs=row.get("event_cluster_ids", ""),
            event_member_count=_nonnegative_int(
                path,
                row_number,
                row,
                "event_member_count",
            ),
            consolidation_state=state,
            consolidation_winner_group_hypothesis_id=row.get(
                "consolidation_winner_group_hypothesis_id", ""
            ).strip(),
            quantifiable_rescue_count=quantifiable_rescue_count,
            review_rescue_count=review_rescue_count,
        )
        states[state] += 1
        if state == "primary_loser":
            loser_quantifiable_rescue_count += quantifiable_rescue_count
            loser_review_rescue_count += review_rescue_count
            if quantifiable_rescue_count > 0 or review_rescue_count > 0:
                loser_row_with_rescue_count += 1
    _validate_consolidation_relationships(path, groups)
    return _ReviewCensus(
        groups=groups,
        consolidation_counts=dict(sorted(states.items())),
        loser_row_with_rescue_count=loser_row_with_rescue_count,
        loser_quantifiable_rescue_count=loser_quantifiable_rescue_count,
        loser_review_rescue_count=loser_review_rescue_count,
    )


def _validate_consolidation_relationships(
    path: Path,
    groups: Mapping[str, _ReviewGroup],
) -> None:
    for group_id, group in groups.items():
        winner_id = group.consolidation_winner_group_hypothesis_id
        if group.consolidation_state == "primary_winner":
            if winner_id != group_id:
                raise ValueError(
                    f"{path}: primary_winner {group_id!r} must reference itself"
                )
        elif group.consolidation_state == "not_consolidated" and winner_id:
            raise ValueError(
                f"{path}: not_consolidated group {group_id!r} must not reference "
                "a consolidation winner"
            )

    for group_id, group in groups.items():
        if group.consolidation_state != "primary_loser":
            continue
        seen = {group_id}
        winner_id = group.consolidation_winner_group_hypothesis_id
        while True:
            if winner_id in seen:
                raise ValueError(
                    f"{path}: primary_loser {group_id!r} has a cyclic winner chain"
                )
            seen.add(winner_id)
            winner = groups.get(winner_id)
            if winner is None:
                raise ValueError(
                    f"{path}: primary_loser {group_id!r} references missing "
                    f"winner {winner_id!r}"
                )
            if winner.consolidation_state == "primary_winner":
                break
            if winner.consolidation_state != "primary_loser":
                raise ValueError(
                    f"{path}: primary_loser {group_id!r} references non-winner "
                    f"group {winner_id!r}"
                )
            winner_id = winner.consolidation_winner_group_hypothesis_id


def _read_backfill_evidence(
    path: Path,
    *,
    known_group_ids: frozenset[str],
    candidate_samples: Mapping[str, str],
    known_sample_stems: frozenset[str],
    group_feature_family_ids: Mapping[str, str] | None = None,
    group_public_family_ids: Mapping[str, str] | None = None,
) -> _BackfillCensus:
    row_count = 0
    candidate_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for owner_result in iter_owner_bound_backfill_evidence_tsv(
        path,
        owner_bindings=BackfillEvidenceOwnerBindings(
            known_group_ids=known_group_ids,
            candidate_samples=candidate_samples,
            known_sample_stems=known_sample_stems,
            group_feature_family_ids=group_feature_family_ids or {},
            group_public_family_ids=group_public_family_ids or {},
        ),
    ):
        row_count += 1
        assignment = owner_result.candidate_assignment
        if assignment is not None:
            candidate_groups[
                (assignment.sample_stem, assignment.source_candidate_id)
            ].add(assignment.group_hypothesis_id)
    return _BackfillCensus(
        row_count=row_count,
        candidate_group_ids={
            candidate_key: frozenset(group_ids)
            for candidate_key, group_ids in candidate_groups.items()
        },
    )


def _read_request_proxy(
    path: Path,
    *,
    known_group_ids: frozenset[str],
    known_sample_stems: frozenset[str],
) -> _RequestProxyCensus:
    row_count = 0
    projected_request_count = 0
    duplicate_count = 0
    canonical_group_by_key: dict[bytes, str] = {}
    per_group: Counter[str] = Counter()
    per_group_duplicates: Counter[str] = Counter()
    for row_number, row in _iter_delimited_rows(
        path,
        delimiter="\t",
        required_columns=_PROVENANCE_COLUMNS,
    ):
        row_count += 1
        _require_schema_version(
            path,
            row_number,
            row,
            PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
        )
        group_id = _required_text(path, row_number, row, "group_hypothesis_id")
        if group_id not in known_group_ids:
            raise ValueError(
                f"{path}: row {row_number}: unknown group_hypothesis_id {group_id!r}"
            )
        mz = row.get("backfill_seed_mz", "")
        rt = row.get("backfill_seed_rt", "")
        if not mz and not rt:
            continue
        if not mz or not rt:
            raise ValueError(
                f"{path}: row {row_number}: partial displayed backfill seed"
            )
        sample_stem = _required_text(path, row_number, row, "sample_stem")
        if sample_stem not in known_sample_stems:
            raise ValueError(
                f"{path}: row {row_number}: unknown sample_stem {sample_stem!r}"
            )
        projected_request_count += 1
        per_group[group_id] += 1
        key = _request_proxy_key(sample_stem, mz, rt)
        canonical_group = canonical_group_by_key.get(key)
        if canonical_group is not None:
            duplicate_count += 1
            if group_id < canonical_group:
                per_group_duplicates[canonical_group] += 1
                canonical_group_by_key[key] = group_id
            else:
                per_group_duplicates[group_id] += 1
        else:
            canonical_group_by_key[key] = group_id
    return _RequestProxyCensus(
        row_count=row_count,
        projected_request_count=projected_request_count,
        projected_duplicate_count=duplicate_count,
        per_group_count=dict(per_group),
        per_group_duplicate_count=dict(per_group_duplicates),
    )


def _request_proxy_key(sample_stem: str, mz: str, rt: str) -> bytes:
    payload = "\0".join((sample_stem, mz, rt)).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).digest()


def _read_timing(
    path: Path,
    *,
    expected_sample_stems: tuple[str, ...],
) -> _TimingCensus:
    payload = _read_json_object(path)
    schema_version = payload.get("schema_version")
    if schema_version is None:
        schema_status = "unavailable_legacy_unversioned_timing_projection"
    elif schema_version == "timing":
        schema_status = "current_timing_projection"
    else:
        raise ValueError(f"{path}: unsupported timing schema {schema_version!r}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"{path}: records must be a list")
    assignment_count = 0
    owner_count = 0
    owner_sample_stems: set[str] = set()
    backfill_scope_metrics: Mapping[str, Any] | None = None
    backfill_scope_record_number: int | None = None
    backfill_metrics: Mapping[str, Any] | None = None
    backfill_record_number: int | None = None
    backfill_elapsed_sec = 0.0
    pre_backfill_consolidation_observed = False
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"{path}: record {index} must be an object")
        stage = record.get("stage")
        metrics = record.get("metrics", {})
        if not isinstance(metrics, dict):
            raise ValueError(f"{path}: record {index} metrics must be an object")
        if stage == "alignment.build_owners.sample_complete":
            sample_stem = record.get("sample_stem")
            if not isinstance(sample_stem, str) or not sample_stem:
                raise ValueError(
                    f"{path}: record {index}: sample_complete sample_stem is required"
                )
            if sample_stem in owner_sample_stems:
                raise ValueError(
                    f"{path}: record {index}: duplicate sample_complete sample_stem "
                    f"{sample_stem!r}"
                )
            owner_sample_stems.add(sample_stem)
            assignment_count += _metric_nonnegative_int(
                path,
                index,
                metrics,
                "assignment_count",
            )
            owner_count += _metric_nonnegative_int(
                path,
                index,
                metrics,
                "owner_count",
            )
        elif stage == "alignment.backfill_scope":
            if backfill_scope_metrics is not None:
                raise ValueError(f"{path}: duplicate alignment.backfill_scope record")
            backfill_scope_metrics = metrics
            backfill_scope_record_number = index
        elif stage == "alignment.owner_backfill":
            if backfill_metrics is not None:
                raise ValueError(f"{path}: duplicate alignment.owner_backfill record")
            backfill_metrics = metrics
            backfill_record_number = index
            backfill_elapsed_sec = _record_nonnegative_float(
                path,
                index,
                record,
                "elapsed_sec",
            )
        elif stage == "alignment.pre_backfill_consolidation":
            pre_backfill_consolidation_observed = True
    if (
        backfill_scope_metrics is None
        or backfill_scope_record_number is None
        or backfill_metrics is None
        or backfill_record_number is None
    ):
        raise ValueError(f"{path}: required Backfill timing records are missing")
    expected_samples = set(expected_sample_stems)
    if owner_sample_stems != expected_samples:
        missing = sorted(expected_samples - owner_sample_stems)
        unexpected = sorted(owner_sample_stems - expected_samples)
        raise ValueError(
            f"{path}: alignment.build_owners.sample_complete sample universe does "
            f"not match Discovery; missing={missing!r}; unexpected={unexpected!r}"
        )
    backfill_scope = _metric_required_text(
        path,
        backfill_scope_record_number,
        backfill_scope_metrics,
        "backfill_scope",
    )
    input_feature_count = _metric_nonnegative_int(
        path,
        backfill_scope_record_number,
        backfill_scope_metrics,
        "input_feature_count",
    )
    backfill_feature_count = _metric_nonnegative_int(
        path,
        backfill_scope_record_number,
        backfill_scope_metrics,
        "backfill_feature_count",
    )
    skipped_feature_count = _metric_nonnegative_int(
        path,
        backfill_scope_record_number,
        backfill_scope_metrics,
        "skipped_feature_count",
    )
    if input_feature_count != backfill_feature_count + skipped_feature_count:
        raise ValueError(
            f"{path}: timing record {backfill_scope_record_number}: Backfill "
            "feature counts do not close: "
            f"input={input_feature_count}; backfill={backfill_feature_count}; "
            f"skipped={skipped_feature_count}"
        )
    if (
        backfill_scope != "full-audit"
        or skipped_feature_count != 0
        or input_feature_count != backfill_feature_count
    ):
        raise ValueError(
            f"{path}: decision ranking requires a complete full-audit Backfill "
            f"scope; scope={backfill_scope!r}; input={input_feature_count}; "
            f"backfill={backfill_feature_count}; skipped={skipped_feature_count}"
        )
    return _TimingCensus(
        schema_status=schema_status,
        assignment_count=assignment_count,
        owner_count=owner_count,
        backfill_scope=backfill_scope,
        input_feature_count=input_feature_count,
        backfill_feature_count=backfill_feature_count,
        skipped_feature_count=skipped_feature_count,
        backfill_request_target_count=_metric_nonnegative_int(
            path,
            backfill_scope_record_number,
            backfill_scope_metrics,
            "request_target_count",
        ),
        backfill_extract_request_count=_metric_nonnegative_int(
            path,
            backfill_scope_record_number,
            backfill_scope_metrics,
            "extract_request_count",
        ),
        backfill_extract_xic_count=_metric_nonnegative_int(
            path,
            backfill_record_number,
            backfill_metrics,
            "extract_xic_count",
        ),
        backfill_batch_count=_metric_nonnegative_int(
            path,
            backfill_record_number,
            backfill_metrics,
            "extract_xic_batch_count",
        ),
        backfill_raw_call_count=_metric_nonnegative_int(
            path,
            backfill_record_number,
            backfill_metrics,
            "raw_chromatogram_call_count",
        ),
        backfill_point_count=_metric_nonnegative_int(
            path,
            backfill_record_number,
            backfill_metrics,
            "point_count",
        ),
        backfill_elapsed_sec=backfill_elapsed_sec,
        pre_backfill_consolidation_observed=pre_backfill_consolidation_observed,
    )


def _summary(
    *,
    artifact_root: Path,
    expected_code_revision: str,
    manifest_evidence: _ManifestEvidence,
    heartbeat: Mapping[str, Any],
    candidate_census: Mapping[str, int | float | str],
    review: _ReviewCensus,
    backfill: _BackfillCensus,
    request_proxy: _RequestProxyCensus,
    timing: _TimingCensus,
) -> dict[str, Any]:
    group_count = len(review.groups)
    loser_count = review.consolidation_counts.get("primary_loser", 0)
    decision = _decision(
        loser_count=loser_count,
        group_count=group_count,
    )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "complete",
        "validation_status": VALIDATION_STATUS,
        "artifact": {
            "artifact_name": artifact_root.name,
            "owner_validated_code_revision": expected_code_revision,
            "caller_expected_code_revision": expected_code_revision,
            "manifest_schema_version": manifest_evidence.schema_version,
            "manifest_rehydration_status": (manifest_evidence.rehydration_status),
            "freshness": "stale_revision_diagnostic_cost_evidence_only",
            "current_head_parity_claimed": False,
            "raw_opened": False,
        },
        "candidate_layer": {
            **candidate_census,
            "physical_feature_identity_uniqueness_status": (
                "owner_parser_invariant_accepted_feature_backed_states"
            ),
            "duplicate_physical_feature_count_basis": (
                "owner_parser_rejects_duplicate_sample_tag_feature_identity_"
                "in_feature_backed_states"
            ),
            "pairing_verdict": (
                "candidate_layer_dedupe_defect_not_reproduced_"
                "feature_backed_owner_invariant_accepted"
            ),
            "claim_ceiling": (
                "owner_bound_candidate_fields; strict scan content not replayed"
            ),
        },
        "alignment_owner_projection": {
            "assignment_count": timing.assignment_count,
            "owner_count": timing.owner_count,
            "status": "aggregate_owner_timing_only",
            "candidate_to_owner_assignment": (
                "unavailable_missing_owner_assignment_payload"
            ),
        },
        "cross_sample_and_settlement": {
            "group_row_count": group_count,
            "consolidation_counts": review.consolidation_counts,
            "primary_loser_fraction": _safe_ratio(loser_count, group_count),
            "loser_row_with_rescue_count": review.loser_row_with_rescue_count,
            "loser_quantifiable_rescue_count": (review.loser_quantifiable_rescue_count),
            "loser_review_rescue_count": review.loser_review_rescue_count,
            "earliest_retained_settlement_stage": (
                "alignment.primary_consolidation_post_backfill"
                if not timing.pre_backfill_consolidation_observed
                else "mixed_pre_and_post_backfill_projection"
            ),
            "status": "persisted_projection_not_owner_rehydrated",
        },
        "request_identity_and_cost": {
            "timing_schema_status": timing.schema_status,
            "backfill_scope": timing.backfill_scope,
            "input_feature_count": timing.input_feature_count,
            "backfill_feature_count": timing.backfill_feature_count,
            "skipped_feature_count": timing.skipped_feature_count,
            "exact_logical_request_identity": "unavailable",
            "exact_equivalence_class": "unavailable",
            "request_to_batch_call_point_correlation": "unavailable",
            "display_request_proxy_count": request_proxy.projected_request_count,
            "display_request_proxy_duplicate_count": (
                request_proxy.projected_duplicate_count
            ),
            "display_request_proxy_duplicate_fraction": _safe_ratio(
                request_proxy.projected_duplicate_count,
                request_proxy.projected_request_count,
            ),
            "display_request_proxy_status": (
                "projection_only_not_exact_request_equivalence"
            ),
            "backfill_request_target_count": timing.backfill_request_target_count,
            "backfill_extract_request_count": timing.backfill_extract_request_count,
            "backfill_extract_xic_count": timing.backfill_extract_xic_count,
            "backfill_batch_count": timing.backfill_batch_count,
            "backfill_raw_call_count": timing.backfill_raw_call_count,
            "backfill_point_count": timing.backfill_point_count,
            "backfill_elapsed_sec": timing.backfill_elapsed_sec,
            "cost_status": "aggregate_only_no_request_correlation",
        },
        "artifact_row_counts": {
            "backfill_cell_evidence_rows": backfill.row_count,
            "provisional_cell_provenance_rows": request_proxy.row_count,
        },
        "diagnostic_layers": {
            "A_same_candidate_other_ms2_evidence": (
                "not_reproduced_feature_backed_owner_uniqueness_invariant_accepted"
            ),
            "B_same_peak_distinct_scientific_hypotheses": (
                "inconclusive_missing_owner_physical_peak_payload"
            ),
            "C_alignment_owner_consolidation": (
                "partial_aggregate_counts_only_assignment_lineage_unavailable"
            ),
            "D_cross_sample_winner_loser_settlement": (
                "partial_final_projection_pre_backfill_knowability_unavailable"
            ),
            "E_exact_request_and_batch_reuse": (
                "inconclusive_exact_identity_and_request_cost_lineage_unavailable"
            ),
        },
        "hypothesis_verdict": {
            "candidate_layer": (
                "defect_not_reproduced_feature_backed_owner_uniqueness_"
                "invariant_accepted"
            ),
            "downstream_owner_and_request_layer": (
                "inconclusive_missing_owner_and_exact_request_lineage"
            ),
            "overall": ("candidate_layer_not_reproduced_downstream_inconclusive"),
        },
        "decision": {
            **decision,
            "dependency_order": (
                "decision_ledger_then_owner_valid_early_settlement_shadow_then_"
                "exact_request_identity_and_coalescing_then_artifact_format_then_"
                "recovery"
            ),
            "falsifiers": [
                (
                    "An owner-valid pre-Backfill shadow shows loser status is not "
                    "knowable without changing scientific output."
                ),
                (
                    "Owner-minted exact request identities show materially more "
                    "exact reuse than the display proxy suggests."
                ),
                (
                    "A comparable run attributes negligible Backfill cost to rows "
                    "that later settle as losers."
                ),
            ],
        },
        "instrumentation_gap": [
            "complete owner-rehydratable candidate-to-owner assignment payload",
            "owner-minted logical request identity and equivalence class",
            "request-to-batch-call-point lineage including super-window membership",
        ],
        "residual_risks": [
            (
                "The manifest binds the recorded revision, but is not "
                "current-head parity evidence."
            ),
            (
                "Final consolidation rows do not prove winner/loser knowability "
                "before Backfill."
            ),
            (
                "Aggregate XIC/batch/call/point counts cannot estimate savings for "
                "either optimization."
            ),
        ],
        "workflow_wall_elapsed_sec": _heartbeat_elapsed(heartbeat),
    }


def _decision(
    *,
    loser_count: int,
    group_count: int,
) -> dict[str, str]:
    if loser_count > 0:
        return {
            "status": "observed_evidence_supports_priority",
            "value_priority": (
                "early_loser_settlement_shadow_before_exact_request_coalescing"
            ),
            "reason": (
                f"The artifact contains {loser_count} primary_loser rows among "
                f"{group_count} settlement rows, while exact request equivalence "
                "is unavailable and the request-reuse count is only a display "
                "proxy."
            ),
        }
    return {
        "status": "inconclusive_no_observed_losers_or_exact_request_identity",
        "value_priority": "inconclusive_no_optimization_priority",
        "reason": (
            "The artifact contains no primary_loser rows, and exact request "
            "equivalence is unavailable; it cannot prioritize either optimization."
        ),
    }


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    candidate = _mapping(summary, "candidate_layer")
    settlement = _mapping(summary, "cross_sample_and_settlement")
    request = _mapping(summary, "request_identity_and_cost")
    verdict = _mapping(summary, "hypothesis_verdict")
    decision = _mapping(summary, "decision")
    return "\n".join(
        (
            "# Backfill decision ledger summary",
            "",
            f"- Validation status: `{summary['validation_status']}`",
            f"- Pairing verdict: `{verdict['overall']}`",
            f"- Decision status: `{decision['status']}`",
            f"- Value priority: `{decision['value_priority']}`",
            f"- Decision reason: {decision['reason']}",
            "",
            "## Observed evidence",
            "",
            (
                f"- Candidates: {candidate['candidate_count']}; populated physical "
                f"feature IDs: {candidate['populated_physical_feature_count']}; "
                f"duplicates: {candidate['duplicate_physical_feature_count']}; "
                f"multi-seed candidates: {candidate['multi_seed_candidate_count']}."
            ),
            (
                f"- Backfill feature rows: {settlement['group_row_count']}; final "
                f"primary losers: "
                f"{settlement['consolidation_counts'].get('primary_loser', 0)}."
            ),
            (
                f"- Display request proxy duplicates: "
                f"{request['display_request_proxy_duplicate_count']} / "
                f"{request['display_request_proxy_count']}; this is not exact "
                "request equivalence."
            ),
            (
                f"- Owner Backfill: {request['backfill_elapsed_sec']:.3f} s; "
                f"{request['backfill_extract_xic_count']} XIC extractions; "
                f"{request['backfill_raw_call_count']} RAW calls; "
                f"{request['backfill_point_count']} points."
            ),
            "",
            "## Authority ceiling",
            "",
            (
                "Candidate fields are owner-bound. Owner assignment, cross-sample "
                "group, exact request identity, equivalence class, and per-request "
                "cost remain unavailable or persisted projections only."
            ),
            "",
        )
    )


def _read_manifest_evidence(
    path: Path,
    *,
    artifact_root: Path,
    expected_code_revision: str,
) -> _ManifestEvidence:
    validated = validate_provisional_run_manifest(path, root=artifact_root)
    if validated.get("code_revision") != expected_code_revision:
        raise ValueError(
            f"{artifact_root}: owner-validated code_revision does not match "
            f"expected {expected_code_revision!r}"
        )
    return _ManifestEvidence(
        schema_version=PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
        rehydration_status="current_owner_rehydrated",
    )


def _earliest_settlement_stage(
    consolidation_state: str,
    *,
    timing: _TimingCensus,
) -> str:
    if consolidation_state not in {"primary_winner", "primary_loser"}:
        return "not_settled_in_retained_run"
    if timing.pre_backfill_consolidation_observed:
        return "mixed_pre_and_post_backfill_projection_unavailable"
    return (
        "observed_post_backfill_primary_consolidation_"
        "pre_backfill_knowability_unavailable"
    )


def _iter_delimited_rows(
    path: Path,
    *,
    delimiter: str,
    required_columns: tuple[str, ...],
) -> Iterable[tuple[int, dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            validate_delimited_header(
                reader.fieldnames,
                path=path,
                required_columns=required_columns,
                exact_columns=required_columns,
            )
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise ValueError(
                        f"{path}: row {row_number}: more cells than header"
                    )
                truncated = [column for column, value in row.items() if value is None]
                if truncated:
                    joined = ", ".join(truncated)
                    raise ValueError(
                        f"{path}: row {row_number}: fewer cells than header; "
                        f"missing {joined}"
                    )
                yield row_number, row
    except OSError as exc:
        raise ValueError(f"{path}: file could not be read: {exc}") from exc


def _mapping_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_mapping_without_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return payload


def _write_text_atomic(path: Path, text: str) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(path)


def _required_file(path: Path) -> Path:
    if not path.is_file():
        raise ValueError(f"required artifact file is missing: {path}")
    return path


def _required_text(
    path: Path,
    row_number: int,
    row: Mapping[str, str],
    field: str,
) -> str:
    value = row.get(field, "")
    if not value:
        raise ValueError(f"{path}: row {row_number}: {field} is required")
    return value


def _require_schema_version(
    path: Path,
    row_number: int,
    row: Mapping[str, str],
    expected: str,
) -> None:
    observed = _required_text(path, row_number, row, "schema_version")
    if observed != expected:
        raise ValueError(
            f"{path}: row {row_number}: schema_version must be {expected!r}, "
            f"got {observed!r}"
        )


def _nonnegative_int(
    path: Path,
    row_number: int,
    row: Mapping[str, str],
    field: str,
) -> int:
    value = _required_text(path, row_number, row, field)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {field} must be an integer"
        ) from exc
    if parsed < 0:
        raise ValueError(f"{path}: row {row_number}: {field} must be nonnegative")
    return parsed


def _metric_nonnegative_int(
    path: Path,
    record_number: int,
    metrics: Mapping[str, Any],
    field: str,
) -> int:
    value = metrics.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"{path}: timing record {record_number}: {field} must be a "
            "nonnegative integer"
        )
    return value


def _metric_required_text(
    path: Path,
    record_number: int,
    metrics: Mapping[str, Any],
    field: str,
) -> str:
    value = metrics.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{path}: timing record {record_number}: {field} must be nonblank text"
        )
    return value.strip()


def _record_nonnegative_float(
    path: Path,
    record_number: int,
    record: Mapping[str, Any],
    field: str,
) -> float:
    value = record.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(
            f"{path}: timing record {record_number}: {field} must be a finite "
            "nonnegative number"
        )
    return float(value)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _heartbeat_elapsed(heartbeat: Mapping[str, Any]) -> float | None:
    value = heartbeat.get("elapsed_sec")
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("heartbeat elapsed_sec must be a finite nonnegative number")
    return float(value)


def _mapping(value: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    result = value.get(field)
    if not isinstance(result, dict):
        raise ValueError(f"summary field {field} must be an object")
    return result


__all__ = (
    "BackfillDecisionLedgerOutputs",
    "LEDGER_SCHEMA_VERSION",
    "SUMMARY_SCHEMA_VERSION",
    "VALIDATION_STATUS",
    "build_backfill_decision_ledger",
)
