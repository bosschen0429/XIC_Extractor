"""Read completed Alignment facts without importing publication or execution."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Callable, Container, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import (
    CandidateNotFoundError,
    ExplanationError,
)

from .review import REVIEW_SCHEMA_VERSION, decode_row_evidence
from .storage import (
    CANDIDATE_SELECT,
    RecordKey,
    decode_candidate,
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
    """Hydrate only selected rows; scalar readers never call this detail decoder."""
    samples = [sample["sample_key"] for sample in summary["samples"]]
    query = (
        "SELECT r.id,r.payload,e.payload FROM records r "
        "JOIN row_evidence e ON e.row_record_id=r.id WHERE r.kind='rows'"
    )
    rows = (
        connection.execute(query)
        if row_ids is None
        else (
            row
            for row_id in row_ids
            for row in connection.execute(query + " AND r.record_id=?", (row_id,))
        )
    )
    result = {}
    for row_id, record, evidence in rows:
        feature_id = decode_object(record)["feature_id"]
        details = decode_row_evidence(evidence, sample_count=len(samples))
        scalars = list(
            connection.execute(
                "SELECT sample,variant,value,source,support_origin,observation_ids "
                "FROM cells WHERE row_record_id=? ORDER BY sample",
                (row_id,),
            )
        )
        if set(details) != {row[0] for row in scalars}:
            raise ExplanationError(
                "row evidence and cell samples differ; rerun Alignment"
            )
        for sample, variant, value, source, origin, observations in scalars:
            sid = samples[sample]
            result[feature_id, sid] = {
                "feature_id": feature_id,
                "sample_key": sid,
                "variant": variant,
                "value": value,
                "source": source,
                "evidence_states": details[sample]["evidence_states"],
                "reason_codes": details[sample]["reason_codes"],
                "support_origin": origin,
                "observation_ids": json.loads(observations),
                "signal_quality": details[sample]["signal_quality"],
                "membership_risks": details[sample]["membership_risks"],
                **{
                    key: value
                    for key, value in details[sample].items()
                    if key
                    not in {
                        "evidence_states",
                        "reason_codes",
                        "signal_quality",
                        "membership_risks",
                    }
                },
            }
    return result


@contextmanager
def open_review_cells(
    path: Path,
    summary: Mapping[str, Any],
) -> Iterator[Callable[[str], dict[tuple[str, str], dict[str, Any]]]]:
    """Keep a read-only row lookup open during bounded population selection."""
    with open_readonly(path) as connection:
        yield lambda row_id: read_review_cells(connection, summary, (row_id,))


def read_review_row_samples(path: Path) -> dict[str, frozenset[str]]:
    """Read actual cell presence without decoding scientific detail payloads."""
    rows: dict[str, set[str]] = {}
    with open_readonly(path) as db:
        for rid, sid in db.execute(
            "SELECT r.record_id,s.sample_key FROM cells c "
            "JOIN records r ON r.id=c.row_record_id JOIN samples s ON s.id=c.sample"
        ):
            rows.setdefault(rid, set()).add(sid)
    return {rid: frozenset(samples) for rid, samples in rows.items()}


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
    path: Path, feature_ids: Container[str]
) -> Iterator[tuple[str, str, Any, Any, Any]]:
    """Read value, support origin and observation locators without cold evidence."""
    from itertools import chain, groupby

    with open_readonly(path) as db:
        rows = db.execute(
            "SELECT r.id,r.payload,s.sample_key,c.value,"
            "c.support_origin,c.observation_ids "
            "FROM cells c JOIN records r ON r.id=c.row_record_id "
            "JOIN samples s ON s.id=c.sample ORDER BY c.row_record_id,c.sample"
        )
        for _, cells in groupby(rows, key=lambda row: row[0]):
            first = next(cells)
            feature_id = decode_object(first[1])["feature_id"]
            if feature_id not in feature_ids:
                continue
            for _, _, sample, value, origin, observations in chain((first,), cells):
                yield feature_id, sample, value, origin, json.loads(observations)


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
            (candidate["candidate_id"], candidate["sample_key"]): candidate
            for row in connection.execute(CANDIDATE_SELECT)
            for _, candidate in (decode_candidate(row),)
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
        if not compact_selection:
            cells = read_review_cells(connection, summary)
            if cells:
                records.setdefault("cell_decisions", {}).update(cells.items())
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
                CANDIDATE_SELECT + " WHERE c.candidate_id=? AND s.sample_key=?",
                (cid, sid),
            ).fetchone()
            if row is not None:
                candidates[cid, sid] = decode_candidate(row)[1]
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
        cells = read_review_cells(connection, summary, row_ids)
        if cells:
            records.setdefault("cell_decisions", {}).update(cells.items())
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
            decode_candidate(row)[1]
            for row in connection.execute(
                CANDIDATE_SELECT + " WHERE c.candidate_id=? ORDER BY s.sample_key",
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
        row_ids = [row["feature_id"] for row in records.get("rows", [])]
        if row_ids:
            records.setdefault("cell_decisions", []).extend(
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


def read_candidate_examples(path: Path) -> list[dict[str, Any]]:
    """The diagnostic CLI's small preview, queried rather than copied into metadata."""
    with open_readonly(path) as db:
        return [
            decode_candidate(row)[1]
            for row in db.execute(
                CANDIDATE_SELECT + " ORDER BY c.candidate_id,s.sample_key LIMIT 5"
            )
        ]


def read_review_row_counts(
    path: Path, summary: Mapping[str, Any]
) -> Iterator[dict[str, Any]]:
    """Overview facts from named cell columns, without reasons, risks or curves."""
    samples = [s["sample_key"] for s in summary["samples"]]
    with open_readonly(path) as db:
        for record, numeric, unresolved in db.execute(
            "SELECT r.payload,"
            "group_concat(CASE WHEN c.variant='numeric' THEN c.sample END),"
            "sum(c.variant='unresolved') "
            "FROM records r LEFT JOIN cells c ON c.row_record_id=r.id "
            "WHERE r.kind='rows' GROUP BY r.id"
        ):
            yield {
                "row": decode_object(record),
                "numeric_samples": [samples[int(i)] for i in numeric.split(",")]
                if numeric
                else [],
                "unresolved_count": unresolved or 0,
            }
