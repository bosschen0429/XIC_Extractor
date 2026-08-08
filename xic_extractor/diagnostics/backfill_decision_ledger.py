from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.provisional_matrix import (
    PROVISIONAL_PROVENANCE_COLUMNS,
    PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.tsv_writer import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
    ALIGNMENT_REVIEW_COLUMNS,
)
from xic_extractor.extraction.provisional_run_manifest import (
    PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
    validate_provisional_run_manifest,
)
from xic_extractor.tabular_io import validate_delimited_header, write_tsv

LEDGER_SCHEMA_VERSION: Final = "backfill_decision_ledger_v1"
SUMMARY_SCHEMA_VERSION: Final = "backfill_decision_summary_v1"
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

_LEGACY_MANIFEST_SCHEMA_VERSION: Final = "untargeted_provisional_matrix_run_v1"
_LEGACY_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema_version",
        "code_revision",
        "preset_name",
        "effective_configuration",
        "runtime",
        "inputs",
        "artifacts",
        "validation_status",
        "row_identity_status",
        "identity_settlement_status",
        "product_authority",
    }
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
    group_hypothesis_id: str
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
    candidate_group_ids: Mapping[str, frozenset[str]]


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
    summary: Mapping[str, int | float]
    candidate_ids: frozenset[str]


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

    alignment_dir = artifact_root / "workflow" / "alignment"
    review = _read_review_groups(_required_file(alignment_dir / "alignment_review.tsv"))
    backfill = _read_backfill_evidence(
        _required_file(alignment_dir / "alignment_backfill_cell_evidence.tsv"),
        known_group_ids=frozenset(review.groups),
    )
    request_proxy = _read_request_proxy(
        _required_file(alignment_dir / "provisional_cell_provenance.tsv"),
        known_group_ids=frozenset(review.groups),
    )
    timing = _read_timing(_required_file(artifact_root / "timing.json"))
    heartbeat = _read_json_object(_required_file(artifact_root / "heartbeat.json"))

    candidate_census = _read_candidate_census(
        artifact_root=artifact_root,
    )
    unknown_candidate_ids = sorted(
        set(backfill.candidate_group_ids) - candidate_census.candidate_ids
    )
    if unknown_candidate_ids:
        preview = ", ".join(repr(value) for value in unknown_candidate_ids[:3])
        raise ValueError(
            "Backfill projection references unknown source_candidate_id values: "
            f"{preview}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_tsv = output_dir / "backfill_decision_ledger.tsv"
    _write_ledger(
        ledger_tsv,
        artifact_root=artifact_root,
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
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    summary_markdown = output_dir / "backfill_decision_summary.md"
    _write_text_atomic(summary_markdown, _summary_markdown(summary))
    return BackfillDecisionLedgerOutputs(
        ledger_tsv=ledger_tsv,
        summary_json=summary_json,
        summary_markdown=summary_markdown,
        summary=summary,
    )


def _read_candidate_census(
    *,
    artifact_root: Path,
) -> _CandidateCensus:
    batch_index = _required_file(
        artifact_root / "workflow" / "discovery" / "discovery_batch_index.csv"
    )
    batch = read_discovery_batch_index(batch_index)
    physical_feature_ids: set[tuple[str, str, str]] = set()
    duplicate_physical_feature_count = 0
    candidate_count = 0
    populated_physical_feature_count = 0
    multi_seed_candidate_count = 0
    display_family_counts: Counter[tuple[str, str]] = Counter()
    display_family_best_scans: dict[tuple[str, str], set[int]] = defaultdict(set)
    candidate_ids: set[str] = set()

    for sample_stem in batch.sample_order:
        receipt = batch.candidate_receipts.get(sample_stem)
        if receipt is None:
            raise ValueError(
                f"{batch_index}: sample {sample_stem!r} has no owner receipt"
            )
        candidate_path = _required_file(
            artifact_root
            / "workflow"
            / "discovery"
            / sample_stem
            / "discovery_candidates.csv"
        )
        candidates = read_owner_bound_discovery_candidates_csv(
            candidate_path,
            receipt=receipt,
            require_successor_columns=True,
        )
        for candidate in candidates:
            candidate_count += 1
            candidate_ids.add(candidate.candidate_id)
            if len(candidate.seed_scan_ids) > 1:
                multi_seed_candidate_count += 1
            family_key = (candidate.sample_stem, candidate.feature_family_id)
            display_family_counts[family_key] += 1
            display_family_best_scans[family_key].add(candidate.best_ms2_scan_id)

            physical_id = candidate.ms1_feature_row_id
            if physical_id:
                populated_physical_feature_count += 1
                feature_key = (
                    candidate.sample_stem,
                    candidate.neutral_loss_tag,
                    physical_id,
                )
                if feature_key in physical_feature_ids:
                    duplicate_physical_feature_count += 1
                physical_feature_ids.add(feature_key)

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
            "unique_physical_feature_count": len(physical_feature_ids),
            "duplicate_physical_feature_count": duplicate_physical_feature_count,
            "multi_seed_candidate_count": multi_seed_candidate_count,
            "display_family_multi_candidate_group_count": multi_display_families,
            "display_family_excess_candidate_count": (
                display_family_excess_candidate_count
            ),
            "display_family_multi_best_scan_group_count": (multi_scan_display_families),
        },
        candidate_ids=frozenset(candidate_ids),
    )


def _write_ledger(
    path: Path,
    *,
    artifact_root: Path,
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
    review: _ReviewCensus,
    backfill: _BackfillCensus,
    request_proxy: _RequestProxyCensus,
    timing: _TimingCensus,
) -> Iterator[Mapping[str, object]]:
    batch_index = _required_file(
        artifact_root / "workflow" / "discovery" / "discovery_batch_index.csv"
    )
    batch = read_discovery_batch_index(batch_index)
    for sample_stem in batch.sample_order:
        receipt = batch.candidate_receipts.get(sample_stem)
        if receipt is None:
            raise ValueError(
                f"{batch_index}: sample {sample_stem!r} has no owner receipt"
            )
        candidate_path = _required_file(
            artifact_root
            / "workflow"
            / "discovery"
            / sample_stem
            / "discovery_candidates.csv"
        )
        candidates = read_owner_bound_discovery_candidates_csv(
            candidate_path,
            receipt=receipt,
            require_successor_columns=True,
        )
        for candidate in candidates:
            projected_groups = backfill.candidate_group_ids.get(
                candidate.candidate_id,
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
                "seed_scan_ids": ";".join(
                    str(value) for value in candidate.seed_scan_ids
                ),
                "scientific_hypothesis_status": (
                    "candidate_declared_precursor_product_hypothesis"
                ),
                "owner_ids": "",
                "owner_projection_refs": "",
                "assignment_role": "",
                "owner_assignment_status": (
                    "unavailable_missing_owner_assignment_payload"
                ),
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
            group_hypothesis_id=group_id,
            owner_projection_refs=row.get("event_cluster_ids", ""),
            event_member_count=_nonnegative_int(
                path,
                row_number,
                row,
                "event_member_count",
            ),
            consolidation_state=state,
            consolidation_winner_group_hypothesis_id=row.get(
                "consolidation_winner_group_hypothesis_id",
                "",
            ),
            quantifiable_rescue_count=quantifiable_rescue_count,
            review_rescue_count=review_rescue_count,
        )
        states[state] += 1
        if state == "primary_loser":
            loser_quantifiable_rescue_count += quantifiable_rescue_count
            loser_review_rescue_count += review_rescue_count
            if quantifiable_rescue_count > 0 or review_rescue_count > 0:
                loser_row_with_rescue_count += 1
    return _ReviewCensus(
        groups=groups,
        consolidation_counts=dict(sorted(states.items())),
        loser_row_with_rescue_count=loser_row_with_rescue_count,
        loser_quantifiable_rescue_count=loser_quantifiable_rescue_count,
        loser_review_rescue_count=loser_review_rescue_count,
    )


def _read_backfill_evidence(
    path: Path,
    *,
    known_group_ids: frozenset[str],
) -> _BackfillCensus:
    row_count = 0
    candidate_groups: dict[str, set[str]] = defaultdict(set)
    for row_number, row in _iter_delimited_rows(
        path,
        delimiter="\t",
        required_columns=_BACKFILL_COLUMNS,
    ):
        row_count += 1
        _require_schema_version(
            path,
            row_number,
            row,
            ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
        )
        group_id = _required_text(path, row_number, row, "group_hypothesis_id")
        if group_id not in known_group_ids:
            raise ValueError(
                f"{path}: row {row_number}: unknown group_hypothesis_id {group_id!r}"
            )
        candidate_id = row.get("source_candidate_id", "")
        if candidate_id:
            candidate_groups[candidate_id].add(group_id)
    return _BackfillCensus(
        row_count=row_count,
        candidate_group_ids={
            candidate_id: frozenset(group_ids)
            for candidate_id, group_ids in candidate_groups.items()
        },
    )


def _read_request_proxy(
    path: Path,
    *,
    known_group_ids: frozenset[str],
) -> _RequestProxyCensus:
    row_count = 0
    projected_request_count = 0
    duplicate_count = 0
    seen: set[bytes] = set()
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
        projected_request_count += 1
        per_group[group_id] += 1
        key = _request_proxy_key(sample_stem, mz, rt)
        if key in seen:
            duplicate_count += 1
            per_group_duplicates[group_id] += 1
        else:
            seen.add(key)
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


def _read_timing(path: Path) -> _TimingCensus:
    payload = _read_json_object(path)
    schema_version = payload.get("schema_version")
    if schema_version is None:
        schema_status = "unavailable_legacy_unversioned_timing_projection"
    elif schema_version == "timing.v1":
        schema_status = "current_timing_v1_projection"
    else:
        raise ValueError(f"{path}: unsupported timing schema {schema_version!r}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"{path}: records must be a list")
    assignment_count = 0
    owner_count = 0
    owner_sample_record_count = 0
    backfill_scope_metrics: Mapping[str, Any] | None = None
    backfill_metrics: Mapping[str, Any] | None = None
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
            owner_sample_record_count += 1
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
        elif stage == "alignment.owner_backfill":
            if backfill_metrics is not None:
                raise ValueError(f"{path}: duplicate alignment.owner_backfill record")
            backfill_metrics = metrics
            backfill_elapsed_sec = _record_nonnegative_float(
                path,
                index,
                record,
                "elapsed_sec",
            )
        elif stage == "alignment.pre_backfill_consolidation":
            pre_backfill_consolidation_observed = True
    if backfill_scope_metrics is None or backfill_metrics is None:
        raise ValueError(f"{path}: required Backfill timing records are missing")
    if owner_sample_record_count == 0:
        raise ValueError(
            f"{path}: alignment.build_owners.sample_complete records are missing"
        )
    return _TimingCensus(
        schema_status=schema_status,
        assignment_count=assignment_count,
        owner_count=owner_count,
        backfill_request_target_count=_metric_nonnegative_int(
            path,
            0,
            backfill_scope_metrics,
            "request_target_count",
        ),
        backfill_extract_request_count=_metric_nonnegative_int(
            path,
            0,
            backfill_scope_metrics,
            "extract_request_count",
        ),
        backfill_extract_xic_count=_metric_nonnegative_int(
            path,
            0,
            backfill_metrics,
            "extract_xic_count",
        ),
        backfill_batch_count=_metric_nonnegative_int(
            path,
            0,
            backfill_metrics,
            "extract_xic_batch_count",
        ),
        backfill_raw_call_count=_metric_nonnegative_int(
            path,
            0,
            backfill_metrics,
            "raw_chromatogram_call_count",
        ),
        backfill_point_count=_metric_nonnegative_int(
            path,
            0,
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
    candidate_census: Mapping[str, int | float],
    review: _ReviewCensus,
    backfill: _BackfillCensus,
    request_proxy: _RequestProxyCensus,
    timing: _TimingCensus,
) -> dict[str, Any]:
    group_count = len(review.groups)
    loser_count = review.consolidation_counts.get("primary_loser", 0)
    candidate_pairing_defect_reproduced = (
        candidate_census["duplicate_physical_feature_count"] > 0
    )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "complete",
        "validation_status": VALIDATION_STATUS,
        "artifact": {
            "artifact_name": artifact_root.name,
            "legacy_manifest_asserted_code_revision": expected_code_revision,
            "caller_expected_code_revision": expected_code_revision,
            "manifest_schema_version": manifest_evidence.schema_version,
            "manifest_rehydration_status": (manifest_evidence.rehydration_status),
            "freshness": "stale_revision_diagnostic_cost_evidence_only",
            "current_head_parity_claimed": False,
            "raw_opened": False,
        },
        "candidate_layer": {
            **candidate_census,
            "pairing_verdict": (
                "candidate_layer_dedupe_defect_reproduced"
                if candidate_pairing_defect_reproduced
                else "candidate_layer_dedupe_defect_not_reproduced"
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
                "reproduced_duplicate_owner_bound_feature_ids"
                if candidate_pairing_defect_reproduced
                else "not_reproduced_zero_duplicate_owner_bound_feature_ids"
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
                "defect_reproduced"
                if candidate_pairing_defect_reproduced
                else "defect_not_reproduced"
            ),
            "downstream_owner_and_request_layer": (
                "inconclusive_missing_owner_and_exact_request_lineage"
            ),
            "overall": (
                "candidate_layer_not_reproduced_downstream_inconclusive"
                if not candidate_pairing_defect_reproduced
                else "candidate_layer_defect_requires_owner_fix"
            ),
        },
        "decision": {
            "value_priority": (
                "early_loser_settlement_shadow_before_exact_request_coalescing"
            ),
            "dependency_order": (
                "decision_ledger_then_owner_valid_early_settlement_shadow_then_"
                "exact_request_identity_and_coalescing_then_artifact_format_then_"
                "recovery"
            ),
            "reason": (
                "The retained run shows a large post-Backfill loser population, "
                "while the smaller request-reuse number is a display proxy and "
                "exact request equivalence is unavailable."
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
                "The legacy manifest's asserted revision is not owner-validated "
                "and is not current-head parity evidence."
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
            f"- Value priority: `{decision['value_priority']}`",
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
    manifest = _read_json_object(path)
    schema_version = manifest.get("schema_version")
    if schema_version == PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION:
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
    if schema_version != _LEGACY_MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"{artifact_root}: unsupported manifest schema {schema_version!r}"
        )
    if set(manifest) != _LEGACY_MANIFEST_FIELDS:
        raise ValueError(
            f"{artifact_root}: legacy manifest field set is invalid; current owner "
            "rehydration is unavailable"
        )

    try:
        validate_provisional_run_manifest(path, root=artifact_root)
    except ValueError:
        rehydration_status = (
            "unavailable_legacy_schema_rejected_by_current_manifest_owner"
        )
    else:
        raise ValueError(
            f"{artifact_root}: legacy schema unexpectedly passed the current owner"
        )
    observed_revision = manifest.get("code_revision")
    if observed_revision != expected_code_revision:
        raise ValueError(
            f"{artifact_root}: code_revision {observed_revision!r} does not match "
            f"expected {expected_code_revision!r}"
        )
    expected_fields: dict[str, object] = {
        "validation_status": VALIDATION_STATUS,
        "product_authority": False,
        "row_identity_status": "provisional",
        "identity_settlement_status": "not_run",
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise ValueError(
                f"{artifact_root}: manifest {field} must be {expected!r}, got "
                f"{manifest.get(field)!r}"
            )
    return _ManifestEvidence(
        schema_version=_LEGACY_MANIFEST_SCHEMA_VERSION,
        rehydration_status=rehydration_status,
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


def _record_nonnegative_float(
    path: Path,
    record_number: int,
    record: Mapping[str, Any],
    field: str,
) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(
            f"{path}: timing record {record_number}: {field} must be nonnegative"
        )
    return float(value)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _heartbeat_elapsed(heartbeat: Mapping[str, Any]) -> float | None:
    value = heartbeat.get("elapsed_sec")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
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
