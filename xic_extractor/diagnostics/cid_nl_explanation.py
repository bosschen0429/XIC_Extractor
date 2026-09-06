"""Versioned diagnostic-only storage and indexed, acquisition-free queries."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

REPORT_FILENAME = "cid_nl_explanation.sqlite"
SCHEMA_VERSION = 1


class ExplanationError(ValueError):
    """The requested diagnostic cannot be interpreted."""


class ExplanationUnavailableError(ExplanationError):
    """No completed explanation is available."""


class CandidateNotFoundError(ExplanationError):
    """The exact candidate ID is absent from this run."""


def write_explanation_report(
    path: Path,
    *,
    summary: Mapping[str, Any],
    candidates: Iterable[tuple[str, str, Mapping[str, Any]]],
    records: Mapping[tuple[str, str], Mapping[str, Any]],
    links: Iterable[tuple[str, str, str, str]],
) -> Path:
    """Publish one complete projection; never overwrite an existing artifact."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"explanation artifact already exists: {path}")
    with temporary.open("xb"):
        pass
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        with connection:
            connection.executescript(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, "
                "payload TEXT NOT NULL, PRIMARY KEY(candidate_id, sample_id));"
                "CREATE TABLE records (kind TEXT, record_id TEXT, "
                "payload TEXT NOT NULL, "
                "PRIMARY KEY(kind, record_id));"
                "CREATE TABLE links (candidate_id TEXT, sample_id TEXT, kind TEXT, "
                "record_id TEXT, "
                "PRIMARY KEY(candidate_id, sample_id, kind, record_id));"
            )
            connection.executemany(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                ((cid, sid, _json(payload)) for cid, sid, payload in candidates),
            )
            connection.executemany(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    (kind, rid, _json(payload))
                    for (kind, rid), payload in records.items()
                ),
            )
            connection.executemany("INSERT INTO links VALUES (?, ?, ?, ?)", links)
            connection.execute(
                "INSERT INTO metadata VALUES ('summary', ?)",
                (
                    _json(
                        dict(
                            summary,
                            schema_version=SCHEMA_VERSION,
                            diagnostic_only=True,
                            completion="post_matrix_publication",
                        )
                    ),
                ),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.close()
        connection = None
        if path.exists():
            raise FileExistsError(f"explanation artifact already exists: {path}")
        os.replace(temporary, path)
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
    return path


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _object(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ExplanationError("malformed explanation: expected object payload")
    return value


def read_cid_nl_explanation(
    path: str | Path, *, candidate_id: str | None = None
) -> dict[str, Any]:
    """Read a compact summary or only records linked to one exact candidate ID."""
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(f"explanation unavailable: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ExplanationError(f"unsupported explanation schema version: {version}")
        expected = {
            "metadata": ("key", "payload"),
            "candidates": ("candidate_id", "sample_id", "payload"),
            "records": ("kind", "record_id", "payload"),
            "links": ("candidate_id", "sample_id", "kind", "record_id"),
        }
        for table, columns in expected.items():
            actual = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != columns:
                raise ExplanationError(f"malformed explanation schema: {table}")
        row = connection.execute(
            "SELECT payload FROM metadata WHERE key='summary'"
        ).fetchone()
        if row is None:
            raise ExplanationError("malformed explanation: missing completion summary")
        summary = _object(row[0])
        if (
            summary.get("schema_version") != SCHEMA_VERSION
            or summary.get("diagnostic_only") is not True
            or summary.get("completion") != "post_matrix_publication"
        ):
            raise ExplanationError("malformed explanation: invalid completion summary")
        if candidate_id is None:
            return summary
        candidates = [
            _object(row[0])
            for row in connection.execute(
                "SELECT payload FROM candidates "
                "WHERE candidate_id=? ORDER BY sample_id",
                (candidate_id,),
            )
        ]
        if not candidates:
            raise CandidateNotFoundError(f"candidate not found: {candidate_id}")
        records: dict[str, list[dict[str, Any]]] = {}
        for kind, _record_id, payload in connection.execute(
            "SELECT DISTINCT r.kind, r.record_id, r.payload FROM links l "
            "LEFT JOIN records r ON r.kind=l.kind AND r.record_id=l.record_id "
            "WHERE l.candidate_id=? ORDER BY r.kind, r.record_id",
            (candidate_id,),
        ):
            records.setdefault(kind, []).append(_object(payload))
        return {
            "schema_version": SCHEMA_VERSION,
            "diagnostic_only": True,
            "completion": summary["completion"],
            "candidate_id": candidate_id,
            "candidates": candidates,
            "records": records,
        }
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
        raise ExplanationError(f"malformed explanation: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
