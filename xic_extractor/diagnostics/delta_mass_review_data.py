"""Saved Delta Mass candidates and newly acquired, read-only review evidence.

This artifact preserves candidate answers. Its RAW series and MS2 spectra are
new diagnostic reads, not a replay of the original detector or its decisions.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

REVIEW_SCHEMA = "delta_mass_candidate_review"


@dataclass(frozen=True)
class ReviewPosition:
    label: str
    delta_da: float
    expected_mz: float
    observed_mz: float | None
    apex_rt_min: float | None
    height: float | None
    height_ratio_to_base: float | None
    shape_r: float | None
    ratio_status: str
    position_status: str
    raw_rt: tuple[float, ...] = ()
    raw_intensity: tuple[float, ...] = ()

    @property
    def query_mz(self) -> float:
        return self.observed_mz if self.observed_mz is not None else self.expected_mz


@dataclass(frozen=True)
class ReviewSpectrum:
    scan_number: int
    rt_min: float
    precursor_mz: float
    precursor_metadata: Ms2PrecursorMetadata | None
    mz: tuple[float, ...]
    intensity: tuple[float, ...]


@dataclass(frozen=True)
class ReviewReadError:
    scan_number: int
    requested_rt_window: tuple[float, float]
    message: str


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    sample_stem: str
    method_label: str
    pattern_id: str
    status: str
    reject_reason: str
    base_mz: float
    base_rt_apex_min: float
    ratio_reference_label: str
    shape_reference_label: str
    positions: tuple[ReviewPosition, ...]
    raw_file: str
    xic_ppm: float
    rt_window: tuple[float, float]
    spectra: tuple[ReviewSpectrum, ...] = ()
    ms2_read_errors: tuple[ReviewReadError, ...] = ()


@dataclass(frozen=True)
class DeltaMassReview:
    discovery_directory: str
    candidates: tuple[ReviewCandidate, ...]


def write_delta_mass_review(review: DeltaMassReview, path: Path) -> None:
    payload = {"schema_version": REVIEW_SCHEMA, **asdict(review)}
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def decode_delta_mass_review(payload: object) -> DeltaMassReview:
    """Validate a saved review at the reader boundary, without RAW or science."""
    root = _mapping(payload, "review")
    if root.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError("Unsupported Delta Mass review schema")
    candidates = tuple(_candidate(row) for row in _rows(root, "candidates"))
    identities = [(row.sample_stem, row.candidate_id) for row in candidates]
    if not candidates or len(identities) != len(set(identities)):
        raise ValueError("Review must contain distinct sample/candidate pairs")
    return DeltaMassReview(_text(root, "discovery_directory"), candidates)


def _candidate(value: object) -> ReviewCandidate:
    row = _mapping(value, "candidate")
    positions = tuple(_position(p) for p in _rows(row, "positions"))
    labels = [p.label for p in positions]
    if not positions or len(labels) != len(set(labels)):
        raise ValueError("Candidate must contain distinct position labels")
    spectra = tuple(_spectrum(s) for s in _rows(row, "spectra"))
    if len({s.scan_number for s in spectra}) != len(spectra):
        raise ValueError("Candidate contains duplicate MS2 scan numbers")
    errors = []
    for value in _rows(row, "ms2_read_errors"):
        error = _mapping(value, "MS2 read error")
        errors.append(
            ReviewReadError(
                _scan_number(error["scan_number"]),
                _window(error["requested_rt_window"]),
                _text(error, "message"),
            )
        )
    return ReviewCandidate(
        candidate_id=_text(row, "candidate_id"),
        sample_stem=_text(row, "sample_stem"),
        method_label=_text(row, "method_label"),
        pattern_id=_text(row, "pattern_id"),
        status=_text(row, "status"),
        reject_reason=_text(row, "reject_reason", allow_empty=True),
        base_mz=_positive(row["base_mz"], "base_mz"),
        base_rt_apex_min=_number(row["base_rt_apex_min"], "base_rt_apex_min"),
        ratio_reference_label=_text(row, "ratio_reference_label"),
        shape_reference_label=_text(row, "shape_reference_label"),
        positions=positions,
        raw_file=_text(row, "raw_file"),
        xic_ppm=_positive(row["xic_ppm"], "xic_ppm"),
        rt_window=_window(row["rt_window"]),
        spectra=spectra,
        ms2_read_errors=tuple(errors),
    )


def _position(value: object) -> ReviewPosition:
    row = _mapping(value, "position")
    rt = _vector(row["raw_rt"], "raw_rt")
    intensity = _vector(row["raw_intensity"], "raw_intensity")
    if len(rt) != len(intensity) or any(
        a > b for a, b in zip(rt, rt[1:], strict=False)
    ):
        raise ValueError("Position RAW arrays must pair on ordered RT")
    return ReviewPosition(
        label=_text(row, "label"),
        delta_da=_number(row["delta_da"], "delta_da"),
        expected_mz=_positive(row["expected_mz"], "expected_mz"),
        observed_mz=_optional_number(row["observed_mz"], "observed_mz"),
        apex_rt_min=_optional_number(row["apex_rt_min"], "apex_rt_min"),
        height=_optional_number(row["height"], "height"),
        height_ratio_to_base=_optional_number(
            row["height_ratio_to_base"], "height_ratio_to_base"
        ),
        shape_r=_optional_number(row["shape_r"], "shape_r"),
        ratio_status=_text(row, "ratio_status", allow_empty=True),
        position_status=_text(row, "position_status"),
        raw_rt=rt,
        raw_intensity=intensity,
    )


def _spectrum(value: object) -> ReviewSpectrum:
    row = _mapping(value, "spectrum")
    mz = _vector(row["mz"], "MS2 mz")
    intensity = _vector(row["intensity"], "MS2 intensity")
    if len(mz) != len(intensity) or any(m <= 0 for m in mz):
        raise ValueError("MS2 arrays must pair positive m/z with intensity")
    metadata = row["precursor_metadata"]
    if metadata is not None:
        metadata = Ms2PrecursorMetadata(**_mapping(metadata, "precursor_metadata"))
    return ReviewSpectrum(
        _scan_number(row["scan_number"]),
        _number(row["rt_min"], "MS2 rt_min"),
        _positive(row["precursor_mz"], "precursor_mz"),
        metadata,
        mz,
        intensity,
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _rows(row: dict[str, Any], name: str) -> list[Any]:
    value = row[name]
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _text(row: dict[str, Any], name: str, *, allow_empty: bool = False) -> str:
    value = row[name]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be text")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive(value: object, name: str) -> float:
    number = _number(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _vector(value: object, name: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return tuple(_number(v, name) for v in value)


def _window(value: object) -> tuple[float, float]:
    values = _vector(value, "RT window")
    if len(values) != 2 or not 0 <= values[0] < values[1]:
        raise ValueError("RT window must be a nonnegative, increasing pair")
    return values[0], values[1]


def _scan_number(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("MS2 scan number must be a positive integer")
    return value
