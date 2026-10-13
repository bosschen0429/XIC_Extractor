from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION = "signal_processing_profile"
SIGNAL_PROCESSING_PROFILE_ID_PREFIX = "signal-profile:"
DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS = 15

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
    "segment_min_prominence_fraction",
    "segment_universe_limit",
    "segment_selection_behavior",
    "area_basis",
    "relative_shape_basis",
)
_FIXED_PROFILE_VALUES: dict[str, object] = {
    "gaussian_sigma_rule": "window_points_div_6",
    "gaussian_kernel_normalization": "unit_sum",
    "gaussian_edge_policy": "zero_pad_same",
    "baseline_method": "asls_rt",
    "baseline_lam": 1.0,
    "baseline_lam_unit": "minutes^4",
    "baseline_p": 0.01,
    "baseline_n_iter": 10,
    "physical_signal_formula": "gaussian_raw_minus_nonnegative_asls_rt_raw_positive",
    "boundary_basis": "chrom_peak_supported_lobes_sustained_valleys",
    "boundary_min_sigma_rt": 0.08,
    "segment_min_prominence_fraction": 0.04,
    "segment_universe_limit": 64,
    "segment_selection_behavior": "complete_supported_envelope_contains_query",
    "area_basis": "trapezoid_counts_seconds_on_canonical_signal",
    "relative_shape_basis": "own_max_canonical_signal",
}


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
    boundary_basis: str = "chrom_peak_supported_lobes_sustained_valleys"
    boundary_min_sigma_rt: float = 0.08
    segment_min_prominence_fraction: float = 0.04
    segment_universe_limit: int = 64
    segment_selection_behavior: str = "complete_supported_envelope_contains_query"
    area_basis: str = "trapezoid_counts_seconds_on_canonical_signal"
    relative_shape_basis: str = "own_max_canonical_signal"

    def __post_init__(self) -> None:
        if self.schema_version != SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported signal-processing profile schema_version")
        if self.method != "gaussian":
            raise ValueError("signal-processing method must be 'gaussian'")
        _validate_window_points(self.window_points)
        for field_name, expected_value in _FIXED_PROFILE_VALUES.items():
            actual_value = getattr(self, field_name)
            if (
                type(actual_value) is not type(expected_value)
                or actual_value != expected_value
            ):
                raise ValueError(f"unsupported signal-processing field: {field_name}")

    @property
    def profile_hash(self) -> str:
        return hashlib.sha256(
            canonical_signal_processing_profile_bytes(self)
        ).hexdigest()

    @property
    def profile_id(self) -> str:
        return f"{SIGNAL_PROCESSING_PROFILE_ID_PREFIX}{self.profile_hash}"


def resolve_signal_processing_profile(
    *,
    signal_processing_method: object = "gaussian",
    signal_processing_window_points: object = DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS,
) -> SignalProcessingProfile:
    if not isinstance(signal_processing_method, str):
        raise TypeError("signal_processing_method must be a string")
    return SignalProcessingProfile(
        method=signal_processing_method,
        window_points=_validate_window_points(signal_processing_window_points),
    )


def canonical_signal_processing_profile_bytes(
    profile: SignalProcessingProfile,
) -> bytes:
    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
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
    *,
    expected_profile_hash: str | None = None,
    expected_profile_id: str | None = None,
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
    try:
        profile = SignalProcessingProfile(**content)
    except TypeError as exc:
        raise ValueError("profile payload has invalid field types") from exc
    if canonical_signal_processing_profile_bytes(profile) != raw:
        raise ValueError("profile payload is not the canonical representation")
    if (
        expected_profile_hash is not None
        and profile.profile_hash != expected_profile_hash
    ):
        raise ValueError("profile_hash does not match canonical profile content")
    if expected_profile_id is not None and profile.profile_id != expected_profile_id:
        raise ValueError("profile_id does not match canonical profile content")
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
DEFAULT_SIGNAL_PROCESSING_PROFILE_ID = DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id


__all__ = [
    "DEFAULT_SIGNAL_PROCESSING_PROFILE",
    "DEFAULT_SIGNAL_PROCESSING_PROFILE_ID",
    "DEFAULT_SIGNAL_PROCESSING_WINDOW_POINTS",
    "SIGNAL_PROCESSING_PROFILE_ID_PREFIX",
    "SIGNAL_PROCESSING_PROFILE_SCHEMA_VERSION",
    "SignalProcessingProfile",
    "canonical_signal_processing_profile_bytes",
    "deserialize_signal_processing_profile",
    "resolve_signal_processing_profile",
    "serialize_signal_processing_profile",
]
