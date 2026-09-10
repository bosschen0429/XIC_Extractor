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


class ExplanationReportWriter:
    """One bounded SQLite transaction, published only by explicit finalization."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.temporary = self.path.with_name(self.path.name + ".tmp")
        self.connection: sqlite3.Connection | None = None
        self.candidate_count = 0
        self.record_counts: dict[str, int] = {}
        self._candidates: dict[tuple[str, str], dict[str, Any]] = {}
        self._records: dict[tuple[str, str], str] = {}
        self._buffered_records: list[tuple[str, str, str]] = []
        self._buffered_links: list[tuple[str, str, str, str]] = []

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute("PRAGMA page_size = 32768;")
            self.connection.execute("PRAGMA synchronous = OFF;")
            self.connection.execute("PRAGMA journal_mode = MEMORY;")
            self.connection.execute("PRAGMA cache_size = -64000;")
            self.connection.execute("PRAGMA temp_store = MEMORY;")
            self.connection.executescript(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, "
                "payload TEXT NOT NULL, PRIMARY KEY(candidate_id, sample_id));"
                "CREATE TABLE records (kind TEXT, record_id TEXT, "
                "payload TEXT NOT NULL, "
                "PRIMARY KEY(kind, record_id));"
                "CREATE TABLE links (candidate_id TEXT, sample_id TEXT, kind TEXT, "
                "record_id TEXT, "
                "PRIMARY KEY(candidate_id, sample_id, kind, record_id));"
                "BEGIN;"
            )
        except BaseException:
            self.abort()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self.abort()

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("explanation writer is not active")
        return self.connection

    def add_candidate(self, cid: str, sid: str, payload: Mapping[str, Any]) -> None:
        key = (cid, sid)
        if key in self._candidates:
            raise ValueError("duplicate diagnostic candidate/sample")
        p = dict(payload)
        if "availability" in p and isinstance(p["availability"], Mapping):
            p["availability"] = dict(p["availability"])
        self._candidates[key] = p
        self.candidate_count += 1

    def update_availability(self, key: tuple[str, str], **values: str) -> None:
        if key not in self._candidates:
            raise KeyError(key)
        payload = self._candidates[key]
        avail = payload.setdefault("availability", {})
        avail.update(values)

    def add_record(
        self,
        kind: str,
        identity: str,
        payload: Mapping[str, Any],
        related: Iterable[tuple[str, str]],
    ) -> bool:
        encoded = _json(payload)
        key = (kind, identity)
        existing = self._records.get(key)
        if (
            existing is not None
            and existing != encoded
            and _object(existing) != _object(encoded)
        ):
            raise ValueError(
                f"conflicting diagnostic owner identity: {(kind, identity)}"
            )
        is_new = existing is None
        if is_new:
            self._records[key] = encoded
            self._buffered_records.append((kind, identity, encoded))
            self.record_counts[kind] = self.record_counts.get(kind, 0) + 1
            if len(self._buffered_records) >= 20000:
                self._flush_records()
        for cid, sid in related:
            self._buffered_links.append((cid, sid, kind, identity))
        if len(self._buffered_links) >= 50000:
            self._flush_links()
        return is_new

    def _flush_records(self) -> None:
        if self._buffered_records:
            self._connection().executemany(
                "INSERT INTO records VALUES (?, ?, ?)", self._buffered_records
            )
            self._buffered_records.clear()

    def _flush_links(self) -> None:
        if self._buffered_links:
            self._connection().executemany(
                "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?)", self._buffered_links
            )
            self._buffered_links.clear()

    def candidate_examples(self) -> list[dict[str, Any]]:
        if self._candidates:
            sorted_keys = sorted(self._candidates.keys())[:5]
            return [_object(_json(self._candidates[k])) for k in sorted_keys]
        return [
            _object(row[0])
            for row in self._connection().execute(
                "SELECT payload FROM candidates "
                "ORDER BY candidate_id, sample_id LIMIT 5"
            )
        ]

    def finalize(self, summary: Mapping[str, Any]) -> Path:
        self._flush_records()
        self._flush_links()
        if self._candidates:
            self._connection().executemany(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                (
                    (cid, sid, _json(payload))
                    for (cid, sid), payload in self._candidates.items()
                ),
            )
            self._candidates.clear()
        self._connection().execute(
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
        self._connection().execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._connection().commit()
        self._connection().close()
        self.connection = None
        if self.path.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        os.replace(self.temporary, self.path)
        return self.path

    def abort(self) -> None:
        if self.connection is not None:
            self._connection().close()
            self.connection = None
        self._candidates.clear()
        self._records.clear()
        self._buffered_records.clear()
        self._buffered_links.clear()
        self.temporary.unlink(missing_ok=True)


def write_explanation_report(
    path: Path,
    *,
    summary: Mapping[str, Any],
    candidates: Iterable[tuple[str, str, Mapping[str, Any]]],
    records: Mapping[tuple[str, str], Mapping[str, Any]],
    links: Iterable[tuple[str, str, str, str]],
) -> Path:
    """Publish one complete projection; never overwrite an existing artifact."""
    with ExplanationReportWriter(path) as writer:
        for cid, sid, payload in candidates:
            writer.add_candidate(cid, sid, payload)
            del payload
        for (kind, identity), payload in records.items():
            writer.add_record(kind, identity, payload, ())
            del payload
        writer._connection().executemany(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?)", links
        )
        return writer.finalize(summary)


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
