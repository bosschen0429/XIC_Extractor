"""Sample-local detected MS1 feature model for Delta Mass / Pt pattern Discovery.

``DetectedFeature`` is the typed input the Pt pattern matcher in
``xic_extractor.discovery.delta_mass_patterns`` consumes. ``DeltaMassPatternSettings``
carries the tunable matcher parameters (pattern, ppm tolerance, RT tolerance,
ratio basis) with defaults that resolve to the built-in ``PT_PATTERN``.

This module intentionally has no runtime dependency on
``xic_extractor.discovery.delta_mass_patterns`` other than importing the
``DeltaMassPattern`` type and ``PT_PATTERN`` constant for the settings default,
mirroring the existing ``models.py`` / ``evidence_config.py`` split in this
package.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN, DeltaMassPattern
from xic_extractor.numeric_validation import require_finite_real
from xic_extractor.tabular_io import (
    read_delimited_rows,
    read_tsv_with_header,
    validate_delimited_header,
    write_delimited_rows,
)

DETECTED_FEATURES_COLUMNS = (
    "sample_stem",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "height",
    "area",
    "trace_quality",
    "shape_score",
    "source",
)

DETECTED_FEATURES_SCHEMA_VERSION = "detected_features.v1"

EXTERNAL_FEATURE_TABLE_REQUIRED_COLUMNS = (
    "external_tool",
    "external_run_id",
    "external_tool_version",
    "external_adapter_version",
    "sample_id",
    "feature_id",
    "mz",
    "rt",
    "area_or_intensity",
    "area_or_intensity_semantics",
    "mz_tolerance_unit",
    "rt_tolerance_unit",
    "duplicate_feature_policy",
    "missing_value_policy",
)

_HEIGHT_SEMANTICS = frozenset(
    {
        "height",
        "peak_height",
        "intensity",
        "peak_intensity",
        "max_intensity",
        "apex_intensity",
    }
)
_AREA_SEMANTICS = frozenset({"area", "peak_area"})
_TRACE_QUALITY_COLUMNS = ("trace_quality", "ms1_trace_quality")
_SHAPE_SCORE_COLUMNS = ("shape_score", "ms1_shape_score")
_SOURCE_FEATURE_ROW_ID_COLUMNS = ("source_feature_row_id", "ms1_feature_row_id")
_UNSPECIFIED_TRACE_QUALITY = "UNSPECIFIED"
LOCAL_MS1_FEATURE_SOURCE = "local_ms1_feature"
CID_NL_SHARED_IDENTITY_SOURCE = "cid_nl_ms1_feature_handoff_v1"
LOCAL_MS1_FEATURE_TRACE_QUALITY_OK = "OK"
LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT = "WEAK_SUPPORT"
LOCAL_MS1_FEATURE_TRACE_QUALITIES = frozenset(
    {
        LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
        LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    }
)
_MZMINE_REQUIRED_COLUMNS = ("row ID", "row m/z", "row retention time")
_MZMINE_PEAK_AREA_SUFFIX = " Peak area"
_MZMINE_MZML_SUFFIX = ".mzML"
_MZMINE_RAW_SUFFIX = ".raw"
_MZMINE_WIDE_ADAPTER_VERSION = "mzmine_wide_area_proxy_v1"


@dataclass(frozen=True)
class DetectedFeature:
    """A single sample-local detected MS1 feature.

    ``source_feature_row_id`` is an optional pointer back to the originating
    row (e.g. an existing CID-NL candidate's ``ms1_feature_row_id``) used by
    later composite/intersection strategies; it is not required for Pt-only
    matching.
    """

    sample_stem: str
    feature_id: str
    source_feature_row_id: str | None
    mz: float
    rt_apex_min: float
    rt_start_min: float | None
    rt_end_min: float | None
    height: float
    area: float | None
    trace_quality: str
    shape_score: float | None
    source: str


DetectedFeatureIdentity = tuple[str, str]


class DetectedFeatureIdentityNamespace(str, Enum):
    """Namespaces whose identifiers are explicitly comparable across providers."""

    CID_NL_MS1_FEATURE_ROW_ID = "cid_nl_ms1_feature_row_id"


@dataclass(frozen=True, slots=True)
class ComparableDetectedFeatureIdentity:
    """A positive capability to compare one feature in a named namespace."""

    namespace: DetectedFeatureIdentityNamespace
    values: frozenset[str]


def detected_feature_identity(feature: DetectedFeature) -> DetectedFeatureIdentity:
    """Return the canonical sample-local identity for one detected feature."""

    return (feature.sample_stem, feature.feature_id)


def comparable_cid_nl_identity(
    feature: DetectedFeature,
) -> ComparableDetectedFeatureIdentity | None:
    """Return CID-NL-comparable identity only for the explicit handoff source.

    ``source_feature_row_id`` is otherwise only a provenance locator. A
    non-empty detector-local ID must not silently grant cross-provider join
    authority, so unknown, local-MS1, MZmine, OpenMS, and scan-proxy sources all
    return ``None`` and remain eligible only for the bounded m/z/RT fallback.
    """

    if feature.source != CID_NL_SHARED_IDENTITY_SOURCE:
        return None
    feature_id = feature.feature_id.strip()
    if not feature_id:
        raise ValueError("CID-NL-comparable feature_id must be non-empty")
    values = {feature_id}
    if feature.source_feature_row_id is not None:
        source_feature_row_id = feature.source_feature_row_id.strip()
        if source_feature_row_id:
            values.add(source_feature_row_id)
    return ComparableDetectedFeatureIdentity(
        namespace=DetectedFeatureIdentityNamespace.CID_NL_MS1_FEATURE_ROW_ID,
        values=frozenset(values),
    )


def validate_unique_detected_feature_identities(
    features: Sequence[DetectedFeature],
    *,
    source_label: str,
) -> None:
    """Reject duplicate ``(sample_stem, feature_id)`` rows fail-closed.

    ``feature_id`` is intentionally sample-local. The same identifier may be
    reused by different samples, but two rows in one sample cannot safely be
    distinguished by downstream candidate and evidence joins.
    """

    seen: set[DetectedFeatureIdentity] = set()
    duplicates: set[DetectedFeatureIdentity] = set()
    for feature in features:
        identity = detected_feature_identity(feature)
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
    if duplicates:
        rendered = ", ".join(
            f"({sample_stem!r}, {feature_id!r})"
            for sample_stem, feature_id in sorted(duplicates)
        )
        raise ValueError(
            f"{source_label}: duplicate detected feature identity "
            f"(sample_stem, feature_id): {rendered}"
        )


def validate_detected_features(
    features: Sequence[DetectedFeature],
    *,
    source_label: str,
) -> None:
    """Validate domain fields and sample-local identities before matching."""

    for row_index, feature in enumerate(features):
        _validate_detected_feature(
            feature,
            row_label=f"{source_label} row {row_index}",
        )
    validate_unique_detected_feature_identities(
        features,
        source_label=source_label,
    )


@dataclass(frozen=True)
class DeltaMassPatternSettings:
    """Tunable parameters for ``match_delta_mass_pattern()``.

    ``pattern`` defaults to the built-in ``PT_PATTERN`` singleton, and
    ``mz_tolerance_ppm`` / ``rt_tolerance_sec`` default to that same pattern's
    tolerances so that ``DeltaMassPatternSettings()`` and ``PT_PATTERN`` stay
    numerically consistent by construction.
    """

    pattern: DeltaMassPattern = field(default=PT_PATTERN)
    pattern_id: str = "pt"
    method_label: str = "pt_pattern"
    mz_tolerance_ppm: float = PT_PATTERN.mz_tolerance_ppm
    rt_tolerance_sec: float = PT_PATTERN.rt_tolerance_sec
    ratio_basis: str = "both"

    def __post_init__(self) -> None:
        if not self.pattern_id.strip():
            raise ValueError("pattern_id must be non-empty")
        if not self.method_label.strip():
            raise ValueError("method_label must be non-empty")
        mz_tolerance_ppm = require_finite_real(
            self.mz_tolerance_ppm,
            name="mz_tolerance_ppm",
        )
        if mz_tolerance_ppm <= 0:
            raise ValueError("mz_tolerance_ppm must be finite and > 0")
        rt_tolerance_sec = require_finite_real(
            self.rt_tolerance_sec,
            name="rt_tolerance_sec",
        )
        if rt_tolerance_sec <= 0:
            raise ValueError("rt_tolerance_sec must be finite and > 0")


def load_detected_features_csv(path: Path) -> tuple[DetectedFeature, ...]:
    """Load and validate the Detected Features CSV v1 input contract.

    Required non-empty fields: ``sample_stem``, ``feature_id``, ``mz``,
    ``rt_apex_min``, ``height``, ``trace_quality``, and ``source``. ``height``
    must additionally be ``> 0``. ``mz``, ``rt_apex_min``, ``height``, and
    any non-blank optional numeric field must be finite. Rows with invalid
    required fields raise ``ValueError`` rather than being silently skipped.
    """

    rows = read_delimited_rows(
        path,
        required_columns=DETECTED_FEATURES_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )

    features = tuple(
        _parse_detected_feature(row, path=path, row_index=row_index)
        for row_index, row in enumerate(rows)
    )
    validate_detected_features(features, source_label=str(path))
    return features


def load_external_feature_table_tsv(path: Path) -> tuple[DetectedFeature, ...]:
    """Adapt a validated external feature table into ``DetectedFeature`` rows.

    This is the product-facing feature-source bridge for Delta Mass / Pt
    matching. Rows must provide m/z, RT, and a positive area/intensity value.
    If the source is area-only, the same positive value is used as the matcher's
    intensity proxy while the ``area`` field preserves the original semantics.
    Missing trace/shape support is recorded as ``UNSPECIFIED`` and must be
    tightened later by trace-level evidence when that evidence is available.
    External-table v1 row IDs are preserved as provenance only; this adapter
    does not declare them comparable to CID-NL ``ms1_feature_row_id`` values.
    """

    _header, rows = read_tsv_with_header(
        path,
        required_columns=EXTERNAL_FEATURE_TABLE_REQUIRED_COLUMNS,
        encoding="utf-8-sig",
    )
    features = tuple(
        _parse_external_feature_table_row(row, path=path, row_index=row_index)
        for row_index, row in enumerate(rows)
    )
    validate_detected_features(features, source_label=str(path))
    return features


def load_mzmine_wide_feature_table_csv(
    path: Path,
    *,
    external_run_id: str | None = None,
) -> tuple[DetectedFeature, ...]:
    """Adapt a MZmine wide feature table into sample-local detected features.

    Expected shape: ``row ID``, ``row m/z``, ``row retention time``, then one or
    more ``<sample>.mzML Peak area`` columns. Blank and non-positive peak areas
    are treated as missing for that sample. MZmine peak area is used as the
    matcher's positive intensity proxy because this table has no apex-height
    column.
    """

    run_id = external_run_id or path.stem
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = validate_delimited_header(
            reader.fieldnames,
            path=path,
            required_columns=_MZMINE_REQUIRED_COLUMNS,
        )
        sample_area_columns = _mzmine_sample_area_columns(header, path)
        features: list[DetectedFeature] = []
        for row_index, row in enumerate(reader):
            features.extend(
                _parse_mzmine_wide_feature_table_row(
                    row,
                    sample_area_columns=sample_area_columns,
                    run_id=run_id,
                    path=path,
                    row_index=row_index,
                )
            )
    result = tuple(features)
    validate_detected_features(result, source_label=str(path))
    return result


def write_detected_features_csv(
    path: Path,
    features: Sequence[DetectedFeature],
) -> Path:
    """Write the Detected Features CSV v1 handoff surface."""

    rows = tuple(
        _validated_detected_feature_row(feature, path=path, row_index=row_index)
        for row_index, feature in enumerate(features)
    )
    validate_unique_detected_feature_identities(features, source_label=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        path,
        rows,
        DETECTED_FEATURES_COLUMNS,
        extrasaction="raise",
        formatter=_format_machine_csv_value,
    )
    return path


def _parse_detected_feature(
    row: Mapping[str, str | None], *, path: Path, row_index: int
) -> DetectedFeature:
    row_label = _row_label(path, row_index)
    sample_stem = _required_text(row.get("sample_stem"), "sample_stem", row_label)
    feature_id = _required_text(row.get("feature_id"), "feature_id", row_label)
    trace_quality = _required_text(
        row.get("trace_quality"), "trace_quality", row_label
    )
    source = _required_text(row.get("source"), "source", row_label)

    return DetectedFeature(
        sample_stem=sample_stem,
        feature_id=feature_id,
        source_feature_row_id=_optional_text(row.get("source_feature_row_id")),
        mz=_required_finite_float(row.get("mz"), "mz", row_label),
        rt_apex_min=_required_finite_float(
            row.get("rt_apex_min"), "rt_apex_min", row_label
        ),
        rt_start_min=_optional_finite_float(
            row.get("rt_start_min"), "rt_start_min", row_label
        ),
        rt_end_min=_optional_finite_float(
            row.get("rt_end_min"), "rt_end_min", row_label
        ),
        height=_positive_height(row.get("height"), row_label),
        area=_optional_finite_float(row.get("area"), "area", row_label),
        trace_quality=_validate_trace_quality_for_source(
            trace_quality,
            source=source,
            row_label=row_label,
        ),
        shape_score=_optional_finite_float(
            row.get("shape_score"), "shape_score", row_label
        ),
        source=source,
    )


def _parse_external_feature_table_row(
    row: Mapping[str, str | None], *, path: Path, row_index: int
) -> DetectedFeature:
    row_label = _row_label(path, row_index)
    for column in EXTERNAL_FEATURE_TABLE_REQUIRED_COLUMNS:
        _required_text(row.get(column), column, row_label)

    trace_quality = _first_optional_text(row, _TRACE_QUALITY_COLUMNS)
    shape_score = _first_optional_finite_float(row, _SHAPE_SCORE_COLUMNS, row_label)

    semantics = _normalized_text(row.get("area_or_intensity_semantics"))
    area_or_intensity = _required_finite_float(
        row.get("area_or_intensity"), "area_or_intensity", row_label
    )
    height = _first_optional_finite_float(row, ("height", "ms1_height"), row_label)
    if height is None:
        if semantics in _HEIGHT_SEMANTICS:
            height = area_or_intensity
        elif semantics in _AREA_SEMANTICS:
            height = area_or_intensity
        else:
            raise ValueError(
                f"{row_label} has unsupported area_or_intensity_semantics: "
                f"{row.get('area_or_intensity_semantics')!r}"
            )
    if height <= 0:
        raise ValueError(
            f"{row_label} has invalid height: must be > 0, got {height!r}"
        )

    area = _first_optional_finite_float(row, ("area", "ms1_area"), row_label)
    if area is None and semantics in _AREA_SEMANTICS:
        area = area_or_intensity

    return DetectedFeature(
        sample_stem=_required_text(row.get("sample_id"), "sample_id", row_label),
        feature_id=_required_text(row.get("feature_id"), "feature_id", row_label),
        source_feature_row_id=_first_optional_text(row, _SOURCE_FEATURE_ROW_ID_COLUMNS),
        mz=_required_finite_float(row.get("mz"), "mz", row_label),
        rt_apex_min=_required_finite_float(row.get("rt"), "rt", row_label),
        rt_start_min=_first_optional_finite_float(
            row, ("rt_start_min", "peak_start_rt"), row_label
        ),
        rt_end_min=_first_optional_finite_float(
            row, ("rt_end_min", "peak_end_rt"), row_label
        ),
        height=height,
        area=area,
        trace_quality=trace_quality or _UNSPECIFIED_TRACE_QUALITY,
        shape_score=shape_score,
        source=_external_source_label(row, row_label),
    )


def _parse_mzmine_wide_feature_table_row(
    row: Mapping[str, str | None],
    *,
    sample_area_columns: Sequence[str],
    run_id: str,
    path: Path,
    row_index: int,
) -> list[DetectedFeature]:
    row_label = _row_label(path, row_index)
    row_id = _required_text(row.get("row ID"), "row ID", row_label)
    mz = _required_finite_float(row.get("row m/z"), "row m/z", row_label)
    rt = _required_finite_float(
        row.get("row retention time"), "row retention time", row_label
    )
    features: list[DetectedFeature] = []
    for column in sample_area_columns:
        area = _optional_finite_float(row.get(column), column, row_label)
        if area is None or area <= 0:
            continue
        sample_stem = _mzmine_sample_stem(column)
        source_feature_row_id = f"{run_id}|row|{row_id}"
        features.append(
            DetectedFeature(
                sample_stem=sample_stem,
                feature_id=f"{sample_stem}|mzmine_row|{row_id}",
                source_feature_row_id=source_feature_row_id,
                mz=mz,
                rt_apex_min=rt,
                rt_start_min=None,
                rt_end_min=None,
                height=area,
                area=area,
                trace_quality=_UNSPECIFIED_TRACE_QUALITY,
                shape_score=None,
                source=f"external_feature_table:MZmine:{_MZMINE_WIDE_ADAPTER_VERSION}",
            )
        )
    return features


def _detected_feature_row(feature: DetectedFeature) -> dict[str, object]:
    return {
        "sample_stem": feature.sample_stem,
        "feature_id": feature.feature_id,
        "source_feature_row_id": feature.source_feature_row_id,
        "mz": feature.mz,
        "rt_apex_min": feature.rt_apex_min,
        "rt_start_min": feature.rt_start_min,
        "rt_end_min": feature.rt_end_min,
        "height": feature.height,
        "area": feature.area,
        "trace_quality": feature.trace_quality,
        "shape_score": feature.shape_score,
        "source": feature.source,
    }


def _format_machine_csv_value(value: object) -> str:
    """Format machine data without lossy display-oriented float rounding."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def _validated_detected_feature_row(
    feature: DetectedFeature, *, path: Path, row_index: int
) -> dict[str, object]:
    return _validate_detected_feature(
        feature,
        row_label=_row_label(path, row_index),
    )


def _validate_detected_feature(
    feature: DetectedFeature,
    *,
    row_label: str,
) -> dict[str, object]:
    sample_stem = _required_feature_text(
        feature.sample_stem,
        "sample_stem",
        row_label,
    )
    feature_id = _required_feature_text(feature.feature_id, "feature_id", row_label)
    trace_quality = _required_feature_text(
        feature.trace_quality,
        "trace_quality",
        row_label,
    )
    source = _required_feature_text(feature.source, "source", row_label)
    _finite_feature_value(feature.mz, "mz", row_label)
    _finite_feature_value(feature.rt_apex_min, "rt_apex_min", row_label)
    for field_name, value in (
        ("rt_start_min", feature.rt_start_min),
        ("rt_end_min", feature.rt_end_min),
        ("area", feature.area),
        ("shape_score", feature.shape_score),
    ):
        if value is not None:
            _finite_feature_value(value, field_name, row_label)
    height = _finite_feature_value(feature.height, "height", row_label)
    if height <= 0:
        raise ValueError(
            f"{row_label} has invalid height: must be > 0, got {height!r}"
        )
    _validate_trace_quality_for_source(
        trace_quality,
        source=source,
        row_label=row_label,
    )
    row = _detected_feature_row(feature)
    row.update(
        sample_stem=sample_stem,
        feature_id=feature_id,
        trace_quality=trace_quality,
        source=source,
    )
    return row


def _required_feature_text(value: object, field: str, row_label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{row_label} is missing required field {field!r}")
    return value.strip()


def _finite_feature_value(value: float, field: str, row_label: str) -> float:
    return require_finite_real(value, name=f"{row_label} field {field!r}")


def _external_source_label(row: Mapping[str, str | None], row_label: str) -> str:
    tool = _required_text(row.get("external_tool"), "external_tool", row_label)
    adapter = _required_text(
        row.get("external_adapter_version"), "external_adapter_version", row_label
    )
    return f"external_feature_table:{tool}:{adapter}"


def is_local_ms1_feature_source(source: str) -> bool:
    return source == LOCAL_MS1_FEATURE_SOURCE


def _validate_trace_quality_for_source(
    trace_quality: str,
    *,
    source: str,
    row_label: str,
) -> str:
    if (
        is_local_ms1_feature_source(source)
        and trace_quality not in LOCAL_MS1_FEATURE_TRACE_QUALITIES
    ):
        allowed = ", ".join(sorted(LOCAL_MS1_FEATURE_TRACE_QUALITIES))
        raise ValueError(
            f"{row_label} has invalid local_ms1_feature trace_quality: "
            f"{trace_quality!r}; expected one of {allowed}"
        )
    return trace_quality


def _mzmine_sample_area_columns(header: Sequence[str], path: Path) -> tuple[str, ...]:
    columns = tuple(
        column
        for column in header
        if column and column.endswith(_MZMINE_PEAK_AREA_SUFFIX)
    )
    if not columns:
        raise ValueError(f"{path}: no MZmine '<sample> Peak area' columns found")
    return columns


def _mzmine_sample_stem(column: str) -> str:
    sample = column[: -len(_MZMINE_PEAK_AREA_SUFFIX)]
    if sample.lower().endswith(_MZMINE_MZML_SUFFIX.lower()):
        sample = sample[: -len(_MZMINE_MZML_SUFFIX)]
    if sample.lower().endswith(_MZMINE_RAW_SUFFIX.lower()):
        sample = sample[: -len(_MZMINE_RAW_SUFFIX)]
    return sample


def _row_label(path: Path, row_index: int) -> str:
    return f"{path} row {row_index}"


def _required_text(value: str | None, field: str, row_label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    return text


def _optional_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _normalized_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _first_optional_text(
    row: Mapping[str, str | None],
    columns: Sequence[str],
) -> str | None:
    for column in columns:
        value = _optional_text(row.get(column))
        if value is not None:
            return value
    return None


def _required_finite_float(value: str | None, field: str, row_label: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric required field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite required field {field!r}: {value!r}"
        )
    return parsed


def _optional_finite_float(
    value: str | None, field: str, row_label: str
) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric optional field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite optional field {field!r}: {value!r}"
        )
    return parsed


def _first_optional_finite_float(
    row: Mapping[str, str | None],
    columns: Sequence[str],
    row_label: str,
) -> float | None:
    for column in columns:
        value = _optional_finite_float(row.get(column), column, row_label)
        if value is not None:
            return value
    return None


def _positive_height(value: str | None, row_label: str) -> float:
    height = _required_finite_float(value, "height", row_label)
    if height <= 0:
        raise ValueError(
            f"{row_label} has invalid height: must be > 0, got {height!r}"
        )
    return height
