from __future__ import annotations

import csv
from pathlib import Path
from typing import cast

from xic_extractor.analysis_set import discover_raw_files
from xic_extractor.injection_rolling import read_injection_order
from xic_extractor.instrument_qc.classification import (
    InstrumentQCClass,
    classify_instrument_qc_raw,
)
from xic_extractor.instrument_qc.models import (
    ActivationMethod,
    InstrumentQCDiagnostic,
)

_KNOWN_ACTIVATION_METHODS = frozenset({"CID", "wHCD", "HCD", "CIDwHCD", "unknown"})


def read_sequence_manifest_context(
    path: Path | None,
) -> dict[str, tuple[str, ActivationMethod]]:
    if path is None or not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        context: dict[str, tuple[str, ActivationMethod]] = {}
        for row in reader:
            raw_stem = row.get("raw_stem", "").strip()
            if not raw_stem:
                continue
            activation = row.get("activation_method", "unknown").strip()
            if activation not in _KNOWN_ACTIVATION_METHODS:
                activation = "unknown"
            context[raw_stem] = (
                row.get("instrument_method", "").strip(),
                cast(ActivationMethod, activation),
            )
        return context


def discover_sdolek_raws(
    raw_dir: Path,
    diagnostics: list[InstrumentQCDiagnostic],
) -> tuple[Path, ...]:
    candidates = [
        path
        for path in discover_raw_files(raw_dir)
        if classify_instrument_qc_raw(path, raw_dir) == InstrumentQCClass.SDOLEK
    ]
    if not candidates:
        raise FileNotFoundError(f"No SDOLEK RAW files found below {raw_dir}")
    selected: list[Path] = []
    seen_stems: set[str] = set()
    for path in sorted(candidates, key=lambda path: (len(path.parts), path)):
        normalized_stem = path.stem.casefold()
        if normalized_stem in seen_stems:
            diagnostics.append(
                InstrumentQCDiagnostic(
                    sample_name=path.stem,
                    raw_path=path,
                    issue="DUPLICATE_RAW_STEM",
                    detail="Duplicate SDOLEK RAW stem skipped.",
                )
            )
            continue
        seen_stems.add(normalized_stem)
        selected.append(path)
    return tuple(selected)


def read_optional_injection_order(
    path: Path | None,
    raw_paths: tuple[Path, ...],
    diagnostics: list[InstrumentQCDiagnostic],
) -> dict[str, int]:
    if path is None:
        for raw_path in raw_paths:
            diagnostics.append(
                InstrumentQCDiagnostic(
                    sample_name=raw_path.stem,
                    raw_path=raw_path,
                    issue="INJECTION_ORDER_MISSING",
                    detail="No injection-order file supplied.",
                )
            )
        return {}
    return read_injection_order(path)


def metadata_source_status(path: Path | None) -> dict[str, str]:
    if path is None:
        return {
            "injection_order_source": "",
            "injection_order_status": "missing",
        }
    return {
        "injection_order_source": str(path),
        "injection_order_status": "provided",
    }
