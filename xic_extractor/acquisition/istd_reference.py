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
    """Extract ISTD peaks across raw files and construct in-memory reference."""
    from xic_extractor.peak_detection.facade import find_peak_and_area
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.xic_models import XICRequest

    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("No ISTD targets (is_istd=True) found in targets")

    requests = tuple(
        XICRequest(
            mz=target.mz,
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            ppm_tol=target.ppm_tol,
        )
        for target in istd_targets
    )

    coordinates: dict[tuple[str, str], float] = {}
    for sample_stem, raw_path in sorted(raw_paths_by_sample.items()):
        with open_raw(raw_path, dll_dir=dll_dir) as raw:
            traces = raw.extract_xic_many(requests)
            for target, trace in zip(istd_targets, traces, strict=True):
                peak_result = find_peak_and_area(
                    trace.rt,
                    trace.intensity,
                    peak_config,
                    evidence_role="ISTD",
                )
                if peak_result.peak is not None and peak_result.peak.rt > 0:
                    peak_rt = float(peak_result.peak.rt)
                    coordinates[(target.label, sample_stem)] = peak_rt

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
