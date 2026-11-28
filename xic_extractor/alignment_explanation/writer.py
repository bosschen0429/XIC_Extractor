"""One atomic write transaction for completed Alignment review evidence.

The producer names samples, peaks, candidates and groups by their domain keys;
the writer inserts each fact as it arrives and keeps only the key-to-row-id
maps. Facts needed only to relate candidates to groups live in TEMP tables of
the write connection and are gone once the relation is materialized.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError

from .review import competition_peers
from .storage import create_schema, encode_json

PeakCoordinates = tuple[float | None, float | None, float | None]

_CELL_FIELDS = frozenset(
    {
        "feature_id",
        "sample_key",
        "variant",
        "value",
        "apex_rt_min",
        "source",
        "support_origin",
        "observation_ids",
        "evidence_states",
        "reason_codes",
        "signal_quality",
        "membership_risks",
    }
)

_WRITE_ONLY = """
CREATE TEMP TABLE candidate_subjects (
    candidate INTEGER PRIMARY KEY, method_subject_id TEXT NOT NULL);
CREATE TEMP TABLE hypotheses (
    candidate INTEGER PRIMARY KEY, sample INTEGER NOT NULL,
    hypothesis_id TEXT NOT NULL);
CREATE INDEX temp.hypotheses_subject ON hypotheses (sample, hypothesis_id);
CREATE TEMP TABLE member_kinds (member INTEGER PRIMARY KEY, subject_kind TEXT);
CREATE TEMP TABLE backfill_support (
    sample INTEGER NOT NULL, method_subject_id TEXT NOT NULL,
    peak INTEGER NOT NULL);
"""

_GROUP_CANDIDATES = """
CREATE TEMP TABLE peak_candidates AS
    SELECT terminal_peak AS peak, id AS candidate FROM main.candidates
    WHERE terminal_peak IS NOT NULL
    UNION
    SELECT b.peak, c.id FROM temp.backfill_support b
    JOIN temp.candidate_subjects s ON s.method_subject_id = b.method_subject_id
    JOIN main.candidates c ON c.id = s.candidate AND c.sample = b.sample;
CREATE INDEX temp.peak_candidates_peak ON peak_candidates (peak);
INSERT INTO main.group_candidates
    SELECT DISTINCT m.group_id, candidate FROM (
        SELECT m.id AS member, h.candidate FROM main.members m
        JOIN temp.member_kinds k ON k.member = m.id
        JOIN temp.hypotheses h
          ON h.sample = m.sample AND h.hypothesis_id = m.subject_id
        WHERE k.subject_kind = 'peak_hypothesis'
        UNION
        SELECT m.id, pc.candidate FROM main.members m
        LEFT JOIN temp.member_kinds k ON k.member = m.id
        JOIN main.peaks p
          ON p.sample = m.sample AND p.physical_peak_key = m.subject_id
        JOIN temp.peak_candidates pc ON pc.peak = p.id
        WHERE k.subject_kind IS NOT 'peak_hypothesis'
        UNION
        SELECT mp.member, pc.candidate FROM main.member_peaks mp
        JOIN temp.peak_candidates pc ON pc.peak = mp.peak
    ) related JOIN main.members m ON m.id = related.member;
"""

_CELL = "VALUES (:group,:sample,{})"
_MEMBER_CELL = (
    "SELECT c.group_id,c.sample,{} FROM members m "
    "JOIN cells c ON c.group_id=m.group_id AND c.sample=m.sample "
    "WHERE m.sample=:sample AND m.subject_id=:subject"
)


def _low_signal_to_noise(quality: Mapping[str, Any]) -> int | None:
    flag = quality.get("low_signal_to_noise")
    return int(flag) if isinstance(flag, bool) else None


class ExplanationReportWriter:
    """Write one run's evidence; publish it only by explicit finalization."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.temporary = self.path.with_name(self.path.name + ".tmp")
        self.connection: sqlite3.Connection | None = None
        self._samples: dict[str, int] = {}
        self._peaks: dict[tuple[str, str], int] = {}
        self._groups: dict[str, int] = {}
        self._candidates: dict[tuple[str, str], int] = {}
        self._risk_batch = 0
        self.candidate_count = 0
        self.record_count = 0

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute("PRAGMA journal_mode = OFF")
            self.connection.execute("PRAGMA synchronous = OFF")
            self.connection.execute("PRAGMA foreign_keys = ON")
            create_schema(self.connection)
            self.connection.executescript(_WRITE_ONLY)
            self.connection.execute("BEGIN")
        except BaseException:
            self.abort()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self.abort()

    @property
    def _db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("explanation writer is not active")
        return self.connection

    def _sample(self, sample_key: str) -> int:
        try:
            return self._samples[sample_key]
        except KeyError:
            raise ExplanationError(f"undeclared sample: {sample_key}") from None

    def declare_samples(self, samples: Sequence[Mapping[str, Any]]) -> None:
        """Samples in run order; every other table refers to these ordinals."""
        if self._samples:
            raise ValueError("samples are declared once")
        rows = [
            (
                index,
                sample["sample_key"],
                sample["sample_stem"],
                sample.get("injection_order"),
            )
            for index, sample in enumerate(samples)
        ]
        self._db.executemany("INSERT INTO samples VALUES (?,?,?,?)", rows)
        self._samples = {key: index for index, key, _, _ in rows}

    def add_peak(
        self,
        sample_key: str,
        physical_peak_key: str,
        coordinates: PeakCoordinates = (None, None, None),
    ) -> int:
        """Record one physical peak once; its coordinates are fixed by the peak."""
        key = sample_key, physical_peak_key
        known = self._peaks.get(key)
        if known is not None and coordinates == (None, None, None):
            return known
        (peak_id,) = self._db.execute(
            "INSERT INTO peaks (sample,physical_peak_key,mz,rt_apex_min,ppm) "
            "VALUES (?,?,?,?,?) ON CONFLICT (sample,physical_peak_key) DO UPDATE SET "
            "mz=COALESCE(excluded.mz,mz),"
            "rt_apex_min=COALESCE(excluded.rt_apex_min,rt_apex_min),"
            "ppm=COALESCE(excluded.ppm,ppm) RETURNING id",
            (self._sample(sample_key), physical_peak_key, *coordinates),
        ).fetchone()
        self._peaks[key] = peak_id
        return peak_id

    def _peak_ids(self, sample_key: str, keys: Iterable[str]) -> list[int]:
        return [self.add_peak(sample_key, key) for key in keys]

    def add_candidate(
        self,
        candidate_id: str,
        sample_key: str,
        *,
        precursor_mz: float | None,
        neutral_loss_tag: str,
        method_subject_id: str,
        ms1_apex_mz: float | None = None,
    ) -> None:
        """One declared Discovery candidate."""
        (row_id,) = self._db.execute(
            "INSERT INTO candidates "
            "(candidate_id,sample,precursor_mz,ms1_apex_mz,neutral_loss_tag) "
            "VALUES (?,?,?,?,?) RETURNING id",
            (
                candidate_id,
                self._sample(sample_key),
                precursor_mz,
                ms1_apex_mz,
                neutral_loss_tag,
            ),
        ).fetchone()
        self._db.execute(
            "INSERT INTO temp.candidate_subjects VALUES (?,?)",
            (row_id, method_subject_id),
        )
        self._candidates[candidate_id, sample_key] = row_id
        self.candidate_count += 1

    def _candidate(self, candidate_id: str, sample_key: str) -> int:
        try:
            return self._candidates[candidate_id, sample_key]
        except KeyError:
            raise ExplanationError(
                f"undeclared candidate: {candidate_id} in {sample_key}"
            ) from None

    def add_terminal(
        self,
        candidate_id: str,
        sample_key: str,
        *,
        hypothesis_id: str | None = None,
        physical_peak_key: str | None = None,
    ) -> None:
        """A candidate's terminal hypothesis and the peak it observed."""
        candidate = self._candidate(candidate_id, sample_key)
        if hypothesis_id is not None:
            self._db.execute(
                "INSERT INTO temp.hypotheses VALUES (?,?,?)",
                (candidate, self._sample(sample_key), hypothesis_id),
            )
        if physical_peak_key is not None:
            (peak,) = self._peak_ids(sample_key, (physical_peak_key,))
            self._db.execute(
                "UPDATE candidates SET terminal_peak=? WHERE id=?", (peak, candidate)
            )

    def add_backfill_support(
        self,
        sample_key: str,
        method_subject_id: str,
        physical_peak_keys: Sequence[str],
    ) -> None:
        """Peaks a Backfill SUPPORT assessment lists; they bind the subject's
        candidates of that sample, since Backfill has no terminal hypothesis."""
        sample = self._sample(sample_key)
        self._db.executemany(
            "INSERT INTO temp.backfill_support VALUES (?,?,?)",
            (
                (sample, method_subject_id, peak)
                for peak in self._peak_ids(sample_key, physical_peak_keys)
            ),
        )

    def add_exclusion(
        self, candidate_id: str, sample_key: str, reason_code: str
    ) -> None:
        candidate = self._candidate(candidate_id, sample_key)
        (stored,) = self._db.execute(
            "UPDATE candidates SET exclusion_reason=COALESCE(exclusion_reason,?) "
            "WHERE id=? RETURNING exclusion_reason",
            (reason_code, candidate),
        ).fetchone()
        if stored != reason_code:
            raise ValueError(
                f"conflicting candidate exclusion: {(candidate_id, sample_key)}"
            )

    def _members(self, group: int, members: Sequence[Mapping[str, Any]]) -> None:
        for member in members:
            sample_key = member["sample_key"]
            (member_id,) = self._db.execute(
                "INSERT INTO members (group_id,sample,subject_id) VALUES (?,?,?) "
                "ON CONFLICT (sample,subject_id) "
                "DO UPDATE SET group_id=excluded.group_id RETURNING id",
                (group, self._sample(sample_key), member["subject_id"]),
            ).fetchone()
            self._db.execute(
                "INSERT OR IGNORE INTO temp.member_kinds VALUES (?,?)",
                (member_id, member.get("subject_kind")),
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO member_peaks VALUES (?,?)",
                (
                    (member_id, peak)
                    for peak in self._peak_ids(
                        sample_key, member.get("physical_peak_keys", ())
                    )
                ),
            )

    def add_final_group(
        self,
        group_id: str,
        *,
        feature_id: str | None,
        disposition: str,
        members: Sequence[Mapping[str, Any]],
        center_mz: float | None = None,
        ppm: float | None = None,
    ) -> None:
        """One final group; with a feature id it is also one result row."""
        if group_id in self._groups:
            raise ValueError(f"duplicate final group: {group_id}")
        (row_id,) = self._db.execute(
            "INSERT INTO groups (feature_id,disposition,center_mz,ppm) "
            "VALUES (?,?,?,?) RETURNING id",
            (feature_id, disposition, center_mz, ppm),
        ).fetchone()
        self._groups[group_id] = row_id
        self.record_count += 1
        self._members(row_id, members)

    def extend_final_group(
        self, group_id: str, *, members: Sequence[Mapping[str, Any]]
    ) -> None:
        """Add members of another batch's established group to the Matrix row."""
        self._members(self._groups[group_id], members)

    def set_row_destination(self, group_id: str, destination: str) -> None:
        self._db.execute(
            "UPDATE groups SET destination=? WHERE id=?",
            (destination, self._groups[group_id]),
        )

    def add_cells(self, group_id: str, cells: Sequence[Mapping[str, Any]]) -> None:
        """A result row's cells; one cell per sample, scalars as columns.

        A cell's ``membership_risks`` are the risks no group member owns
        (Backfill admissions); ``add_member_risks`` adds the members' own.
        """
        group = self._groups[group_id]
        for cell in cells:
            unknown = set(cell) - _CELL_FIELDS
            if unknown:
                raise ValueError(f"undeclared cell fields: {sorted(unknown)}")
            sample_key = cell["sample_key"]
            sample = self._sample(sample_key)
            self._db.execute(
                "INSERT INTO cells VALUES (?,?,?,?,?,NULL,?,?,?,?)",
                (
                    group,
                    sample,
                    cell["variant"],
                    cell.get("value"),
                    cell.get("apex_rt_min"),
                    cell.get("source"),
                    cell.get("support_origin", "unavailable"),
                    encode_json(list(cell.get("evidence_states", ()))),
                    encode_json(list(cell.get("reason_codes", ()))),
                ),
            )
            self._risks(
                _CELL,
                {"group": group, "sample": sample},
                cell.get("membership_risks", ()),
                sample_key,
                batch=0,
            )
            quality = {
                q["observation_id"]: _low_signal_to_noise(q)
                for q in cell.get("signal_quality", ())
            }
            keys = dict.fromkeys((*cell.get("observation_ids", ()), *quality))
            self._db.executemany(
                "INSERT INTO cell_peaks VALUES (?,?,?,?)",
                (
                    (group, sample, peak, quality.get(key))
                    for key, peak in zip(
                        keys, self._peak_ids(sample_key, keys), strict=True
                    )
                ),
            )
            self.record_count += 1

    def _risks(
        self,
        cell: str,
        key: Mapping[str, Any],
        risks: Iterable[Mapping[str, Any]],
        sample_key: str,
        *,
        batch: int,
    ) -> None:
        """Record risks on the cell ``cell`` selects with ``key``.

        A flag or a competing alternative the cell already has is kept once.
        """
        risks = tuple(risks)
        self._db.executemany(
            "INSERT OR IGNORE INTO cell_risk_flags " + cell.format(":flag"),
            ({**key, "flag": flag} for risk in risks for flag in risk["risk_flags"]),
        )
        self._db.executemany(
            "INSERT OR IGNORE INTO cell_competition "
            + cell.format(":batch,:position,:peers"),
            (
                {
                    **key,
                    "batch": batch,
                    "position": position,
                    "peers": encode_json(peers),
                }
                for position, peers in enumerate(competition_peers(risks, sample_key))
            ),
        )

    def add_member_risks(
        self, risks: Mapping[tuple[str, str], Mapping[str, Any]]
    ) -> None:
        """Accepted membership risks by (sample, member subject); each is shown
        on its member's cell in the member's final group, ahead of the risks
        recorded before it."""
        for (sample_key, subject_id), risk in risks.items():
            self._risk_batch -= 1
            self._risks(
                _MEMBER_CELL,
                {"sample": self._sample(sample_key), "subject": subject_id},
                (risk,),
                sample_key,
                batch=self._risk_batch,
            )

    def set_cell_position(self, group_id: str, sample_key: str, rt_min: float) -> None:
        """The recorded group position used to inspect an empty cell."""
        self._db.execute(
            "UPDATE cells SET position_rt_min=? WHERE group_id=? AND sample=?",
            (rt_min, self._groups[group_id], self._sample(sample_key)),
        )

    def set_cell_curve(self, group_id: str, sample_key: str, curve: bytes) -> None:
        """The measured RT and raw intensity of the extraction window that gave
        a cell its value, as ``encode_review_curve`` stores them at full
        precision; readers recompute processed signals.
        """
        self._db.execute(
            "INSERT INTO cell_curves VALUES (?,?,?)",
            (self._groups[group_id], self._sample(sample_key), curve),
        )

    def finalize(self, summary: Mapping[str, Any]) -> Path:
        db = self._db
        if not self._samples:
            self.declare_samples(summary["samples"])
        calibration = summary.get("sample_rt_calibration")
        db.execute(
            "INSERT INTO run VALUES (1,?,?,?,?,?)",
            (
                summary["processing_attempt_id"],
                Path(
                    os.path.relpath(Path(summary["matrix_path"]), self.path.parent)
                ).as_posix(),
                summary["matrix_sha256"],
                encode_json(dict(sorted(summary["neutral_loss_by_tag"].items()))),
                None if calibration is None else encode_json(calibration),
            ),
        )
        db.executescript(_GROUP_CANDIDATES)
        db.execute("ANALYZE")
        db.commit()
        db.close()
        self.connection = None
        with self.temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        if self.path.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        os.replace(self.temporary, self.path)
        return self.path

    def abort(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.temporary.unlink(missing_ok=True)
