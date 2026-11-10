"""SQLite format of completed Alignment evidence and read-only connection lifetime.

Each concept has one table: a run summary, samples, peaks, method subjects,
Discovery candidates (with their terminal observation), final groups, initial
groups, group members, cells, method assessments and exclusions. Initial edges
and curves have tables only in runs that retain them. Rows refer to each other
by integer row ids; a list of references is a JSON array of row ids.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .errors import ExplanationError, ExplanationUnavailableError
from .review import SUPPORT_ORIGINS

EXPLANATION_ARTIFACT_NAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_evidence"
RecordKey = str | tuple[str, str]

# Summary keys written by the producer; counts are derived when read.
STORED_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "plot_evidence_capability",
        "diagnostic_only",
        "completion",
        "scientific_input_schema_version",
        "processing_algorithm_version",
        "processing_contract_version",
        "processing_attempt_id",
        "acquisition_sources",
        "matrix_row_count",
        "matrix_path",
        "matrix_sha256",
        "alignment_config",
        "neutral_loss_by_tag",
        "retention",
        "selection_evidence_capability",
        "curve_retention",
        "curve_mz_ranges",
        "initial_edge_coverage",
        "audit_availability",
        "sample_rt_calibration",
        "injection_order",
    }
)

TABLES: dict[str, tuple[str, ...]] = {
    "run": ("payload",),
    "samples": ("id", "sample_key", "sample_stem"),
    "peaks": ("id", "sample", "physical_peak_key", "mz", "rt_apex_min", "ppm"),
    "method_subjects": (
        "id",
        "method_subject_id",
        "subject_kind",
        "precursor_mz",
        "precursor_tolerance_ppm",
        "neutral_loss_da",
        "composition",
    ),
    "candidates": (
        "id",
        "candidate_id",
        "sample",
        "precursor_mz",
        "ms1_apex_mz",
        "neutral_loss_tag",
        "peak_status",
        "method_subject",
        "terminal_variant",
        "hypothesis_id",
        "terminal_peak",
        "physical_state",
        "physical_reason",
        "rt_apex_min",
    ),
    "groups": (
        "id",
        "feature_id",
        "disposition",
        "destination",
        "center_mz",
        "ppm",
        "candidates",
    ),
    "initial_groups": (
        "id",
        "disposition",
        "inference_basis",
        "inference_decisions",
        "candidates",
    ),
    "members": (
        "id",
        "subject_id",
        "sample",
        "subject_kind",
        "initial_group",
        "final_group",
        "peaks",
        "accepted_risk",
        "risk_flags",
    ),
    "cells": (
        "group_id",
        "sample",
        "variant",
        "value",
        "apex_rt_min",
        "source",
        "support_origin",
        "peaks",
        "position_rt_min",
        "risks",
        "details",
    ),
    "method_assessments": (
        "id",
        "sample",
        "method_subject",
        "provider_id",
        "method_id",
        "method_version",
        "detector_invocation_id",
        "technical_state",
        "method_result",
        "reason_codes",
        "peaks",
        "acquisition_source_id",
        "evaluation_receipt",
    ),
    "exclusions": ("candidate", "reason_code"),
}
OPTIONAL_TABLES: dict[str, tuple[str, ...]] = {
    "initial_edges": (
        "id",
        "left_subject_id",
        "right_subject_id",
        "disposition",
        "initial_evidence",
        "initial_context",
        "alignment_config",
        "candidates",
    ),
    "curves": (
        "payload_id",
        "sample_id",
        "kind",
        "record_id",
        "candidate_id",
        "physical_peak_key",
        "codec",
        "payload",
        "candidates",
    ),
}

_DDL = {
    "run": "CREATE TABLE run (payload TEXT NOT NULL)",
    "samples": (
        "CREATE TABLE samples (id INTEGER PRIMARY KEY, "
        "sample_key TEXT UNIQUE NOT NULL, sample_stem TEXT NOT NULL)"
    ),
    "peaks": (
        "CREATE TABLE peaks (id INTEGER PRIMARY KEY, sample INTEGER NOT NULL, "
        "physical_peak_key TEXT NOT NULL, mz REAL, rt_apex_min REAL, ppm REAL, "
        "UNIQUE(sample, physical_peak_key))"
    ),
    "method_subjects": (
        "CREATE TABLE method_subjects (id INTEGER PRIMARY KEY, "
        "method_subject_id TEXT UNIQUE NOT NULL, subject_kind TEXT, "
        "precursor_mz REAL, precursor_tolerance_ppm REAL, neutral_loss_da REAL, "
        "composition TEXT)"
    ),
    "candidates": (
        "CREATE TABLE candidates (id INTEGER PRIMARY KEY, "
        "candidate_id TEXT NOT NULL, sample INTEGER NOT NULL, precursor_mz REAL, "
        "ms1_apex_mz REAL, neutral_loss_tag TEXT, peak_status TEXT, "
        "method_subject INTEGER, terminal_variant TEXT, hypothesis_id TEXT, "
        "terminal_peak INTEGER, physical_state TEXT, physical_reason TEXT, "
        "rt_apex_min REAL, UNIQUE(candidate_id, sample))"
    ),
    "groups": (
        "CREATE TABLE groups (id INTEGER PRIMARY KEY, feature_id TEXT UNIQUE, "
        "disposition TEXT, destination TEXT, center_mz REAL, ppm REAL, "
        "candidates TEXT NOT NULL)"
    ),
    "initial_groups": (
        "CREATE TABLE initial_groups (id INTEGER PRIMARY KEY, disposition TEXT, "
        "inference_basis BLOB, inference_decisions BLOB, candidates TEXT NOT NULL)"
    ),
    "members": (
        "CREATE TABLE members (id INTEGER PRIMARY KEY, subject_id TEXT NOT NULL, "
        "sample INTEGER NOT NULL, subject_kind TEXT, initial_group INTEGER, "
        "final_group INTEGER, peaks TEXT NOT NULL, accepted_risk BLOB, "
        "risk_flags TEXT, UNIQUE(sample, subject_id))"
    ),
    "cells": (
        "CREATE TABLE cells (group_id INTEGER NOT NULL, sample INTEGER NOT NULL, "
        "variant TEXT NOT NULL, value REAL, apex_rt_min REAL, source TEXT, "
        "support_origin TEXT NOT NULL, peaks TEXT NOT NULL, position_rt_min REAL, "
        "risks BLOB, details BLOB NOT NULL, PRIMARY KEY(group_id, sample)) "
        "WITHOUT ROWID"
    ),
    "method_assessments": (
        "CREATE TABLE method_assessments (id INTEGER PRIMARY KEY, "
        "sample INTEGER NOT NULL, method_subject INTEGER NOT NULL, provider_id TEXT, "
        "method_id TEXT, method_version TEXT, detector_invocation_id TEXT, "
        "technical_state TEXT, method_result TEXT, reason_codes TEXT NOT NULL, "
        "peaks TEXT NOT NULL, acquisition_source_id TEXT, evaluation_receipt TEXT)"
    ),
    "exclusions": (
        "CREATE TABLE exclusions (candidate INTEGER PRIMARY KEY, "
        "reason_code TEXT NOT NULL)"
    ),
    "initial_edges": (
        "CREATE TABLE initial_edges (id INTEGER PRIMARY KEY, "
        "left_subject_id TEXT NOT NULL, right_subject_id TEXT NOT NULL, "
        "disposition TEXT, initial_evidence TEXT, initial_context TEXT, "
        "alignment_config TEXT, candidates TEXT NOT NULL, "
        "UNIQUE(left_subject_id, right_subject_id))"
    ),
    "curves": (
        "CREATE TABLE curves (payload_id INTEGER PRIMARY KEY, "
        "sample_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "candidate_id TEXT, physical_peak_key TEXT, codec TEXT NOT NULL, "
        "payload BLOB NOT NULL, candidates TEXT NOT NULL)"
    ),
}
_INDEXES = (
    "CREATE INDEX candidates_subject ON candidates(method_subject, sample)",
    "CREATE INDEX candidates_mz ON candidates(precursor_mz)",
    "CREATE INDEX assessments_subject ON method_assessments(method_subject, sample)",
    "CREATE INDEX members_final ON members(final_group)",
    "CREATE INDEX members_initial ON members(initial_group)",
)


def encode_object(value: Mapping[str, Any] | list[Any] | None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def decode_json(payload: str | bytes | None) -> Any:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        payload = zlib.decompress(payload).decode("utf-8")
    return json.loads(payload)


def encode_details(value: Mapping[str, Any]) -> bytes:
    """Cell details keep the decision owner's field order for display."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return zlib.compress(text.encode("utf-8"), 1)


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(";\n".join((*(_DDL[name] for name in TABLES), *_INDEXES)) + ";")


def create_optional_table(db: sqlite3.Connection, name: str) -> None:
    db.execute(_DDL[name])


def present_tables(db: sqlite3.Connection) -> set[str]:
    return {
        name
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def read_completed_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Admit only the current format; older reports must be regenerated."""
    tables = present_tables(connection)
    if "run" not in tables:
        raise ExplanationError(
            "不支援此解釋資料格式；請重新執行 Alignment 產生新輸出。"
        )
    for table, columns in (
        *TABLES.items(),
        *((name, cols) for name, cols in OPTIONAL_TABLES.items() if name in tables),
    ):
        actual = tuple(
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != columns:
            raise ExplanationError(
                f"malformed explanation schema: {table}; rerun Alignment"
            )
    row = connection.execute("SELECT payload FROM run").fetchone()
    if row is None:
        raise ExplanationError("malformed explanation: missing completion summary")
    stored = decode_json(row[0])
    if not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION:
        raise ExplanationError(
            "不支援此解釋資料格式；請重新執行 Alignment 產生新輸出。"
        )
    if set(stored) != STORED_SUMMARY_KEYS:
        raise ExplanationError(
            "explanation summary fields do not match the schema; rerun Alignment"
        )
    if (
        stored["diagnostic_only"] is not True
        or stored["completion"] != "post_matrix_publication"
        or stored["plot_evidence_capability"]
        not in ("retained_curves", "metadata_only")
    ):
        raise ExplanationError("malformed explanation: invalid completion summary")
    origins = sorted(SUPPORT_ORIGINS)
    if connection.execute(
        "SELECT 1 FROM cells WHERE support_origin NOT IN "
        f"({','.join('?' for _ in origins)}) LIMIT 1",
        origins,
    ).fetchone():
        raise ExplanationError(
            "檢閱資料含有目前版本不再產生的支持來源；請重新執行 Alignment。"
        )
    summary = {**stored, **_derived_counts(connection, tables)}
    summary = {key: summary[key] for key in sorted(summary)}
    summary["samples"] = [
        {"sample_key": key, "sample_stem": stem}
        for key, stem in connection.execute(
            "SELECT sample_key,sample_stem FROM samples ORDER BY id"
        )
    ]
    return summary


def _derived_counts(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    """Counts of stored facts, computed instead of stored a second time."""

    def count(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])

    counts = {
        "cell_decisions": count("SELECT COUNT(*) FROM cells"),
        "exclusions": count("SELECT COUNT(*) FROM exclusions"),
        "final_groups": count("SELECT COUNT(*) FROM groups"),
        "initial_groups": count("SELECT COUNT(*) FROM initial_groups"),
        "method_assessments": count("SELECT COUNT(*) FROM method_assessments"),
        "method_subjects": count("SELECT COUNT(*) FROM method_subjects"),
        "rows": count("SELECT COUNT(*) FROM groups WHERE feature_id IS NOT NULL"),
        "terminal_subjects": count(
            "SELECT COUNT(*) FROM candidates WHERE terminal_variant IS NOT NULL"
        ),
    }
    if "curves" in tables:
        counts["capture_evidence"] = count("SELECT COUNT(*) FROM curves")
    if "initial_edges" in tables:
        counts["initial_edges"] = count("SELECT COUNT(*) FROM initial_edges")
    reasons: dict[str, int] = {}

    def add(reason: Any, number: int = 1) -> None:
        if isinstance(reason, str):
            reasons[reason] = reasons.get(reason, 0) + number

    for code, number in connection.execute(
        "SELECT j.value, COUNT(*) FROM method_assessments, json_each(reason_codes) j "
        "GROUP BY j.value"
    ):
        add(code, number)
    for reason, number in connection.execute(
        "SELECT reason_code, COUNT(*) FROM exclusions GROUP BY reason_code"
    ):
        add(reason, number)
    for reason, number in connection.execute(
        "SELECT disposition, COUNT(*) FROM groups "
        "WHERE feature_id IS NOT NULL GROUP BY disposition"
    ):
        add(reason, number)
    if "initial_edges" in tables:
        for (evidence,) in connection.execute(
            "SELECT initial_evidence FROM initial_edges"
        ):
            add((decode_json(evidence) or {}).get("reason"))
    return {
        "candidate_count": count("SELECT COUNT(*) FROM candidates"),
        "reason_counts": dict(sorted(reasons.items())),
        "record_counts": {key: value for key, value in counts.items() if value},
    }


def selection_mask_width(sample_count: int) -> int:
    """Bytes of one per-sample bit mask in review selection facts."""
    return max(1, (sample_count + 7) // 8)


def validate_details(details: Any) -> dict[str, Any]:
    if (
        not isinstance(details, dict)
        or not {
            "evidence_states",
            "reason_codes",
            "signal_quality",
        }
        <= details.keys()
    ):
        raise ExplanationError(
            "cell details are missing required fields; rerun Alignment"
        )
    return details


@contextmanager
def open_readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Close on every exit and translate persisted-input failures at the boundary.

    SQLite's connection context manager only ends a transaction; it does not
    close the connection. Read-only mode still permits request-local temp tables.
    """
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(
            f"explanation unavailable: {path}; rerun Alignment（請重新執行 Alignment）"
        )
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            yield db
    except (
        sqlite3.Error,
        json.JSONDecodeError,
        zlib.error,
        UnicodeError,
        TypeError,
    ) as exc:
        raise ExplanationError(f"malformed explanation: {exc}") from exc
