"""Strict durable codecs, TSV I/O, and persisted row contracts for Backfill facts.

This module houses persisted row models, batch invariants, strict TSV reading,
and streaming chunked writes for durable peak fact provenance artifacts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from xic_extractor.alignment.backfill_peak_fact_projection import (
    BACKFILL_CELL_PROJECTION_ID_PREFIX,
    BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION,
    BackfillCellProjection,
    _compact_json_bytes,
    _float_text,
    _nonnegative_int,
    _positive_int,
    _require_lower_sha256,
    _require_typed_id,
    _required_finite_float,
    _required_text,
    _safe_text,
    _unique_string_tuple,
    _validate_optional_range,
)
from xic_extractor.alignment.production_decisions import (
    BACKFILL_AUTHORITY_CELL_ID_PREFIX,
    PRODUCTION_CELL_DECISION_ID_PREFIX,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakState,
)

BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION = (
    "alignment_owner_backfill_peak_fact_provenance"
)
BACKFILL_PROVENANCE_ROW_ID_PREFIX = "owner-backfill-provenance-row:"

RowKind = Literal["run_profile", "physical_question", "cell_projection"]
ClaimState = Literal["verified", "unavailable_without_live_owner"]

_PROVENANCE_ROWS_PER_CHUNK = 2048
_PROVENANCE_VERIFY_BLOCK_BYTES = 1 << 20

_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")

BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS = (
    "row_kind",
    "schema_version",
    "work_unit_key",
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "candidate_index",
    "candidate_phase",
    "request_identity_id",
    "acquisition_source_id",
    "trace_content_id",
    "trace_point_count",
    "trace_rt_start_min",
    "trace_rt_end_min",
    "profile_schema_version",
    "profile_id",
    "profile_hash",
    "signal_processing_method",
    "signal_processing_window_points",
    "physical_signal_formula",
    "prepared_signal_id",
    "segment_universe_id",
    "segment_candidate_count",
    "segment_candidate_ids",
    "physical_question_schema_version",
    "physical_question_id",
    "selection_subject_id",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
    "admissible_segment_count",
    "admissible_segment_ids",
    "selection_policy_version",
    "state",
    "reason",
    "physical_answer_id",
    "physical_segment_id",
    "apex_rt_min",
    "left_rt_min",
    "right_rt_min",
    "height",
    "scan_support_count",
    "area_counts_seconds",
    "area_source",
    "relative_shape_schema_version",
    "relative_shape_point_count",
    "relative_shape_content_hash",
    "fact_content_id",
    "selected_work_unit_key",
    "selected_fact_content_id",
    "owner_backfill_cell_content_id",
    "production_cell_decision_id",
    "cell_status",
    "consolidation_state",
    "consolidation_winner_group_hypothesis_id",
    "consolidation_source_group_hypothesis_id",
    "matrix_value_counts_seconds",
    "matrix_value_source",
    "writer_schema_version",
    "cell_projection_id",
    "projection_content_id",
)

_PROFILE_FIELDS = frozenset(
    {
        "profile_schema_version",
        "profile_id",
        "profile_hash",
        "signal_processing_method",
        "signal_processing_window_points",
        "physical_signal_formula",
    }
)
_STAGE_FIELDS = frozenset(
    {
        "work_unit_key",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "candidate_index",
        "candidate_phase",
        "request_identity_id",
        "acquisition_source_id",
    }
)
_TRACE_QUESTION_FIELDS = frozenset(
    {
        "trace_content_id",
        "trace_point_count",
        "trace_rt_start_min",
        "trace_rt_end_min",
        "prepared_signal_id",
        "segment_universe_id",
        "segment_candidate_count",
        "segment_candidate_ids",
        "physical_question_schema_version",
        "physical_question_id",
        "selection_subject_id",
        "query_rt_min",
        "context_rt_start_min",
        "context_rt_end_min",
        "admissible_segment_count",
        "admissible_segment_ids",
        "selection_policy_version",
        "state",
        "reason",
        "physical_answer_id",
        "physical_segment_id",
        "apex_rt_min",
        "left_rt_min",
        "right_rt_min",
        "height",
        "scan_support_count",
        "area_counts_seconds",
        "area_source",
        "relative_shape_schema_version",
        "relative_shape_point_count",
        "relative_shape_content_hash",
        "fact_content_id",
    }
)
_CELL_FIELDS = frozenset(
    {
        "selected_work_unit_key",
        "selected_fact_content_id",
        "owner_backfill_cell_content_id",
        "production_cell_decision_id",
        "cell_status",
        "consolidation_state",
        "consolidation_winner_group_hypothesis_id",
        "consolidation_source_group_hypothesis_id",
        "matrix_value_counts_seconds",
        "matrix_value_source",
        "writer_schema_version",
        "cell_projection_id",
    }
)
_ALWAYS_FIELDS = frozenset({"row_kind", "schema_version", "projection_content_id"})


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceRow:
    row_kind: RowKind
    schema_version: str = BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION
    work_unit_key: str = ""
    group_hypothesis_id: str = ""
    public_family_id: str = ""
    sample_stem: str = ""
    candidate_index: int | None = None
    candidate_phase: str = ""
    request_identity_id: str = ""
    acquisition_source_id: str = ""
    trace_content_id: str = ""
    trace_point_count: int | None = None
    trace_rt_start_min: float | None = None
    trace_rt_end_min: float | None = None
    profile_schema_version: str = ""
    profile_id: str = ""
    profile_hash: str = ""
    signal_processing_method: str = ""
    signal_processing_window_points: int | None = None
    physical_signal_formula: str = ""
    prepared_signal_id: str = ""
    segment_universe_id: str = ""
    segment_candidate_ids: tuple[str, ...] = ()
    physical_question_schema_version: str = ""
    physical_question_id: str = ""
    selection_subject_id: str = ""
    query_rt_min: float | None = None
    context_rt_start_min: float | None = None
    context_rt_end_min: float | None = None
    admissible_segment_ids: tuple[str, ...] = ()
    selection_policy_version: str = ""
    state: PhysicalPeakState | Literal[""] = ""
    reason: str = ""
    physical_answer_id: str = ""
    physical_segment_id: str = ""
    apex_rt_min: float | None = None
    left_rt_min: float | None = None
    right_rt_min: float | None = None
    height: float | None = None
    scan_support_count: int | None = None
    area_counts_seconds: float | None = None
    area_source: str = ""
    relative_shape_schema_version: str = ""
    relative_shape_point_count: int | None = None
    relative_shape_content_hash: str = ""
    fact_content_id: str = ""
    selected_work_unit_key: str = ""
    selected_fact_content_id: str = ""
    owner_backfill_cell_content_id: str = ""
    production_cell_decision_id: str = ""
    cell_status: str = ""
    consolidation_state: str = ""
    consolidation_winner_group_hypothesis_id: str = ""
    consolidation_source_group_hypothesis_id: str = ""
    matrix_value_counts_seconds: float | None = None
    matrix_value_source: str = ""
    writer_schema_version: str = ""
    cell_projection_id: str = ""

    @property
    def segment_candidate_count(self) -> int | None:
        return (
            len(self.segment_candidate_ids)
            if self.row_kind == "physical_question"
            else None
        )

    @property
    def admissible_segment_count(self) -> int | None:
        return (
            len(self.admissible_segment_ids)
            if self.row_kind == "physical_question"
            else None
        )

    @property
    def projection_content_id(self) -> str:
        payload = [
            _semantic_field_text(self, name)
            for name in BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS[:-1]
        ]
        digest = hashlib.sha256(_compact_json_bytes(payload)).hexdigest()
        return f"{BACKFILL_PROVENANCE_ROW_ID_PREFIX}{digest}"

    def __post_init__(self) -> None:
        _validate_row(self)


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceBatch:
    rows: tuple[BackfillPeakFactProvenanceRow, ...]

    def __post_init__(self) -> None:
        _validate_batch(self.rows)


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceReadResult:
    rows: tuple[BackfillPeakFactProvenanceRow, ...]
    projection_integrity: Literal["verified"] = "verified"
    stage_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )
    physical_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )
    cell_projection_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )


def _run_profile_row(
    profile: SignalProcessingProfile,
) -> BackfillPeakFactProvenanceRow:
    return BackfillPeakFactProvenanceRow(
        row_kind="run_profile",
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
    )


def _cell_projection_row(
    projection: BackfillCellProjection,
    profile: SignalProcessingProfile,
) -> BackfillPeakFactProvenanceRow:
    if projection.profile_id != profile.profile_id:
        raise ValueError("cell projection crosses signal-processing profile")
    return BackfillPeakFactProvenanceRow(
        row_kind="cell_projection",
        group_hypothesis_id=projection.group_hypothesis_id,
        public_family_id=projection.public_family_id,
        sample_stem=projection.sample_stem,
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
        selected_work_unit_key=projection.selected_work_unit_key,
        selected_fact_content_id=projection.selected_fact_content_id,
        owner_backfill_cell_content_id=projection.owner_backfill_cell_content_id,
        production_cell_decision_id=projection.production_cell_decision_id,
        cell_status=projection.cell_status,
        consolidation_state=projection.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            projection.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            projection.consolidation_source_group_hypothesis_id
        ),
        matrix_value_counts_seconds=projection.matrix_value_counts_seconds,
        matrix_value_source=projection.matrix_value_source,
        writer_schema_version=projection.writer_schema_version,
        cell_projection_id=projection.cell_projection_id,
    )


def build_backfill_peak_fact_provenance_batch_from_rows(
    physical_rows: Sequence[BackfillPeakFactProvenanceRow],
    *,
    profile: SignalProcessingProfile,
    cell_projections: Sequence[BackfillCellProjection] = (),
) -> BackfillPeakFactProvenanceBatch:
    """Freeze already owner-projected physical rows with final cell authority."""

    if any(row.row_kind != "physical_question" for row in physical_rows):
        raise ValueError("prepared Backfill rows must be physical-question rows")
    return BackfillPeakFactProvenanceBatch(
        rows=(
            _run_profile_row(profile),
            *physical_rows,
            *(_cell_projection_row(item, profile) for item in cell_projections),
        )
    )


def write_backfill_peak_fact_provenance_tsv(
    path: Path,
    batch: BackfillPeakFactProvenanceBatch,
) -> Path:
    if not isinstance(batch, BackfillPeakFactProvenanceBatch):
        raise TypeError("batch must be a typed provenance batch")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        expected = hashlib.sha256()
        expected_size = 0
        with temporary.open("wb") as handle:
            for chunk in _iter_backfill_peak_fact_provenance_chunks(batch):
                handle.write(chunk)
                expected.update(chunk)
                expected_size += len(chunk)
        landed = hashlib.sha256()
        landed_size = 0
        with temporary.open("rb") as handle:
            for block in iter(lambda: handle.read(_PROVENANCE_VERIFY_BLOCK_BYTES), b""):
                landed.update(block)
                landed_size += len(block)
        if landed_size != expected_size or landed.digest() != expected.digest():
            raise OSError("provenance temporary write verification failed")
        temporary.replace(path)
        return path
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _iter_backfill_peak_fact_provenance_chunks(
    batch: BackfillPeakFactProvenanceBatch,
) -> Iterator[bytes]:
    """Yield exactly the bytes render_...() returns, in row-aligned chunks."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    pending = 0
    for row in batch.rows:
        writer.writerow(_row_projection(row))
        pending += 1
        if pending >= _PROVENANCE_ROWS_PER_CHUNK:
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)
            pending = 0
    tail = buffer.getvalue()
    if tail:
        yield tail.encode("utf-8")


def render_backfill_peak_fact_provenance_tsv(
    batch: BackfillPeakFactProvenanceBatch,
) -> bytes:
    if not isinstance(batch, BackfillPeakFactProvenanceBatch):
        raise TypeError("batch must be a typed provenance batch")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in batch.rows:
        writer.writerow(_row_projection(row))
    return buffer.getvalue().encode("utf-8")


def read_backfill_peak_fact_provenance_tsv(
    path: Path,
) -> BackfillPeakFactProvenanceReadResult:
    payload = path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{path}: UTF-8 BOM is forbidden")
    if not payload.endswith(b"\n"):
        raise ValueError(f"{path}: provenance must end with one LF-terminated row")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: provenance must be strict UTF-8") from exc
    if "\r" in text:
        raise ValueError(f"{path}: provenance must use LF line endings")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    _validate_exact_header(path, reader.fieldnames)
    rows: list[BackfillPeakFactProvenanceRow] = []
    for row_number, raw in enumerate(reader, start=2):
        try:
            projection = _strict_projection(raw)
            expected_id = projection.pop("projection_content_id")
            row = _row_from_projection(projection)
            if expected_id != row.projection_content_id:
                raise ValueError("projection_content_id does not match row content")
            rows.append(row)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: row {row_number}: {exc}") from exc
    batch = BackfillPeakFactProvenanceBatch(tuple(rows))
    if render_backfill_peak_fact_provenance_tsv(batch) != payload:
        raise ValueError(f"{path}: provenance bytes are not canonical writer output")
    return BackfillPeakFactProvenanceReadResult(rows=batch.rows)


def _validate_row(row: BackfillPeakFactProvenanceRow) -> None:
    if row.row_kind not in {"run_profile", "physical_question", "cell_projection"}:
        raise ValueError("unknown provenance row_kind")
    if row.schema_version != BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION:
        raise ValueError("unsupported provenance schema_version")
    populated = _populated_fields(row)
    if row.row_kind == "run_profile":
        allowed = _ALWAYS_FIELDS | _PROFILE_FIELDS
    elif row.row_kind == "physical_question":
        allowed = (
            _ALWAYS_FIELDS | _PROFILE_FIELDS | _STAGE_FIELDS | _TRACE_QUESTION_FIELDS
        )
    else:
        allowed = (
            _ALWAYS_FIELDS
            | _PROFILE_FIELDS
            | {
                "group_hypothesis_id",
                "public_family_id",
                "sample_stem",
            }
            | _CELL_FIELDS
        )
    outside = populated - allowed
    if outside:
        raise ValueError(
            f"{row.row_kind} populates forbidden fields: {tuple(sorted(outside))!r}"
        )
    _validate_profile_fields(row)
    if row.row_kind == "physical_question":
        _validate_physical_row(row)
    elif row.row_kind == "cell_projection":
        _validate_cell_row(row)


def _validate_profile_fields(row: BackfillPeakFactProvenanceRow) -> None:
    for name in (
        "profile_schema_version",
        "profile_id",
        "profile_hash",
        "signal_processing_method",
        "physical_signal_formula",
    ):
        _required_text(getattr(row, name), field=name)
    canonical_profile = SignalProcessingProfile(
        schema_version=row.profile_schema_version,
        method=row.signal_processing_method,
        window_points=cast(int, row.signal_processing_window_points),
        physical_signal_formula=row.physical_signal_formula,
    )
    if row.profile_hash != canonical_profile.profile_hash:
        raise ValueError("profile_hash does not match canonical profile content")
    if row.profile_id != canonical_profile.profile_id:
        raise ValueError("profile_id does not match canonical profile content")


def _validate_physical_row(row: BackfillPeakFactProvenanceRow) -> None:
    for name in (
        "work_unit_key",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "candidate_phase",
        "request_identity_id",
        "acquisition_source_id",
        "trace_content_id",
        "prepared_signal_id",
        "segment_universe_id",
        "physical_question_schema_version",
        "physical_question_id",
        "selection_subject_id",
        "selection_policy_version",
        "state",
        "physical_answer_id",
    ):
        _required_text(cast(str, getattr(row, name)), field=name)
    _nonnegative_int(row.candidate_index, field="candidate_index")
    _nonnegative_int(row.trace_point_count, field="trace_point_count")
    _required_finite_float(row.query_rt_min, field="query_rt_min")
    _required_finite_float(row.context_rt_start_min, field="context_rt_start_min")
    _required_finite_float(row.context_rt_end_min, field="context_rt_end_min")
    _validate_optional_range(
        row.trace_rt_start_min, row.trace_rt_end_min, label="trace"
    )
    _validate_optional_range(
        row.context_rt_start_min, row.context_rt_end_min, label="context"
    )
    _unique_string_tuple(row.segment_candidate_ids, field="segment_candidate_ids")
    _unique_string_tuple(row.admissible_segment_ids, field="admissible_segment_ids")
    if not set(row.admissible_segment_ids).issubset(row.segment_candidate_ids):
        raise ValueError("admissible segments must be a subset of the segment universe")
    _require_typed_id(
        row.trace_content_id, prefix="trace-content:", field="trace_content_id"
    )
    _require_typed_id(
        row.prepared_signal_id, prefix="prepared-signal:", field="prepared_signal_id"
    )
    _require_typed_id(
        row.segment_universe_id,
        prefix="segment-universe:",
        field="segment_universe_id",
    )
    _require_typed_id(
        row.physical_question_id,
        prefix="physical-question:",
        field="physical_question_id",
    )
    _require_typed_id(
        row.physical_answer_id, prefix="physical-answer:", field="physical_answer_id"
    )
    if row.state == "available":
        for name in (
            "physical_segment_id",
            "area_source",
            "relative_shape_schema_version",
            "relative_shape_content_hash",
            "fact_content_id",
        ):
            _required_text(cast(str, getattr(row, name)), field=name)
        for name in (
            "apex_rt_min",
            "left_rt_min",
            "right_rt_min",
            "height",
            "area_counts_seconds",
        ):
            _required_finite_float(cast(float | None, getattr(row, name)), field=name)
        _positive_int(row.scan_support_count, field="scan_support_count")
        _positive_int(
            row.relative_shape_point_count, field="relative_shape_point_count"
        )
        _require_typed_id(
            row.physical_segment_id,
            prefix="physical-segment:",
            field="physical_segment_id",
        )
        _require_typed_id(
            row.fact_content_id, prefix="physical-facts:", field="fact_content_id"
        )
        _require_lower_sha256(
            row.relative_shape_content_hash, field="relative_shape_content_hash"
        )
    elif row.state in {"unavailable", "failed"}:
        _required_text(row.reason, field="reason")
        terminal_fields = (
            row.physical_segment_id,
            row.apex_rt_min,
            row.left_rt_min,
            row.right_rt_min,
            row.height,
            row.scan_support_count,
            row.area_counts_seconds,
            row.area_source,
            row.relative_shape_schema_version,
            row.relative_shape_point_count,
            row.relative_shape_content_hash,
            row.fact_content_id,
        )
        if any(value not in {"", None} for value in terminal_fields):
            raise ValueError("non-available physical row must not assert facts")
    else:
        raise ValueError("unsupported physical state")


def _validate_cell_row(row: BackfillPeakFactProvenanceRow) -> None:
    for name in (
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "selected_work_unit_key",
        "selected_fact_content_id",
        "owner_backfill_cell_content_id",
        "production_cell_decision_id",
        "cell_status",
        "consolidation_state",
        "matrix_value_source",
        "writer_schema_version",
        "cell_projection_id",
    ):
        _required_text(cast(str, getattr(row, name)), field=name)
    _required_finite_float(
        row.matrix_value_counts_seconds, field="matrix_value_counts_seconds"
    )
    for name in (
        "consolidation_winner_group_hypothesis_id",
        "consolidation_source_group_hypothesis_id",
    ):
        value = getattr(row, name)
        if value:
            _required_text(value, field=name)
    if row.writer_schema_version != BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION:
        raise ValueError("unsupported provenance writer_schema_version")
    _require_typed_id(
        row.selected_fact_content_id,
        prefix="physical-facts:",
        field="selected_fact_content_id",
    )
    _require_typed_id(
        row.owner_backfill_cell_content_id,
        prefix=BACKFILL_AUTHORITY_CELL_ID_PREFIX,
        field="owner_backfill_cell_content_id",
    )
    _require_typed_id(
        row.production_cell_decision_id,
        prefix=PRODUCTION_CELL_DECISION_ID_PREFIX,
        field="production_cell_decision_id",
    )
    _require_typed_id(
        row.cell_projection_id,
        prefix=BACKFILL_CELL_PROJECTION_ID_PREFIX,
        field="cell_projection_id",
    )
    expected = BackfillCellProjection(
        group_hypothesis_id=row.group_hypothesis_id,
        public_family_id=row.public_family_id,
        sample_stem=row.sample_stem,
        profile_id=row.profile_id,
        selected_work_unit_key=row.selected_work_unit_key,
        selected_fact_content_id=row.selected_fact_content_id,
        owner_backfill_cell_content_id=row.owner_backfill_cell_content_id,
        production_cell_decision_id=row.production_cell_decision_id,
        cell_status=row.cell_status,
        consolidation_state=row.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            row.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            row.consolidation_source_group_hypothesis_id
        ),
        matrix_value_counts_seconds=cast(float, row.matrix_value_counts_seconds),
        matrix_value_source=row.matrix_value_source,
        writer_schema_version=row.writer_schema_version,
    )
    if row.cell_projection_id != expected.cell_projection_id:
        raise ValueError("cell_projection_id does not match row content")


def _validate_batch(rows: Sequence[BackfillPeakFactProvenanceRow]) -> None:
    if not rows or rows[0].row_kind != "run_profile":
        raise ValueError("provenance requires one first run_profile row")
    if sum(row.row_kind == "run_profile" for row in rows) != 1:
        raise ValueError("provenance requires exactly one run_profile row")
    kinds = tuple(row.row_kind for row in rows)
    row_kind_order: dict[RowKind, int] = {
        "run_profile": 0,
        "physical_question": 1,
        "cell_projection": 2,
    }
    if kinds != tuple(sorted(kinds, key=row_kind_order.__getitem__)):
        raise ValueError("provenance row kinds are out of order")
    envelope = rows[0]
    work_rows: dict[str, BackfillPeakFactProvenanceRow] = {}
    cell_keys: set[tuple[str, str]] = set()
    for row in rows[1:]:
        if _profile_binding(row) != _profile_binding(envelope):
            raise ValueError("provenance row crosses the run profile")
        if row.row_kind == "physical_question":
            if row.work_unit_key in work_rows:
                raise ValueError("duplicate work_unit_key")
            work_rows[row.work_unit_key] = row
        else:
            key = (row.group_hypothesis_id, row.sample_stem)
            if key in cell_keys:
                raise ValueError("duplicate final cell key")
            cell_keys.add(key)
            selected = work_rows.get(row.selected_work_unit_key)
            if (
                selected is None
                or selected.fact_content_id != row.selected_fact_content_id
            ):
                raise ValueError("cell projection lacks its selected physical fact")
            if selected.state != "available":
                raise ValueError("cell projection selects a non-available physical row")
            if selected.sample_stem != row.sample_stem:
                raise ValueError("cell projection crosses selected work sample")
            if row.consolidation_state == "moved_to_primary_winner":
                if (
                    row.consolidation_source_group_hypothesis_id
                    != selected.group_hypothesis_id
                    or row.consolidation_winner_group_hypothesis_id
                    != row.group_hypothesis_id
                ):
                    raise ValueError("cell projection has invalid moved lineage")
            else:
                if (
                    selected.group_hypothesis_id,
                    selected.public_family_id,
                ) != (row.group_hypothesis_id, row.public_family_id):
                    raise ValueError("cell projection crosses selected work identity")
                if row.consolidation_state == "not_consolidated":
                    if (
                        row.consolidation_winner_group_hypothesis_id
                        or row.consolidation_source_group_hypothesis_id
                    ):
                        raise ValueError("unconsolidated cell must not assert lineage")
                elif row.consolidation_state == "primary_winner":
                    if (
                        row.consolidation_winner_group_hypothesis_id
                        != row.group_hypothesis_id
                        or selected.group_hypothesis_id
                        not in row.consolidation_source_group_hypothesis_id.split(";")
                    ):
                        raise ValueError(
                            "cell projection has invalid primary lineage"
                        )
                else:
                    raise ValueError("unsupported cell consolidation state")


def _row_projection(row: BackfillPeakFactProvenanceRow) -> dict[str, str]:
    return {
        name: row.projection_content_id
        if name == "projection_content_id"
        else _semantic_field_text(row, name)
        for name in BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS
    }


def _semantic_field_text(row: BackfillPeakFactProvenanceRow, name: str) -> str:
    if name == "segment_candidate_count":
        value: object = row.segment_candidate_count
    elif name == "admissible_segment_count":
        value = row.admissible_segment_count
    else:
        value = getattr(row, name)
    if value is None:
        return ""
    if isinstance(value, str):
        _safe_text(value, field=name)
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return _float_text(value)
    if isinstance(value, tuple):
        if row.row_kind != "physical_question":
            return ""
        _unique_string_tuple(value, field=name)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise TypeError(f"{name} has no TSV scalar codec")


def _row_from_projection(raw: Mapping[str, str]) -> BackfillPeakFactProvenanceRow:
    kind = raw["row_kind"]
    if kind not in {"run_profile", "physical_question", "cell_projection"}:
        raise ValueError("unknown provenance row_kind")
    return BackfillPeakFactProvenanceRow(
        row_kind=cast(RowKind, kind),
        schema_version=_required_projection_text(raw, "schema_version"),
        work_unit_key=raw["work_unit_key"],
        group_hypothesis_id=raw["group_hypothesis_id"],
        public_family_id=raw["public_family_id"],
        sample_stem=raw["sample_stem"],
        candidate_index=_parse_optional_int(raw, "candidate_index"),
        candidate_phase=raw["candidate_phase"],
        request_identity_id=raw["request_identity_id"],
        acquisition_source_id=raw["acquisition_source_id"],
        trace_content_id=raw["trace_content_id"],
        trace_point_count=_parse_optional_int(raw, "trace_point_count"),
        trace_rt_start_min=_parse_optional_float(raw, "trace_rt_start_min"),
        trace_rt_end_min=_parse_optional_float(raw, "trace_rt_end_min"),
        profile_schema_version=raw["profile_schema_version"],
        profile_id=raw["profile_id"],
        profile_hash=raw["profile_hash"],
        signal_processing_method=raw["signal_processing_method"],
        signal_processing_window_points=_parse_optional_int(
            raw, "signal_processing_window_points"
        ),
        physical_signal_formula=raw["physical_signal_formula"],
        prepared_signal_id=raw["prepared_signal_id"],
        segment_universe_id=raw["segment_universe_id"],
        segment_candidate_ids=_parse_json_string_tuple(raw, "segment_candidate_ids"),
        physical_question_schema_version=raw["physical_question_schema_version"],
        physical_question_id=raw["physical_question_id"],
        selection_subject_id=raw["selection_subject_id"],
        query_rt_min=_parse_optional_float(raw, "query_rt_min"),
        context_rt_start_min=_parse_optional_float(raw, "context_rt_start_min"),
        context_rt_end_min=_parse_optional_float(raw, "context_rt_end_min"),
        admissible_segment_ids=_parse_json_string_tuple(raw, "admissible_segment_ids"),
        selection_policy_version=raw["selection_policy_version"],
        state=cast(PhysicalPeakState | Literal[""], raw["state"]),
        reason=raw["reason"],
        physical_answer_id=raw["physical_answer_id"],
        physical_segment_id=raw["physical_segment_id"],
        apex_rt_min=_parse_optional_float(raw, "apex_rt_min"),
        left_rt_min=_parse_optional_float(raw, "left_rt_min"),
        right_rt_min=_parse_optional_float(raw, "right_rt_min"),
        height=_parse_optional_float(raw, "height"),
        scan_support_count=_parse_optional_int(raw, "scan_support_count"),
        area_counts_seconds=_parse_optional_float(raw, "area_counts_seconds"),
        area_source=raw["area_source"],
        relative_shape_schema_version=raw["relative_shape_schema_version"],
        relative_shape_point_count=_parse_optional_int(
            raw, "relative_shape_point_count"
        ),
        relative_shape_content_hash=raw["relative_shape_content_hash"],
        fact_content_id=raw["fact_content_id"],
        selected_work_unit_key=raw["selected_work_unit_key"],
        selected_fact_content_id=raw["selected_fact_content_id"],
        owner_backfill_cell_content_id=raw["owner_backfill_cell_content_id"],
        production_cell_decision_id=raw["production_cell_decision_id"],
        cell_status=raw["cell_status"],
        consolidation_state=raw["consolidation_state"],
        consolidation_winner_group_hypothesis_id=(
            raw["consolidation_winner_group_hypothesis_id"]
        ),
        consolidation_source_group_hypothesis_id=(
            raw["consolidation_source_group_hypothesis_id"]
        ),
        matrix_value_counts_seconds=_parse_optional_float(
            raw, "matrix_value_counts_seconds"
        ),
        matrix_value_source=raw["matrix_value_source"],
        writer_schema_version=raw["writer_schema_version"],
        cell_projection_id=raw["cell_projection_id"],
    )


def _strict_projection(
    raw: Mapping[str | None, str | list[str] | None],
) -> dict[str, str]:
    if tuple(raw) != BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        raise ValueError("row does not match the exact provenance schema")
    projection: dict[str, str] = {}
    for name in BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        value = raw[name]
        if not isinstance(value, str):
            raise ValueError("row has missing or excess fields")
        _safe_text(value, field=name)
        projection[name] = value
    _require_typed_id(
        projection["projection_content_id"],
        prefix=BACKFILL_PROVENANCE_ROW_ID_PREFIX,
        field="projection_content_id",
    )
    for count_name, list_name in (
        ("segment_candidate_count", "segment_candidate_ids"),
        ("admissible_segment_count", "admissible_segment_ids"),
    ):
        count_text = projection[count_name]
        list_values = _parse_json_string_tuple(projection, list_name)
        if count_text:
            if _parse_nonnegative_int_text(count_text, field=count_name) != len(
                list_values
            ):
                raise ValueError(f"{count_name} does not match {list_name}")
        elif list_values:
            raise ValueError(f"{list_name} requires {count_name}")
    return projection


def _validate_exact_header(path: Path, header: Sequence[str] | None) -> None:
    if len(tuple(header or ())) == 58:
        raise ValueError(
            f"{path}: incomplete provenance is not readable; rerun alignment"
        )
    if tuple(header or ()) != BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        raise ValueError(f"{path}: expected exact provenance schema")


def _parse_json_string_tuple(row: Mapping[str, str], field: str) -> tuple[str, ...]:
    text = row[field]
    if not text:
        return ()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be compact JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a JSON array of strings")
    result = tuple(value)
    _unique_string_tuple(result, field=field)
    if json.dumps(result, ensure_ascii=False, separators=(",", ":")) != text:
        raise ValueError(f"{field} must use the canonical compact JSON codec")
    return result


def _parse_optional_int(row: Mapping[str, str], field: str) -> int | None:
    text = row[field]
    return None if not text else _parse_nonnegative_int_text(text, field=field)


def _parse_nonnegative_int_text(text: str, *, field: str) -> int:
    if _NONNEGATIVE_INTEGER.fullmatch(text) is None:
        raise ValueError(f"{field} must be a canonical non-negative integer")
    return int(text)


def _parse_optional_float(row: Mapping[str, str], field: str) -> float | None:
    text = row[field]
    if not text:
        return None
    try:
        value = float.fromhex(text)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a canonical finite hexadecimal float"
        ) from exc
    if not math.isfinite(value) or _float_text(value) != text:
        raise ValueError(f"{field} must be a canonical finite hexadecimal float")
    return value


def _required_projection_text(row: Mapping[str, str], field: str) -> str:
    return _required_text(row[field], field=field)


def _populated_fields(row: BackfillPeakFactProvenanceRow) -> set[str]:
    projection = _row_projection(row)
    return {name for name, value in projection.items() if value != ""}


def _profile_binding(row: BackfillPeakFactProvenanceRow) -> tuple[object, ...]:
    return tuple(getattr(row, name) for name in _PROFILE_FIELDS)


EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH = BackfillPeakFactProvenanceBatch(
    (_run_profile_row(DEFAULT_SIGNAL_PROCESSING_PROFILE),)
)


__all__ = [
    "EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH",
    "BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS",
    "BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION",
    "BACKFILL_PROVENANCE_ROW_ID_PREFIX",
    "BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION",
    "ClaimState",
    "BackfillPeakFactProvenanceBatch",
    "BackfillPeakFactProvenanceReadResult",
    "BackfillPeakFactProvenanceRow",
    "RowKind",
    "_PROVENANCE_ROWS_PER_CHUNK",
    "_PROVENANCE_VERIFY_BLOCK_BYTES",
    "_cell_projection_row",
    "_iter_backfill_peak_fact_provenance_chunks",
    "_parse_json_string_tuple",
    "_parse_nonnegative_int_text",
    "_parse_optional_float",
    "_parse_optional_int",
    "_populated_fields",
    "_profile_binding",
    "_required_projection_text",
    "_row_from_projection",
    "_row_projection",
    "_run_profile_row",
    "_semantic_field_text",
    "_strict_projection",
    "_validate_batch",
    "_validate_cell_row",
    "_validate_exact_header",
    "_validate_physical_row",
    "_validate_profile_fields",
    "_validate_row",
    "build_backfill_peak_fact_provenance_batch_from_rows",
    "read_backfill_peak_fact_provenance_tsv",
    "render_backfill_peak_fact_provenance_tsv",
    "write_backfill_peak_fact_provenance_tsv",
]
