"""One atomic write transaction for completed Alignment evidence.

The producer names samples, peaks, candidates and groups by their domain keys;
the writer stores each concept once and turns references into row ids.
"""

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
from .storage import (
    SCHEMA_VERSION,
    create_optional_table,
    create_schema,
    encode_object,
)

CandidateKey = tuple[str, str]
# m/z, apex RT and the query ppm of one physical peak; None when not recorded.
PeakCoordinates = tuple[float | None, float | None, float | None]

# The fields of one cell as the decision owner reports them.
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
        "observation",
    }
)


def _ordered_json(value: Any) -> str:
    """JSON keeping the owner's field order, as readers display it."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _compressed(value: Any) -> bytes:
    return zlib.compress(_ordered_json(value).encode("utf-8"), 1)


class ExplanationReportWriter:
    """Buffer one run's evidence; publish it only by explicit finalization."""

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
        self._samples: dict[str, int] = {}
        self._peaks: dict[tuple[str, str], list[Any]] = {}
        self._subjects: dict[str, list[Any]] = {}
        self._candidates: dict[CandidateKey, dict[str, Any]] = {}
        self._groups: dict[str, list[Any]] = {}
        self._group_ids: list[str] = []
        self._initial_groups: list[list[Any]] = []
        self._members: dict[tuple[str, str], list[Any]] = {}
        self._cells: dict[tuple[str, str], list[Any]] = {}
        self._assessments: list[tuple[Any, ...]] = []
        self._exclusions: dict[CandidateKey, str] = {}
        self._edges: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._last_record_id = 0
        self._curves: dict[tuple[str, str, str, bytes], list[Any]] = {}

    def _record_id(self) -> int:
        """Records without a natural key share one numbering, in emission order,
        so a displayed record reference names exactly one record."""
        self._last_record_id += 1
        return self._last_record_id

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    @property
    def record_count(self) -> int:
        return (
            len(self._subjects)
            + len(self._assessments)
            + len(self._exclusions)
            + len(self._groups)
            + len(self._initial_groups)
            + len(self._edges)
            + len(self._curves)
            + len(self._cells)
        )

    def __enter__(self) -> ExplanationReportWriter:
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        with self.temporary.open("xb"):
            pass
        try:
            self.connection = sqlite3.connect(self.temporary)
            self.connection.execute("PRAGMA page_size = 4096;")
            self.connection.execute("PRAGMA synchronous = OFF;")
            self.connection.execute("PRAGMA journal_mode = OFF;")
            create_schema(self.connection)
            self.connection.execute("BEGIN")
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

    # Peaks and subjects -------------------------------------------------

    def add_peak(
        self,
        sample_key: str,
        physical_peak_key: str,
        coordinates: PeakCoordinates = (None, None, None),
    ) -> None:
        """Record one physical peak once; its coordinates are fixed by the peak."""
        stored = self._peaks.setdefault(
            (sample_key, physical_peak_key), [len(self._peaks) + 1, None, None, None]
        )
        for index, value in enumerate(coordinates, start=1):
            if value is None:
                continue
            if stored[index] is None:
                stored[index] = value
            elif stored[index] != value:
                raise ValueError(
                    f"distinct peaks share one locator: {physical_peak_key}"
                )

    def _peak_ids(self, sample_key: str, keys: Iterable[str]) -> list[int]:
        ids = []
        for key in keys:
            self.add_peak(sample_key, key)
            ids.append(self._peaks[sample_key, key][0])
        return ids

    def add_method_subject(self, payload: Mapping[str, Any]) -> None:
        sid = payload["method_subject_id"]
        row = [
            len(self._subjects) + 1,
            sid,
            payload.get("subject_kind"),
            payload.get("precursor_mz"),
            payload.get("precursor_tolerance_ppm"),
            payload.get("neutral_loss_da"),
            None
            if payload.get("composition") is None
            else encode_object(payload["composition"]),
        ]
        existing = self._subjects.get(sid)
        if existing is not None and all(value is None for value in existing[2:]):
            # A candidate named this subject first; its facts arrive now.
            existing[2:] = row[2:]
        elif existing is None:
            self._subjects[sid] = row
        elif existing[1:] != row[1:]:
            raise ValueError(f"conflicting method subject: {sid}")

    def _subject_id(self, method_subject_id: str | None) -> int | None:
        if method_subject_id is None:
            return None
        if method_subject_id not in self._subjects:
            self.add_method_subject({"method_subject_id": method_subject_id})
        return int(self._subjects[method_subject_id][0])

    # Candidates ---------------------------------------------------------

    def add_candidate(
        self,
        candidate_id: str,
        sample_key: str,
        *,
        precursor_mz: float | None,
        neutral_loss_tag: str,
        method_subject_id: str,
        ms1_apex_mz: float | None = None,
        peak_status: str | None = None,
    ) -> None:
        """One declared Discovery candidate: its tag and method subject are part
        of what readers show for it."""
        key = candidate_id, sample_key
        if key in self._candidates:
            raise ValueError("duplicate diagnostic candidate/sample")
        # Register the subject now; finalize writes subjects before candidates.
        self._subject_id(method_subject_id)
        self._candidates[key] = {
            "id": len(self._candidates) + 1,
            "precursor_mz": precursor_mz,
            "ms1_apex_mz": ms1_apex_mz,
            "neutral_loss_tag": neutral_loss_tag,
            "peak_status": peak_status,
            "method_subject": method_subject_id,
            "terminal_variant": None,
            "hypothesis_id": None,
            "terminal_peak": None,
        }

    def add_terminal(
        self,
        candidate_id: str,
        sample_key: str,
        *,
        variant: str,
        hypothesis_id: str | None = None,
        physical_peak_key: str | None = None,
    ) -> None:
        """A candidate's terminal search subject: its variant and observed peak."""
        candidate = self._candidates[candidate_id, sample_key]
        if candidate["terminal_variant"] is not None:
            raise ValueError("duplicate terminal subject for one candidate")
        candidate["terminal_variant"] = variant
        candidate["hypothesis_id"] = hypothesis_id
        if physical_peak_key is not None:
            candidate["terminal_peak"] = self._peak_ids(
                sample_key, (physical_peak_key,)
            )[0]

    # Method evidence ----------------------------------------------------

    def add_assessment(
        self,
        payload: Mapping[str, Any],
        *,
        candidate_physical_peak_keys: Sequence[str],
    ) -> None:
        sample_key = payload["sample_key"]
        self._sample(sample_key)
        self._assessments.append(
            (
                self._record_id(),
                sample_key,
                self._subject_id(payload["method_subject_id"]),
                payload.get("provider_id"),
                payload.get("method_id"),
                payload.get("method_version"),
                payload.get("detector_invocation_id"),
                payload.get("technical_state"),
                payload.get("method_result"),
                json.dumps(list(payload.get("reason_codes", ())), ensure_ascii=False),
                json.dumps(self._peak_ids(sample_key, candidate_physical_peak_keys)),
                None if self.review_only else payload.get("acquisition_source_id"),
                None
                if self.review_only or payload.get("evaluation_receipt") is None
                else encode_object(payload["evaluation_receipt"]),
            )
        )

    def add_exclusion(
        self, candidate_id: str, sample_key: str, reason_code: str
    ) -> None:
        key = candidate_id, sample_key
        if self._exclusions.setdefault(key, reason_code) != reason_code:
            raise ValueError(f"conflicting candidate exclusion: {key}")

    # Groups -------------------------------------------------------------

    def _sample(self, sample_key: str) -> None:
        if sample_key not in self._samples:
            raise ExplanationError(f"undeclared sample: {sample_key}")

    def declare_samples(self, samples: Sequence[Mapping[str, str]]) -> None:
        """Samples in run order; every other table refers to these ordinals."""
        if self._samples:
            raise ValueError("samples are declared once")
        self._sample_rows = [
            (index, sample["sample_key"], sample["sample_stem"])
            for index, sample in enumerate(samples)
        ]
        self._samples = {key: index for index, key, _ in self._sample_rows}

    def _member(
        self, member: Mapping[str, Any], *, initial: int | None, final: str | None
    ) -> None:
        sample_key = member["sample_key"]
        key = sample_key, member["subject_id"]
        peaks = self._peak_ids(sample_key, member.get("physical_peak_keys", ()))
        row = self._members.setdefault(
            key,
            [
                len(self._members) + 1,
                member.get("subject_kind"),
                None,
                None,
                peaks,
                None,
                None,
            ],
        )
        if row[1] != member.get("subject_kind") or row[4] != peaks:
            raise ValueError(f"one group member differs between groups: {key}")
        slot, value = (2, initial) if final is None else (3, final)
        if row[slot] is not None and row[slot] != value:
            raise ValueError(f"group member belongs to two groups: {key}")
        row[slot] = value

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
        self._groups[group_id] = [
            self._record_id(),
            feature_id,
            disposition,
            None,
            center_mz,
            ppm,
        ]
        for member in members:
            self._member(member, initial=None, final=group_id)

    def set_row_destination(self, group_id: str, destination: str) -> None:
        self._groups[group_id][3] = destination

    def add_initial_group(
        self,
        *,
        disposition: str,
        members: Sequence[Mapping[str, Any]],
        inference_basis: Mapping[str, Any] | None = None,
        inference_decisions: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        row_id = self._record_id()
        for member in members:
            self._member(member, initial=row_id, final=None)
        owners = {member["subject_id"]: member["sample_key"] for member in members}
        decisions = []
        for decision in inference_decisions or ():
            risk = decision.get("accepted_risk")
            owner = decision.get("owner_id")
            if risk is not None and isinstance(owner, str) and owner in owners:
                # An accepted risk belongs to its member; cells read it there.
                # Its flags are a column so selection reads them undecoded.
                row = self._members[owners[owner], owner]
                row[5] = _compressed({**risk, "risk_flags": None})
                row[6] = json.dumps(list(risk["risk_flags"]), ensure_ascii=False)
                decision = {**decision, "accepted_risk": None}
            decisions.append(decision)
        self._initial_groups.append(
            [
                row_id,
                disposition,
                None if inference_basis is None else _compressed(inference_basis),
                None if inference_decisions is None else _compressed(decisions),
            ]
        )

    def add_cells(self, group_id: str, cells: Sequence[Mapping[str, Any]]) -> None:
        """A result row's cells; one cell per sample, scalars as columns.

        A cell's ``membership_risks`` are the risks no group member owns
        (Backfill admissions); a member's accepted risk is stored with the member.
        """
        for cell in cells:
            sample_key = cell["sample_key"]
            self._sample(sample_key)
            key = group_id, sample_key
            if key in self._cells:
                raise ValueError("duplicate review cell for group and sample")
            unknown = set(cell) - _CELL_FIELDS
            if unknown:
                raise ValueError(f"undeclared cell fields: {sorted(unknown)}")
            risks = cell.get("membership_risks", ())
            signal_quality = [
                {
                    **quality,
                    "observation_id": self._peak_ids(
                        sample_key, (quality["observation_id"],)
                    )[0],
                }
                for quality in cell.get("signal_quality", [])
            ]
            self._cells[key] = [
                cell["variant"],
                cell.get("value"),
                cell.get("apex_rt_min"),
                cell.get("source"),
                cell.get("support_origin", "unavailable"),
                json.dumps(self._peak_ids(sample_key, cell.get("observation_ids", ()))),
                None,
                _ordered_json(list(cell.get("evidence_states", ()))),
                _ordered_json(list(cell.get("reason_codes", ()))),
                _ordered_json(signal_quality),
                _compressed(list(risks)) if risks else None,
                None
                if cell.get("observation") is None
                else _compressed(cell["observation"]),
            ]

    def set_cell_position(self, group_id: str, sample_key: str, rt_min: float) -> None:
        """The recorded group position used to inspect an empty cell."""
        self._cells[group_id, sample_key][6] = rt_min

    def add_initial_edge(self, payload: Mapping[str, Any]) -> None:
        key = payload["left_subject_id"], payload["right_subject_id"]
        row = (
            payload.get("disposition"),
            encode_object(payload.get("initial_evidence")),
            encode_object(payload.get("initial_context")),
            encode_object(payload.get("alignment_config")),
        )
        if self._edges.setdefault(key, row) != row:
            raise ValueError(f"conflicting initial edge: {key}")

    # Curves -------------------------------------------------------------

    def add_curve_payload(
        self,
        sample_id: str,
        kind: str,
        record_id: str,
        payload: Mapping[str, Any],
        codec: str = "zlib-json-utf8",
    ) -> int:
        """Store exact curve data once and return its database-local id.

        The provenance method subject is stored as a reference; the payload keeps
        a null in its place and readers restore it.
        """
        validate_curve_payload(
            payload, sample_id=sample_id, kind=kind, record_id=record_id
        )
        if codec != "zlib-json-utf8":
            raise ExplanationError(f"unsupported curve codec: {codec}")
        provenance = payload["provenance"]
        subject = provenance.get("method_subject_id")
        self._subject_id(subject)
        stored = {**payload, "provenance": {**provenance, "method_subject_id": None}}
        compressed = zlib.compress(
            json.dumps(
                stored,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            level=1,
        )
        key = (sample_id, kind, record_id, compressed)
        row = self._curves.get(key)
        if row is None:
            row = self._curves[key] = [
                self._record_id(),
                payload.get("candidate_id"),
                payload.get("physical_peak_key"),
                codec,
                subject,
            ]
        elif row[4] != subject:
            raise ValueError(f"one curve names two method subjects: {record_id}")
        return int(row[0])

    # Publication --------------------------------------------------------

    def finalize(self, summary: Mapping[str, Any]) -> Path:
        db = self._connection()
        if not self._samples:
            self.declare_samples(summary["samples"])
        db.executemany("INSERT INTO samples VALUES (?,?,?)", self._sample_rows)
        db.executemany(
            "INSERT INTO peaks VALUES (?,?,?,?,?,?)",
            (
                (row[0], self._samples[sample], key, *row[1:])
                for (sample, key), row in self._peaks.items()
            ),
        )
        db.executemany(
            "INSERT INTO method_subjects VALUES (?,?,?,?,?,?,?)",
            self._subjects.values(),
        )
        db.executemany(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    row["id"],
                    cid,
                    self._samples[sid],
                    row["precursor_mz"],
                    row["ms1_apex_mz"],
                    row["neutral_loss_tag"],
                    row["peak_status"],
                    self._subject_id(row["method_subject"]),
                    row["terminal_variant"],
                    row["hypothesis_id"],
                    row["terminal_peak"],
                )
                for (cid, sid), row in self._candidates.items()
            ),
        )
        db.executemany("INSERT INTO groups VALUES (?,?,?,?,?,?)", self._groups.values())
        db.executemany(
            "INSERT INTO initial_groups VALUES (?,?,?,?)", self._initial_groups
        )
        db.executemany(
            "INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?)",
            (
                (
                    row[0],
                    subject_id,
                    self._samples[sample],
                    row[1],
                    row[2],
                    None if row[3] is None else self._groups[row[3]][0],
                    json.dumps(row[4]),
                    row[5],
                    row[6],
                )
                for (sample, subject_id), row in self._members.items()
            ),
        )
        db.executemany(
            "INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (self._groups[group_id][0], self._samples[sample], *row)
                for (group_id, sample), row in self._cells.items()
            ),
        )
        db.executemany(
            "INSERT INTO method_assessments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ((row[0], self._samples[row[1]], *row[2:]) for row in self._assessments),
        )
        db.executemany(
            "INSERT INTO exclusions VALUES (?,?)",
            (
                (self._candidates[key]["id"], reason)
                for key, reason in self._exclusions.items()
            ),
        )
        if self._edges:
            create_optional_table(db, "initial_edges")
            db.executemany(
                "INSERT INTO initial_edges VALUES (?,?,?,?,?,?,?)",
                (
                    (index, *key, *row)
                    for index, (key, row) in enumerate(self._edges.items(), start=1)
                ),
            )
        if self._curves:
            create_optional_table(db, "curves")
            db.executemany(
                "INSERT INTO curves VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    (
                        row[0],
                        sample_id,
                        kind,
                        record_id,
                        row[1],
                        row[2],
                        row[3],
                        compressed,
                        self._subject_id(row[4]),
                    )
                    for (sample_id, kind, record_id, compressed), row in (
                        self._curves.items()
                    )
                ),
            )
        db.execute(
            "INSERT INTO run VALUES (?)",
            (
                encode_object(
                    {
                        **{
                            key: value
                            for key, value in summary.items()
                            if key != "samples"
                        },
                        "schema_version": SCHEMA_VERSION,
                        "plot_evidence_capability": (
                            "retained_curves"
                            if not self.review_only or self._curves
                            else "metadata_only"
                        ),
                        "diagnostic_only": True,
                        "completion": "post_matrix_publication",
                    }
                ),
            ),
        )
        db.execute("ANALYZE")
        db.commit()
        db.close()
        self.connection = None
        if self.path.exists():
            raise FileExistsError(f"explanation artifact already exists: {self.path}")
        os.replace(self.temporary, self.path)
        return self.path

    def abort(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.temporary.unlink(missing_ok=True)
