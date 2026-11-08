"""One atomic, bounded write transaction for completed Alignment explanations."""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError

from .curves import validate_curve_payload
from .query import build_query_index
from .review import REVIEW_SCHEMA_VERSION, encode_row_evidence
from .storage import SCHEMA_VERSION, create_schema, encode_object


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
        self._candidate_rowids: dict[tuple[str, str], int] = {}
        self._record_rowids: dict[tuple[str, str | int], int] = {}
        self._buffered_records: dict[int, tuple[str, str | int, str | bytes]] = {}
        self._related_sets: dict[tuple[tuple[str, str], ...], int] = {}
        self._buffered_link_members: list[tuple[int, int]] = []
        self._buffered_record_links: list[tuple[int, int]] = []
        self._buffered_curve_payloads: list[tuple[int, str, str, str, str, bytes]] = []
        self._curve_rowids: dict[tuple[str, str, str, bytes], int] = {}

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute("PRAGMA foreign_keys = ON;")
            self.connection.execute(
                "PRAGMA page_size = 4096;"
                if self.review_only
                else "PRAGMA page_size = 32768;"
            )
            self.connection.execute("PRAGMA synchronous = OFF;")
            self.connection.execute("PRAGMA journal_mode = OFF;")
            self.connection.execute("PRAGMA cache_size = -32000;")
            self.connection.execute("PRAGMA temp_store = FILE;")
            create_schema(self.connection, review_only=self.review_only)
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
        self._candidates[key] = dict(payload)
        self._candidate_rowids[key] = len(self._candidate_rowids) + 1
        self.candidate_count += 1

    def add_record(
        self,
        kind: str,
        identity: str | int | None,
        payload: Mapping[str, Any],
        related: Iterable[tuple[str, str]],
    ) -> bool:
        # Records without a natural locator use their actual database primary key.
        if identity is None:
            identity = len(self._record_rowids) + 1
        encoded = encode_object(payload)
        key = (kind, identity)
        stored_payload = (
            zlib.compress(encoded.encode("utf-8"), 1) if self.review_only else encoded
        )
        rowid = self._record_rowids.get(key)
        is_new = rowid is None
        if rowid is not None:
            pending = self._buffered_records.get(rowid)
            existing_payload = (
                pending[2]
                if pending is not None
                else self._connection()
                .execute("SELECT payload FROM records WHERE id=?", (rowid,))
                .fetchone()[0]
            )
            if existing_payload != stored_payload:
                raise ValueError(f"conflicting diagnostic owner identity: {key}")
        else:
            rowid = len(self._record_rowids) + 1
            self._record_rowids[key] = rowid
            self._buffered_records[rowid] = (kind, identity, stored_payload)
            self.record_counts[kind] = self.record_counts.get(kind, 0) + 1
            if len(self._buffered_records) >= 20000:
                self._flush_records()
        self._link_record(kind, identity, related)
        return is_new

    def add_row_cells(
        self,
        row_id: str,
        cells: Sequence[Mapping[str, Any]],
        samples: Mapping[str, int],
    ) -> None:
        record_id = self._record_rowids["rows", row_id]
        evidence = encode_row_evidence(cells, samples)
        self._connection().executemany(
            "INSERT INTO cells VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    record_id,
                    samples[cell["sample_key"]],
                    cell["variant"],
                    cell.get("value"),
                    cell.get("apex_rt_min"),
                    cell.get("source"),
                    cell.get("support_origin", "unavailable"),
                    json.dumps(cell.get("observation_ids", []), separators=(",", ":")),
                )
                for cell in cells
            ),
        )
        self._connection().execute(
            "INSERT INTO row_evidence VALUES (?, ?)", (record_id, evidence)
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
                (set_id, self._candidate_rowids[cid, sid]) for cid, sid in members
            )
        self._buffered_record_links.append(
            (set_id, self._record_rowids[kind, identity])
        )
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
        validate_curve_payload(
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

    def _flush_records(self) -> None:
        if self._buffered_records:
            self._connection().executemany(
                "INSERT INTO records VALUES (?, ?, ?, ?)",
                ((rowid, *record) for rowid, record in self._buffered_records.items()),
            )
            self._buffered_records.clear()

    def _flush_links(self) -> None:
        if self._buffered_record_links:
            self._flush_records()
            self._connection().executemany(
                "INSERT INTO link_members VALUES (?, ?)",
                self._buffered_link_members,
            )
            self._connection().executemany(
                "INSERT OR IGNORE INTO record_links VALUES (?, ?)",
                self._buffered_record_links,
            )
            self._buffered_link_members.clear()
            self._buffered_record_links.clear()

    def finalize(self, summary: Mapping[str, Any]) -> Path:
        self._flush_records()
        self._flush_links()
        self._flush_curve_payloads()
        samples = {
            sample["sample_key"]: i for i, sample in enumerate(summary["samples"])
        }
        self._connection().executemany(
            "INSERT INTO samples VALUES (?, ?, ?)",
            (
                (i, sample["sample_key"], sample["sample_stem"])
                for i, sample in enumerate(summary["samples"])
            ),
        )
        if self._candidates:
            located_fields = {
                "candidate_id",
                "sample_key",
                "sample_stem",
                "precursor_mz",
                "ms1_apex_mz",
            }
            self._connection().executemany(
                "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        self._candidate_rowids[cid, sid],
                        cid,
                        samples[sid],
                        payload.get("precursor_mz"),
                        payload.get("ms1_apex_mz"),
                        encode_object(
                            {
                                k: v
                                for k, v in payload.items()
                                if k not in located_fields
                            }
                        ),
                    )
                    for (cid, sid), payload in self._candidates.items()
                ),
            )
            self._candidates.clear()
        self._connection().execute(
            "INSERT INTO metadata VALUES ('summary', ?)",
            (
                encode_object(
                    dict(
                        {
                            key: value
                            for key, value in summary.items()
                            if key != "samples"
                        },
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
            build_query_index(
                self._connection(),
                dict(summary),
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
        self._candidate_rowids.clear()
        self._record_rowids.clear()
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
