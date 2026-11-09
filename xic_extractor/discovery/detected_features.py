"""Sample-local detected MS1 features, identities, validation, and table adapters.

Feature sources share this model and CSV handoff without loading a method's
pattern settings or matcher.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import (
    read_delimited_rows,
    write_delimited_rows,
)
from xic_extractor.validation_primitives import require_finite_real

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

DETECTED_FEATURES_SCHEMA_VERSION = "detected_features"

LOCAL_MS1_FEATURE_SOURCE = "local_ms1_feature"
LOCAL_MS1_FEATURE_TRACE_QUALITY_OK = "OK"
LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT = "WEAK_SUPPORT"
LOCAL_MS1_FEATURE_TRACE_QUALITIES = frozenset(
    {
        LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
        LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    }
)


@dataclass(frozen=True)
class DetectedFeature:
    """A single sample-local detected MS1 feature.

    ``source_feature_row_id`` is an optional pointer back to the originating
    row (e.g. an existing CID-NL ``peak_id``) used by
    later composite/intersection strategies; it is not required for delta-mass
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


def detected_feature_identity(feature: DetectedFeature) -> DetectedFeatureIdentity:
    """Return the canonical sample-local identity for one detected feature."""

    return (feature.sample_stem, feature.feature_id)


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


def load_detected_features_csv(path: Path) -> tuple[DetectedFeature, ...]:
    """Load and validate the Detected Features CSV input contract.

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


def write_detected_features_csv(
    path: Path,
    features: Sequence[DetectedFeature],
) -> Path:
    """Write the Detected Features CSV handoff surface."""

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
    trace_quality = _required_text(row.get("trace_quality"), "trace_quality", row_label)
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
        raise ValueError(f"{row_label} has invalid height: must be > 0, got {height!r}")
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


def _positive_float(value: float, field: str, row_label: str) -> float:
    if value <= 0:
        raise ValueError(f"{row_label} has invalid {field}: must be > 0, got {value!r}")
    return value


def _positive_height(value: str | None, row_label: str) -> float:
    height = _required_finite_float(value, "height", row_label)
    return _positive_float(height, "height", row_label)
