"""Diagnostic-only storage and indexed, acquisition-free queries."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.diagnostics.explanation_review import (
    REVIEW_SCHEMA_VERSION,
    pack_cells,
    unpack_cells,
    validate_membership_risks,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult

REPORT_FILENAME = "cid_nl_explanation.sqlite"
GENERIC_REPORT_FILENAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_explanation.v2"

VALID_CURVE_KINDS = frozenset(
    {
        "discovery_physical",
        "initial_capture",
        "reacquired_capture",
        "backfill_observation",
    }
)
ALLOWED_CURVE_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "sample_id",
        "candidate_id",
        "peak_observation_content_id",
        "trace_coordinate",
        "curves",
        "integration",
        "provenance",
    }
)


class ExplanationError(ValueError):
    """The requested diagnostic cannot be interpreted."""


class ExplanationUnavailableError(ExplanationError):
    """No completed explanation is available."""


class CandidateNotFoundError(ExplanationError):
    """The exact candidate ID is absent from this run."""


_CURVE_KEYS = frozenset({"rt", "raw", "baseline", "smoothed", "positive"})
_COORDINATE_KEYS = frozenset({"mz", "rt_min", "rt_max", "ppm_tol"})
_PROFILE_KEYS = frozenset(field.name for field in fields(SignalProcessingProfile))
_INTEGRATION_KEYS = frozenset(field.name for field in fields(IntegrationResult)) | {
    "apex_rt_min",
    "area_profile_selected",
    "boundary_source",
}
_PROVENANCE_KEYS = frozenset(
    {
        "profile_version",
        "acquisition_source_id",
        "processing_request_id",
        "processing_attempt_id",
        "selected_trace_indices",
        "resolver_mode",
        "proposal_sources",
        "method_subject_id",
        "profile",
        "physical_state",
        "physical_reason",
    }
)


def _validate_curve_payload(
    payload: Mapping[str, Any],
    *,
    sample_id: str,
    kind: str,
    record_id: str,
) -> None:
    """Validate the selected persisted envelope once at its storage boundary."""
    if kind not in VALID_CURVE_KINDS:
        raise ExplanationError(f"undeclared curve kind: {kind}")
    if not isinstance(payload, Mapping):
        raise ExplanationError("curve payload must be an object")
    extra = set(payload) - ALLOWED_CURVE_PAYLOAD_KEYS
    if extra:
        raise ExplanationError(f"undeclared curve payload keys: {sorted(extra)}")
    if set(payload) != ALLOWED_CURVE_PAYLOAD_KEYS:
        raise ExplanationError("curve payload missing required fields")
    if payload["schema_version"] != "curve_payload.v2":
        raise ExplanationError(
            "不支援此曲線資料格式；請重新執行 Alignment 產生新輸出。"
        )
    if payload["kind"] != kind or payload["sample_id"] != sample_id:
        raise ExplanationError("curve payload crosses sample or kind binding")
    if not isinstance(sample_id, str) or not sample_id or not record_id:
        raise ExplanationError("curve payload missing sample or record identity")
    provenance = payload["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) - _PROVENANCE_KEYS:
        raise ExplanationError("undeclared provenance fields")
    profile = provenance.get("profile")
    if profile is not None and (
        not isinstance(profile, Mapping) or set(profile) != _PROFILE_KEYS
    ):
        raise ExplanationError("undeclared or missing profile fields")
    if record_id not in (
        payload["peak_observation_content_id"],
        payload["candidate_id"],
        provenance.get("method_subject_id"),
    ):
        raise ExplanationError("curve payload crosses record binding")

    curves = payload["curves"]
    if not isinstance(curves, Mapping) or set(curves) != _CURVE_KEYS:
        raise ExplanationError("undeclared or missing curve array fields")
    rt = curves["rt"]
    if not isinstance(rt, (list, tuple)):
        raise ExplanationError("curve RT must be an array")
    if not rt and (
        provenance.get("physical_state") != "unavailable"
        or payload["peak_observation_content_id"] is not None
        or payload["integration"] is not None
        or any(
            curves[name] is not None for name in ("baseline", "smoothed", "positive")
        )
    ):
        raise ExplanationError(
            "empty acquisition cannot claim observed or processed evidence"
        )
    for name, values in curves.items():
        if values is None and name not in {"rt", "raw"}:
            continue
        if not isinstance(values, (list, tuple)) or len(values) != len(rt):
            raise ExplanationError(f"curve array length mismatch: {name}")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise ExplanationError(f"curve values must be finite numbers: {name}")
    if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
        raise ExplanationError("curve RT must be strictly increasing")

    coordinate = payload["trace_coordinate"]
    if not isinstance(coordinate, Mapping) or set(coordinate) != _COORDINATE_KEYS:
        raise ExplanationError("undeclared or missing trace coordinate fields")
    for name, value in coordinate.items():
        if value is None and name == "ppm_tol":
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ExplanationError(f"invalid trace coordinate: {name}")
    if coordinate["mz"] <= 0 or coordinate["rt_min"] > coordinate["rt_max"]:
        raise ExplanationError("invalid trace coordinate bounds")
    if coordinate["ppm_tol"] is not None and coordinate["ppm_tol"] <= 0:
        raise ExplanationError("invalid trace coordinate tolerance")

    integration = payload["integration"]
    if integration is not None:
        if not isinstance(integration, Mapping) or set(integration) - _INTEGRATION_KEYS:
            raise ExplanationError("undeclared integration fields")
        for name, value in integration.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ExplanationError(f"nonfinite integration value: {name}")


class ExplanationReportWriter:
    """One bounded SQLite transaction, published only by explicit finalization."""

    def __init__(
        self,
        path: Path,
        *,
        review_only: bool = False,
        curve_mz_ranges: Sequence[tuple[float, float]] = (),
    ):
        self.path = Path(path)
        self.temporary = self.path.with_name(self.path.name + ".tmp")
        self.connection: sqlite3.Connection | None = None
        self.review_only = review_only
        self.curve_mz_ranges = tuple(curve_mz_ranges)
        self.candidate_count = 0
        self.record_counts: dict[str, int] = {}
        self._candidates: dict[tuple[str, str], dict[str, Any]] = {}
        self._record_hashes: dict[tuple[str, str | int], bytes] = {}
        self._buffered_records: list[tuple[str, str | int, str | bytes]] = []
        self._related_sets: dict[tuple[tuple[str, str], ...], int] = {}
        self._buffered_link_members: list[tuple[int, str, str]] = []
        self._buffered_record_links: list[tuple[int, str, str | int]] = []
        self._buffered_curve_payloads: list[tuple[int, str, str, str, str, bytes]] = []
        self._curve_rowids: dict[tuple[str, str, str, bytes], int] = {}

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute(
                "PRAGMA page_size = 4096;"
                if self.review_only
                else "PRAGMA page_size = 32768;"
            )
            self.connection.execute("PRAGMA synchronous = OFF;")
            self.connection.execute("PRAGMA journal_mode = OFF;")
            self.connection.execute("PRAGMA cache_size = -32000;")
            self.connection.execute("PRAGMA temp_store = FILE;")
            self.connection.executescript(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, "
                "payload TEXT NOT NULL, PRIMARY KEY(candidate_id, sample_id));"
                "CREATE TABLE records (kind TEXT, record_id TEXT, "
                "payload TEXT NOT NULL, "
                "PRIMARY KEY(kind, record_id));"
                "CREATE TABLE link_members (set_id INTEGER, candidate_id TEXT, "
                "sample_id TEXT, PRIMARY KEY(set_id, candidate_id, sample_id)) "
                "WITHOUT ROWID;"
                "CREATE INDEX idx_link_members_candidate "
                "ON link_members(candidate_id, sample_id, set_id);"
                "CREATE TABLE record_links (set_id INTEGER, kind TEXT, "
                "record_id TEXT, PRIMARY KEY(set_id, kind, record_id)) WITHOUT ROWID;"
                "CREATE VIEW links AS SELECT DISTINCT m.candidate_id, m.sample_id, "
                "r.kind, r.record_id FROM link_members m "
                "JOIN record_links r USING(set_id);"
                "CREATE TABLE curve_payloads (payload_id INTEGER PRIMARY KEY, "
                "sample_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
                "codec TEXT NOT NULL, payload BLOB NOT NULL);"
                "CREATE INDEX idx_curve_payloads_sample_record "
                "ON curve_payloads(sample_id, record_id);"
                "BEGIN;"
            )
            if self.review_only:
                self.connection.execute(
                    "CREATE TABLE row_cells (row_key_id TEXT PRIMARY KEY, "
                    "group_id TEXT NOT NULL, payload BLOB NOT NULL, "
                    "sample_mask BLOB NOT NULL)"
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
        identity: str | int,
        payload: Mapping[str, Any],
        related: Iterable[tuple[str, str]],
    ) -> bool:
        encoded = _json(payload)
        key = (kind, identity)
        content_hash = hashlib.blake2b(encoded.encode("utf-8"), digest_size=16).digest()
        existing_hash = self._record_hashes.get(key)
        if existing_hash is not None and existing_hash != content_hash:
            raise ValueError(
                f"conflicting diagnostic owner identity: {(kind, identity)}"
            )
        is_new = existing_hash is None
        if is_new:
            self._record_hashes[key] = content_hash
            self._buffered_records.append(
                (
                    kind,
                    identity,
                    zlib.compress(encoded.encode("utf-8"), 1)
                    if self.review_only
                    else encoded,
                )
            )
            self.record_counts[kind] = self.record_counts.get(kind, 0) + 1
            if len(self._buffered_records) >= 20000:
                self._flush_records()
        self._link_record(kind, identity, related)
        return is_new

    def add_row_cells(
        self,
        row_id: str,
        group_id: str,
        cells: Sequence[Mapping[str, Any]],
        samples: Mapping[str, int],
    ) -> None:
        self._connection().execute(
            "INSERT INTO row_cells VALUES (?, ?, ?, ?)",
            (
                row_id,
                group_id,
                pack_cells(cells, samples),
                sum(
                    1 << ordinal
                    for ordinal in {samples[cell["sample_key"]] for cell in cells}
                ).to_bytes((len(samples) + 7) // 8, "little"),
            ),
        )
        self.record_counts["cell_decisions"] = self.record_counts.get(
            "cell_decisions", 0
        ) + len(cells)

    def _link_record(
        self, kind: str, identity: str | int, related: Iterable[tuple[str, str]]
    ) -> None:
        # Sample cells share their group's candidate set. Store the set once;
        # the logical links view expands it only for a reader's selected scope.
        members = tuple(sorted(set(related)))
        if not members:
            return
        set_id = self._related_sets.get(members)
        if set_id is None:
            set_id = len(self._related_sets)
            self._related_sets[members] = set_id
            self._buffered_link_members.extend(
                (set_id, cid, sid) for cid, sid in members
            )
        self._buffered_record_links.append((set_id, kind, identity))
        if len(self._buffered_record_links) + len(self._buffered_link_members) >= 50000:
            self._flush_links()

    def add_curve_payload(
        self,
        sample_id: str,
        kind: str,
        record_id: str,
        payload: Mapping[str, Any],
        codec: str = "zlib-json-utf8",
    ) -> int:
        """Store exact curve data once and return its database-local rowid."""
        _validate_curve_payload(
            payload, sample_id=sample_id, kind=kind, record_id=record_id
        )
        if codec != "zlib-json-utf8":
            raise ExplanationError(f"unsupported curve codec: {codec}")
        encoded_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        compressed = zlib.compress(encoded_json, level=1)
        key = (sample_id, kind, record_id, compressed)
        existing = self._curve_rowids.get(key)
        if existing is not None:
            return existing
        payload_id = len(self._curve_rowids) + 1
        self._curve_rowids[key] = payload_id
        self._buffered_curve_payloads.append(
            (payload_id, sample_id, kind, record_id, codec, compressed)
        )
        if len(self._buffered_curve_payloads) >= 5000:
            self._flush_curve_payloads()
        return payload_id

    def _flush_curve_payloads(self) -> None:
        if self._buffered_curve_payloads:
            self._connection().executemany(
                "INSERT INTO curve_payloads VALUES (?, ?, ?, ?, ?, ?)",
                self._buffered_curve_payloads,
            )
            self._buffered_curve_payloads.clear()
            self._connection().commit()
            self._connection().execute("BEGIN;")

    def _flush_records(self) -> None:
        if self._buffered_records:
            self._connection().executemany(
                "INSERT INTO records VALUES (?, ?, ?)", self._buffered_records
            )
            self._buffered_records.clear()
            self._connection().commit()
            self._connection().execute("BEGIN;")

    def _flush_links(self) -> None:
        if self._buffered_record_links:
            self._connection().executemany(
                "INSERT INTO link_members VALUES (?, ?, ?)",
                self._buffered_link_members,
            )
            self._connection().executemany(
                "INSERT OR IGNORE INTO record_links VALUES (?, ?, ?)",
                self._buffered_record_links,
            )
            self._buffered_link_members.clear()
            self._buffered_record_links.clear()
            self._connection().commit()
            self._connection().execute("BEGIN;")

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
        self._flush_curve_payloads()
        if self._candidates:
            self._connection().executemany(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                (
                    (
                        cid,
                        sid,
                        zlib.compress(_json(payload).encode("utf-8"), 1)
                        if self.review_only
                        else _json(payload),
                    )
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
                        schema_version=REVIEW_SCHEMA_VERSION
                        if self.review_only
                        else SCHEMA_VERSION,
                        plot_evidence_capability=(
                            "retained_curves"
                            if not self.review_only or self._curve_rowids
                            else "metadata_only"
                        ),
                        diagnostic_only=True,
                        completion="post_matrix_publication",
                    )
                ),
            ),
        )
        if self.review_only:
            from xic_extractor.diagnostics.explanation_query import build_query_index

            build_query_index(
                self._connection(), _read_completed_summary(self._connection())
            )
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
        self._record_hashes.clear()
        self._buffered_records.clear()
        self._related_sets.clear()
        self._buffered_link_members.clear()
        self._buffered_record_links.clear()
        self._buffered_curve_payloads.clear()
        self._curve_rowids.clear()
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
        for cid, sid, kind, identity in links:
            writer._link_record(kind, identity, ((cid, sid),))
        return writer.finalize(summary)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(payload: str | bytes) -> dict[str, Any]:
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


def _read_completed_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    expected = {
        "metadata": ("key", "payload"),
        "candidates": ("candidate_id", "sample_id", "payload"),
        "records": ("kind", "record_id", "payload"),
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
            raise ExplanationError(f"malformed explanation schema: {table}")
    row = connection.execute(
        "SELECT payload FROM metadata WHERE key='summary'"
    ).fetchone()
    if row is None:
        raise ExplanationError("malformed explanation: missing completion summary")
    summary = _object(row[0])
    if summary.get("schema_version") not in (SCHEMA_VERSION, REVIEW_SCHEMA_VERSION):
        raise ExplanationError(
            "不支援此解釋資料格式；請重新執行 Alignment 產生新輸出。"
        )
    if summary.get("query_index_version") not in (None, 1, 2):
        raise ExplanationError("unsupported explanation query index version")
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("completion") != "post_matrix_publication"
        or summary.get("plot_evidence_capability")
        not in ("retained_curves", "metadata_only")
    ):
        raise ExplanationError("malformed explanation: invalid completion summary")
    if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(row_cells)")
        )
        if columns not in (
            ("row_key_id", "group_id", "payload"),
            ("row_key_id", "group_id", "payload", "sample_mask"),
        ):
            raise ExplanationError("malformed explanation schema: row_cells")
    return summary


@dataclass(frozen=True, slots=True)
class ExplanationMetadata:
    """Completed review facts and exact links, without curve payloads."""

    summary: dict[str, Any]
    candidates: dict[tuple[str, str], dict[str, Any]]
    records: dict[str, dict[str, dict[str, Any]]]
    links: tuple[tuple[str, str, str, str], ...]


def _read_review_cells(
    connection: sqlite3.Connection,
    summary: Mapping[str, Any],
    row_ids: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    sample_ids = [sample["sample_key"] for sample in summary["samples"]]
    result: dict[str, dict[str, Any]] = {}
    rows: Iterable[tuple[str, str, bytes]]
    if row_ids is None:
        rows = connection.execute("SELECT row_key_id, group_id, payload FROM row_cells")
    else:
        rows = (
            row
            for row_id in row_ids
            for row in connection.execute(
                "SELECT row_key_id, group_id, payload FROM row_cells "
                "WHERE row_key_id=?",
                (row_id,),
            )
        )
    for row_id, group_id, payload in rows:
        result.update(
            unpack_cells(
                payload, row_id=row_id, group_id=group_id, sample_ids=sample_ids
            )
        )
    return result


@contextmanager
def open_review_cells(
    path: Path,
    summary: Mapping[str, Any],
) -> Iterator[Callable[[str], dict[str, dict[str, Any]]]]:
    """Keep a read-only row lookup open during bounded population selection."""
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        yield lambda row_id: _read_review_cells(connection, summary, (row_id,))


@contextmanager
def open_review_population(
    path: Path, summary: Mapping[str, Any]
) -> Iterator[Iterator[dict[str, Any]]]:
    """Stream review rows and cells within one read-only connection lifetime."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        samples = [s["sample_key"] for s in summary["samples"]]
        yield (
            {
                "row": _object(record),
                "cells": list(
                    unpack_cells(
                        payload, row_id=row_id, group_id=group_id, sample_ids=samples
                    ).values()
                ),
            }
            for row_id, group_id, payload, record in db.execute(
                "SELECT c.row_key_id,c.group_id,c.payload,r.payload FROM row_cells c "
                "JOIN records r ON r.kind='rows' AND r.record_id=c.row_key_id"
            )
        )


def read_review_row_samples(
    path: Path, summary: Mapping[str, Any]
) -> dict[str, frozenset[str]]:
    """Read row membership without unpacking scalar values or evidence reasons."""
    sample_ids = [s["sample_key"] for s in summary["samples"]]
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(row_cells)")}
        if "sample_mask" in columns:
            cohorts: dict[bytes, frozenset[str]] = {}
            rows = {}
            for row_id, mask in db.execute(
                "SELECT row_key_id, sample_mask FROM row_cells"
            ):
                if mask not in cohorts:
                    bits = int.from_bytes(mask, "little")
                    cohorts[mask] = frozenset(
                        sid
                        for ordinal, sid in enumerate(sample_ids)
                        if bits & (1 << ordinal)
                    )
                rows[row_id] = cohorts[mask]
            return rows
        # Existing compact reports remain queryable without rewriting them.
        return {
            row_id: frozenset(
                cell["sample_key"]
                for cell in unpack_cells(
                    payload, row_id=row_id, group_id=group_id, sample_ids=sample_ids
                ).values()
            )
            for row_id, group_id, payload in db.execute(
                "SELECT row_key_id, group_id, payload FROM row_cells"
            )
        }


def read_explanation_metadata(
    path: str | Path,
    *,
    selection_only: bool = False,
    query_only: bool = False,
) -> ExplanationMetadata:
    """Read the review population through the storage owner, without curves."""
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(f"explanation unavailable: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        summary = _read_completed_summary(connection)
        compact_selection = (
            selection_only and summary["schema_version"] == REVIEW_SCHEMA_VERSION
        )
        record_filter = (
            " WHERE kind IN ('rows', 'final_groups', 'terminal_subjects', "
            "'initial_edges')"
            if compact_selection and query_only
            else " WHERE kind NOT IN ('initial_groups', 'capture_evidence')"
            if compact_selection
            else ""
        )
        candidates = {
            (cid, sid): _object(payload)
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            )
        }
        records: dict[str, dict[str, dict[str, Any]]] = {}
        for kind, rid, payload in connection.execute(
            "SELECT kind, record_id, payload FROM records" + record_filter
        ):
            records.setdefault(kind, {})[rid] = _object(payload)
        links = tuple(
            connection.execute(
                "SELECT candidate_id, sample_id, kind, record_id FROM links"
                + record_filter
            )
        )
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION and not compact_selection:
            records["cell_decisions"] = _read_review_cells(connection, summary)
        return ExplanationMetadata(summary, candidates, records, links)
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
        raise ExplanationError(f"malformed explanation: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def read_explanation_selection(
    path: Path,
    *,
    candidate_keys: Sequence[tuple[str, str]],
    row_ids: Sequence[str],
) -> ExplanationMetadata:
    """Restore detailed review records only for the selected witnesses and rows."""
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        summary = _read_completed_summary(connection)
        candidates = {}
        links: set[tuple[str, str, str, str]] = set()
        for cid, sid in candidate_keys:
            row = connection.execute(
                "SELECT payload FROM candidates WHERE candidate_id=? AND sample_id=?",
                (cid, sid),
            ).fetchone()
            if row is not None:
                candidates[cid, sid] = _object(row[0])
            links.update(
                connection.execute(
                    "SELECT candidate_id, sample_id, kind, record_id FROM links "
                    "WHERE candidate_id=? AND sample_id=?",
                    (cid, sid),
                )
            )
        records: dict[str, dict[str, dict[str, Any]]] = {}
        requested = {(kind, rid) for _, _, kind, rid in links}
        # Scalar-only rows have no candidate witness links. They are still
        # eligible for sample/attention review and must hydrate by row identity.
        requested.update(("rows", row_id) for row_id in row_ids)
        for kind, rid in sorted(requested):
            row = connection.execute(
                "SELECT payload FROM records WHERE kind=? AND record_id=?", (kind, rid)
            ).fetchone()
            if row is None:
                raise ExplanationError("Selected review links to a missing record")
            records.setdefault(kind, {})[rid] = _object(row[0])
        for payload in records.get("rows", {}).values():
            gid = payload.get("group_hypothesis_id")
            if gid is None or gid in records.get("final_groups", {}):
                continue
            row = connection.execute(
                "SELECT payload FROM records WHERE kind='final_groups' AND record_id=?",
                (gid,),
            ).fetchone()
            if row is not None:
                records.setdefault("final_groups", {})[gid] = _object(row[0])
        records["cell_decisions"] = _read_review_cells(connection, summary, row_ids)
        return ExplanationMetadata(summary, candidates, records, tuple(sorted(links)))


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
        summary = _read_completed_summary(connection)
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
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
            row_ids = [row["row_key_id"] for row in records.get("rows", [])]
            records["cell_decisions"] = list(
                _read_review_cells(connection, summary, row_ids).values()
            )
        return {
            "schema_version": summary["schema_version"],
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


def read_cid_nl_curves(
    path: str | Path,
    *,
    sample_ids: Sequence[str] | None = None,
    record_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Selectively read and decompress curve payloads without rehydration."""
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(f"explanation unavailable: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        _read_completed_summary(connection)
        if sample_ids is not None and not sample_ids:
            return []
        if record_ids is not None and not record_ids:
            return []

        query = (
            "SELECT payload_id, sample_id, kind, record_id, codec, payload "
            "FROM curve_payloads WHERE 1=1"
        )
        params: list[Any] = []
        if sample_ids:
            query += f" AND sample_id IN ({','.join('?' for _ in sample_ids)})"
            params.extend(sample_ids)
        if record_ids:
            query += f" AND record_id IN ({','.join('?' for _ in record_ids)})"
            params.extend(record_ids)

        results: list[dict[str, Any]] = []
        for (
            payload_id,
            sample_id,
            kind,
            record_id,
            codec,
            blob,
        ) in connection.execute(query, params):
            if codec != "zlib-json-utf8":
                raise ExplanationError(f"unsupported curve payload codec: {codec}")
            decompressed = zlib.decompress(blob)
            payload_data = json.loads(decompressed.decode("utf-8"))
            _validate_curve_payload(
                payload_data,
                sample_id=sample_id,
                kind=kind,
                record_id=record_id,
            )
            results.append({**payload_data, "payload_id": payload_id})
        return results
    except (
        sqlite3.Error,
        json.JSONDecodeError,
        zlib.error,
        UnicodeError,
        TypeError,
    ) as exc:
        raise ExplanationError(f"failed reading curve payloads: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


read_alignment_explanation = read_cid_nl_explanation
read_alignment_curves = read_cid_nl_curves
