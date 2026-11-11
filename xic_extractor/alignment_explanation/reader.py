"""Read completed Alignment facts without importing publication or execution.

Each stored concept is read as one object: a Discovery candidate with its
method subject, assessments, exclusion and terminal observation; a final group
with its members, cells and result row; an initial group; a retained curve.
Which candidates a group, an initial group or a curve relates to follows from
the stored references (see ``relations``) and is read with that object.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Container, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import (
    CandidateNotFoundError,
    ExplanationError,
)

from .relations import relation
from .review import validate_membership_risks
from .storage import (
    decode_json,
    encode_object,
    open_readonly,
    present_tables,
    read_completed_summary,
)

CandidateKey = tuple[str, str]

_GROUPS = "SELECT id,feature_id,disposition,destination,center_mz,ppm FROM groups"


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """One Discovery candidate and the facts recorded about it.

    ``groups``, ``initial_groups`` and ``curves`` are the references of the
    read groups and curves this candidate relates to.
    """

    fields: dict[str, Any]
    method_subject_id: str
    method_subject: dict[str, Any]
    assessments: tuple[tuple[str, dict[str, Any]], ...]
    exclusion: dict[str, Any] | None
    terminal: dict[str, Any] | None
    groups: tuple[str, ...]
    initial_groups: tuple[str, ...]
    curves: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroupEvidence:
    """One final group: its record, its result row and its cells by sample."""

    feature_id: str | None
    record: dict[str, Any]
    row: dict[str, Any] | None
    candidates: frozenset[CandidateKey]
    cells: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    """Completed facts for the read candidates and groups, without curve arrays."""

    summary: dict[str, Any]
    candidates: dict[CandidateKey, CandidateEvidence]
    groups: dict[str, GroupEvidence]
    initial_groups: dict[str, dict[str, Any]]
    curves: dict[str, dict[str, Any]]


def _sorted(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


def _chunks(values: Sequence[Any], size: int = 900) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _in(column: str, chunk: Sequence[Any]) -> str:
    return f"{column} IN ({','.join('?' for _ in chunk)})"


class _Report:
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
                "hypothesis_id,terminal_peak FROM candidates ORDER BY id"
            )
        }
        self.candidate_ids = {
            (row[1], self.samples[row[2]]): row_id
            for row_id, row in self.candidate_rows.items()
        }

    def key(self, candidate_row_id: int) -> CandidateKey:
        row = self.candidate_rows[candidate_row_id]
        return row[1], self.samples[row[2]]

    def related(
        self, view: str, column: str, candidates: Sequence[int]
    ) -> dict[int, list[Any]]:
        """Records of a relation view each given candidate relates to."""
        result: dict[int, list[Any]] = defaultdict(list)
        table = relation(self.db, view)
        for chunk in _chunks(candidates):
            for candidate, record in self.db.execute(
                f"SELECT candidate,{column} FROM {table} "
                f"WHERE {_in('candidate', chunk)} ORDER BY candidate,{column}",
                chunk,
            ):
                result[candidate].append(record)
        return result

    def peak_keys(self, encoded: str) -> list[str]:
        return [self.peaks[peak_id][0] for peak_id in json.loads(encoded)]

    # Candidates --------------------------------------------------------

    def candidate(self, row_id: int) -> dict[str, Any]:
        row = self.candidate_rows[row_id]
        return {
            "configured_neutral_loss_da": self.summary["neutral_loss_by_tag"][row[5]],
            "method_subject_id": self.subjects[row[7]][0],
            "neutral_loss_tag": row[5],
            "peak_status": row[6],
            "candidate_id": row[1],
            "sample_key": self.samples[row[2]],
            "sample_stem": self.stems[row[2]],
            "precursor_mz": row[3],
            "ms1_apex_mz": row[4],
        }

    def terminal(self, row_id: int) -> dict[str, Any] | None:
        """The terminal observation, as the Inspector's evidence card shows it."""
        row = self.candidate_rows[row_id]
        if row[8] is None:
            return None
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

    def candidates(
        self,
        row_ids: Sequence[int],
        groups: Mapping[int, list[Any]],
        initial_groups: Mapping[int, list[Any]],
        curves: Mapping[int, list[Any]],
    ) -> dict[CandidateKey, CandidateEvidence]:
        assessments: dict[tuple[int, int], list[tuple[str, dict[str, Any]]]] = (
            defaultdict(list)
        )
        pairs = sorted(
            {(self.candidate_rows[i][7], self.candidate_rows[i][2]) for i in row_ids}
        )
        for subject, sample in pairs:
            for row in self.db.execute(
                "SELECT * FROM method_assessments WHERE method_subject=? AND sample=? "
                "ORDER BY id",
                (subject, sample),
            ):
                assessments[subject, sample].append((str(row[0]), self.assessment(row)))
        exclusions: dict[int, str] = {}
        for chunk in _chunks(list(row_ids)):
            exclusions.update(
                self.db.execute(
                    "SELECT candidate,reason_code FROM exclusions "
                    f"WHERE {_in('candidate', chunk)}",
                    chunk,
                )
            )
        result = {}
        for row_id in row_ids:
            row = self.candidate_rows[row_id]
            reason = exclusions.get(row_id)
            result[self.key(row_id)] = CandidateEvidence(
                fields=self.candidate(row_id),
                method_subject_id=self.subjects[row[7]][0],
                method_subject=self.method_subject(row[7]),
                assessments=tuple(assessments.get((row[7], row[2]), ())),
                exclusion=None if reason is None else self.exclusion(row_id, reason),
                terminal=self.terminal(row_id),
                groups=tuple(groups.get(row_id, ())),
                initial_groups=tuple(initial_groups.get(row_id, ())),
                curves=tuple(curves.get(row_id, ())),
            )
        return result

    # Groups ------------------------------------------------------------

    def members(self, column: str, group_ids: Iterable[int]) -> dict[int, list[Any]]:
        ids = sorted(set(group_ids))
        result: dict[int, list[Any]] = defaultdict(list)
        for chunk in _chunks(ids):
            for group, subject, sample, kind, peaks in self.db.execute(
                f"SELECT {column},subject_id,sample,subject_kind,peaks FROM members "
                f"WHERE {_in(column, chunk)}",
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

    def groups(
        self,
        rows: Sequence[tuple[Any, ...]],
        candidates: Mapping[int, set[CandidateKey]],
        cells: Mapping[str, dict[str, dict[str, Any]]],
    ) -> dict[str, GroupEvidence]:
        """Each group with its final-group record and, with a feature id, its row."""
        members = self.members("final_group", (row[0] for row in rows))
        positions: dict[int, list[Any]] = defaultdict(list)
        ids = [row[0] for row in rows]
        for chunk in _chunks(ids):
            for group, sample, peaks, position in self.db.execute(
                "SELECT group_id,sample,peaks,position_rt_min FROM cells "
                f"WHERE {_in('group_id', chunk)} ORDER BY group_id,sample",
                chunk,
            ):
                positions[group].append((sample, peaks, position))
        flags = self._row_risk_flags([row[0] for row in rows if row[1] is not None])
        result = {}
        for group_id, feature_id, disposition, destination, center, ppm in rows:
            extractions = []
            for sample, peaks, position in positions.get(group_id, ()):
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
            result[feature_id if feature_id is not None else str(group_id)] = (
                GroupEvidence(
                    feature_id=feature_id,
                    record={
                        "disposition": disposition,
                        **({} if feature_id is None else {"feature_id": feature_id}),
                        "member_subjects": members.get(group_id, []),
                        "xic_extractions": extractions,
                    },
                    row=None
                    if feature_id is None
                    else {
                        "feature_id": feature_id,
                        "membership_risk_flags": flags.get(group_id, []),
                        "route": {"destination": destination, "reason": disposition},
                    },
                    candidates=frozenset(candidates.get(group_id, ())),
                    cells=cells.get(feature_id, {}) if feature_id is not None else {},
                )
            )
        return result

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
                f"FROM members WHERE {_in(column, chunk)} "
                "AND accepted_risk IS NOT NULL ORDER BY sample,subject_id",
                chunk,
            ):
                value = decode_json(risk)
                value["risk_flags"] = json.loads(flags)
                result[group, sample].append((subject, value))
        return result

    def _row_risk_flags(self, group_ids: Sequence[int]) -> dict[int, list[str]]:
        flags: dict[int, set[str]] = defaultdict(set)
        for chunk in _chunks(sorted(set(group_ids))):
            for group, values in self.db.execute(
                "SELECT final_group,risk_flags FROM members "
                f"WHERE {_in('final_group', chunk)} AND risk_flags IS NOT NULL",
                chunk,
            ):
                flags[group].update(json.loads(values))
            for group, risks in self.db.execute(
                "SELECT group_id,risks FROM cells "
                f"WHERE {_in('group_id', chunk)} AND risks IS NOT NULL",
                chunk,
            ):
                for risk in decode_json(risks):
                    flags[group].update(risk["risk_flags"])
        return {group: sorted(values) for group, values in flags.items()}

    def initial_groups(self, group_ids: Iterable[int]) -> dict[str, dict[str, Any]]:
        ids = sorted(set(group_ids))
        rows: list[tuple[Any, ...]] = []
        for chunk in _chunks(ids):
            rows.extend(
                self.db.execute(
                    f"SELECT * FROM initial_groups WHERE {_in('id', chunk)}", chunk
                )
            )
        rows.sort(key=lambda row: row[0])
        members = self.members("initial_group", ids)
        owner_risks = self._member_risks("initial_group", ids)
        result = {}
        for group_id, disposition, basis, decisions in rows:
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
            result[str(group_id)] = json.loads(encode_object(record))
        return result

    def curves(self, payload_ids: Iterable[int]) -> dict[str, dict[str, Any]]:
        ids = sorted(set(payload_ids))
        result = {}
        for chunk in _chunks(ids):
            for payload_id, sample_id, kind, record_id, cid, peak in self.db.execute(
                "SELECT payload_id,sample_id,kind,record_id,candidate_id,"
                f"physical_peak_key FROM curves WHERE {_in('payload_id', chunk)} "
                "ORDER BY payload_id",
                chunk,
            ):
                result[str(payload_id)] = {
                    "candidate_id": cid,
                    "kind": kind,
                    "payload_id": payload_id,
                    "physical_peak_key": peak,
                    "record_id": record_id,
                    "sample_id": sample_id,
                }
        return result

    def cells(
        self, feature_ids: Sequence[str] | None
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Cells of the given rows (or of every row) by feature and sample."""
        query = (
            "SELECT g.feature_id,c.sample,c.variant,c.value,c.apex_rt_min,c.source,"
            "c.support_origin,c.peaks,c.evidence_states,c.reason_codes,"
            "c.signal_quality,c.observation,c.group_id,c.risks FROM cells c "
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
        member_risks = self._member_risks("final_group", (row[12] for row in rows))
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (
            feature_id,
            sample,
            variant,
            value,
            apex,
            source,
            origin,
            peaks,
            evidence_states,
            reason_codes,
            signal_quality,
            observation,
            group,
            stored_risks,
        ) in rows:
            risks = [
                *(risk for _, risk in member_risks.get((group, sample), ())),
                *(decode_json(stored_risks) or ()),
            ]
            try:
                validate_membership_risks(risks)
            except ValueError as exc:
                raise ExplanationError(f"malformed explanation: {exc}") from exc
            sid = self.samples[sample]
            result.setdefault(feature_id, {})[sid] = {
                "feature_id": feature_id,
                "sample_key": sid,
                "variant": variant,
                "value": value,
                "apex_rt_min": apex,
                "source": source,
                "evidence_states": json.loads(evidence_states),
                "reason_codes": json.loads(reason_codes),
                "support_origin": origin,
                "observation_ids": self.peak_keys(peaks),
                "signal_quality": [
                    {
                        **quality,
                        "observation_id": self.peaks[quality["observation_id"]][0],
                    }
                    for quality in json.loads(signal_quality)
                ],
                "membership_risks": risks,
                **(
                    {}
                    if observation is None
                    else {"observation": decode_json(observation)}
                ),
            }
        return result

    def evidence(
        self, candidate_keys: Iterable[CandidateKey] | None, row_ids: Sequence[str]
    ) -> ReviewEvidence:
        """Facts of the given candidates (or all) and the groups they relate to.

        A group lists only the read candidates it relates to. Rows in
        ``row_ids`` are read with their cells even when no read candidate
        relates to them.
        """
        if candidate_keys is None:
            selected = list(self.candidate_rows)
        else:
            selected = sorted(
                {
                    self.candidate_ids[key]
                    for key in candidate_keys
                    if key in self.candidate_ids
                }
            )
        groups = self.related("group_candidates", "group_id", selected)
        initial = self.related("initial_group_candidates", "group_id", selected)
        curves = (
            self.related("curve_candidates", "payload_id", selected)
            if "curves" in self.tables
            else {}
        )
        group_candidates: dict[int, set[CandidateKey]] = defaultdict(set)
        for candidate, ids in groups.items():
            for group in ids:
                group_candidates[group].add(self.key(candidate))
        if candidate_keys is None:
            group_rows = list(self.db.execute(_GROUPS + " ORDER BY id"))
            cells = self.cells(None)
        else:
            group_rows = [
                row
                for row in self.db.execute(_GROUPS + " ORDER BY id")
                if row[0] in group_candidates or row[1] in row_ids
            ]
            cells = self.cells(row_ids)
        read_groups = self.groups(group_rows, group_candidates, cells)
        group_refs = {
            row[0]: row[1] if row[1] is not None else str(row[0]) for row in group_rows
        }
        return ReviewEvidence(
            summary=dict(self.summary),
            candidates=self.candidates(
                selected,
                {
                    candidate: [group_refs[group] for group in ids]
                    for candidate, ids in groups.items()
                },
                {
                    candidate: [str(group) for group in ids]
                    for candidate, ids in initial.items()
                },
                {
                    candidate: [str(payload) for payload in ids]
                    for candidate, ids in curves.items()
                },
            ),
            groups=read_groups,
            initial_groups=self.initial_groups(
                group for ids in initial.values() for group in ids
            ),
            curves=self.curves(payload for ids in curves.values() for payload in ids),
        )


def read_review_evidence(
    path: str | Path,
    *,
    candidate_keys: Iterable[CandidateKey] | None = None,
    row_ids: Sequence[str] = (),
    summary: Mapping[str, Any] | None = None,
) -> ReviewEvidence:
    """Read the given candidates and rows, or every one, without curve arrays.

    A caller that already admitted this report's summary passes it on.
    """
    with open_readonly(path) as connection:
        summary = dict(summary) if summary else read_completed_summary(connection)
        return _Report(connection, summary).evidence(candidate_keys, row_ids)


def read_explanation_summary(path: Path) -> dict[str, Any]:
    """Read only the validated completion summary, without facts or cells."""
    with open_readonly(path) as connection:
        return read_completed_summary(connection)


def read_group_candidate_ids(path: Path, group_id: str) -> list[str]:
    """Candidate locators for a saved group, without loading its curve payloads."""
    with open_readonly(path) as db:
        groups = relation(db, "group_candidates")
        return [
            candidate_id
            for (candidate_id,) in db.execute(
                f"SELECT DISTINCT c.candidate_id FROM {groups} r "
                "JOIN groups g ON g.id=r.group_id "
                "JOIN candidates c ON c.id=r.candidate "
                "WHERE g.feature_id=? ORDER BY c.candidate_id",
                (group_id,),
            )
        ]


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


def read_alignment_explanation(
    path: str | Path, *, candidate_id: str | None = None
) -> dict[str, Any]:
    """Read a compact summary, or the facts of one exact candidate ID.

    A candidate is read with its terminal peak, method evidence and exclusion;
    the groups it relates to are read with their result rows and cells.
    """
    with open_readonly(path) as connection:
        summary = read_completed_summary(connection)
        if candidate_id is None:
            return summary
        report = _Report(connection, summary)
        keys = sorted(
            (key for key in report.candidate_ids if key[0] == candidate_id),
            key=lambda key: key[1],
        )
        if not keys:
            raise CandidateNotFoundError(f"candidate not found: {candidate_id}")
        evidence = report.evidence(keys, ())
        cells = report.cells(
            sorted(
                group.feature_id
                for group in evidence.groups.values()
                if group.feature_id is not None
            )
        )
        candidates = []
        subjects: dict[str, dict[str, Any]] = {}
        assessments: dict[str, dict[str, Any]] = {}
        for key in keys:
            candidate = evidence.candidates[key]
            terminal = candidate.terminal
            observations = (terminal or {}).get("observations", [])
            candidates.append(
                {
                    **candidate.fields,
                    "terminal": None
                    if terminal is None
                    else {
                        "variant": terminal["variant"],
                        "hypothesis_id": terminal.get("hypothesis_id"),
                        "peak": observations[0] if observations else None,
                    },
                    "exclusion_reason": None
                    if candidate.exclusion is None
                    else candidate.exclusion["reason_code"],
                }
            )
            subjects[candidate.method_subject_id] = candidate.method_subject
            assessments.update(candidate.assessments)
        return {
            "schema_version": summary["schema_version"],
            "diagnostic_only": True,
            "completion": summary["completion"],
            "candidate_id": candidate_id,
            "candidates": candidates,
            "method_subjects": [subjects[key] for key in sorted(subjects)],
            "method_assessments": [assessments[ref] for ref in sorted(assessments)],
            "groups": [
                {
                    **group.record,
                    **(
                        {}
                        if group.row is None
                        else {
                            "destination": group.row["route"]["destination"],
                            "membership_risk_flags": group.row["membership_risk_flags"],
                            "cells": list(cells.get(ref, {}).values()),
                        }
                    ),
                }
                for ref, group in sorted(evidence.groups.items())
            ],
            "initial_groups": [
                evidence.initial_groups[ref] for ref in sorted(evidence.initial_groups)
            ],
            "capture_evidence": [
                evidence.curves[ref] for ref in sorted(evidence.curves)
            ],
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
            "SELECT payload_id, sample_id, kind, record_id, codec, payload, "
            "candidate_id, physical_peak_key, "
            "(SELECT method_subject_id FROM method_subjects m "
            "WHERE m.id=curves.method_subject) "
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
        for (
            payload_id,
            sample_id,
            kind,
            record_id,
            codec,
            blob,
            candidate_id,
            peak,
            subject,
        ) in connection.execute(
            query + " ORDER BY sample_id, record_id, payload_id", params
        ):
            if codec != "zlib-json-utf8":
                raise ExplanationError(f"unsupported curve payload codec: {codec}")
            payload_data = decode_json(blob)
            # These fields are stored once, as the curve's columns.
            payload_data.update(
                sample_id=sample_id,
                kind=kind,
                candidate_id=candidate_id,
                physical_peak_key=peak,
            )
            payload_data["provenance"]["method_subject_id"] = subject
            validate_curve_payload(
                payload_data, sample_id=sample_id, kind=kind, record_id=record_id
            )
            results.append({**payload_data, "payload_id": payload_id})
        return results


def read_candidate_examples(path: Path) -> list[dict[str, Any]]:
    """The diagnostic CLI's small preview, queried rather than copied into metadata."""
    with open_readonly(path) as db:
        summary = read_completed_summary(db)
        report = _Report(db, summary)
        keys = sorted(report.candidate_ids)[:5]
        return [report.candidate(report.candidate_ids[key]) for key in keys]


def read_review_row_counts(
    path: Path, summary: Mapping[str, Any]
) -> Iterator[dict[str, Any]]:
    """Overview facts from named cell columns, without reasons, risks or curves."""
    samples = [s["sample_key"] for s in summary["samples"]]
    with open_readonly(path) as db:
        report = _Report(db, summary)
        rows = list(db.execute(_GROUPS + " WHERE feature_id IS NOT NULL ORDER BY id"))
        counts = {
            group: (numeric, unresolved)
            for group, numeric, unresolved in db.execute(
                "SELECT group_id,"
                "group_concat(CASE WHEN variant='numeric' THEN sample END),"
                "sum(variant='unresolved') FROM cells GROUP BY group_id"
            )
        }
        for (group_id, *_), group in zip(
            rows, report.groups(rows, {}, {}).values(), strict=True
        ):
            numeric, unresolved = counts.get(group_id, (None, 0))
            yield {
                "row": group.row,
                "numeric_samples": [samples[int(i)] for i in numeric.split(",")]
                if numeric
                else [],
                "unresolved_count": unresolved or 0,
            }
