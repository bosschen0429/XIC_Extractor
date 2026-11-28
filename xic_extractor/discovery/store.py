"""SQLite format of one Discovery batch: its samples, CID-NL peaks and events,
and Delta Mass candidates.

One file per batch. Each Discovery worker writes its sample to a private part
file of the same schema; the batch owner merges the parts into the published
file and removes them. The schema text is the format: a file whose
``sqlite_schema`` differs from ``SCHEMA`` is not admitted and Discovery is
rerun. A peak's attached-event count and matched tags, and an event's observed
loss and loss error, follow from stored facts and are derived when read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from xic_extractor.configuration.mass_recalibration import (
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
    serialize_mass_recalibration_result,
)
from xic_extractor.discovery.models import (
    PEAK_STATUSES,
    DiscoveryEvent,
    DiscoveryPeak,
    EventAttachment,
    located_event_order,
)
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import AcquisitionSourceIdentity

DISCOVERY_ARTIFACT_NAME = "discovery.sqlite"
_PART_DIRECTORY = ".discovery-parts"
_STATUSES = ", ".join(f"'{status}'" for status in sorted(PEAK_STATUSES))

SCHEMA = f"""
CREATE TABLE tags (
    tag TEXT PRIMARY KEY,
    neutral_loss_da REAL NOT NULL,
    position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE samples (
    id INTEGER PRIMARY KEY,
    sample_stem TEXT NOT NULL UNIQUE,
    raw_file TEXT NOT NULL,
    acquisition_source TEXT
        CHECK (acquisition_source IS NULL OR json_type(acquisition_source) = 'object'),
    mass_recalibration_result TEXT NOT NULL
) STRICT;
CREATE TABLE peaks (
    id INTEGER PRIMARY KEY,
    peak_id TEXT NOT NULL UNIQUE,
    sample INTEGER NOT NULL REFERENCES samples,
    peak_mz REAL NOT NULL CHECK (peak_mz > 0),
    ms1_apex_mz REAL,
    ms1_apex_rt REAL NOT NULL,
    neutral_loss_tag TEXT NOT NULL REFERENCES tags,
    representative_scan INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_STATUSES})),
    evidence_score INTEGER NOT NULL
) STRICT;
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    sample INTEGER NOT NULL REFERENCES samples,
    peak INTEGER REFERENCES peaks,
    unattached_reason TEXT,
    scan_number INTEGER NOT NULL,
    rt REAL NOT NULL,
    neutral_loss_tag TEXT NOT NULL REFERENCES tags,
    product_mz REAL NOT NULL,
    product_intensity REAL NOT NULL,
    precursor_mz REAL NOT NULL,
    precursor_mz_basis TEXT NOT NULL CHECK (precursor_mz_basis IN
        ('scan_precursor', 'scan_monoisotopic', 'product_plus_neutral_loss')),
    scan_precursor_mz REAL,
    precursor_monoisotopic_mz REAL,
    precursor_charge INTEGER,
    isolation_width_da REAL,
    isolation_offset_da REAL,
    parent_ms1_scan_number INTEGER,
    parent_ms1_rt REAL,
    parent_ms1_centroid_mzs TEXT CHECK (
        (parent_ms1_scan_number IS NULL) = (parent_ms1_centroid_mzs IS NULL)
        AND (parent_ms1_centroid_mzs IS NULL
             OR json_type(parent_ms1_centroid_mzs) = 'array')),
    CHECK (peak IS NOT NULL OR unattached_reason IS NOT NULL)
) STRICT;
CREATE INDEX events_peak ON events (peak);
CREATE TABLE delta_mass_run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    method_label TEXT NOT NULL,
    method TEXT NOT NULL CHECK (json_type(method) = 'object')
) STRICT;
CREATE TABLE delta_mass_candidates (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    sample INTEGER NOT NULL REFERENCES samples,
    pattern_id TEXT NOT NULL,
    base_mz REAL NOT NULL,
    base_rt_apex_min REAL NOT NULL,
    base_height REAL,
    cid_nl_peak_id TEXT
) STRICT;
CREATE TABLE delta_mass_positions (
    candidate INTEGER NOT NULL REFERENCES delta_mass_candidates,
    ordinal INTEGER NOT NULL,
    position_label TEXT NOT NULL,
    delta_da REAL NOT NULL,
    expected_mz REAL NOT NULL,
    position_status TEXT NOT NULL,
    feature_id TEXT,
    observed_mz REAL,
    apex_rt_min REAL,
    height REAL,
    height_ratio_to_base REAL,
    shape_r REAL,
    PRIMARY KEY (candidate, ordinal),
    UNIQUE (candidate, position_label)
) STRICT, WITHOUT ROWID;
CREATE TABLE delta_mass_features (
    sample INTEGER NOT NULL REFERENCES samples,
    feature_id TEXT NOT NULL,
    mz REAL NOT NULL,
    rt_start_min REAL,
    rt_end_min REAL,
    PRIMARY KEY (sample, feature_id)
) STRICT, WITHOUT ROWID;
CREATE VIEW discovery_peaks AS
    SELECT p.id, p.peak_id, s.sample_stem, p.status, p.evidence_score,
        (SELECT COUNT(*) FROM events e
         WHERE e.peak = p.id AND e.unattached_reason IS NULL)
            AS attached_event_count,
        p.peak_mz, p.ms1_apex_mz, p.ms1_apex_rt, p.representative_scan,
        p.neutral_loss_tag,
        (SELECT json_group_array(tag) FROM (
            SELECT DISTINCT t.tag FROM events e
            JOIN tags t ON t.tag = e.neutral_loss_tag
            WHERE e.peak = p.id ORDER BY t.position, t.tag)) AS matched_tag_names
    FROM peaks p JOIN samples s ON s.id = p.sample;
CREATE VIEW discovery_events AS
    SELECT e.id, s.sample_stem, s.raw_file, e.peak, p.peak_id,
        e.unattached_reason, e.scan_number, e.rt, e.neutral_loss_tag,
        t.neutral_loss_da AS configured_neutral_loss_da,
        CASE WHEN e.precursor_mz_basis = 'product_plus_neutral_loss'
            THEN t.neutral_loss_da ELSE e.precursor_mz - e.product_mz END
            AS observed_neutral_loss_da,
        e.product_mz, e.product_intensity, e.precursor_mz, e.precursor_mz_basis,
        e.scan_precursor_mz, e.precursor_monoisotopic_mz, e.precursor_charge,
        e.isolation_width_da, e.isolation_offset_da, e.parent_ms1_scan_number,
        e.parent_ms1_rt, e.parent_ms1_centroid_mzs
    FROM events e
    JOIN samples s ON s.id = e.sample
    JOIN tags t ON t.tag = e.neutral_loss_tag
    LEFT JOIN peaks p ON p.id = e.peak;
CREATE VIEW delta_mass_candidate_table AS
    SELECT s.sample_stem AS sample, c.candidate_id, c.pattern_id,
        c.base_mz AS mz, c.base_rt_apex_min AS rt_min, c.base_height AS height,
        (SELECT COUNT(*) FROM delta_mass_positions p
         WHERE p.candidate = c.id AND p.position_status = 'pass')
            AS positions_found,
        (SELECT COALESCE(group_concat(position_label, ';'), '') FROM (
            SELECT position_label FROM delta_mass_positions p
            WHERE p.candidate = c.id AND p.position_status = 'pass'
            ORDER BY p.ordinal)) AS positions,
        COALESCE(c.cid_nl_peak_id, '') AS cid_nl_peak_id,
        s.id AS sample_order
    FROM delta_mass_candidates c JOIN samples s ON s.id = c.sample;
"""


@dataclass(frozen=True, slots=True)
class DiscoverySample:
    """One sample of the batch and the RAW revision Discovery read it from.

    Delta Mass records no acquisition source; CID-NL always does.
    """

    sample_stem: str
    raw_file: Path
    acquisition_source: AcquisitionSourceIdentity | None
    mass_recalibration_result: MassRecalibrationResult


@dataclass(frozen=True)
class DiscoveryBatchInput:
    """The published Discovery batch an Alignment reads, in sample order."""

    path: Path
    sample_order: tuple[str, ...]
    samples: dict[str, DiscoverySample]

    @property
    def raw_files(self) -> dict[str, Path]:
        return {stem: self.samples[stem].raw_file for stem in self.sample_order}


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


def _schema_of(db: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_stat%' ORDER BY type,name"
        )
    )


@cache
def _expected_schema() -> tuple[tuple[Any, ...], ...]:
    with closing(sqlite3.connect(":memory:")) as db:
        create_schema(db)
        return _schema_of(db)


@contextmanager
def open_discovery(path: str | Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection to a published, never rewritten Discovery file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path}: Discovery output does not exist")
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as db:
        try:
            current = _schema_of(db) == _expected_schema()
        except sqlite3.DatabaseError:
            current = False
        if not current:
            raise ValueError(f"{path}: not a current Discovery output; rerun Discovery")
        yield db


def open_writer(path: Path) -> sqlite3.Connection:
    """A fresh file in the batch schema, written in one pass."""
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode = OFF")
    db.execute("PRAGMA synchronous = OFF")
    db.execute("PRAGMA foreign_keys = ON")
    create_schema(db)
    return db


def publish(db: sqlite3.Connection, temporary: Path, target: Path) -> Path:
    """Commit, flush and atomically put a written file in place."""
    db.execute("ANALYZE")
    db.commit()
    db.close()
    with temporary.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def _tag_rows(neutral_loss_by_tag: Mapping[str, float]) -> list[tuple[Any, ...]]:
    return [
        (tag, float(loss), position)
        for position, (tag, loss) in enumerate(neutral_loss_by_tag.items())
    ]


def sample_row(index: int, sample: DiscoverySample) -> tuple[Any, ...]:
    source = sample.acquisition_source
    return (
        index,
        sample.sample_stem,
        str(sample.raw_file),
        None if source is None else json.dumps(asdict(source), sort_keys=True),
        serialize_mass_recalibration_result(sample.mass_recalibration_result),
    )


def part_path(output_dir: Path, sample_stem: str) -> Path:
    return output_dir / _PART_DIRECTORY / f"{sample_stem}.sqlite"


def write_sample_part(
    path: Path,
    *,
    sample_index: int,
    sample: DiscoverySample,
    neutral_loss_by_tag: Mapping[str, float],
    peaks: Sequence[DiscoveryPeak],
    attachments: Sequence[EventAttachment],
) -> Path:
    """One worker's sample, in the batch schema, for the batch owner to merge."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    db = open_writer(temporary)
    try:
        db.executemany(
            "INSERT INTO tags VALUES (?,?,?)", _tag_rows(neutral_loss_by_tag)
        )
        db.execute(
            "INSERT INTO samples VALUES (?,?,?,?,?)", sample_row(sample_index, sample)
        )
        peak_ids = {}
        for row_id, peak in enumerate(peaks, start=1):
            peak_ids[peak.peak_id] = row_id
            db.execute(
                "INSERT INTO peaks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id,
                    peak.peak_id,
                    sample_index,
                    peak.peak_mz,
                    peak.ms1_apex_mz,
                    peak.ms1_apex_rt,
                    peak.neutral_loss_tag,
                    peak.best_ms2_scan_id,
                    peak.status,
                    peak.evidence_score,
                ),
            )
        db.executemany(
            "INSERT INTO events VALUES (NULL," + ",".join("?" * 18) + ")",
            (
                _event_row(sample_index, attachment, peak_ids)
                for attachment in attachments
            ),
        )
        return publish(db, temporary, path)
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise


def _event_row(
    sample_index: int, attachment: EventAttachment, peak_ids: Mapping[str, int]
) -> tuple[Any, ...]:
    event = attachment.event
    metadata = event.precursor_metadata or Ms2PrecursorMetadata()
    parent = event.ms1_precursor_observation
    return (
        sample_index,
        peak_ids[attachment.peak_id] if attachment.peak_id else None,
        attachment.unattached_reason or None,
        event.scan_number,
        event.rt,
        event.neutral_loss_tag,
        event.product_mz,
        event.product_intensity,
        event.precursor_mz,
        event.precursor_mz_basis,
        event.scan_precursor_mz,
        metadata.monoisotopic_mz,
        metadata.charge,
        metadata.isolation_width_da,
        metadata.isolation_offset_da,
        None if parent is None else parent.scan_number,
        None if parent is None else parent.rt_min,
        None if parent is None else json.dumps(list(parent.centroid_mzs)),
    )


def merge_sample_parts(target: Path, parts: Sequence[Path]) -> Path:
    """Publish the batch from its samples' part files, in sample order."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    db = open_writer(temporary)
    try:
        db.commit()
        tags = None
        for part in parts:
            db.execute("ATTACH ? AS part", (str(part),))
            part_tags = db.execute("SELECT * FROM part.tags ORDER BY tag").fetchall()
            if tags is None:
                tags = part_tags
                db.execute("INSERT INTO tags SELECT * FROM part.tags")
            elif part_tags != tags:
                raise ValueError(f"{part}: Discovery samples disagree on tags")
            (peak_offset,) = db.execute(
                "SELECT COALESCE(MAX(id),0) FROM peaks"
            ).fetchone()
            db.execute("INSERT INTO samples SELECT * FROM part.samples")
            db.execute(
                "INSERT INTO peaks SELECT id+?,peak_id,sample,peak_mz,"
                "ms1_apex_mz,ms1_apex_rt,neutral_loss_tag,representative_scan,"
                "status,evidence_score FROM part.peaks",
                (peak_offset,),
            )
            db.execute(
                "INSERT INTO events SELECT NULL,sample,peak+?,unattached_reason,"
                "scan_number,rt,neutral_loss_tag,product_mz,product_intensity,"
                "precursor_mz,precursor_mz_basis,scan_precursor_mz,"
                "precursor_monoisotopic_mz,precursor_charge,isolation_width_da,"
                "isolation_offset_da,parent_ms1_scan_number,parent_ms1_rt,"
                "parent_ms1_centroid_mzs FROM part.events ORDER BY id",
                (peak_offset,),
            )
            db.commit()
            db.execute("DETACH part")
        publish(db, temporary, target)
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    for part in parts:
        part.unlink(missing_ok=True)
    with_parts = target.parent / _PART_DIRECTORY
    if with_parts.is_dir() and not any(with_parts.iterdir()):
        with_parts.rmdir()
    return target


def _sample_from_row(row: Sequence[Any]) -> DiscoverySample:
    stem, raw_file, source, recalibration = row
    if source is None:
        identity = None
    else:
        fields = json.loads(source)
        revision = fields["local_file_revision"]
        identity = AcquisitionSourceIdentity(
            **{
                **fields,
                "local_file_revision": None if revision is None else tuple(revision),
            }
        )
    return DiscoverySample(
        stem,
        Path(raw_file),
        identity,
        deserialize_mass_recalibration_result(recalibration),
    )


def read_discovery_batch(path: Path) -> DiscoveryBatchInput:
    """The published batch's samples, in the order Discovery ran them."""
    with open_discovery(path) as db:
        samples = {
            row[0]: _sample_from_row(row)
            for row in db.execute(
                "SELECT sample_stem,raw_file,acquisition_source,"
                "mass_recalibration_result FROM samples ORDER BY id"
            )
        }
    return DiscoveryBatchInput(path=path, sample_order=tuple(samples), samples=samples)


def read_peaks(
    db: sqlite3.Connection, where: str = "", parameters: Sequence[Any] = ()
) -> list[tuple[int, DiscoveryPeak]]:
    """Peaks of ``discovery_peaks`` by row id, without their located events."""
    return [
        (
            row_id,
            DiscoveryPeak(
                peak_id=peak_id,
                sample_stem=stem,
                peak_mz=mz,
                neutral_loss_tag=tag,
                best_ms2_scan_id=scan,
                attached_event_count=attached,
                ms1_apex_rt=rt,
                status=status,
                ms1_apex_mz=apex_mz,
                evidence_score=score,
                matched_tag_names=tuple(json.loads(tags)),
            ),
        )
        for (
            row_id,
            peak_id,
            stem,
            mz,
            tag,
            scan,
            rt,
            status,
            apex_mz,
            score,
            attached,
            tags,
        ) in db.execute(
            "SELECT id,peak_id,sample_stem,peak_mz,neutral_loss_tag,"
            "representative_scan,ms1_apex_rt,status,ms1_apex_mz,evidence_score,"
            f"attached_event_count,matched_tag_names FROM discovery_peaks {where} "
            "ORDER BY id",
            parameters,
        )
    ]


def event_from_row(row: Sequence[Any]) -> tuple[int | None, str, DiscoveryEvent]:
    """An event's located peak row, reason and facts; the loss error follows
    from the observed and configured losses."""
    (
        peak,
        reason,
        scan,
        rt,
        tag,
        configured,
        observed,
        product_mz,
        intensity,
        precursor_mz,
        basis,
        selected_mz,
        mono,
        charge,
        width,
        offset,
        parent_scan,
        parent_rt,
        centroids,
        stem,
        raw_file,
    ) = row
    metadata = Ms2PrecursorMetadata(
        monoisotopic_mz=mono,
        charge=charge,
        isolation_width_da=width,
        isolation_offset_da=offset,
    )
    event = DiscoveryEvent(
        raw_file=Path(raw_file),
        sample_stem=stem,
        scan_number=scan,
        rt=rt,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        product_intensity=intensity,
        neutral_loss_tag=tag,
        configured_neutral_loss_da=configured,
        observed_neutral_loss_da=observed,
        observed_loss_error_ppm=None
        if basis == "product_plus_neutral_loss"
        else absolute_ppm_error(observed, configured),
        scan_precursor_mz=selected_mz,
        precursor_mz_basis=basis,
        precursor_metadata=None if metadata == Ms2PrecursorMetadata() else metadata,
        ms1_precursor_observation=None
        if parent_scan is None
        else Ms1PrecursorObservation(
            scan_number=parent_scan,
            rt_min=parent_rt,
            centroid_mzs=tuple(json.loads(centroids)),
        ),
    )
    return peak, reason or "", event


def read_events(
    db: sqlite3.Connection, where: str = "", parameters: Sequence[Any] = ()
) -> Iterator[tuple[int | None, str, DiscoveryEvent]]:
    """Events of ``discovery_events`` in stored order."""
    for row in db.execute(
        "SELECT peak,unattached_reason,scan_number,rt,neutral_loss_tag,"
        "configured_neutral_loss_da,observed_neutral_loss_da,product_mz,"
        "product_intensity,precursor_mz,precursor_mz_basis,scan_precursor_mz,"
        "precursor_monoisotopic_mz,precursor_charge,isolation_width_da,"
        "isolation_offset_da,parent_ms1_scan_number,parent_ms1_rt,"
        f"parent_ms1_centroid_mzs,sample_stem,raw_file FROM discovery_events {where} "
        "ORDER BY id",
        parameters,
    ):
        yield event_from_row(row)


def read_sample_peaks(path: Path, sample_stem: str) -> tuple[DiscoveryPeak, ...]:
    """One sample's peaks with their located events, for diagnostics."""
    with open_discovery(path) as db:
        peaks = read_peaks(db, "WHERE sample_stem=?", (sample_stem,))
        located: dict[int, list[DiscoveryEvent]] = {}
        for peak, _reason, event in read_events(
            db, "WHERE sample_stem=? AND peak IS NOT NULL", (sample_stem,)
        ):
            assert peak is not None
            located.setdefault(peak, []).append(event)
    return tuple(_with_events(peak, located.get(row_id, ())) for row_id, peak in peaks)


def _with_events(
    peak: DiscoveryPeak, events: Iterable[DiscoveryEvent]
) -> DiscoveryPeak:
    return replace(peak, located_events=located_event_order(events))


def read_peak_counts(path: Path) -> tuple[dict[str, int], int]:
    """All and admitted peaks of the batch, and its sample count."""
    with open_discovery(path) as db:
        total, admitted = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(status='admitted'),0) FROM peaks"
        ).fetchone()
        (samples,) = db.execute("SELECT COUNT(*) FROM samples").fetchone()
    return {"total": total, "admitted": admitted}, samples
