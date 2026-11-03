"""SQLite format, payload codec, and read-only connection lifetime."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .review import REVIEW_SCHEMA_VERSION, validate_membership_risks

EXPLANATION_ARTIFACT_NAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_explanation"
RecordKey = str | tuple[str, str]


class ExplanationError(ValueError):
    """The requested diagnostic cannot be interpreted."""


class ExplanationUnavailableError(ExplanationError):
    """No completed explanation is available."""


class CandidateNotFoundError(ExplanationError):
    """The exact candidate ID is absent from this run."""


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
        "candidates": ("candidate_id", "sample_id", "payload"),
        "records": ("id", "kind", "record_id", "payload"),
        "link_sets": ("id",),
        "link_members": ("set_id", "candidate_id", "sample_id"),
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
        "samples",
        "acquisition_sources",
        "candidate_count",
        "matrix_row_count",
        "candidate_examples",
        "writer_row_count",
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
    }:
        raise ExplanationError(
            "explanation summary fields do not match the schema; rerun Alignment"
        )
    if any(
        not isinstance(sample, dict) or set(sample) != {"sample_key", "sample_stem"}
        for sample in summary["samples"]
    ):
        raise ExplanationError(
            "explanation sample fields do not match the schema; rerun Alignment"
        )
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("completion") != "post_matrix_publication"
        or summary.get("plot_evidence_capability")
        not in ("retained_curves", "metadata_only")
    ):
        raise ExplanationError("malformed explanation: invalid completion summary")
    if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
        query_tables = {
            "row_cells": ("row_record_id", "payload", "sample_mask"),
            "query_samples": ("id", "sample_id"),
            "query_candidates": ("id", "candidate_id", "sample", "mz", "sample_stem"),
            "query_items": ("id", "kind", "identifier", "group_id", "sort_key"),
            "query_members": ("item", "candidate"),
            "query_scope": ("sample", "item"),
            "query_witnesses": ("item", "ordinal", "candidate", "rt", "observation_id"),
            "query_peers": ("item", "peer", "priority", "edge_ref"),
            "query_review": ("item", "sample_facts"),
        }
        for table, columns in query_tables.items():
            actual = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != columns:
                raise ExplanationError(
                    f"malformed explanation schema: {table}; rerun Alignment"
                )
    return summary


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
    """Create the chosen retention layout before starting its write transaction."""
    db.executescript(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, "
        "payload TEXT NOT NULL, PRIMARY KEY(candidate_id, sample_id));"
        "CREATE TABLE records (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
        "record_id TEXT NOT NULL, payload TEXT NOT NULL, "
        "UNIQUE(kind, record_id));"
        "CREATE TABLE link_sets (id INTEGER PRIMARY KEY);"
        "CREATE TABLE link_members (set_id INTEGER, candidate_id TEXT, "
        "sample_id TEXT, PRIMARY KEY(set_id, candidate_id, sample_id), "
        "FOREIGN KEY(set_id) REFERENCES link_sets(id), "
        "FOREIGN KEY(candidate_id, sample_id) "
        "REFERENCES candidates(candidate_id, sample_id) "
        "DEFERRABLE INITIALLY DEFERRED) "
        "WITHOUT ROWID;"
        "CREATE INDEX idx_link_members_candidate "
        "ON link_members(candidate_id, sample_id, set_id);"
        "CREATE TABLE record_links (set_id INTEGER REFERENCES link_sets(id), "
        "record_rowid INTEGER REFERENCES records(id), "
        "PRIMARY KEY(set_id, record_rowid)) WITHOUT ROWID;"
        "CREATE VIEW links AS SELECT DISTINCT m.candidate_id, m.sample_id, "
        "r.kind, r.record_id FROM link_members m "
        "JOIN record_links l USING(set_id) "
        "JOIN records r ON r.id=l.record_rowid;"
        "CREATE TABLE curve_payloads (payload_id INTEGER PRIMARY KEY, "
        "sample_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "codec TEXT NOT NULL, payload BLOB NOT NULL);"
        "CREATE INDEX idx_curve_payloads_sample_record "
        "ON curve_payloads(sample_id, record_id);"
    )
    if review_only:
        db.execute(
            "CREATE TABLE row_cells (row_record_id INTEGER PRIMARY KEY "
            "REFERENCES records(id) DEFERRABLE INITIALLY DEFERRED, "
            "payload BLOB NOT NULL, "
            "sample_mask BLOB NOT NULL)"
        )

        db.executescript("""
            CREATE TABLE query_samples (id INTEGER PRIMARY KEY, sample_id TEXT UNIQUE);
            CREATE TABLE query_candidates (
                id INTEGER PRIMARY KEY, candidate_id TEXT, sample INTEGER,
                mz REAL, sample_stem TEXT, UNIQUE(candidate_id, sample));
            CREATE TABLE query_items (
                id INTEGER PRIMARY KEY, kind TEXT, identifier TEXT, group_id TEXT,
                sort_key BLOB UNIQUE);
            CREATE TABLE query_members (
                item INTEGER, candidate INTEGER, PRIMARY KEY(item,candidate)
            ) WITHOUT ROWID;
            CREATE INDEX query_members_candidate ON query_members(candidate,item);
            CREATE TABLE query_scope (
                sample INTEGER, item INTEGER, PRIMARY KEY(sample,item)
            ) WITHOUT ROWID;
            CREATE INDEX query_scope_item ON query_scope(item,sample);
            CREATE TABLE query_witnesses (
                item INTEGER, ordinal INTEGER, candidate INTEGER, rt REAL,
                observation_id TEXT, PRIMARY KEY(item,ordinal)
            ) WITHOUT ROWID;
            CREATE INDEX query_candidates_mz ON query_candidates(mz,id);
            CREATE INDEX query_witnesses_candidate ON query_witnesses(candidate,item);
            CREATE INDEX query_witnesses_rt ON query_witnesses(rt,item);
            CREATE INDEX query_items_group ON query_items(group_id,id);
            CREATE TABLE query_peers (
                item INTEGER, peer INTEGER, priority INTEGER, edge_ref TEXT,
                PRIMARY KEY(item,peer,edge_ref)
            ) WITHOUT ROWID;
            CREATE TABLE query_review (item INTEGER PRIMARY KEY, sample_facts BLOB);
        """)

    db.execute("BEGIN")
