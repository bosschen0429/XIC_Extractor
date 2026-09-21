"""Diagnostic-only storage and indexed, acquisition-free queries."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)

REPORT_FILENAME = "cid_nl_explanation.sqlite"
GENERIC_REPORT_FILENAME = "alignment_explanation.sqlite"
SCHEMA_VERSION = "alignment_explanation"

VALID_CURVE_KINDS = frozenset(
    {
        "discovery_physical",
        "initial_capture",
        "reacquired_capture",
        "recovery_observation",
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


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    """Immutable provenance locator pointing to a completed evidence DB."""

    report_path: Path
    matrix_path: Path
    matrix_sha256: str
    processing_request_id: str
    processing_attempt_id: str
    schema_version: str


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
        "profile_id",
        "profile_version",
        "trace_content_id",
        "prepared_signal_id",
        "acquisition_source_id",
        "full_trace_sha256",
        "selection_trace_sha256",
        "processing_request_id",
        "processing_attempt_id",
        "observation_artifact_id",
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
    if payload["schema_version"] != "curve_payload":
        raise ExplanationError("unsupported curve payload schema version")
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
    if any(right <= left for left, right in zip(rt, rt[1:])):
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

    def __init__(self, path: Path):
        self.path = Path(path)
        self.temporary = self.path.with_name(self.path.name + ".tmp")
        self.connection: sqlite3.Connection | None = None
        self.candidate_count = 0
        self.record_counts: dict[str, int] = {}
        self._candidates: dict[tuple[str, str], dict[str, Any]] = {}
        self._record_hashes: dict[tuple[str, str], bytes] = {}
        self._buffered_records: list[tuple[str, str, str]] = []
        self._related_sets: dict[tuple[tuple[str, str], ...], int] = {}
        self._buffered_link_members: list[tuple[int, str, str]] = []
        self._buffered_record_links: list[tuple[int, str, str]] = []
        self._buffered_curve_payloads: list[tuple[str, str, str, str, str, bytes]] = []
        self._curve_hashes: dict[str, bytes] = {}

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute("PRAGMA page_size = 32768;")
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
                "CREATE TABLE curve_payloads (payload_id TEXT PRIMARY KEY, "
                "sample_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
                "codec TEXT NOT NULL, payload BLOB NOT NULL);"
                "CREATE INDEX idx_curve_payloads_sample_record "
                "ON curve_payloads(sample_id, record_id);"
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
        content_hash = hashlib.blake2b(encoded.encode("utf-8"), digest_size=16).digest()
        existing_hash = self._record_hashes.get(key)
        if existing_hash is not None and existing_hash != content_hash:
            raise ValueError(
                f"conflicting diagnostic owner identity: {(kind, identity)}"
            )
        is_new = existing_hash is None
        if is_new:
            self._record_hashes[key] = content_hash
            self._buffered_records.append((kind, identity, encoded))
            self.record_counts[kind] = self.record_counts.get(kind, 0) + 1
            if len(self._buffered_records) >= 20000:
                self._flush_records()
        self._link_record(kind, identity, related)
        return is_new

    def _link_record(
        self, kind: str, identity: str, related: Iterable[tuple[str, str]]
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
        if (
            len(self._buffered_record_links) + len(self._buffered_link_members)
            >= 50000
        ):
            self._flush_links()

    def add_curve_payload(
        self,
        payload_id: str | None,
        sample_id: str,
        kind: str,
        record_id: str,
        payload: Mapping[str, Any],
        codec: str = "zlib-json-utf8",
    ) -> str:
        """Encode once for storage and, when absent, the content-derived ID."""
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
        if payload_id is None:
            payload_id = (
                "xic.inspector-curve:" + hashlib.sha256(encoded_json).hexdigest()
            )
        binding = _json(
            {
                "sample_id": sample_id,
                "kind": kind,
                "record_id": record_id,
                "codec": codec,
            }
        ).encode("utf-8")
        digest = hashlib.sha256(binding + b"\0" + encoded_json).digest()
        previous = self._curve_hashes.get(payload_id)
        if previous is not None:
            if previous != digest:
                raise ExplanationError(
                    f"conflicting curve payload identity: {payload_id}"
                )
            return payload_id
        self._curve_hashes[payload_id] = digest
        compressed = zlib.compress(encoded_json, level=6)
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
                        plot_evidence_capability="retained_curves",
                        diagnostic_only=True,
                        completion="post_matrix_publication",
                    )
                ),
            ),
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
        self._curve_hashes.clear()
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


def _object(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ExplanationError("malformed explanation: expected object payload")
    return value


def _read_completed_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    expected = {
        "metadata": ("key", "payload"),
        "candidates": ("candidate_id", "sample_id", "payload"),
        "records": ("kind", "record_id", "payload"),
        "links": ("candidate_id", "sample_id", "kind", "record_id"),
        "curve_payloads": (
            "payload_id", "sample_id", "kind", "record_id", "codec", "payload",
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
    if (
        summary.get("schema_version") != SCHEMA_VERSION
        or summary.get("diagnostic_only") is not True
        or summary.get("completion") != "post_matrix_publication"
        or summary.get("plot_evidence_capability") != "retained_curves"
    ):
        raise ExplanationError("malformed explanation: invalid completion summary")
    return summary


@dataclass(frozen=True, slots=True)
class ExplanationMetadata:
    """Completed review facts and exact links, without curve payloads."""

    summary: dict[str, Any]
    candidates: dict[tuple[str, str], dict[str, Any]]
    records: dict[str, dict[str, dict[str, Any]]]
    links: tuple[tuple[str, str, str, str], ...]


def read_explanation_metadata(path: str | Path) -> ExplanationMetadata:
    """Read the review population through the storage owner, without curves."""
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(f"explanation unavailable: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        summary = _read_completed_summary(connection)
        candidates = {
            (cid, sid): _object(payload)
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            )
        }
        records: dict[str, dict[str, dict[str, Any]]] = {}
        for kind, rid, payload in connection.execute(
            "SELECT kind, record_id, payload FROM records"
        ):
            records.setdefault(kind, {})[rid] = _object(payload)
        links = tuple(connection.execute(
            "SELECT candidate_id, sample_id, kind, record_id FROM links"
        ))
        return ExplanationMetadata(summary, candidates, records, links)
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
        raise ExplanationError(f"malformed explanation: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


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
