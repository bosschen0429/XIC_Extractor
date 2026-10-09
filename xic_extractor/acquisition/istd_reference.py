from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.configuration.models import ExtractionConfig, Target


def extract_batch_known_istd_reference(
    targets: Sequence[Target],
    raw_paths_by_sample: Mapping[str, Path],
    dll_dir: Path,
    peak_config: ExtractionConfig,
    *,
    source_identifier: str = "<in-memory>",
    source_sha256: str = "",
) -> KnownIstdReference:
    """Extract unique physically supported, native NL-qualified ISTD coordinates."""
    from xic_extractor.configuration.signal_processing_profile import (
        resolve_signal_processing_profile,
    )
    from xic_extractor.evidence.neutral_loss import check_nl
    from xic_extractor.ms2_scan_models import Ms2ScanEvent, Ms2Window
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        prepare_physical_signal,
        qualified_physical_segments,
    )
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.xic_models import XICRequest

    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("No ISTD targets (is_istd=True) found in targets")

    requests = tuple(
        XICRequest(
            mz=target.mz,
            rt_min=max(0.0, target.rt_min - 1.0),
            rt_max=target.rt_max + 1.0,
            ppm_tol=target.ppm_tol,
        )
        for target in istd_targets
    )
    profile = resolve_signal_processing_profile(
        signal_processing_method=peak_config.signal_processing_method,
        signal_processing_window_points=peak_config.signal_processing_window_points,
    )

    coordinates: dict[tuple[str, str], float] = {}
    for sample_stem, raw_path in sorted(raw_paths_by_sample.items()):
        with open_raw(raw_path, dll_dir=dll_dir) as raw:
            traces = raw.extract_xic_many(requests)
            ms2_windows: dict[tuple[float, float], tuple[Ms2ScanEvent, ...]] = {}
            for target, trace in zip(istd_targets, traces, strict=True):
                if (
                    target.neutral_loss_da is None
                    or target.nl_ppm_warn is None
                    or target.nl_ppm_max is None
                ):
                    continue
                prepared = prepare_physical_signal(
                    trace.rt, trace.intensity, profile=profile
                )
                if prepared.prepared is None:
                    continue
                universe = enumerate_physical_segment_universe(prepared)
                segments = qualified_physical_segments(
                    prepared.prepared, universe.segments
                )
                window_key = (target.rt_min, target.rt_max)
                qualified_rts = []
                for segment in segments:
                    # Context supports morphology, but never expands the declared
                    # identity search or supplies a nearest/strongest tie-break.
                    if not target.rt_min <= segment.apex_rt_min <= target.rt_max:
                        continue
                    if window_key not in ms2_windows:
                        ms2_windows[window_key] = tuple(raw.iter_ms2_scans(*window_key))
                    events = tuple(
                        event
                        for event in ms2_windows[window_key]
                        if event.scan is not None
                        and segment.left_rt_min <= event.scan.rt <= segment.right_rt_min
                    )
                    nl = check_nl(
                        Ms2Window(
                            sample_stem,
                            segment.left_rt_min,
                            segment.right_rt_min,
                            events,
                        ),
                        precursor_mz=target.mz,
                        neutral_loss_da=target.neutral_loss_da,
                        nl_ppm_warn=target.nl_ppm_warn,
                        nl_ppm_max=target.nl_ppm_max,
                        ms2_precursor_tol_da=peak_config.ms2_precursor_tol_da,
                        nl_min_intensity_ratio=peak_config.nl_min_intensity_ratio,
                    )
                    if nl.status in {"OK", "WARN"}:
                        qualified_rts.append(float(segment.apex_rt_min))
                if len(qualified_rts) == 1 and qualified_rts[0] > 0:
                    coordinates[(target.label, sample_stem)] = qualified_rts[0]

    return mint_known_istd_reference_from_measurements(
        istd_targets,
        coordinates,
        source_identifier=source_identifier,
        source_sha256=source_sha256,
    )


def read_known_istd_reference(path: Path) -> KnownIstdReference:
    """Read the current report schema and validate declared targets at their owner."""
    if not isinstance(path, Path):
        raise TypeError("known ISTD reference workbook must be a Path")
    payload = path.read_bytes()
    workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)

    def rows(sheet: str, required: tuple[str, ...]) -> Iterable[dict[str, object]]:
        if sheet not in workbook.sheetnames:
            raise ValueError(f"known ISTD reference requires sheet {sheet}")
        values = iter(workbook[sheet].values)
        headers = tuple(
            str(value).strip() if value is not None else ""
            for value in next(values, ())
        )
        if any(headers.count(key) != 1 for key in required):
            raise ValueError(
                f"{sheet}: missing or duplicate required headers {required}"
            )
        for row in values:
            if any(value is not None for value in row):
                yield dict(zip(headers, row))

    def number(row: dict[str, object], key: str) -> float:
        value = row[key]
        parsed = (
            float(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else None
        )
        if parsed is not None and not math.isfinite(parsed):
            parsed = None
        if isinstance(value, bool) or parsed is None:
            raise ValueError(f"known ISTD reference: invalid required {key}: {value!r}")
        return parsed

    def text(row: dict[str, object], key: str) -> str:
        value = row[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"known ISTD reference: invalid required {key}")
        return value.strip()

    try:
        targets = []
        for row in rows(
            "Targets",
            (
                "Label",
                "Role",
                "m/z",
                "RT min",
                "RT max",
                "ppm tol",
                "NL (Da)",
                "NL ppm warn",
                "NL ppm max",
            ),
        ):
            if str(row["Role"]).strip() != "ISTD":
                continue
            targets.append(
                Target(
                    label=text(row, "Label"),
                    mz=number(row, "m/z"),
                    rt_min=number(row, "RT min"),
                    rt_max=number(row, "RT max"),
                    ppm_tol=number(row, "ppm tol"),
                    neutral_loss_da=number(row, "NL (Da)"),
                    nl_ppm_warn=number(row, "NL ppm warn"),
                    nl_ppm_max=number(row, "NL ppm max"),
                    is_istd=True,
                    istd_pair="",
                )
            )
        if not targets or len({t.label for t in targets}) != len(targets):
            raise ValueError("known ISTD reference requires unique ISTD target labels")
        labels = {target.label for target in targets}
        coordinates: dict[tuple[str, str], float] = {}
        seen: set[tuple[str, str]] = set()
        sample: str | None = None
        for row in rows("XIC Results", ("SampleName", "Target", "Role", "RT")):
            # The report carries SampleName across each sample block, whose
            # first named row can be an analyte rather than an ISTD.
            if row["SampleName"] is not None and str(row["SampleName"]).strip():
                sample = text(row, "SampleName")
            if str(row["Role"]).strip() != "ISTD":
                continue
            if sample is None:
                raise ValueError(
                    "XIC Results: ISTD reference has no preceding SampleName"
                )
            label = text(row, "Target")
            if label not in labels:
                raise ValueError(f"XIC Results: undeclared ISTD target {label}")
            key = label, sample
            if key in seen:
                raise ValueError(f"XIC Results: duplicate ISTD reference {key}")
            seen.add(key)
            if row["RT"] is None or str(row["RT"]).strip() == "ND":
                continue
            rt = number(row, "RT")
            if rt < 0:
                raise ValueError("XIC Results: reference RT must be nonnegative")
            coordinates[key] = rt
        return mint_known_istd_reference_from_measurements(
            targets,
            coordinates,
            source_identifier=str(path.resolve()),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        workbook.close()
