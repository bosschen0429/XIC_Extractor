"""Read completed Alignment facts without importing publication or execution.

Each stored concept is read as one object: a Discovery candidate with its
exclusion and terminal peak; a final group with its members, cells and result
row. Which candidates explain a group is the stored ``group_candidates``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import CandidateNotFoundError

from .storage import decode_review_curve, open_readonly, read_counts, read_run

CandidateKey = tuple[str, str]

_GROUPS = "SELECT id,feature_id,disposition,destination,center_mz,ppm FROM groups"


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """One Discovery candidate and the facts recorded about it.

    ``groups`` names the final groups this candidate explains.
    """

    fields: dict[str, Any]
    exclusion: dict[str, Any] | None
    terminal: dict[str, Any] | None
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroupEvidence:
    """One final group; with a feature ID it is a published row.

    ``risk_flags`` are the membership risk flags of a row's cells.
    """

    feature_id: str | None
    disposition: str | None
    destination: str | None
    members: list[dict[str, Any]]
    risk_flags: list[str]
    candidates: frozenset[CandidateKey]
    cells: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    """Completed facts for the read candidates and groups, without curve arrays."""

    summary: dict[str, Any]
    candidates: dict[CandidateKey, CandidateEvidence]
    groups: dict[str, GroupEvidence]


def _chunks(values: Sequence[Any], size: int = 900) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _in(column: str, chunk: Sequence[Any]) -> str:
    return f"{column} IN ({','.join('?' for _ in chunk)})"


class _Report:
    """Request-local lookups over one completed report."""

    def __init__(
        self,
        db: sqlite3.Connection,
        summary: Mapping[str, Any],
        *,
        candidate_id: str | None = None,
        candidate_keys: Iterable[CandidateKey] | None = None,
    ):
        self.db = db
        self.summary = summary
        self.samples = [sample["sample_key"] for sample in summary["samples"]]
        self.stems = [sample["sample_stem"] for sample in summary["samples"]]
        self.scoped = candidate_id is not None or candidate_keys is not None
        query = (
            "SELECT c.id,c.candidate_id,c.sample,c.precursor_mz,c.ms1_apex_mz,"
            "c.neutral_loss_tag,c.exclusion_reason,p.physical_peak_key,p.mz,"
            "p.rt_apex_min,p.ppm FROM candidates c "
            "LEFT JOIN peaks p ON p.id=c.terminal_peak"
        )
        if candidate_id is not None:
            rows = db.execute(query + " WHERE c.candidate_id=?", (candidate_id,))
        elif candidate_keys is not None:
            rows = db.execute(
                query + " JOIN samples s ON s.id=c.sample "
                "WHERE (c.candidate_id,s.sample_key) IN "
                "(SELECT value->>0,value->>1 FROM json_each(?))",
                (json.dumps(list(candidate_keys)),),
            )
        else:
            rows = db.execute(query)
        self.candidate_rows = {row[0]: row for row in sorted(rows)}

    def key(self, candidate_row_id: int) -> CandidateKey:
        row = self.candidate_rows[candidate_row_id]
        return row[1], self.samples[row[2]]

    def candidate_groups(self, candidates: Sequence[int]) -> dict[int, list[int]]:
        result: dict[int, list[int]] = defaultdict(list)
        if not self.scoped:
            rows: Iterable[tuple[int, int]] = self.db.execute(
                "SELECT candidate,group_id FROM group_candidates "
                "ORDER BY candidate,group_id"
            )
        else:
            rows = (
                row
                for chunk in _chunks(candidates)
                for row in self.db.execute(
                    "SELECT candidate,group_id FROM group_candidates "
                    f"WHERE {_in('candidate', chunk)} ORDER BY candidate,group_id",
                    chunk,
                )
            )
        for candidate, group in rows:
            result[candidate].append(group)
        return result

    def candidate(self, row_id: int, groups: Sequence[str]) -> CandidateEvidence:
        _, cid, sample, mz, apex_mz, tag, reason, peak, peak_mz, rt, ppm = (
            self.candidate_rows[row_id]
        )
        sid = self.samples[sample]
        return CandidateEvidence(
            fields={
                "candidate_id": cid,
                "sample_key": sid,
                "sample_stem": self.stems[sample],
                "precursor_mz": mz,
                "ms1_apex_mz": apex_mz,
                "neutral_loss_tag": tag,
                "neutral_loss_da": self.summary["neutral_loss_by_tag"].get(tag),
            },
            exclusion=None
            if reason is None
            else {"candidate_id": cid, "sample_key": sid, "reason_code": reason},
            terminal=None
            if peak is None
            else {
                "physical_peak_keys": [peak],
                "observations": [
                    {
                        "mz": peak_mz,
                        "physical_peak_key": peak,
                        "ppm": ppm,
                        "rt_apex_min": rt,
                    }
                ],
            },
            groups=tuple(groups),
        )

    def members(self, group_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
        rows: dict[tuple[int, int, str], list[str]] = defaultdict(list)
        for chunk in _chunks(group_ids):
            for group, sample, subject, peak in self.db.execute(
                "SELECT m.group_id,m.sample,m.subject_id,p.physical_peak_key "
                "FROM members m LEFT JOIN member_peaks mp ON mp.member=m.id "
                "LEFT JOIN peaks p ON p.id=mp.peak "
                f"WHERE {_in('m.group_id', chunk)} ORDER BY m.id,p.id",
                chunk,
            ):
                peaks = rows[group, sample, subject]
                if peak is not None:
                    peaks.append(peak)
        result: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for (group, sample, subject), peaks in sorted(rows.items()):
            result[group].append(
                {
                    "physical_peak_keys": peaks,
                    "sample_key": self.samples[sample],
                    "subject_id": subject,
                }
            )
        return result

    def cells(
        self, feature_ids: Sequence[str] | None
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Cells of the given rows (or of every row) by feature and sample."""
        query = (
            "SELECT g.feature_id,c.group_id,c.sample,c.variant,c.value,c.apex_rt_min,"
            "c.source,c.support_origin,c.evidence_states,c.reason_codes,"
            "(SELECT json_group_array(flag) FROM (SELECT flag FROM cell_risk_flags f "
            "WHERE f.group_id=c.group_id AND f.sample=c.sample ORDER BY flag)) "
            "FROM cells c JOIN groups g ON g.id=c.group_id"
        )
        if feature_ids is None:
            rows = self.db.execute(query + " ORDER BY c.group_id,c.sample").fetchall()
        else:
            rows = [
                row
                for chunk in _chunks(feature_ids)
                for row in self.db.execute(
                    query + f" WHERE {_in('g.feature_id', chunk)} "
                    "ORDER BY c.group_id,c.sample",
                    chunk,
                )
            ]
        peaks: dict[tuple[int, int], list[tuple[str, int | None]]] = defaultdict(list)
        for chunk in _chunks(sorted({row[1] for row in rows})):
            for group, sample, key, low in self.db.execute(
                "SELECT cp.group_id,cp.sample,p.physical_peak_key,"
                "cp.low_signal_to_noise FROM cell_peaks cp "
                "JOIN peaks p ON p.id=cp.peak "
                f"WHERE {_in('cp.group_id', chunk)} ORDER BY p.physical_peak_key",
                chunk,
            ):
                peaks[group, sample].append((key, low))
        competition: dict[tuple[int, int], list[Any]] = defaultdict(list)
        for chunk in _chunks(sorted({row[1] for row in rows})):
            for group, sample, alternative in self.db.execute(
                "SELECT group_id,sample,peers FROM cell_competition "
                f"WHERE {_in('group_id', chunk)} "
                "ORDER BY group_id,sample,batch,position",
                chunk,
            ):
                competition[group, sample].append(json.loads(alternative))
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (
            feature_id,
            group,
            sample,
            variant,
            value,
            apex,
            source,
            origin,
            evidence_states,
            reason_codes,
            risk_flags,
        ) in rows:
            sid = self.samples[sample]
            observed = peaks.get((group, sample), [])
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
                "observation_ids": [key for key, _ in observed],
                "signal_quality": [
                    {"observation_id": key, "low_signal_to_noise": bool(low)}
                    for key, low in observed
                    if low is not None
                ],
                "risk_flags": json.loads(risk_flags),
                "competition": competition.get((group, sample), []),
            }
        return result

    def groups(
        self,
        rows: Sequence[tuple[Any, ...]],
        candidates: Mapping[int, set[CandidateKey]],
        cells: Mapping[str, dict[str, dict[str, Any]]],
    ) -> dict[str, GroupEvidence]:
        """Each read group, keyed by its feature ID or else its row id."""
        members = self.members([row[0] for row in rows])
        result = {}
        for group_id, feature_id, disposition, destination, _center, _ppm in rows:
            group_cells = cells.get(feature_id, {}) if feature_id is not None else {}
            result[feature_id if feature_id is not None else str(group_id)] = (
                GroupEvidence(
                    feature_id=feature_id,
                    disposition=disposition,
                    destination=destination,
                    members=members.get(group_id, []),
                    risk_flags=sorted(
                        {
                            flag
                            for cell in group_cells.values()
                            for flag in cell["risk_flags"]
                        }
                    ),
                    candidates=frozenset(candidates.get(group_id, ())),
                    cells=group_cells,
                )
            )
        return result

    def evidence(self, row_ids: Sequence[str]) -> ReviewEvidence:
        """The admitted candidates and their groups, plus cells of requested rows."""
        selected = list(self.candidate_rows)
        groups = self.candidate_groups(selected)
        group_candidates: dict[int, set[CandidateKey]] = defaultdict(set)
        for candidate, ids in groups.items():
            if candidate in self.candidate_rows:
                for group in ids:
                    group_candidates[group].add(self.key(candidate))
        if not self.scoped:
            group_rows = list(self.db.execute(_GROUPS + " ORDER BY id"))
            cells = self.cells(None)
        else:
            related_rows = {
                row[0]: row
                for column, ids in (
                    ("id", sorted(group_candidates)),
                    ("feature_id", list(row_ids)),
                )
                for chunk in _chunks(ids)
                for row in self.db.execute(
                    _GROUPS + " WHERE " + _in(column, chunk), chunk
                )
            }
            group_rows = [related_rows[key] for key in sorted(related_rows)]
            cells = self.cells(row_ids)
        refs = {
            row[0]: row[1] if row[1] is not None else str(row[0]) for row in group_rows
        }
        return ReviewEvidence(
            summary=dict(self.summary),
            candidates={
                self.key(row_id): self.candidate(
                    row_id, [refs[group] for group in groups.get(row_id, ())]
                )
                for row_id in selected
            },
            groups=self.groups(group_rows, group_candidates, cells),
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
        summary = dict(summary) if summary else read_run(connection)
        return _Report(connection, summary, candidate_keys=candidate_keys).evidence(
            row_ids
        )


def read_explanation_metadata(path: Path) -> dict[str, Any]:
    """Read the run without computing report-wide counts."""
    with open_readonly(path) as connection:
        return read_run(connection)


@dataclass(frozen=True, slots=True)
class ReviewCurveLocation:
    """Where one sample's review curve of a group or candidate is read.

    A peak's location is its measured apex; ``physical_peak_key`` is None at the
    recorded group position of an empty cell. ``curve`` is the saved RT and raw
    intensity of the peak that gave a cell its value, when the run kept it.
    """

    sample_key: str
    sample_stem: str
    physical_peak_key: str | None
    mz: float
    ppm: float
    rt_apex_min: float
    curve: tuple[tuple[float, ...], tuple[float, ...]] | None


@dataclass(frozen=True, slots=True)
class SameMzPeak:
    """The apex of one Matrix value of a row sharing the reviewed row's m/z."""

    sample_key: str
    sample_stem: str
    feature_id: str
    apex_rt_min: float


@dataclass(frozen=True, slots=True)
class ReviewCurves:
    """The locations of one item and the run facts that place them in time.

    A result row also carries the valued apexes of every published row whose
    center m/z lies within its ppm, itself included, and that m/z and ppm.
    """

    locations: tuple[ReviewCurveLocation, ...]
    sample_rt_calibration: dict[str, Any] | None
    injection_order: dict[str, int]
    same_mz_peaks: tuple[SameMzPeak, ...] = ()
    row_mz: float | None = None
    row_ppm: float | None = None

    def needs_raw(self, sample_ids: Iterable[str]) -> bool:
        """Whether a requested sample has a location without a saved curve."""
        requested = set(sample_ids)
        return any(
            location.curve is None
            for location in self.locations
            if location.sample_key in requested
        )


_SAVED_CURVE = (
    "(SELECT cc.curve FROM cells v JOIN cell_peaks vp "
    "ON vp.group_id=v.group_id AND vp.sample=v.sample "
    "JOIN cell_curves cc ON cc.group_id=v.group_id AND cc.sample=v.sample "
    "WHERE v.sample=p.sample AND v.apex_rt_min=p.rt_apex_min "
    "AND vp.peak=p.id{scope})"
)
_GROUP_LOCATIONS = (
    "SELECT s.sample_key,s.sample_stem,p.physical_peak_key,"
    "CASE WHEN p.id IS NULL THEN g.center_mz ELSE p.mz END,"
    "CASE WHEN p.id IS NULL THEN g.ppm ELSE p.ppm END,"
    "CASE WHEN p.id IS NULL THEN c.position_rt_min ELSE p.rt_apex_min END,{curve} "
    "FROM groups g JOIN cells c ON c.group_id=g.id JOIN samples s ON s.id=c.sample "
    "LEFT JOIN cell_peaks j ON j.group_id=c.group_id AND j.sample=c.sample "
    "LEFT JOIN peaks p ON p.id=j.peak "
    "WHERE g.feature_id=? AND (p.id IS NOT NULL OR c.position_rt_min IS NOT NULL) "
    "ORDER BY c.sample,p.id"
)
_CANDIDATE_LOCATIONS = (
    "SELECT s.sample_key,s.sample_stem,p.physical_peak_key,p.mz,p.ppm,"
    "p.rt_apex_min,{curve} "
    "FROM candidates k JOIN samples s ON s.id=k.sample "
    "JOIN peaks p ON p.id=k.terminal_peak WHERE k.candidate_id=? ORDER BY k.sample"
)
_SAME_MZ_PEAKS = (
    "SELECT s.sample_key,s.sample_stem,o.feature_id,c.apex_rt_min "
    "FROM groups g JOIN groups o ON o.feature_id IS NOT NULL "
    "AND abs(o.center_mz-g.center_mz)<=g.center_mz*g.ppm*1e-6 "
    "JOIN cells c ON c.group_id=o.id JOIN samples s ON s.id=c.sample "
    "WHERE g.feature_id=? AND c.value IS NOT NULL AND c.apex_rt_min IS NOT NULL "
    "ORDER BY o.feature_id,c.sample"
)


def read_review_curves(
    path: Path, *, feature_id: str | None = None, candidate_id: str | None = None
) -> ReviewCurves:
    """Read one result row's or candidate's curve locations and saved curves."""
    with open_readonly(path) as db:
        if feature_id is not None:
            curve = _SAVED_CURVE.format(scope=" AND v.group_id=g.id")
            query, key = _GROUP_LOCATIONS, feature_id
        else:
            curve = _SAVED_CURVE.format(scope=" LIMIT 1")
            query, key = _CANDIDATE_LOCATIONS, str(candidate_id)
        rows = db.execute(query.format(curve=curve), (key,)).fetchall()
        (calibration,) = db.execute("SELECT sample_rt_calibration FROM run").fetchone()
        injection_order = dict(
            db.execute(
                "SELECT sample_stem,injection_order FROM samples "
                "WHERE injection_order IS NOT NULL ORDER BY id"
            )
        )
        same_mz: tuple[SameMzPeak, ...] = ()
        row_mz = row_ppm = None
        if feature_id is not None:
            same_mz = tuple(
                SameMzPeak(*row) for row in db.execute(_SAME_MZ_PEAKS, (feature_id,))
            )
            center = db.execute(
                "SELECT center_mz,ppm FROM groups WHERE feature_id=?", (feature_id,)
            ).fetchone()
            if center is not None:
                row_mz, row_ppm = center
    return ReviewCurves(
        tuple(
            ReviewCurveLocation(
                sample_key,
                stem,
                peak_key,
                mz,
                ppm,
                rt,
                None if payload is None else decode_review_curve(payload),
            )
            for sample_key, stem, peak_key, mz, ppm, rt, payload in rows
        ),
        None if calibration is None else json.loads(calibration),
        injection_order,
        same_mz,
        row_mz,
        row_ppm,
    )


def read_row_cell_values(
    path: Path, feature_ids: Iterable[str]
) -> Iterator[tuple[str, str, float | None, str, float | None]]:
    """Read owner value, support origin and selected apex of the given rows."""
    with open_readonly(path) as db:
        yield from db.execute(
            "SELECT g.feature_id,s.sample_key,c.value,c.support_origin,c.apex_rt_min "
            "FROM cells c JOIN groups g ON g.id=c.group_id "
            "JOIN samples s ON s.id=c.sample "
            "WHERE g.feature_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY c.group_id,c.sample",
            (json.dumps(list(feature_ids)),),
        )


def read_alignment_explanation(
    path: str | Path, *, candidate_id: str | None = None
) -> dict[str, Any]:
    """Read the run with its counts, or the facts of one exact candidate ID.

    A candidate is read with its terminal peak and exclusion; the groups it
    explains are read with their result rows and cells.
    """
    with open_readonly(path) as connection:
        summary = read_run(connection)
        if candidate_id is None:
            return {**summary, **read_counts(connection)}
        report = _Report(connection, summary, candidate_id=candidate_id)
        if not report.candidate_rows:
            raise CandidateNotFoundError(f"candidate not found: {candidate_id}")
        evidence = report.evidence(())
        cells = report.cells(
            sorted(
                group.feature_id
                for group in evidence.groups.values()
                if group.feature_id is not None
            )
        )
    return {
        "candidate_id": candidate_id,
        "candidates": [
            {
                **candidate.fields,
                "terminal_peak": None
                if candidate.terminal is None
                else candidate.terminal["observations"][0],
                "exclusion_reason": None
                if candidate.exclusion is None
                else candidate.exclusion["reason_code"],
            }
            for _, candidate in sorted(
                evidence.candidates.items(), key=lambda item: item[0][1]
            )
        ],
        "groups": [
            {
                "disposition": group.disposition,
                **(
                    {}
                    if group.feature_id is None
                    else {
                        "feature_id": group.feature_id,
                        "destination": group.destination,
                        "membership_risk_flags": group.risk_flags,
                        "cells": list(cells.get(ref, {}).values()),
                    }
                ),
                "member_subjects": group.members,
            }
            for ref, group in sorted(evidence.groups.items())
        ],
    }


def read_summary_with_examples(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The diagnostic CLI's summary and five example labels from the admitted report."""
    with open_readonly(path) as db:
        return {**read_run(db), **read_counts(db)}, [
            {"candidate_id": cid, "sample_stem": stem}
            for cid, stem in db.execute(
                "SELECT c.candidate_id,s.sample_stem FROM candidates c "
                "JOIN samples s ON s.id=c.sample "
                "ORDER BY c.candidate_id,s.sample_key LIMIT 5"
            )
        ]
