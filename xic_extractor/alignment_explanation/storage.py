"""SQLite format, payload codec, and read-only connection lifetime."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .errors import ExplanationError, ExplanationUnavailableError
from .review import (
    REVIEW_SCHEMA_VERSION,
    SELECTION_FLAGS,
    SUPPORT_ORIGINS,
    validate_membership_risks,
)

EXPLANATION_ARTIFACT_NAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_explanation"
RecordKey = str | tuple[str, str]


def encode_object(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def decode_object(payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = zlib.decompress(payload).decode("utf-8")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ExplanationError("malformed explanation: expected object payload")
    if "membership_risks" in value:
        try:
            validate_membership_risks(value["membership_risks"])
        except ValueError as exc:
            raise ExplanationError(f"malformed explanation: {exc}") from exc
    return value


def read_completed_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    expected = {
        "metadata": ("key", "payload"),
        "samples": ("id", "sample_key", "sample_stem"),
        "candidates": (
            "id",
            "candidate_id",
            "sample",
            "precursor_mz",
            "ms1_apex_mz",
            "payload",
        ),
        "records": ("id", "kind", "record_id", "payload"),
        "link_members": ("set_id", "candidate"),
        "record_links": ("set_id", "record_rowid"),
        "links": ("candidate_id", "sample_id", "kind", "record_id"),
        "curve_payloads": (
            "payload_id",
            "sample_id",
            "kind",
            "record_id",
            "codec",
            "payload",
        ),
        "cells": (
            "row_record_id",
            "sample",
            "variant",
            "value",
            "apex_rt_min",
            "source",
            "support_origin",
            "observation_ids",
        ),
        "row_evidence": ("row_record_id", "payload"),
    }
    for table, columns in expected.items():
        actual = tuple(
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != columns:
            raise ExplanationError(
                f"malformed explanation schema: {table}; rerun Alignment"
            )
    row = connection.execute(
        "SELECT payload FROM metadata WHERE key='summary'"
    ).fetchone()
    if row is None:
        raise ExplanationError("malformed explanation: missing completion summary")
    summary = decode_object(row[0])
    reference = summary.get("known_istd_reference")
    if isinstance(reference, dict) and "calibration" in reference:
        raise ExplanationError("檢閱資料仍使用舊版 RT 校正格式；請重新執行 Alignment。")
    if summary.get("schema_version") not in (SCHEMA_VERSION, REVIEW_SCHEMA_VERSION):
        raise ExplanationError(
            "不支援此解釋資料格式；請重新執行 Alignment 產生新輸出。"
        )
    if set(summary) != {
        "schema_version",
        "plot_evidence_capability",
        "diagnostic_only",
        "completion",
        "scientific_input_schema_version",
        "processing_algorithm_version",
        "processing_contract_version",
        "processing_attempt_id",
        "acquisition_sources",
        "candidate_count",
        "matrix_row_count",
        "matrix_path",
        "matrix_sha256",
        "alignment_config",
        "reason_counts",
        "record_counts",
        "retention",
        "selection_evidence_capability",
        "curve_retention",
        "curve_mz_ranges",
        "initial_edge_coverage",
        "audit_availability",
        "known_istd_reference",
        "sample_rt_calibration",
        "injection_order",
    }:
        raise ExplanationError(
            "explanation summary fields do not match the schema; rerun Alignment"
        )
    summary["samples"] = [
        {"sample_key": key, "sample_stem": stem}
        for key, stem in connection.execute(
            "SELECT sample_key,sample_stem FROM samples ORDER BY id"
        )
    ]
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("completion") != "post_matrix_publication"
        or summary.get("plot_evidence_capability")
        not in ("retained_curves", "metadata_only")
    ):
        raise ExplanationError("malformed explanation: invalid completion summary")
    origins = sorted(SUPPORT_ORIGINS)
    unknown_origin = connection.execute(
        "SELECT 1 FROM cells WHERE support_origin NOT IN "
        f"({','.join('?' for _ in origins)}) LIMIT 1",
        origins,
    ).fetchone()
    if unknown_origin is not None:
        raise ExplanationError(
            "檢閱資料含有目前版本不再產生的支持來源；請重新執行 Alignment。"
        )
    if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
        query_tables = {
            "query_items": (
                "id",
                "kind",
                "identifier",
                "group_id",
                "sort_key",
                "sample_facts",
            ),
            "query_scope": ("sample", "item"),
            "query_witnesses": ("item", "ordinal", "candidate", "rt", "observation_id"),
            "query_peers": ("item", "peer", "priority", "edge_ref"),
        }
        for table, columns in query_tables.items():
            actual = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != columns:
                raise ExplanationError(
                    f"malformed explanation schema: {table}; rerun Alignment"
                )
        facts_length = selection_mask_width(len(summary["samples"])) * (
            len(SELECTION_FLAGS) + 1
        )
        stale_facts = connection.execute(
            "SELECT 1 FROM query_items WHERE length(sample_facts) != ? LIMIT 1",
            (facts_length,),
        ).fetchone()
        if stale_facts is not None:
            raise ExplanationError(
                "檢閱資料的樣本選取格式與目前版本不同；請重新執行 Alignment。"
            )
    return summary


def selection_mask_width(sample_count: int) -> int:
    """Bytes of one per-sample bit mask in review selection facts."""
    return max(1, (sample_count + 7) // 8)


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


def create_schema(db: sqlite3.Connection, *, review_only: bool) -> None:
    """One candidate identity and one cell value, shared by both retention modes."""
    db.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE samples (
            id INTEGER PRIMARY KEY, sample_key TEXT UNIQUE NOT NULL,
            sample_stem TEXT NOT NULL);
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL,
            sample INTEGER REFERENCES samples(id), precursor_mz REAL, ms1_apex_mz REAL,
            payload TEXT NOT NULL, UNIQUE(candidate_id,sample));
        CREATE TABLE records (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, record_id TEXT NOT NULL,
            payload BLOB NOT NULL, UNIQUE(kind,record_id));
        CREATE TABLE link_members (
            set_id INTEGER, candidate INTEGER REFERENCES candidates(id)
                DEFERRABLE INITIALLY DEFERRED,
            PRIMARY KEY(set_id,candidate)) WITHOUT ROWID;
        CREATE INDEX idx_link_members_candidate ON link_members(candidate,set_id);
        CREATE TABLE record_links (
            set_id INTEGER, record_rowid INTEGER REFERENCES records(id),
            PRIMARY KEY(set_id,record_rowid)) WITHOUT ROWID;
        CREATE VIEW links AS SELECT DISTINCT c.candidate_id,s.sample_key AS sample_id,
            r.kind,r.record_id FROM link_members m
            JOIN candidates c ON c.id=m.candidate JOIN samples s ON s.id=c.sample
            JOIN record_links l USING(set_id) JOIN records r ON r.id=l.record_rowid;
        CREATE TABLE curve_payloads (
            payload_id INTEGER PRIMARY KEY, sample_id TEXT NOT NULL,
            kind TEXT NOT NULL, record_id TEXT NOT NULL,
            codec TEXT NOT NULL, payload BLOB NOT NULL);
        CREATE INDEX idx_curve_payloads_sample_record
            ON curve_payloads(sample_id,record_id);
        CREATE TABLE cells (
            row_record_id INTEGER REFERENCES records(id) DEFERRABLE INITIALLY DEFERRED,
            sample INTEGER REFERENCES samples(id) DEFERRABLE INITIALLY DEFERRED,
            variant TEXT NOT NULL, value REAL, apex_rt_min REAL, source TEXT,
            support_origin TEXT NOT NULL,
            observation_ids TEXT NOT NULL, PRIMARY KEY(row_record_id,sample)
        ) WITHOUT ROWID;
        CREATE TABLE row_evidence (
            row_record_id INTEGER PRIMARY KEY REFERENCES records(id)
                DEFERRABLE INITIALLY DEFERRED,
            payload BLOB NOT NULL);
    """)
    if review_only:
        db.executescript("""
            CREATE TABLE query_items (
                id INTEGER PRIMARY KEY, kind TEXT, identifier TEXT, group_id TEXT,
                sort_key BLOB UNIQUE, sample_facts BLOB);
            CREATE TABLE query_scope (
                sample INTEGER, item INTEGER, PRIMARY KEY(sample,item)
            ) WITHOUT ROWID;
            CREATE INDEX query_scope_item ON query_scope(item,sample);
            CREATE TABLE query_witnesses (
                item INTEGER, ordinal INTEGER, candidate INTEGER, rt REAL,
                observation_id TEXT, PRIMARY KEY(item,ordinal)
            ) WITHOUT ROWID;
            CREATE INDEX candidates_mz ON candidates(precursor_mz,id);
            CREATE INDEX query_witnesses_candidate ON query_witnesses(candidate,item);
            CREATE INDEX query_witnesses_rt ON query_witnesses(rt,item);
            CREATE INDEX query_items_group ON query_items(group_id,id);
            CREATE TABLE query_peers (
                item INTEGER, peer INTEGER, priority INTEGER, edge_ref TEXT,
                PRIMARY KEY(item,peer,edge_ref)
            ) WITHOUT ROWID;
        """)
    db.execute("BEGIN")


CANDIDATE_SELECT = (
    "SELECT c.id,c.candidate_id,s.sample_key,s.sample_stem,"
    "c.precursor_mz,c.ms1_apex_mz,c.payload "
    "FROM candidates c JOIN samples s ON s.id=c.sample"
)


def decode_candidate(row: tuple) -> tuple[int, dict[str, Any]]:
    """Restore the consumer's candidate facts from their single stored locations."""
    row_id, cid, sid, stem, mz, measured_mz, payload = row
    return row_id, {
        **decode_object(payload),
        "candidate_id": cid,
        "sample_key": sid,
        "sample_stem": stem,
        "precursor_mz": mz,
        "ms1_apex_mz": measured_mz,
    }
