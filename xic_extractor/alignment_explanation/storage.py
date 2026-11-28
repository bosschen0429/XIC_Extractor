"""SQLite format of completed Alignment review evidence and read-only connections.

The file serves Matrix review, Matrix curation and the records that did not
enter the Matrix. Each concept has one table; relations are tables with keys,
and the writer materializes which candidates explain a group. The schema text
is the format: a file whose ``sqlite_schema`` differs from ``SCHEMA`` is not
admitted and its Alignment is rerun.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zlib
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from functools import cache
from pathlib import Path
from typing import Any

from .errors import ExplanationError, ExplanationUnavailableError
from .review import SUPPORT_ORIGINS

EXPLANATION_ARTIFACT_NAME = "alignment_explanation.sqlite"
_ORIGINS = ", ".join(f"'{origin}'" for origin in sorted(SUPPORT_ORIGINS))

SCHEMA = f"""
CREATE TABLE run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    processing_attempt_id TEXT NOT NULL,
    matrix_file TEXT NOT NULL,
    matrix_sha256 TEXT NOT NULL,
    neutral_loss_by_tag TEXT NOT NULL CHECK (json_type(neutral_loss_by_tag) = 'object'),
    sample_rt_calibration TEXT
        CHECK (sample_rt_calibration IS NULL OR json_valid(sample_rt_calibration))
) STRICT;
CREATE TABLE samples (
    id INTEGER PRIMARY KEY,
    sample_key TEXT NOT NULL UNIQUE,
    sample_stem TEXT NOT NULL,
    injection_order INTEGER UNIQUE
) STRICT;
CREATE TABLE peaks (
    id INTEGER PRIMARY KEY,
    sample INTEGER NOT NULL REFERENCES samples,
    physical_peak_key TEXT NOT NULL,
    mz REAL,
    rt_apex_min REAL,
    ppm REAL,
    UNIQUE (sample, physical_peak_key)
) STRICT;
CREATE TRIGGER peaks_fixed BEFORE UPDATE ON peaks
WHEN (OLD.mz IS NOT NULL AND NEW.mz IS NOT OLD.mz)
    OR (OLD.rt_apex_min IS NOT NULL AND NEW.rt_apex_min IS NOT OLD.rt_apex_min)
    OR (OLD.ppm IS NOT NULL AND NEW.ppm IS NOT OLD.ppm)
BEGIN SELECT RAISE(ABORT, 'distinct peaks share one locator'); END;
CREATE TABLE groups (
    id INTEGER PRIMARY KEY,
    feature_id TEXT UNIQUE,
    disposition TEXT NOT NULL,
    destination TEXT CHECK (destination IN ('matrix', 'archive')),
    center_mz REAL,
    ppm REAL
) STRICT;
CREATE TABLE members (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups,
    sample INTEGER NOT NULL REFERENCES samples,
    subject_id TEXT NOT NULL,
    UNIQUE (sample, subject_id)
) STRICT;
CREATE TRIGGER members_one_group BEFORE UPDATE OF group_id ON members
WHEN NEW.group_id IS NOT OLD.group_id
BEGIN SELECT RAISE(ABORT, 'group member belongs to two groups'); END;
CREATE TABLE member_peaks (
    member INTEGER NOT NULL REFERENCES members,
    peak INTEGER NOT NULL REFERENCES peaks,
    PRIMARY KEY (member, peak)
) STRICT, WITHOUT ROWID;
CREATE TABLE cells (
    group_id INTEGER NOT NULL REFERENCES groups,
    sample INTEGER NOT NULL REFERENCES samples,
    variant TEXT NOT NULL,
    value REAL,
    apex_rt_min REAL,
    position_rt_min REAL,
    source TEXT,
    support_origin TEXT NOT NULL
        CHECK (support_origin IN ({_ORIGINS})),
    evidence_states TEXT NOT NULL CHECK (json_type(evidence_states) = 'array'),
    reason_codes TEXT NOT NULL CHECK (json_type(reason_codes) = 'array'),
    risk_flags TEXT NOT NULL DEFAULT '[]' CHECK (json_type(risk_flags) = 'array'),
    competition TEXT NOT NULL DEFAULT '[]' CHECK (json_type(competition) = 'array'),
    PRIMARY KEY (group_id, sample)
) STRICT, WITHOUT ROWID;
CREATE TABLE cell_peaks (
    group_id INTEGER NOT NULL,
    sample INTEGER NOT NULL,
    peak INTEGER NOT NULL REFERENCES peaks,
    low_signal_to_noise INTEGER CHECK (low_signal_to_noise IN (0, 1)),
    PRIMARY KEY (group_id, sample, peak),
    FOREIGN KEY (group_id, sample) REFERENCES cells
) STRICT, WITHOUT ROWID;
CREATE TABLE cell_curves (
    group_id INTEGER NOT NULL,
    sample INTEGER NOT NULL,
    curve BLOB NOT NULL,
    PRIMARY KEY (group_id, sample),
    FOREIGN KEY (group_id, sample) REFERENCES cells
) STRICT;
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    sample INTEGER NOT NULL REFERENCES samples,
    precursor_mz REAL,
    ms1_apex_mz REAL,
    neutral_loss_tag TEXT NOT NULL,
    terminal_peak INTEGER REFERENCES peaks,
    exclusion_reason TEXT,
    UNIQUE (candidate_id, sample)
) STRICT;
CREATE TABLE group_candidates (
    group_id INTEGER NOT NULL REFERENCES groups,
    candidate INTEGER NOT NULL REFERENCES candidates,
    PRIMARY KEY (group_id, candidate)
) STRICT, WITHOUT ROWID;
CREATE INDEX candidates_mz ON candidates (precursor_mz);
CREATE INDEX candidates_terminal ON candidates (terminal_peak);
CREATE INDEX group_candidates_candidate ON group_candidates (candidate);
CREATE INDEX members_group ON members (group_id);
CREATE INDEX member_peaks_peak ON member_peaks (peak);
CREATE INDEX cells_sample_apex ON cells (sample, apex_rt_min)
    WHERE apex_rt_min IS NOT NULL;
CREATE VIEW review_item_candidates AS
    SELECT group_id AS item, candidate FROM group_candidates
    UNION ALL
    SELECT -c.id, c.id FROM candidates c
    WHERE NOT EXISTS (SELECT 1 FROM group_candidates g WHERE g.candidate = c.id);
CREATE VIEW review_samples AS
    SELECT ic.item, c.sample FROM review_item_candidates ic
    JOIN candidates c ON c.id = ic.candidate
    UNION SELECT group_id, sample FROM cells;
CREATE VIEW review_witnesses AS
    SELECT item, sample, sample_key, candidate_id, mz,
        CASE WHEN observed THEN rt END AS rt,
        CASE WHEN observed THEN peak END AS peak,
        sample_stem
    FROM (
        SELECT ic.item, c.sample, s.sample_key, c.candidate_id,
            c.precursor_mz AS mz, p.rt_apex_min AS rt, p.physical_peak_key AS peak,
            s.sample_stem,
            NOT EXISTS (SELECT 1 FROM members m
                        WHERE m.group_id = ic.item AND m.sample = c.sample)
            OR EXISTS (SELECT 1 FROM members m
                       JOIN member_peaks mp ON mp.member = m.id
                       WHERE m.group_id = ic.item AND m.sample = c.sample
                         AND mp.peak = c.terminal_peak) AS observed
        FROM review_item_candidates ic
        JOIN candidates c ON c.id = ic.candidate
        JOIN samples s ON s.id = c.sample
        LEFT JOIN peaks p ON p.id = c.terminal_peak);
"""


def encode_json(value: Any) -> str:
    """JSON keeping the producer's field order, as readers display it."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


_REVIEW_CURVE_FLOAT64 = b"XICF64\x01"


def encode_review_curve(rt: Iterable[float], raw: Iterable[float]) -> bytes:
    """Store both series bit-for-bit, without decimal conversion or rounding."""
    times, intensities = tuple(rt), tuple(raw)
    packed = struct.pack(f"<{2 * len(times)}d", *times, *intensities)
    shuffled = b"".join(packed[byte::8] for byte in range(8))
    return _REVIEW_CURVE_FLOAT64 + zlib.compress(shuffled, 6)


def decode_review_curve(
    payload: bytes,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Read the lossless binary curve format."""
    if not payload.startswith(_REVIEW_CURVE_FLOAT64):
        raise ExplanationError("unsupported review curve format; rerun Alignment")
    shuffled = zlib.decompress(payload[len(_REVIEW_CURVE_FLOAT64) :])
    if len(shuffled) % 16:
        raise ExplanationError("malformed review curve: unequal float64 series")
    value_count = len(shuffled) // 8
    packed = bytearray(len(shuffled))
    for byte in range(8):
        packed[byte::8] = shuffled[byte * value_count : (byte + 1) * value_count]
    values = struct.unpack(f"<{value_count}d", packed)
    midpoint = value_count // 2
    return values[:midpoint], values[midpoint:]


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


def _schema_of(db: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_stat%' ORDER BY type,name"
    ).fetchall()


@cache
def _expected_schema() -> tuple[tuple[Any, ...], ...]:
    with closing(sqlite3.connect(":memory:")) as db:
        create_schema(db)
        return tuple(_schema_of(db))


def read_run(connection: sqlite3.Connection) -> dict[str, Any]:
    """Admit only a file built from ``SCHEMA``; return its run and samples."""
    if tuple(_schema_of(connection)) != _expected_schema():
        raise ExplanationError(
            "不支援此解釋資料格式；請重新執行 Alignment 產生新輸出。"
        )
    row = connection.execute(
        "SELECT processing_attempt_id,matrix_file,matrix_sha256,"
        "neutral_loss_by_tag,sample_rt_calibration FROM run"
    ).fetchone()
    if row is None:
        raise ExplanationError("malformed explanation: missing run")
    attempt, matrix_file, matrix_sha256, losses, calibration = row
    return {
        "processing_attempt_id": attempt,
        "matrix_path": matrix_file,
        "matrix_sha256": matrix_sha256,
        "neutral_loss_by_tag": json.loads(losses),
        "sample_rt_calibration": (
            None if calibration is None else json.loads(calibration)
        ),
        "samples": [
            {"sample_key": key, "sample_stem": stem}
            for key, stem in connection.execute(
                "SELECT sample_key,sample_stem FROM samples ORDER BY id"
            )
        ],
    }


def read_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    """Counts of stored facts, computed instead of stored a second time."""
    reasons: dict[str, int] = {}
    for query in (
        "SELECT exclusion_reason, COUNT(*) FROM candidates "
        "WHERE exclusion_reason IS NOT NULL GROUP BY exclusion_reason",
        "SELECT disposition, COUNT(*) FROM groups "
        "WHERE feature_id IS NOT NULL GROUP BY disposition",
    ):
        for reason, number in connection.execute(query):
            reasons[reason] = reasons.get(reason, 0) + number
    return {
        "candidate_count": connection.execute(
            "SELECT COUNT(*) FROM candidates"
        ).fetchone()[0],
        "matrix_row_count": connection.execute(
            "SELECT COUNT(*) FROM groups WHERE destination = 'matrix'"
        ).fetchone()[0],
        "reason_counts": dict(sorted(reasons.items())),
    }


@contextmanager
def open_readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Close on every exit and translate persisted-input failures at the boundary.

    A published file is never written again, so it opens immutable: no locks and
    no change detection. SQLite's connection context manager only ends a
    transaction; it does not close the connection.
    """
    path = Path(path)
    if not path.is_file():
        raise ExplanationUnavailableError(
            f"找不到這份結果的檢閱資料（{path.name}）；請重新執行 Untargeted 分析。"
        )
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db:
            yield db
    except (sqlite3.Error, json.JSONDecodeError, zlib.error, UnicodeError) as exc:
        raise ExplanationError(f"malformed explanation: {exc}") from exc
