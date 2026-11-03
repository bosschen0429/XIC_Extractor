"""Read completed Alignment facts without importing publication or execution."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Callable, Container, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .review import REVIEW_SCHEMA_VERSION, iter_cell_values, unpack_cells
from .storage import (
    CandidateNotFoundError,
    ExplanationError,
    RecordKey,
    decode_object,
    open_readonly,
    read_completed_summary,
)


@dataclass(frozen=True, slots=True)
class ExplanationMetadata:
    """Completed review facts and exact links, without curve payloads."""

    summary: dict[str, Any]
    candidates: dict[tuple[str, str], dict[str, Any]]
    records: dict[str, dict[RecordKey, dict[str, Any]]]
    links: tuple[tuple[str, str, str, str], ...]


def read_review_cells(
    connection: sqlite3.Connection,
    summary: Mapping[str, Any],
    row_ids: Sequence[str] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    sample_ids = [sample["sample_key"] for sample in summary["samples"]]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    query = (
        "SELECT r.record_id, r.payload, c.payload FROM row_cells c "
        "JOIN records r ON r.id=c.row_record_id"
    )
    rows: Iterable[tuple[str, str | bytes, bytes]]
    if row_ids is None:
        rows = connection.execute(query)
    else:
        rows = (
            row
            for row_id in row_ids
            for row in connection.execute(
                query + " WHERE r.record_id=?",
                (row_id,),
            )
        )
    for row_id, record, payload in rows:
        group_id = decode_object(record)["feature_id"]
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
) -> Iterator[Callable[[str], dict[tuple[str, str], dict[str, Any]]]]:
    """Keep a read-only row lookup open during bounded population selection."""
    with open_readonly(path) as connection:
        yield lambda row_id: read_review_cells(connection, summary, (row_id,))


@contextmanager
def open_review_population(
    path: Path, summary: Mapping[str, Any]
) -> Iterator[Iterator[dict[str, Any]]]:
    """Stream review rows and cells within one read-only connection lifetime."""
    with open_readonly(path) as db:
        samples = [s["sample_key"] for s in summary["samples"]]
        yield (
            {
                "row": decode_object(record),
                "cells": list(
                    unpack_cells(
                        payload,
                        row_id=row_id,
                        group_id=decode_object(record)["feature_id"],
                        sample_ids=samples,
                    ).values()
                ),
            }
            for row_id, payload, record in db.execute(
                "SELECT r.record_id,c.payload,r.payload FROM row_cells c "
                "JOIN records r ON r.id=c.row_record_id"
            )
        )


def read_review_row_samples(
    path: Path, summary: Mapping[str, Any]
) -> dict[str, frozenset[str]]:
    """Read row membership without unpacking scalar values or evidence reasons."""
    sample_ids = [s["sample_key"] for s in summary["samples"]]
    with open_readonly(path) as db:
        cohorts: dict[bytes, frozenset[str]] = {}
        rows = {}
        for row_id, mask in db.execute(
            "SELECT r.record_id, c.sample_mask FROM row_cells c "
            "JOIN records r ON r.id=c.row_record_id"
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


def read_explanation_summary(path: Path) -> dict[str, Any]:
    """Read only the validated completion summary, without records or cells."""
    with open_readonly(path) as connection:
        return read_completed_summary(connection)


def read_group_candidate_ids(path: Path, group_id: str) -> list[str]:
    """Candidate locators for a saved group, without loading its curve payloads."""
    with open_readonly(path) as db:
        return [
            row[0]
            for row in db.execute(
                "SELECT DISTINCT candidate_id FROM links "
                "WHERE kind='final_groups' AND record_id=? ORDER BY candidate_id",
                (group_id,),
            )
        ]


def read_observation_apices(path: Path) -> dict[str, float]:
    """Apex RT of each terminal observation, by physical peak key."""
    with open_readonly(path) as db:
        return {
            observation["physical_peak_key"]: float(observation["rt_apex_min"])
            for (payload,) in db.execute(
                "SELECT payload FROM records WHERE kind='terminal_subjects'"
            )
            for observation in decode_object(payload).get("observations") or ()
        }


def read_row_cell_values(
    path: Path, summary: Mapping[str, Any], feature_ids: Container[str]
) -> Iterator[tuple[str, str, Any, Any, Any]]:
    """Value, support origin and observations of each cell of the named rows.

    Yields ``(feature_id, sample_key, value, support_origin, observation_ids)``.
    Other rows are skipped before their cells are decompressed.
    """
    samples = [sample["sample_key"] for sample in summary["samples"]]
    with open_readonly(path) as db:
        for record, payload in db.execute(
            "SELECT r.payload, c.payload FROM row_cells c "
            "JOIN records r ON r.id=c.row_record_id"
        ):
            feature_id = decode_object(record)["feature_id"]
            if feature_id not in feature_ids:
                continue
            for sample, value, origin, observations in iter_cell_values(
                payload, sample_ids=samples
            ):
                yield feature_id, sample, value, origin, observations


def read_explanation_metadata(
    path: str | Path,
    *,
    selection_only: bool = False,
    query_only: bool = False,
) -> ExplanationMetadata:
    """Read the review population through the storage owner, without curves."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
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
            (cid, sid): decode_object(payload)
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            )
        }
        records: dict[str, dict[RecordKey, dict[str, Any]]] = {}
        for kind, rid, payload in connection.execute(
            "SELECT kind, record_id, payload FROM records" + record_filter
        ):
            records.setdefault(kind, {})[rid] = decode_object(payload)
        links = tuple(
            connection.execute(
                "SELECT candidate_id, sample_id, kind, record_id FROM links"
                + record_filter
            )
        )
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION and not compact_selection:
            records["cell_decisions"] = {
                key: value
                for key, value in read_review_cells(connection, summary).items()
            }
        return ExplanationMetadata(summary, candidates, records, links)


def read_explanation_selection(
    path: Path,
    *,
    candidate_keys: Sequence[tuple[str, str]],
    row_ids: Sequence[str],
) -> ExplanationMetadata:
    """Restore detailed review records only for the selected witnesses and rows."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        candidates = {}
        links: set[tuple[str, str, str, str]] = set()
        for cid, sid in candidate_keys:
            row = connection.execute(
                "SELECT payload FROM candidates WHERE candidate_id=? AND sample_id=?",
                (cid, sid),
            ).fetchone()
            if row is not None:
                candidates[cid, sid] = decode_object(row[0])
            links.update(
                connection.execute(
                    "SELECT candidate_id, sample_id, kind, record_id FROM links "
                    "WHERE candidate_id=? AND sample_id=?",
                    (cid, sid),
                )
            )
        records: dict[str, dict[RecordKey, dict[str, Any]]] = {}
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
            records.setdefault(kind, {})[rid] = decode_object(row[0])
        for payload in records.get("rows", {}).values():
            gid = payload.get("feature_id")
            if gid is None or gid in records.get("final_groups", {}):
                continue
            row = connection.execute(
                "SELECT payload FROM records WHERE kind='final_groups' AND record_id=?",
                (gid,),
            ).fetchone()
            if row is not None:
                records.setdefault("final_groups", {})[gid] = decode_object(row[0])
        records["cell_decisions"] = {
            key: value
            for key, value in read_review_cells(connection, summary, row_ids).items()
        }
        return ExplanationMetadata(summary, candidates, records, tuple(sorted(links)))


def read_alignment_explanation(
    path: str | Path, *, candidate_id: str | None = None
) -> dict[str, Any]:
    """Read a compact summary or only records linked to one exact candidate ID."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        if candidate_id is None:
            return summary
        candidates = [
            decode_object(row[0])
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
            records.setdefault(kind, []).append(decode_object(payload))
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
            row_ids = [row["feature_id"] for row in records.get("rows", [])]
            records["cell_decisions"] = list(
                read_review_cells(connection, summary, row_ids).values()
            )
        return {
            "schema_version": summary["schema_version"],
            "diagnostic_only": True,
            "completion": summary["completion"],
            "candidate_id": candidate_id,
            "candidates": candidates,
            "records": records,
        }


def read_alignment_curves(
    path: str | Path,
    *,
    sample_ids: Sequence[str] | None = None,
    record_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Selectively read and decompress curve payloads without rehydration."""
    from .curves import validate_curve_payload

    with open_readonly(path) as connection:
        read_completed_summary(connection)
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
            validate_curve_payload(
                payload_data,
                sample_id=sample_id,
                kind=kind,
                record_id=record_id,
            )
            results.append({**payload_data, "payload_id": payload_id})
        return results
