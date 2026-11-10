"""Read completed Alignment facts without importing publication or execution.

Readers receive each stored concept in the record shapes they display: a group
is read as its final-group, result-row and initial-group records, a candidate
with its terminal observation, and links relate records to candidates through
the stored references.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Container, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import (
    CandidateNotFoundError,
    ExplanationError,
)

from .review import validate_membership_risks
from .storage import (
    RecordKey,
    decode_json,
    encode_object,
    open_readonly,
    present_tables,
    read_completed_summary,
    validate_details,
)

CandidateKey = tuple[str, str]
Link = tuple[str, str, str, str]

_SCALAR_KINDS = ("rows", "final_groups", "terminal_subjects", "initial_edges")


@dataclass(frozen=True, slots=True)
class ExplanationMetadata:
    """Completed review facts and exact links, without curve payloads."""

    summary: dict[str, Any]
    candidates: dict[tuple[str, str], dict[str, Any]]
    records: dict[str, dict[RecordKey, dict[str, Any]]]
    links: tuple[tuple[str, str, str, str], ...]


def _key(*parts: str) -> str:
    return json.dumps(parts, separators=(",", ":"))


def _sorted(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


class _Evidence:
    """Request-local lookups over one completed report."""

    def __init__(self, db: sqlite3.Connection, summary: Mapping[str, Any]):
        self.db = db
        self.summary = summary
        self.full = summary["retention"] == "full_audit"
        self.tables = present_tables(db)
        self.samples = [sample["sample_key"] for sample in summary["samples"]]
        self.stems = [sample["sample_stem"] for sample in summary["samples"]]
        self.peaks: dict[int, tuple[str, float | None, float | None, float | None]] = {
            row[0]: row[1:]
            for row in db.execute(
                "SELECT id,physical_peak_key,mz,rt_apex_min,ppm FROM peaks"
            )
        }
        self.subjects = {
            row[0]: row[1:]
            for row in db.execute(
                "SELECT id,method_subject_id,subject_kind,precursor_mz,"
                "precursor_tolerance_ppm,neutral_loss_da,composition "
                "FROM method_subjects"
            )
        }
        self.candidate_rows = {
            row[0]: row
            for row in db.execute(
                "SELECT id,candidate_id,sample,precursor_mz,ms1_apex_mz,"
                "neutral_loss_tag,peak_status,method_subject,terminal_variant,"
                "hypothesis_id,terminal_peak,physical_state,physical_reason,"
                "rt_apex_min FROM candidates ORDER BY id"
            )
        }
        self.candidate_ids = {
            (row[1], self.samples[row[2]]): row_id
            for row_id, row in self.candidate_rows.items()
        }
        self._by_subject_sample: dict[tuple[int, int], list[CandidateKey]] | None = None

    def key(self, candidate_row_id: int) -> CandidateKey:
        row = self.candidate_rows[candidate_row_id]
        return row[1], self.samples[row[2]]

    def keys(self, encoded: str) -> list[CandidateKey]:
        return [self.key(row_id) for row_id in json.loads(encoded)]

    def peak_keys(self, encoded: str) -> list[str]:
        return [self.peaks[peak_id][0] for peak_id in json.loads(encoded)]

    def subject_candidates(
        self, subject: int, sample: int | None
    ) -> list[CandidateKey]:
        if self._by_subject_sample is None:
            index: dict[tuple[int, int], list[CandidateKey]] = defaultdict(list)
            for row_id, row in self.candidate_rows.items():
                if row[7] is not None:
                    index[row[7], row[2]].append(self.key(row_id))
            self._by_subject_sample = index
        if sample is not None:
            return self._by_subject_sample.get((subject, sample), [])
        return [
            key
            for (subject_id, _), keys in self._by_subject_sample.items()
            if subject_id == subject
            for key in keys
        ]

    # Records ----------------------------------------------------------

    def candidate(self, row_id: int) -> dict[str, Any]:
        row = self.candidate_rows[row_id]
        if row[7] is not None:
            payload = {
                "configured_neutral_loss_da": self.summary["neutral_loss_by_tag"][
                    row[5]
                ],
                "method_subject_id": self.subjects[row[7]][0],
                "neutral_loss_tag": row[5],
                "peak_status": row[6],
            }
        else:
            payload = {
                "physical_state": row[11],
                "reason": row[12],
                "rt_apex_min": row[13],
            }
        return {
            **payload,
            "candidate_id": row[1],
            "sample_key": self.samples[row[2]],
            "sample_stem": self.stems[row[2]],
            "precursor_mz": row[3],
            "ms1_apex_mz": row[4],
        }

    def terminal(self, row_id: int) -> dict[str, Any]:
        row = self.candidate_rows[row_id]
        cid, sid = row[1], self.samples[row[2]]
        record = {
            "candidate_id": cid,
            "sample_key": sid,
            "trace_group_id": f"{sid}|{cid}",
            "variant": row[8],
        }
        if row[9] is not None:
            key, mz, rt, ppm = self.peaks[row[10]]
            record.update(
                hypothesis_id=row[9],
                physical_peak_keys=[key],
                observations=[
                    {"mz": mz, "physical_peak_key": key, "ppm": ppm, "rt_apex_min": rt}
                ],
            )
        return _sorted(record)

    def method_subject(self, subject: int) -> dict[str, Any]:
        sid, kind, mz, ppm, loss, composition = self.subjects[subject]
        return {
            "composition": decode_json(composition),
            "method_subject_id": sid,
            "neutral_loss_da": loss,
            "precursor_mz": mz,
            "precursor_tolerance_ppm": ppm,
            "subject_kind": kind,
        }

    def assessment(self, row: tuple[Any, ...]) -> dict[str, Any]:
        record = {
            "candidate_physical_peak_keys": self.peak_keys(row[10]),
            "detector_invocation_id": row[6],
            "method_id": row[4],
            "method_result": row[8],
            "method_subject_id": self.subjects[row[2]][0],
            "method_version": row[5],
            "provider_id": row[3],
            "reason_codes": json.loads(row[9]),
            "sample_key": self.samples[row[1]],
            "technical_state": row[7],
        }
        if self.full:
            record.update(
                acquisition_source_id=row[11],
                evaluation_receipt=decode_json(row[12]),
            )
        return _sorted(record)

    def exclusion(self, candidate: int, reason: str) -> dict[str, Any]:
        row = self.candidate_rows[candidate]
        return {
            "candidate_id": row[1],
            "method_subject_id": self.subjects[row[7]][0],
            "peak_status": row[6],
            "reason_code": reason,
            "sample_key": self.samples[row[2]],
        }

    def members(self, column: str, group_ids: Iterable[int]) -> dict[int, list[Any]]:
        ids = sorted(set(group_ids))
        result: dict[int, list[Any]] = defaultdict(list)
        for chunk in _chunks(ids):
            for group, subject, sample, kind, peaks in self.db.execute(
                f"SELECT {column},subject_id,sample,subject_kind,peaks FROM members "
                f"WHERE {column} IN ({','.join('?' for _ in chunk)})",
                chunk,
            ):
                result[group].append((sample, subject, kind, peaks))
        return {
            group: [
                {
                    "physical_peak_keys": self.peak_keys(peaks),
                    "sample_key": self.samples[sample],
                    "subject_id": subject,
                    "subject_kind": kind,
                }
                for sample, subject, kind, peaks in sorted(rows)
            ]
            for group, rows in result.items()
        }

    def group_records(
        self, rows: Sequence[tuple[Any, ...]]
    ) -> Iterator[tuple[str, str, dict[str, Any], list[CandidateKey]]]:
        """Each group as its final-group record and, with a feature id, its row."""
        members = self.members("final_group", (row[0] for row in rows))
        cells: dict[int, list[Any]] = defaultdict(list)
        ids = [row[0] for row in rows]
        for chunk in _chunks(ids):
            for group, sample, peaks, position in self.db.execute(
                "SELECT group_id,sample,peaks,position_rt_min FROM cells "
                f"WHERE group_id IN ({','.join('?' for _ in chunk)}) "
                "ORDER BY group_id,sample",
                chunk,
            ):
                cells[group].append((sample, peaks, position))
        flags = self._row_risk_flags([row[0] for row in rows if row[1] is not None])
        for group_id, feature_id, disposition, destination, center, ppm, keys in rows:
            rid = feature_id if feature_id is not None else str(group_id)
            extractions = []
            for sample, peaks, position in cells.get(group_id, ()):
                sid = self.samples[sample]
                peak_ids = json.loads(peaks)
                for peak_id in peak_ids:
                    key, mz, rt, peak_ppm = self.peaks[peak_id]
                    extractions.append(
                        {
                            "mz": mz,
                            "physical_peak_key": key,
                            "ppm": peak_ppm,
                            "rt_apex_min": rt,
                            "sample_key": sid,
                        }
                    )
                if not peak_ids and position is not None:
                    extractions.append(
                        {
                            "group_position": True,
                            "mz": center,
                            "physical_peak_key": None,
                            "ppm": ppm,
                            "rt_apex_min": position,
                            "sample_key": sid,
                        }
                    )
            group = {
                "disposition": disposition,
                **({} if feature_id is None else {"feature_id": feature_id}),
                "member_subjects": members.get(group_id, []),
                "xic_extractions": extractions,
            }
            related = self.keys(keys)
            yield "final_groups", rid, group, related
            if feature_id is not None:
                yield (
                    "rows",
                    feature_id,
                    {
                        "feature_id": feature_id,
                        "membership_risk_flags": flags.get(group_id, []),
                        "route": {"destination": destination, "reason": disposition},
                    },
                    related,
                )

    def _member_risks(
        self, column: str, group_ids: Iterable[int]
    ) -> dict[tuple[int, int], list[tuple[str, dict[str, Any]]]]:
        """Accepted member risks of the given groups by (group, sample), in member
        order, restored with their flags."""
        result: dict[tuple[int, int], list[tuple[str, dict[str, Any]]]] = defaultdict(
            list
        )
        for chunk in _chunks(sorted(set(group_ids))):
            for group, sample, subject, risk, flags in self.db.execute(
                f"SELECT {column},sample,subject_id,accepted_risk,risk_flags "
                f"FROM members WHERE {column} IN ({','.join('?' for _ in chunk)}) "
                "AND accepted_risk IS NOT NULL ORDER BY sample,subject_id",
                chunk,
            ):
                value = decode_json(risk)
                value["risk_flags"] = json.loads(flags)
                result[group, sample].append((subject, value))
        return result

    @staticmethod
    def _validated(risks: list[Any]) -> list[Any]:
        try:
            validate_membership_risks(risks)
        except ValueError as exc:
            raise ExplanationError(f"malformed explanation: {exc}") from exc
        return risks

    def _row_risk_flags(self, group_ids: Sequence[int]) -> dict[int, list[str]]:
        flags: dict[int, set[str]] = defaultdict(set)
        for chunk in _chunks(sorted(set(group_ids))):
            marks = ",".join("?" for _ in chunk)
            for group, values in self.db.execute(
                "SELECT final_group,risk_flags FROM members "
                f"WHERE final_group IN ({marks}) AND risk_flags IS NOT NULL",
                chunk,
            ):
                flags[group].update(json.loads(values))
            for group, risks in self.db.execute(
                "SELECT group_id,risks FROM cells "
                f"WHERE group_id IN ({marks}) AND risks IS NOT NULL",
                chunk,
            ):
                for risk in decode_json(risks):
                    flags[group].update(risk["risk_flags"])
        return {group: sorted(values) for group, values in flags.items()}

    def initial_group_records(
        self, rows: Sequence[tuple[Any, ...]]
    ) -> Iterator[tuple[str, str, dict[str, Any], list[CandidateKey]]]:
        members = self.members("initial_group", (row[0] for row in rows))
        owner_risks = self._member_risks("initial_group", (row[0] for row in rows))
        for group_id, disposition, basis, decisions, keys in rows:
            record: dict[str, Any] = {
                "disposition": disposition,
                "member_subjects": members.get(group_id, []),
            }
            if basis is not None:
                owners = {
                    subject: risk
                    for (group, _), risks in owner_risks.items()
                    if group == group_id
                    for subject, risk in risks
                }
                record["inference_basis"] = decode_json(basis)
                record["inference_decisions"] = [
                    decision
                    if decision.get("accepted_risk") is not None
                    else {
                        **decision,
                        "accepted_risk": owners.get(decision.get("owner_id")),
                    }
                    for decision in decode_json(decisions)
                ]
            # The written record keeps every field in sorted order.
            record = json.loads(encode_object(record))
            yield "initial_groups", str(group_id), record, self.keys(keys)

    def edge_record(
        self, row: tuple[Any, ...]
    ) -> tuple[str, dict[str, Any], list[Any]]:
        _, left, right, disposition, evidence, context, config, keys = row
        return (
            _key(left, right),
            {
                "alignment_config": decode_json(config),
                "disposition": disposition,
                "initial_context": decode_json(context),
                "initial_evidence": decode_json(evidence),
                "left_subject_id": left,
                "right_subject_id": right,
            },
            self.keys(keys),
        )

    def curve_record(
        self, row: tuple[Any, ...]
    ) -> tuple[str, dict[str, Any], list[Any]]:
        payload_id, sample_id, kind, record_id, cid, peak, keys = row
        return (
            str(payload_id),
            {
                "candidate_id": cid,
                "kind": kind,
                "payload_id": payload_id,
                "physical_peak_key": peak,
                "record_id": record_id,
                "sample_id": sample_id,
            },
            self.keys(keys),
        )

    # Whole-report and selected reads ----------------------------------

    def records(
        self, kinds: Container[str]
    ) -> tuple[dict[str, dict[RecordKey, dict[str, Any]]], list[Link]]:
        records: dict[str, dict[RecordKey, dict[str, Any]]] = {}
        links: list[Link] = []

        def add(kind: str, rid: str, record: dict[str, Any], related: Iterable) -> None:
            records.setdefault(kind, {})[rid] = record
            links.extend((cid, sid, kind, rid) for cid, sid in related)

        if "method_subjects" in kinds:
            for subject in self.subjects:
                related = self.subject_candidates(subject, None)
                add(
                    "method_subjects",
                    self.subjects[subject][0],
                    self.method_subject(subject),
                    related,
                )
        if "method_assessments" in kinds:
            for row in self.db.execute("SELECT * FROM method_assessments ORDER BY id"):
                add(
                    "method_assessments",
                    str(row[0]),
                    self.assessment(row),
                    self.subject_candidates(row[2], row[1]),
                )
        if "exclusions" in kinds:
            for candidate, reason in self.db.execute(
                "SELECT candidate,reason_code FROM exclusions ORDER BY candidate"
            ):
                key = self.key(candidate)
                add("exclusions", _key(*key), self.exclusion(candidate, reason), (key,))
        if "terminal_subjects" in kinds:
            for row_id, row in self.candidate_rows.items():
                if row[8] is not None:
                    key = self.key(row_id)
                    add("terminal_subjects", _key(*key), self.terminal(row_id), (key,))
        if "final_groups" in kinds or "rows" in kinds:
            for kind, rid, record, related in self.group_records(
                list(self.db.execute("SELECT * FROM groups ORDER BY id"))
            ):
                if kind in kinds:
                    add(kind, rid, record, related)
        if "initial_groups" in kinds:
            for kind, rid, record, related in self.initial_group_records(
                list(self.db.execute("SELECT * FROM initial_groups ORDER BY id"))
            ):
                add(kind, rid, record, related)
        if "initial_edges" in kinds and "initial_edges" in self.tables:
            for row in self.db.execute("SELECT * FROM initial_edges ORDER BY id"):
                add("initial_edges", *self.edge_record(row))
        if "capture_evidence" in kinds and "curves" in self.tables:
            for row in self.db.execute(
                "SELECT payload_id,sample_id,kind,record_id,candidate_id,"
                "physical_peak_key,candidates FROM curves ORDER BY payload_id"
            ):
                add("capture_evidence", *self.curve_record(row))
        return records, links

    def cells(
        self, feature_ids: Sequence[str] | None = None
    ) -> dict[tuple[str, str], dict[str, Any]]:
        query = (
            "SELECT g.feature_id,c.sample,c.variant,c.value,c.apex_rt_min,c.source,"
            "c.support_origin,c.peaks,c.details,c.group_id,c.risks FROM cells c "
            "JOIN groups g ON g.id=c.group_id"
        )
        rows: Iterable[tuple[Any, ...]]
        if feature_ids is None:
            rows = self.db.execute(query + " ORDER BY c.group_id,c.sample")
        else:
            rows = (
                row
                for feature_id in feature_ids
                for row in self.db.execute(
                    query + " WHERE g.feature_id=? ORDER BY c.sample", (feature_id,)
                )
            )
        rows = list(rows)
        member_risks = self._member_risks("final_group", (row[9] for row in rows))
        result = {}
        for (
            feature_id,
            sample,
            variant,
            value,
            apex,
            source,
            origin,
            peaks,
            raw,
            group,
            stored_risks,
        ) in rows:
            details = validate_details(decode_json(raw))
            risks = self._validated(
                [
                    *(risk for _, risk in member_risks.get((group, sample), ())),
                    *(decode_json(stored_risks) or ()),
                ]
            )
            sid = self.samples[sample]
            result[feature_id, sid] = {
                "feature_id": feature_id,
                "sample_key": sid,
                "variant": variant,
                "value": value,
                "apex_rt_min": apex,
                "source": source,
                "evidence_states": details["evidence_states"],
                "reason_codes": details["reason_codes"],
                "support_origin": origin,
                "observation_ids": self.peak_keys(peaks),
                "signal_quality": [
                    {
                        **quality,
                        "observation_id": self.peaks[quality["observation_id"]][0],
                    }
                    for quality in details["signal_quality"]
                ],
                "membership_risks": risks,
                **{
                    key: value
                    for key, value in details.items()
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

    def selection(
        self, candidate_keys: Sequence[CandidateKey], row_ids: Sequence[str]
    ) -> tuple[dict[str, dict[RecordKey, dict[str, Any]]], list[Link]]:
        """Records linked to the selected candidates and rows, nothing else."""
        records: dict[str, dict[RecordKey, dict[str, Any]]] = {}
        links: set[Link] = set()
        selected = {
            self.candidate_ids[key]
            for key in candidate_keys
            if key in self.candidate_ids
        }

        def add(kind: str, rid: str, record: dict[str, Any], related: Iterable) -> None:
            linked = [(cid, sid) for cid, sid in related]
            if not any(self.candidate_ids.get(key) in selected for key in linked):
                if not (kind == "rows" and rid in row_ids):
                    return
            records.setdefault(kind, {})[rid] = record
            links.update(
                (cid, sid, kind, rid)
                for cid, sid in linked
                if self.candidate_ids.get((cid, sid)) in selected
            )

        rows = [self.candidate_rows[row_id] for row_id in sorted(selected)]
        subjects = {row[7] for row in rows if row[7] is not None}
        for subject in sorted(subjects):
            add(
                "method_subjects",
                self.subjects[subject][0],
                self.method_subject(subject),
                self.subject_candidates(subject, None),
            )
        pairs = sorted({(row[7], row[2]) for row in rows if row[7] is not None})
        for subject, sample in pairs:
            for row in self.db.execute(
                "SELECT * FROM method_assessments WHERE method_subject=? AND sample=? "
                "ORDER BY id",
                (subject, sample),
            ):
                add(
                    "method_assessments",
                    str(row[0]),
                    self.assessment(row),
                    self.subject_candidates(subject, sample),
                )
        for row_id in sorted(selected):
            reason = self.db.execute(
                "SELECT reason_code FROM exclusions WHERE candidate=?", (row_id,)
            ).fetchone()
            key = self.key(row_id)
            if reason is not None:
                add("exclusions", _key(*key), self.exclusion(row_id, reason[0]), (key,))
            if self.candidate_rows[row_id][8] is not None:
                add("terminal_subjects", _key(*key), self.terminal(row_id), (key,))
        group_rows = [
            row
            for row in self.db.execute("SELECT * FROM groups ORDER BY id")
            if row[1] in row_ids or selected.intersection(json.loads(row[6]))
        ]
        for kind, rid, record, related in self.group_records(group_rows):
            add(kind, rid, record, related)
        initial_rows = [
            row
            for row in self.db.execute("SELECT * FROM initial_groups ORDER BY id")
            if selected.intersection(json.loads(row[4]))
        ]
        for kind, rid, record, related in self.initial_group_records(initial_rows):
            add(kind, rid, record, related)
        if "initial_edges" in self.tables:
            for row in self.db.execute("SELECT * FROM initial_edges ORDER BY id"):
                if selected.intersection(json.loads(row[7])):
                    add("initial_edges", *self.edge_record(row))
        if "curves" in self.tables:
            for row in self.db.execute(
                "SELECT payload_id,sample_id,kind,record_id,candidate_id,"
                "physical_peak_key,candidates FROM curves ORDER BY payload_id"
            ):
                if selected.intersection(json.loads(row[6])):
                    add("capture_evidence", *self.curve_record(row))
        return records, sorted(links)


def _chunks(values: Sequence[Any], size: int = 900) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def read_review_cells(
    connection: sqlite3.Connection,
    summary: Mapping[str, Any],
    row_ids: Sequence[str] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Hydrate only selected rows; scalar readers never call this detail decoder."""
    return _Evidence(connection, summary).cells(row_ids)


@contextmanager
def open_review_cells(
    path: Path,
    summary: Mapping[str, Any],
) -> Iterator[Callable[[str], dict[tuple[str, str], dict[str, Any]]]]:
    """Keep a read-only row lookup open during bounded population selection."""
    with open_readonly(path) as connection:
        evidence = _Evidence(connection, summary)
        yield lambda row_id: evidence.cells((row_id,))


def read_review_row_samples(path: Path) -> dict[str, frozenset[str]]:
    """Read actual cell presence without decoding scientific detail payloads."""
    rows: dict[str, set[str]] = {}
    with open_readonly(path) as db:
        for rid, sid in db.execute(
            "SELECT g.feature_id,s.sample_key FROM cells c "
            "JOIN groups g ON g.id=c.group_id JOIN samples s ON s.id=c.sample"
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
        row = db.execute(
            "SELECT candidates FROM groups WHERE feature_id=?", (group_id,)
        ).fetchone()
        if row is None:
            return []
        ids = json.loads(row[0])
        if not ids:
            return []
        return sorted(
            {
                candidate_id
                for (candidate_id,) in db.execute(
                    "SELECT candidate_id FROM candidates "
                    f"WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
            }
        )


def read_row_cell_values(
    path: Path, feature_ids: Container[str]
) -> Iterator[tuple[str, str, float | None, str, float | None]]:
    """Read owner value, support origin and selected apex without cold evidence."""
    with open_readonly(path) as db:
        for feature_id, sample, value, origin, apex in db.execute(
            "SELECT g.feature_id,s.sample_key,c.value,c.support_origin,c.apex_rt_min "
            "FROM cells c JOIN groups g ON g.id=c.group_id "
            "JOIN samples s ON s.id=c.sample ORDER BY c.group_id,c.sample"
        ):
            if feature_id in feature_ids:
                yield feature_id, sample, value, origin, apex


def read_explanation_metadata(
    path: str | Path,
    *,
    selection_only: bool = False,
    query_only: bool = False,
) -> ExplanationMetadata:
    """Read the review population through the storage owner, without curves."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        evidence = _Evidence(connection, summary)
        compact_selection = selection_only and summary["retention"] == "review"
        kinds: Container[str]
        if compact_selection and query_only:
            kinds = _SCALAR_KINDS
        elif compact_selection:
            kinds = (
                "method_subjects",
                "method_assessments",
                "exclusions",
                "terminal_subjects",
                "final_groups",
                "rows",
                "initial_edges",
            )
        else:
            kinds = (
                "method_subjects",
                "method_assessments",
                "exclusions",
                "terminal_subjects",
                "final_groups",
                "rows",
                "initial_groups",
                "initial_edges",
                "capture_evidence",
            )
        records, links = evidence.records(kinds)
        candidates = {
            evidence.key(row_id): evidence.candidate(row_id)
            for row_id in evidence.candidate_rows
        }
        if not compact_selection:
            cells = evidence.cells()
            if cells:
                records.setdefault("cell_decisions", {}).update(cells.items())
        return ExplanationMetadata(summary, candidates, records, tuple(links))


def read_explanation_selection(
    path: Path,
    *,
    candidate_keys: Sequence[tuple[str, str]],
    row_ids: Sequence[str],
) -> ExplanationMetadata:
    """Restore detailed review records only for the selected witnesses and rows."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        evidence = _Evidence(connection, summary)
        candidates = {
            key: evidence.candidate(evidence.candidate_ids[key])
            for key in candidate_keys
            if key in evidence.candidate_ids
        }
        records, links = evidence.selection(candidate_keys, row_ids)
        for payload in list(records.get("rows", {}).values()):
            gid = payload.get("feature_id")
            if gid is None or gid in records.get("final_groups", {}):
                continue
            rows = list(
                connection.execute("SELECT * FROM groups WHERE feature_id=?", (gid,))
            )
            for kind, rid, record, _ in evidence.group_records(rows):
                if kind == "final_groups":
                    records.setdefault(kind, {})[rid] = record
        cells = evidence.cells(row_ids)
        if cells:
            records.setdefault("cell_decisions", {}).update(cells.items())
        return ExplanationMetadata(summary, candidates, records, tuple(links))


def read_alignment_explanation(
    path: str | Path, *, candidate_id: str | None = None
) -> dict[str, Any]:
    """Read a compact summary or only records linked to one exact candidate ID."""
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        if candidate_id is None:
            return summary
        evidence = _Evidence(connection, summary)
        keys = sorted(
            (key for key in evidence.candidate_ids if key[0] == candidate_id),
            key=lambda key: key[1],
        )
        if not keys:
            raise CandidateNotFoundError(f"candidate not found: {candidate_id}")
        candidates = [evidence.candidate(evidence.candidate_ids[key]) for key in keys]
        selected, _ = evidence.selection(keys, ())
        records: dict[str, list[dict[str, Any]]] = {}
        for kind in sorted(selected):
            for rid in sorted(selected[kind], key=str):
                records.setdefault(kind, []).append(selected[kind][rid])
        row_ids = [row["feature_id"] for row in records.get("rows", [])]
        if row_ids:
            records.setdefault("cell_decisions", []).extend(
                evidence.cells(row_ids).values()
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
        if "curves" not in present_tables(connection):
            return []
        query = (
            "SELECT payload_id, sample_id, kind, record_id, codec, payload "
            "FROM curves WHERE 1=1"
        )
        params: list[Any] = []
        if sample_ids:
            query += f" AND sample_id IN ({','.join('?' for _ in sample_ids)})"
            params.extend(sample_ids)
        if record_ids:
            query += f" AND record_id IN ({','.join('?' for _ in record_ids)})"
            params.extend(record_ids)
        results: list[dict[str, Any]] = []
        for payload_id, sample_id, kind, record_id, codec, blob in connection.execute(
            query + " ORDER BY sample_id, record_id, payload_id", params
        ):
            if codec != "zlib-json-utf8":
                raise ExplanationError(f"unsupported curve payload codec: {codec}")
            payload_data = decode_json(blob)
            validate_curve_payload(
                payload_data, sample_id=sample_id, kind=kind, record_id=record_id
            )
            results.append({**payload_data, "payload_id": payload_id})
        return results


def read_candidate_examples(path: Path) -> list[dict[str, Any]]:
    """The diagnostic CLI's small preview, queried rather than copied into metadata."""
    with open_readonly(path) as db:
        summary = read_completed_summary(db)
        evidence = _Evidence(db, summary)
        keys = sorted(evidence.candidate_ids)[:5]
        return [evidence.candidate(evidence.candidate_ids[key]) for key in keys]


def read_review_row_counts(
    path: Path, summary: Mapping[str, Any]
) -> Iterator[dict[str, Any]]:
    """Overview facts from named cell columns, without reasons, risks or curves."""
    samples = [s["sample_key"] for s in summary["samples"]]
    with open_readonly(path) as db:
        evidence = _Evidence(db, summary)
        rows = list(
            db.execute("SELECT * FROM groups WHERE feature_id IS NOT NULL ORDER BY id")
        )
        counts = {
            group: (numeric, unresolved)
            for group, numeric, unresolved in db.execute(
                "SELECT group_id,"
                "group_concat(CASE WHEN variant='numeric' THEN sample END),"
                "sum(variant='unresolved') FROM cells GROUP BY group_id"
            )
        }
        for kind, _, record, _ in evidence.group_records(rows):
            if kind != "rows":
                continue
            group = next(row[0] for row in rows if row[1] == record["feature_id"])
            numeric, unresolved = counts.get(group, (None, 0))
            yield {
                "row": record,
                "numeric_samples": [samples[int(i)] for i in numeric.split(",")]
                if numeric
                else [],
                "unresolved_count": unresolved or 0,
            }
