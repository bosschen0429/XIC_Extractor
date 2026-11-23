"""Read one neutral-loss Discovery output and its RAW into an adduct count."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.diagnostics.nl_adduct_count import (
    Anchor,
    Candidate,
    RunCount,
    count_candidates,
)
from xic_extractor.discovery.csv_reader import (
    read_discovery_batch_index,
    read_discovery_events_csv,
    read_discovery_peaks_csv,
)
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.matrix_curation.known_nucleosides import read_nucleoside_list
from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import read_delimited_rows
from xic_extractor.xic_models import XICRequest

ANCHOR_PPM = 20.0
ANCHOR_MIN_INTENSITY = 1e6
MS2_PRODUCT_PPM = 10.0
SHAPE_PPM = 10.0
SHAPE_HALF_WINDOW_MIN = 0.5


def read_nucleosides(path: Path) -> list[tuple[MzRtWindow, str]]:
    """Nucleoside windows and the neutral-loss tag their sugar gives."""
    tags = [
        row["neutral_loss_tag"].strip()
        for row in read_delimited_rows(
            path, required_columns=("neutral_loss_tag",), encoding="utf-8-sig"
        )
    ]
    for line, tag in enumerate(tags, 2):
        if not tag:
            raise ValueError(f"{path}:{line}：缺少 neutral_loss_tag。")
    return list(zip(read_nucleoside_list(path), tags, strict=True))


def count_run(
    discovery_dir: Path,
    *,
    dll_dir: Path,
    neutral_loss_by_tag: dict[str, float],
    nucleosides: list[tuple[MzRtWindow, str]],
) -> RunCount:
    index = read_discovery_batch_index(discovery_dir / "discovery_batch_index.csv")
    (sample,) = index.sample_order
    raw, events = index.raw_files[sample], index.event_csvs[sample]
    if raw is None or events is None:
        raise ValueError(f"{discovery_dir}: Discovery output lacks its RAW or events")
    peaks = [p for p in read_discovery_peaks_csv(index.peak_csvs[sample]) if p.admitted]
    scans: dict[str, set[int]] = defaultdict(set)
    loss = None
    for item in read_discovery_events_csv(
        events,
        raw_file=raw,
        sample_stem=sample,
        neutral_loss_by_tag=neutral_loss_by_tag,
    ):
        if item.peak_id:
            scans[item.peak_id].add(item.event.scan_number)
            loss = item.event.configured_neutral_loss_da
    if loss is None:
        raise ValueError(f"{discovery_dir}: no event locates an admitted peak")
    same = [w for w, tag in nucleosides if tag in neutral_loss_by_tag]
    other = [w for w, tag in nucleosides if tag not in neutral_loss_by_tag]

    recalibration = MassRecalibration.EASY_IC_RESTORE
    with open_raw(raw, dll_dir, mass_recalibration=recalibration) as handle:
        ratios = _second_loss_ratios(handle, scans, loss)
        candidates = [
            Candidate(
                p.peak_id,
                p.peak_mz,
                p.ms1_apex_rt,
                p.attached_event_count,
                ratios.get(p.peak_id, 0.0),
            )
            for p in peaks
        ]
        return count_candidates(
            sample,
            candidates,
            loss,
            _apices(handle, same),
            _apices(handle, other),
            lambda requests: _traces(handle, requests),
        )


def _apices(handle, windows: list[MzRtWindow]) -> tuple[Anchor, ...]:
    low, high = handle.acquisition_rt_bounds()
    traces = handle.extract_xic_many(
        [
            XICRequest(
                w.mz, max(low, w.rt_low_min), min(high, w.rt_high_min), ANCHOR_PPM
            )
            for w in windows
        ]
    )
    anchors = []
    for w, trace in zip(windows, traces, strict=True):
        y = np.asarray(trace.intensity)
        seen = len(y) and y.max() > ANCHOR_MIN_INTENSITY
        anchors.append(
            Anchor(
                w.label,
                w.mz,
                float(np.asarray(trace.rt)[y.argmax()]) if seen else None,
            )
        )
    return tuple(anchors)


def _second_loss_ratios(handle, scans, loss: float) -> dict[str, float]:
    wanted = frozenset(n for numbers in scans.values() for n in numbers)
    spectra = {
        e.scan_number: e.scan
        for e in handle.iter_ms2_scan_subset(None, None, wanted)
        if e.scan is not None
    }

    def product(scan, target: float) -> float:
        if target <= 0:
            return 0.0
        inside = np.abs(scan.masses - target) <= mz_tolerance_da(
            target, MS2_PRODUCT_PPM
        )
        return float(scan.intensities[inside].max()) if inside.any() else 0.0

    ratios = {}
    for peak_id, numbers in scans.items():
        best = 0.0
        for scan in filter(None, (spectra.get(n) for n in numbers)):
            one = product(scan, scan.precursor_mz - loss)
            two = product(scan, scan.precursor_mz - 2 * loss)
            if two > 0:
                best = max(best, two / one if one else 99.0)
        ratios[peak_id] = best
    return ratios


def _traces(handle, requests):
    if not requests:
        return []
    return handle.extract_xic_many(
        [
            XICRequest(
                mz, rt - SHAPE_HALF_WINDOW_MIN, rt + SHAPE_HALF_WINDOW_MIN, SHAPE_PPM
            )
            for mz, rt in requests
        ]
    )
