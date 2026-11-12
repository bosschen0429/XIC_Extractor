"""SQLite format of completed Alignment evidence and read-only connection lifetime.

Each concept has one table: a run summary, samples, peaks, method subjects,
Discovery candidates (with their terminal observation), final groups, initial
groups, group members, cells, method assessments and exclusions. A cell with a
value keeps the measured curve of the peak that gave it in ``cell_curves``; full
audit curves have a table only in runs that retain them. Membership comparisons
share their recorded owner facts and ordered peer groups without dropping any
alternative or score. Optional tables are absent from older results. Rows refer
to each other by integer row ids; a list of references is a JSON array of row ids.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .errors import ExplanationError, ExplanationUnavailableError
from .review import SUPPORT_ORIGINS

EXPLANATION_ARTIFACT_NAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_evidence"

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
    ),
    "groups": (
        "id",
        "feature_id",
        "disposition",
        "destination",
        "center_mz",
        "ppm",
    ),
    "initial_groups": (
        "id",
        "disposition",
        "inference_basis",
        "inference_decisions",
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
        "evidence_states",
        "reason_codes",
        "signal_quality",
        "risks",
        "observation",
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
    "membership_owners": ("id", "facts"),
    "membership_owner_groups": ("id", "owners"),
    "cell_curves": ("group_id", "sample", "curve"),
    "curves": (
        "payload_id",
        "sample_id",
        "kind",
        "record_id",
        "candidate_id",
        "physical_peak_key",
        "codec",
        "payload",
        "method_subject",
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
        "terminal_peak INTEGER, UNIQUE(candidate_id, sample))"
    ),
    "groups": (
        "CREATE TABLE groups (id INTEGER PRIMARY KEY, feature_id TEXT UNIQUE, "
        "disposition TEXT, destination TEXT, center_mz REAL, ppm REAL)"
    ),
    "initial_groups": (
        "CREATE TABLE initial_groups (id INTEGER PRIMARY KEY, disposition TEXT, "
        "inference_basis BLOB, inference_decisions BLOB)"
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
        "evidence_states TEXT NOT NULL, reason_codes TEXT NOT NULL, "
        "signal_quality TEXT NOT NULL, risks BLOB, observation BLOB, "
        "PRIMARY KEY(group_id, sample))"
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
    "cell_curves": (
        "CREATE TABLE cell_curves (group_id INTEGER NOT NULL, "
        "sample INTEGER NOT NULL, curve BLOB NOT NULL, "
        "PRIMARY KEY(group_id, sample))"
    ),
    "membership_owner_groups": (
        "CREATE TABLE membership_owner_groups "
        "(id INTEGER PRIMARY KEY, owners BLOB NOT NULL)"
    ),
    "membership_owners": (
        "CREATE TABLE membership_owners (id INTEGER PRIMARY KEY, facts TEXT NOT NULL)"
    ),
    "curves": (
        "CREATE TABLE curves (payload_id INTEGER PRIMARY KEY, "
        "sample_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "candidate_id TEXT, physical_peak_key TEXT, codec TEXT NOT NULL, "
        "payload BLOB NOT NULL, method_subject INTEGER)"
    ),
}
_INDEXES = (
    "CREATE INDEX candidates_subject ON candidates(method_subject, sample)",
    "CREATE INDEX candidates_mz ON candidates(precursor_mz)",
    "CREATE INDEX assessments_subject ON method_assessments(method_subject, sample)",
    "CREATE INDEX members_final ON members(final_group)",
    "CREATE INDEX members_initial ON members(initial_group)",
    "CREATE INDEX candidates_hypothesis ON candidates(sample, hypothesis_id)",
    "CREATE INDEX candidates_terminal ON candidates(terminal_peak)",
    "CREATE INDEX candidates_id ON candidates(candidate_id)",
    "CREATE INDEX cells_sample_apex ON cells(sample, apex_rt_min) "
    "WHERE apex_rt_min IS NOT NULL",
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


# A self-describing BLOB: byte-shuffled little-endian float64, then zlib.
# Each plane holds the same byte of every value (all RTs, then intensities).
_REVIEW_CURVE_FLOAT64 = b"XICF64\x01"


def encode_review_curve(rt: Iterable[float], raw: Iterable[float]) -> bytes:
    """Store both series bit-for-bit, without decimal conversion or rounding."""
    times, intensities = tuple(rt), tuple(raw)
    packed = struct.pack(f"<{2 * len(times)}d", *times, *intensities)
    shuffled = b"".join(packed[byte::8] for byte in range(8))
    return _REVIEW_CURVE_FLOAT64 + zlib.compress(shuffled, 6)


def decode_review_curve(
    payload: bytes,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Read the current lossless binary curve format."""
    if not payload.startswith(_REVIEW_CURVE_FLOAT64):
        raise ExplanationError("unsupported review curve format; rerun Alignment")
    shuffled = zlib.decompress(payload[len(_REVIEW_CURVE_FLOAT64) :])
    if len(shuffled) % 16:
        raise ExplanationError("malformed review curve: unequal float64 series")
    value_count = len(shuffled) // 8
    packed = bytearray(len(shuffled))
    for byte in range(8):
        packed[byte::8] = shuffled[byte * value_count : (byte + 1) * value_count]
    values = struct.unpack(f"<{value_count}d", packed)
    midpoint = value_count // 2
    return values[:midpoint], values[midpoint:]


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
    summary = {**stored, **_derived_counts(connection)}
    summary = {key: summary[key] for key in sorted(summary)}
    summary["samples"] = [
        {"sample_key": key, "sample_stem": stem}
        for key, stem in connection.execute(
            "SELECT sample_key,sample_stem FROM samples ORDER BY id"
        )
    ]
    return summary


def _derived_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    """Counts of stored facts, computed instead of stored a second time."""
    reasons: dict[str, int] = {}
    for query in (
        "SELECT j.value, COUNT(*) FROM method_assessments, json_each(reason_codes) j "
        "GROUP BY j.value",
        "SELECT reason_code, COUNT(*) FROM exclusions GROUP BY reason_code",
        "SELECT disposition, COUNT(*) FROM groups "
        "WHERE feature_id IS NOT NULL GROUP BY disposition",
    ):
        for reason, number in connection.execute(query):
            if isinstance(reason, str):
                reasons[reason] = reasons.get(reason, 0) + number
    return {
        "candidate_count": int(
            connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        ),
        "reason_counts": dict(sorted(reasons.items())),
    }


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
