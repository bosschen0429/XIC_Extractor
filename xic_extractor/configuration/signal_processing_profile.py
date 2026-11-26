from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION = "signal_processing_profile"
DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS = 9
LOCALIZED_VALLEY_MIN_DROP_FRACTION = 0.15

_PROFILE_KEYS = (
    "schema_version",
    "method",
    "window_points",
    "gaussian_sigma_rule",
    "gaussian_kernel_normalization",
    "gaussian_edge_policy",
    "baseline_method",
    "baseline_lam",
    "baseline_lam_unit",
    "baseline_p",
    "baseline_n_iter",
    "physical_signal_formula",
    "boundary_basis",
    "boundary_min_sigma_rt",
    "segment_universe_limit",
    "segment_selection_behavior",
    "area_basis",
)


@dataclass(frozen=True, slots=True)
class SignalProcessingProfile:
    """The complete versioned physical-signal contract for one trace."""

    method: str = "gaussian"
    window_points: int = DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS
    schema_version: str = SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION
    gaussian_sigma_rule: str = "window_points_div_6"
    gaussian_kernel_normalization: str = "unit_sum"
    gaussian_edge_policy: str = "zero_pad_same"
    baseline_method: str = "asls_rt"
    baseline_lam: float = 1.0
    baseline_lam_unit: str = "minutes^4"
    baseline_p: float = 0.01
    baseline_n_iter: int = 10
    physical_signal_formula: str = "gaussian_raw_minus_nonnegative_asls_rt_raw_positive"
    boundary_basis: str = "chrom_peak_clear_valleys_tenth_height_return"
    boundary_min_sigma_rt: float = 0.08
    segment_universe_limit: int = 64
    segment_selection_behavior: str = "apex_supported_local_valley_contains_query"
    area_basis: str = "trapezoid_counts_seconds_on_canonical_signal"


def resolve_signal_processing_profile(
    *,
    signal_processing_method: object = "gaussian",
    signal_processing_window_points: object = DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS,
) -> SignalProcessingProfile:
    if not isinstance(signal_processing_method, str):
        raise TypeError("signal_processing_method must be a string")
    if signal_processing_method != "gaussian":
        raise ValueError("signal-processing method must be 'gaussian'")
    return SignalProcessingProfile(
        method=signal_processing_method,
        window_points=_validate_window_points(signal_processing_window_points),
    )


def canonical_signal_processing_profile_bytes(
    profile: SignalProcessingProfile,
) -> bytes:
    payload = {key: getattr(profile, key) for key in _PROFILE_KEYS}
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def serialize_signal_processing_profile(profile: SignalProcessingProfile) -> bytes:
    return canonical_signal_processing_profile_bytes(profile)


def deserialize_signal_processing_profile(
    payload: bytes | str,
) -> SignalProcessingProfile:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise TypeError("profile payload must be bytes or str")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("profile payload must be UTF-8") from exc
    try:
        content = json.loads(decoded, object_pairs_hook=_unique_mapping)
    except json.JSONDecodeError as exc:
        raise ValueError("profile payload must be valid JSON") from exc
    if not isinstance(content, dict):
        raise ValueError("profile payload must be one JSON object")
    unknown = tuple(key for key in content if key not in _PROFILE_KEYS)
    if unknown:
        raise ValueError(f"profile payload has unknown fields: {unknown}")
    missing = tuple(key for key in _PROFILE_KEYS if key not in content)
    if missing:
        raise ValueError(f"profile payload is missing fields: {missing}")
    if content["schema_version"] != SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported signal-processing profile schema_version")
    if content["method"] != "gaussian":
        raise ValueError("signal-processing method must be 'gaussian'")
    try:
        _validate_window_points(content["window_points"])
        profile = SignalProcessingProfile(**content)
    except TypeError as exc:
        raise ValueError("profile payload has invalid field types") from exc
    for field_name in _PROFILE_KEYS[3:]:
        expected_value = getattr(DEFAULT_SIGNAL_PROCESSING_PROFILE, field_name)
        actual_value = content[field_name]
        if (
            type(actual_value) is not type(expected_value)
            or actual_value != expected_value
        ):
            raise ValueError(f"unsupported signal-processing field: {field_name}")
    if canonical_signal_processing_profile_bytes(profile) != raw:
        raise ValueError("profile payload is not the canonical representation")
    return profile


def _validate_window_points(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("signal_processing_window_points must be a non-bool integer")
    if value < 3 or value > 999 or value % 2 == 0:
        raise ValueError(
            "signal_processing_window_points must be an odd integer from 3 to 999"
        )
    return value


def _unique_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"profile payload has duplicate field: {key}")
        result[key] = value
    return result


DEFAULT_SIGNAL_PROCESSING_PROFILE = SignalProcessingProfile()


__all__ = [
    "DEFAULT_SIGNAL_PROCESSING_PROFILE",
    "DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS",
    "SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION",
    "SignalProcessingProfile",
    "canonical_signal_processing_profile_bytes",
    "deserialize_signal_processing_profile",
    "resolve_signal_processing_profile",
    "serialize_signal_processing_profile",
]
