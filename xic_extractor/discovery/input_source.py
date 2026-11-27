"""Index a published Discovery batch; load peaks and events only on request."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.discovery.event_evidence import representative_order_within_tag
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryPeak,
    located_event_order,
)
from xic_extractor.discovery.store import (
    DiscoveryBatchInput,
    open_discovery,
    read_events,
    read_neutral_loss_by_tag,
    read_peaks,
)


@dataclass(frozen=True, slots=True)
class CandidateLocation:
    candidate_id: str
    sample_stem: str
    peak_mz: float
    neutral_loss_tag: str
    admitted: bool
    row_id: int


def _chunks(values: Sequence[int], size: int = 900) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class DiscoveryInputSource(Mapping[str, tuple[DiscoveryPeak, ...]]):
    """A Discovery batch's peak index; domain values live only in a requested load.

    Alignment candidates are the admitted peaks. The Mapping view is for
    sample-at-a-time diagnostic consumers. Scientific execution uses
    load_candidates with a closed working set of peak IDs, and
    located_events for the events that locate one peak.
    """

    def __init__(self, batch: DiscoveryBatchInput):
        self.batch = batch
        self.locations: dict[str, CandidateLocation] = {}
        self.ms1_apex_mz_by_candidate: dict[str, float] = {}
        self.precursor_mz_by_candidate: dict[str, float] = {}
        wanted = set(batch.sample_order)
        ids: dict[str, list[str]] = {stem: [] for stem in batch.sample_order}
        by_row: dict[int, str] = {}
        pools: dict[str, list[tuple[bool, tuple[object, ...], float]]] = {}
        with open_discovery(batch.path) as db:
            self._losses = read_neutral_loss_by_tag(db)
            for row_id, cid, stem, mz, tag, status, apex_mz in db.execute(
                "SELECT p.id,p.peak_id,s.sample_stem,p.peak_mz,p.neutral_loss_tag,"
                "p.status,p.ms1_apex_mz FROM peaks p "
                "JOIN samples s ON s.id=p.sample ORDER BY p.peak_id"
            ):
                if stem not in wanted:
                    continue
                self.locations[cid] = CandidateLocation(
                    cid, stem, mz, tag, status == "admitted", row_id
                )
                if apex_mz is not None:
                    self.ms1_apex_mz_by_candidate[cid] = apex_mz
                ids[stem].append(cid)
                by_row[row_id] = cid
            for peak, reason, event in read_events(
                db,
                self._losses,
                "JOIN peaks p ON p.id=e.peak "
                "WHERE e.neutral_loss_tag=p.neutral_loss_tag",
            ):
                cid = by_row.get(peak) if peak is not None else None
                if cid is not None:
                    pools.setdefault(cid, []).append(
                        (not reason, _representative_order(event), event.precursor_mz)
                    )
        for cid, rows in pools.items():
            attached = any(item[0] for item in rows)
            pool = [item for item in rows if item[0] or not attached]
            self.precursor_mz_by_candidate[cid] = min(pool, key=lambda item: item[1])[2]
        self._sample_ids = {stem: tuple(values) for stem, values in ids.items()}
        self._feature_ids = {
            stem: tuple(cid for cid in values if self.locations[cid].admitted)
            for stem, values in self._sample_ids.items()
        }

    def __iter__(self) -> Iterator[str]:
        return iter(self.batch.sample_order)

    def __len__(self) -> int:
        return len(self.batch.sample_order)

    def __getitem__(self, stem: str) -> tuple[DiscoveryPeak, ...]:
        return self.load_candidates(self._sample_ids[stem])[stem]

    def load_candidates(
        self, candidate_ids: Sequence[str], *, located_events: bool = False
    ) -> dict[str, tuple[DiscoveryPeak, ...]]:
        """The given peaks by sample; with ``located_events``, each carries the
        events that locate it, attached or not."""
        rows = sorted(self.locations[cid].row_id for cid in candidate_ids)
        selected: dict[str, list[DiscoveryPeak]] = {}
        with open_discovery(self.batch.path) as db:
            for chunk in _chunks(rows):
                marks = ",".join("?" for _ in chunk)
                events: dict[int, list[DiscoveryEvent]] = {}
                if located_events:
                    for located, _reason, event in read_events(
                        db, self._losses, f"WHERE e.peak IN ({marks})", chunk
                    ):
                        assert located is not None
                        events.setdefault(located, []).append(event)
                for row_id, peak in read_peaks(db, f"WHERE p.id IN ({marks})", chunk):
                    if located_events:
                        peak = replace(
                            peak,
                            located_events=located_event_order(events.get(row_id, ())),
                        )
                    selected.setdefault(peak.sample_stem, []).append(peak)
        return {
            stem: tuple(sorted(selected.get(stem, ()), key=lambda peak: peak.peak_id))
            for stem in self.batch.sample_order
        }

    def candidate_ids(self, stem: str) -> tuple[str, ...]:
        return self._sample_ids[stem]

    def feature_candidate_ids(self, stem: str | None = None) -> tuple[str, ...]:
        if stem is not None:
            return self._feature_ids[stem]
        return tuple(sorted(cid for ids in self._feature_ids.values() for cid in ids))

    def feature_view(self) -> Mapping[str, tuple[DiscoveryPeak, ...]]:
        return _FeatureCandidateView(self)


def _representative_order(event: DiscoveryEvent) -> tuple[object, ...]:
    return representative_order_within_tag(
        product_intensity=event.product_intensity,
        observed_loss_error_ppm=event.observed_loss_error_ppm,
        rt=event.rt,
        scan_number=event.scan_number,
        neutral_loss_tag=event.neutral_loss_tag,
        product_mz=event.product_mz,
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
