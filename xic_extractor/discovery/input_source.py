"""Route immutable Discovery peak rows before constructing peak objects."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from xic_extractor.discovery.batch_contract import validate_discovery_peak_artifact
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
    _machine_field,
    _parse_peak_row,
    parse_discovery_event_row,
    require_current_discovery_columns,
)
from xic_extractor.discovery.event_evidence import representative_order_within_tag
from xic_extractor.discovery.models import (
    DISCOVERY_EVENT_COLUMNS,
    DISCOVERY_PEAK_COLUMNS,
    DiscoveryEvent,
    DiscoveryPeak,
    located_event_order,
)


@dataclass(frozen=True, slots=True)
class CandidateLocation:
    candidate_id: str
    sample_stem: str
    peak_mz: float
    neutral_loss_tag: str
    admitted: bool
    offset: int
    byte_size: int
    row_number: int


class _CsvLines:
    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def __iter__(self) -> _CsvLines:
        return self

    def __next__(self) -> str:
        offset = self.stream.tell()
        line = self.stream.readline()
        if not line:
            raise StopIteration
        return line.decode("utf-8-sig" if offset == 0 else "utf-8")


@dataclass(frozen=True, slots=True)
class _EventRowLocation:
    offset: int
    byte_size: int
    row_number: int


class DiscoveryInputSource(Mapping[str, tuple[DiscoveryPeak, ...]]):
    """An artifact-verified peak index; domain values live only in a requested load.

    Alignment candidates are the admitted peaks. The Mapping view is for
    sample-at-a-time diagnostic consumers. Scientific execution uses
    load_candidates with a closed working set of peak IDs, and
    located_events for the event-table rows that locate one peak.
    """

    def __init__(self, batch: DiscoveryBatchInput):
        self.batch = batch
        self.locations: dict[str, CandidateLocation] = {}
        self._sample_ids: dict[str, tuple[str, ...]] = {}
        self._headers: dict[str, tuple[str, ...]] = {}
        self._stats: dict[str, tuple[int, int]] = {}
        self._feature_ids: dict[str, tuple[str, ...]] = {}
        # Discovery's apex MS1 readings; they only set the reported row m/z.
        self.ms1_apex_mz_by_candidate: dict[str, float] = {}
        # The representative event's precursor, from its event-table row.
        self.precursor_mz_by_candidate: dict[str, float] = {}
        for stem in batch.sample_order:
            path = batch.peak_csvs[stem]
            receipt = batch.peak_receipts[stem]
            before = self._stat(path)
            validate_discovery_peak_artifact(receipt, path)
            ids = []
            with path.open("rb") as stream:
                reader = csv.DictReader(_CsvLines(stream))
                header = tuple(reader.fieldnames or ())
                require_current_discovery_columns(
                    path, header, DISCOVERY_PEAK_COLUMNS, table="peak table"
                )
                self._headers[stem] = header
                row_number = 2
                while True:
                    offset = stream.tell()
                    row = next(reader, None)
                    if row is None:
                        break
                    cid = _machine_field(row, "peak_id")
                    mz = float(row["peak_mz"])
                    if not cid or cid in self.locations:
                        raise ValueError(
                            "Discovery peak identity must be globally unique"
                        )
                    if _machine_field(row, "sample_stem") != stem:
                        raise ValueError("Discovery row crossed its receipt sample")
                    if not math.isfinite(mz) or mz <= 0:
                        raise ValueError(
                            "Discovery precursor must be finite and positive"
                        )
                    self.locations[cid] = CandidateLocation(
                        cid,
                        stem,
                        mz,
                        row["neutral_loss_tag"],
                        row["status"] == "admitted",
                        offset,
                        stream.tell() - offset,
                        row_number,
                    )
                    apex_mz = row.get("ms1_apex_mz", "").strip()
                    if apex_mz:
                        self.ms1_apex_mz_by_candidate[cid] = float(apex_mz)
                    ids.append(cid)
                    row_number += 1
            if len(ids) != receipt.peak_count:
                raise ValueError("Discovery row count crossed its owner receipt")
            if self._stat(path) != before:
                raise ValueError("Discovery CSV changed while building its row index")
            self._stats[stem] = before
            self._sample_ids[stem] = tuple(sorted(ids))
            self._feature_ids[stem] = tuple(
                cid for cid in self._sample_ids[stem] if self.locations[cid].admitted
            )
        self._event_rows: dict[str, list[_EventRowLocation]] = {}
        self._event_headers: dict[str, tuple[str, ...]] = {}
        self._event_stats: dict[str, tuple[int, int]] = {}
        for stem in batch.sample_order:
            event_path = batch.event_csvs.get(stem)
            if event_path is not None:
                self._index_event_rows(stem, event_path)

    def _index_event_rows(self, stem: str, path: Path) -> None:
        before = self._stat(path)
        attached_ids: set[str] = set()
        representative_rows: dict[
            str, list[tuple[bool, tuple[object, ...], dict[str, str]]]
        ] = {}
        with path.open("rb") as stream:
            reader = csv.DictReader(_CsvLines(stream))
            header = tuple(reader.fieldnames or ())
            require_current_discovery_columns(
                path, header, DISCOVERY_EVENT_COLUMNS, table="event table"
            )
            row_number = 2
            while True:
                offset = stream.tell()
                row = next(reader, None)
                if row is None:
                    break
                peak_id = _machine_field(row, "peak_id")
                if peak_id:
                    location = self.locations.get(peak_id)
                    if location is None or location.sample_stem != stem:
                        raise ValueError(
                            f"{path}: row {row_number}: event locates an unknown "
                            f"peak {peak_id!r}"
                        )
                    self._event_rows.setdefault(peak_id, []).append(
                        _EventRowLocation(offset, stream.tell() - offset, row_number)
                    )
                    attached = not row["unattached_reason"]
                    if attached:
                        attached_ids.add(peak_id)
                    if row["neutral_loss_tag"] == location.neutral_loss_tag:
                        representative_rows.setdefault(peak_id, []).append(
                            (attached, _representative_order(row), row)
                        )
                row_number += 1
        if self._stat(path) != before:
            raise ValueError("Discovery event table changed while indexing")
        # Discovery chooses the representative among the attached events when
        # any attaches, else among all located events; its tag names the peak.
        for peak_id, rows in representative_rows.items():
            pool = [item for item in rows if item[0] or peak_id not in attached_ids]
            if pool:
                _, _, chosen = min(pool, key=lambda item: item[1])
                self.precursor_mz_by_candidate[peak_id] = float(chosen["precursor_mz"])
        self._event_headers[stem] = header
        self._event_stats[stem] = before

    def located_events(
        self,
        candidate: DiscoveryPeak,
        *,
        neutral_loss_by_tag: Mapping[str, float],
    ) -> tuple[DiscoveryEvent, ...]:
        """The event-table rows locating one peak, attached or not."""
        stem = self.locations[candidate.peak_id].sample_stem
        rows = self._event_rows.get(candidate.peak_id, [])
        if not rows:
            return ()
        path = self.batch.event_csvs[stem]
        raw_file = self.batch.raw_files[stem]
        assert path is not None
        if raw_file is None:
            raise ValueError(f"{stem}: Discovery batch index names no RAW file")
        if self._stat(path) != self._event_stats[stem]:
            raise ValueError("Discovery event table changed after indexing")
        events = []
        with path.open("rb") as stream:
            for location in rows:
                stream.seek(location.offset)
                text = stream.read(location.byte_size).decode("utf-8")
                fields = next(csv.reader(io.StringIO(text, newline="")))
                row = dict(zip(self._event_headers[stem], fields, strict=True))
                events.append(
                    parse_discovery_event_row(
                        path,
                        location.row_number,
                        row,
                        raw_file=raw_file,
                        sample_stem=stem,
                        neutral_loss_by_tag=neutral_loss_by_tag,
                    ).event
                )
        return located_event_order(events)

    @staticmethod
    def _stat(path: Path) -> tuple[int, int]:
        value = path.stat()
        return value.st_size, value.st_mtime_ns

    def __iter__(self) -> Iterator[str]:
        return iter(self.batch.sample_order)

    def __len__(self) -> int:
        return len(self.batch.sample_order)

    def __getitem__(self, stem: str) -> tuple[DiscoveryPeak, ...]:
        return self.load_candidates(self._sample_ids[stem])[stem]

    def load_candidates(
        self, candidate_ids: Sequence[str]
    ) -> dict[str, tuple[DiscoveryPeak, ...]]:
        selected: dict[str, list[CandidateLocation]] = {}
        for cid in candidate_ids:
            location = self.locations[cid]
            selected.setdefault(location.sample_stem, []).append(location)
        result: dict[str, tuple[DiscoveryPeak, ...]] = {
            stem: () for stem in self.batch.sample_order
        }
        for stem, locations in selected.items():
            path = self.batch.peak_csvs[stem]
            if self._stat(path) != self._stats[stem]:
                raise ValueError("Discovery CSV changed after indexing")
            values = []
            with path.open("rb") as stream:
                for location in sorted(locations, key=lambda item: item.candidate_id):
                    stream.seek(location.offset)
                    text = stream.read(location.byte_size).decode("utf-8")
                    fields = next(csv.reader(io.StringIO(text, newline="")))
                    row = dict(zip(self._headers[stem], fields, strict=True))
                    candidate = _parse_peak_row(path, location.row_number, row)
                    if candidate.peak_id != location.candidate_id:
                        raise ValueError("Discovery row index crossed peak identity")
                    values.append(candidate)
            if self._stat(path) != self._stats[stem]:
                raise ValueError("Discovery CSV changed while loading a working set")
            result[stem] = tuple(values)
        return result

    def candidate_ids(self, stem: str) -> tuple[str, ...]:
        return self._sample_ids[stem]

    def feature_candidate_ids(self, stem: str | None = None) -> tuple[str, ...]:
        if stem is not None:
            return self._feature_ids[stem]
        return tuple(sorted(cid for ids in self._feature_ids.values() for cid in ids))

    def feature_view(self) -> Mapping[str, tuple[DiscoveryPeak, ...]]:
        return _FeatureCandidateView(self)


def _representative_order(row: Mapping[str, str]) -> tuple[object, ...]:
    error = row["neutral_loss_mass_error_ppm"]
    return representative_order_within_tag(
        product_intensity=float(row["product_intensity"]),
        observed_loss_error_ppm=float(error) if error else None,
        rt=float(row["ms2_rt"]),
        scan_number=int(row["ms2_scan_number"]),
        neutral_loss_tag=row["neutral_loss_tag"],
        product_mz=float(row["product_mz"]),
    )


class _FeatureCandidateView(Mapping[str, tuple[DiscoveryPeak, ...]]):
    def __init__(self, source: DiscoveryInputSource):
        self.source = source

    def __iter__(self) -> Iterator[str]:
        return iter(self.source)

    def __len__(self) -> int:
        return len(self.source)

    def __getitem__(self, stem: str) -> tuple[DiscoveryPeak, ...]:
        return self.source.load_candidates(self.source.feature_candidate_ids(stem))[
            stem
        ]
