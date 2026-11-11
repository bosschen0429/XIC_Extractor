"""Canonical immutable run specification for CID-NL Discovery."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import (
    TYPE_CHECKING,
    Any,
    Iterator,
    Mapping,
    Protocol,
    cast,
    runtime_checkable,
)

if TYPE_CHECKING:
    from xic_extractor.discovery.settings import DiscoverySettings

from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.validation_primitives import (
    canonical_float_hex,
    canonical_json_bytes,
    json_unique_mapping_hook,
    parse_canonical_float_hex,
    require_integer,
    require_keys,
    require_mapping,
    require_optional_lower_sha256,
    require_optional_text,
    require_text,
)

PROCESSING_RUN_SPEC_SCHEMA_VERSION = "processing_run_spec_source_revision"
CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION = "cid_nl_discovery_method"

_WEIGHT_KEYS = (
    "ms1_peak_present",
    "seed_event_per",
    "seed_event_max",
    "rt_aligned",
    "rt_near",
    "rt_shifted",
    "product_intensity_high",
    "product_intensity_med",
    "area_high",
    "area_med",
    "scan_support_high",
    "scan_support_low",
)
_THRESHOLD_FLOAT_KEYS = (
    "rt_aligned_max_min",
    "rt_near_max_min",
    "rt_shifted_max_min",
    "product_intensity_high_min",
    "product_intensity_med_min",
    "area_high_min",
    "area_med_min",
    "scan_support_high_score_min",
    "scan_support_low_score_max",
)
_THRESHOLD_KEYS = (
    *_THRESHOLD_FLOAT_KEYS[:7],
    "scan_support_target",
    *_THRESHOLD_FLOAT_KEYS[7:],
)


@dataclass(frozen=True, slots=True)
class ProcessingAcquisitionInput:
    """One ordered runtime input bound to owner-minted acquisition content."""

    input_ordinal: int
    adapter_input_key: str
    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    blank_library_sha256: str | None = None
    # Runtime transport only: the canonical run binds its digest, not repeated
    # copies of a cohort library in every sample's persisted sidecar.
    blank_library_content: str | None = dataclass_field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        require_integer(self.input_ordinal, field="input_ordinal", non_negative=True)
        _required_text(self.adapter_input_key, field="adapter_input_key")
        _required_text(self.sample_stem, field="sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise TypeError("acquisition_source must be an AcquisitionSourceIdentity")
        require_optional_lower_sha256(
            self.blank_library_sha256, field="blank_library_sha256"
        )
        if self.blank_library_content is not None:
            if not isinstance(self.blank_library_content, str):
                raise TypeError("blank_library_content must be text or None")
            if (
                hashlib.sha256(self.blank_library_content.encode("utf-8")).hexdigest()
                != self.blank_library_sha256
            ):
                raise ValueError("blank library snapshot does not match its digest")


@runtime_checkable
class DiscoveryMethodSpec(Protocol):
    """Protocol for any method and its Discovery settings."""

    @property
    def settings(self) -> Any: ...

    @property
    def method(self) -> str: ...

    @property
    def schema_version(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CidNlDiscoveryMethodSpec:
    """Complete CID-NL method and Discovery settings."""

    settings: DiscoverySettings
    method: str = "cid_nl"
    schema_version: str = CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION:
            raise ValueError("unsupported CID-NL Discovery method schema_version")
        if self.method != "cid_nl":
            raise ValueError("Discovery method must be 'cid_nl'")
        _validate_discovery_settings(self.settings)


@dataclass(frozen=True, slots=True)
class ProcessingRunSpec:
    """One immutable run specification compiled before any RAW handle is opened."""

    acquisition_inputs: tuple[ProcessingAcquisitionInput, ...]
    discovery_method: DiscoveryMethodSpec | CidNlDiscoveryMethodSpec
    signal_processing_profile: SignalProcessingProfile
    schema_version: str = PROCESSING_RUN_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROCESSING_RUN_SPEC_SCHEMA_VERSION:
            raise ValueError(
                "unsupported ProcessingRunSpec schema_version; "
                "regenerate Discovery with the current version"
            )
        if (
            not isinstance(self.acquisition_inputs, tuple)
            or not self.acquisition_inputs
        ):
            raise ValueError("acquisition_inputs must be a non-empty tuple")
        if any(
            not isinstance(item, ProcessingAcquisitionInput)
            for item in self.acquisition_inputs
        ):
            raise TypeError("acquisition_inputs must contain typed inputs")
        if tuple(item.input_ordinal for item in self.acquisition_inputs) != tuple(
            range(len(self.acquisition_inputs))
        ):
            raise ValueError("acquisition input order is not canonical")
        for field, values in (
            (
                "adapter_input_key",
                tuple(item.adapter_input_key for item in self.acquisition_inputs),
            ),
            (
                "sample_stem",
                tuple(item.sample_stem for item in self.acquisition_inputs),
            ),
            (
                "acquisition_source_id",
                tuple(
                    item.acquisition_source.acquisition_source_id
                    for item in self.acquisition_inputs
                ),
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate ProcessingRunSpec {field}")
        if not isinstance(self.discovery_method, DiscoveryMethodSpec):
            raise TypeError("discovery_method must be a DiscoveryMethodSpec")
        if isinstance(self.discovery_method, CidNlDiscoveryMethodSpec):
            if (
                self.discovery_method.schema_version
                != CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION
            ):
                raise ValueError("run and method schema versions disagree")
        if not isinstance(self.signal_processing_profile, SignalProcessingProfile):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )


def compile_processing_run_spec(
    acquisition_inputs: tuple[ProcessingAcquisitionInput, ...],
    *,
    discovery_settings: DiscoverySettings | None = None,
    discovery_method: DiscoveryMethodSpec | None = None,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> ProcessingRunSpec:
    """Compile already-identified ordered inputs into one run specification."""

    if discovery_method is not None:
        method = discovery_method
    elif discovery_settings is not None:
        method = CidNlDiscoveryMethodSpec(settings=discovery_settings)
    else:
        raise ValueError(
            "compile_processing_run_spec requires "
            "discovery_method or discovery_settings"
        )
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")
    return ProcessingRunSpec(
        acquisition_inputs=acquisition_inputs,
        discovery_method=method,
        signal_processing_profile=signal_processing_profile,
    )


def canonical_cid_nl_method_bytes(method: CidNlDiscoveryMethodSpec) -> bytes:
    if not isinstance(method, CidNlDiscoveryMethodSpec):
        raise TypeError("method must be a CidNlDiscoveryMethodSpec")
    return _canonical_json_bytes(_method_content(method))


def canonical_processing_run_spec_bytes(spec: ProcessingRunSpec) -> bytes:
    if not isinstance(spec, ProcessingRunSpec):
        raise TypeError("spec must be a ProcessingRunSpec")
    return _canonical_json_bytes(_run_content(spec))


def _run_content(spec: ProcessingRunSpec) -> dict[str, object]:
    return {
        "schema_version": spec.schema_version,
        "acquisition_inputs": [
            {
                "input_ordinal": item.input_ordinal,
                "adapter_input_key": item.adapter_input_key,
                "sample_stem": item.sample_stem,
                "acquisition_source": {
                    "content_sha256": item.acquisition_source.content_sha256,
                    "local_file_revision": item.acquisition_source.local_file_revision,
                    "byte_size": item.acquisition_source.byte_size,
                    "acquisition_format": item.acquisition_source.acquisition_format,
                    "observation_namespace": (
                        item.acquisition_source.observation_namespace
                    ),
                },
                "blank_library_sha256": item.blank_library_sha256,
            }
            for item in spec.acquisition_inputs
        ],
        "discovery_method": _method_content(spec.discovery_method),
        "signal_processing_profile": json.loads(
            canonical_signal_processing_profile_bytes(spec.signal_processing_profile)
        ),
    }


def _normalize_generic_setting_value(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return GenericMethodSettings(asdict(value))
    if isinstance(value, GenericMethodSettings):
        return value
    if isinstance(value, Mapping):
        return GenericMethodSettings(value)
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_generic_setting_value(item) for item in value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("floating-point setting values must be finite")
        return value
    return value


def normalize_generic_method_settings(settings: Any) -> GenericMethodSettings:
    """Normalize method settings into a canonical GenericMethodSettings mapping."""
    if settings is None:
        return GenericMethodSettings({})
    if isinstance(settings, GenericMethodSettings):
        return settings
    if hasattr(settings, "__dataclass_fields__"):
        from dataclasses import asdict

        return GenericMethodSettings(asdict(settings))
    if isinstance(settings, Mapping):
        return GenericMethodSettings(settings)
    raise TypeError(
        "Discovery method settings must be a dataclass or Mapping, "
        f"got {type(settings).__name__}"
    )


class GenericMethodSettings(Mapping[str, Any]):
    """Immutable normalized settings mapping with attribute and dict access."""

    __slots__ = ("_data", "_hash")

    def __init__(self, data: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        merged: dict[str, Any] = {}
        if data is not None:
            merged.update(data)
        if kwargs:
            merged.update(kwargs)
        normalized = {
            str(k): _normalize_generic_setting_value(v) for k, v in merged.items()
        }
        self._data: dict[str, Any] = dict(sorted(normalized.items()))
        self._hash: int | None = None

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            ) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GenericMethodSettings):
            return self._data == other._data
        if isinstance(other, Mapping):
            return self._data == dict(other)
        if hasattr(other, "__dataclass_fields__"):
            from dataclasses import asdict

            return self._data == asdict(cast(Any, other))
        return False

    def __hash__(self) -> int:
        if self._hash is None:
            payload = _canonical_json_bytes(self.to_dict())
            self._hash = hash(payload)
        return self._hash

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in self._data.items():
            if isinstance(v, GenericMethodSettings):
                result[k] = v.to_dict()
            elif isinstance(v, tuple):
                result[k] = [
                    item.to_dict() if isinstance(item, GenericMethodSettings) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v!r}" for k, v in self._data.items())
        return f"GenericMethodSettings({items})"


@dataclass(frozen=True, slots=True)
class GenericDiscoveryMethodSpec:
    """Generic Discovery method for non-CID-NL or composite methods."""

    settings: Any
    method: str
    schema_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "settings", normalize_generic_method_settings(self.settings)
        )


def _method_content(
    method: DiscoveryMethodSpec | CidNlDiscoveryMethodSpec,
) -> dict[str, object]:
    if isinstance(method, CidNlDiscoveryMethodSpec):
        settings = method.settings
        weights = settings.evidence_profile.weights
        thresholds = settings.evidence_profile.thresholds
        content: dict[str, object] = {
            "schema_version": method.schema_version,
            "method": method.method,
            "settings": {
                "neutral_loss_profiles": [
                    {
                        "tag": profile.tag,
                        "neutral_loss_da": _float_text(profile.neutral_loss_da),
                    }
                    for profile in settings.neutral_loss_profiles
                ],
                "selected_tag_names": list(settings.selected_tag_names),
                "tag_combine_mode": settings.tag_combine_mode,
                "nl_tolerance_ppm": _float_text(settings.nl_tolerance_ppm),
                "precursor_mz_tolerance_ppm": _float_text(
                    settings.precursor_mz_tolerance_ppm
                ),
                "product_mz_tolerance_ppm": _float_text(
                    settings.product_mz_tolerance_ppm
                ),
                "product_search_ppm": _float_text(settings.product_search_ppm),
                "ms2_precursor_tol_da": _float_text(settings.ms2_precursor_tol_da),
                "nl_min_intensity_ratio": _float_text(settings.nl_min_intensity_ratio),
                "ms1_search_padding_min": _float_text(settings.ms1_search_padding_min),
                "rt_min": None
                if settings.rt_min is None
                else _float_text(settings.rt_min),
                "rt_max": None
                if settings.rt_max is None
                else _float_text(settings.rt_max),
                "resolver_mode": settings.resolver_mode,
                "evidence_profile": {
                    "name": settings.evidence_profile.name,
                    "weights": {key: getattr(weights, key) for key in _WEIGHT_KEYS},
                    "thresholds": {
                        key: (
                            getattr(thresholds, key)
                            if key == "scan_support_target"
                            else _float_text(getattr(thresholds, key))
                        )
                        for key in _THRESHOLD_KEYS
                    },
                },
                "enable_blank_qualification": settings.enable_blank_qualification,
                "blank_library_path": settings.blank_library_path,
                "blank_ppm_tol": _float_text(settings.blank_ppm_tol),
                "blank_fold_change_threshold": _float_text(
                    settings.blank_fold_change_threshold
                ),
                # Absent means the vendor masses were used unchanged.
                **(
                    {"mass_recalibration": settings.mass_recalibration.value}
                    if settings.mass_recalibration is not MassRecalibration.NONE
                    else {}
                ),
            },
        }
        return content
    settings_val = method.settings
    if isinstance(settings_val, GenericMethodSettings):
        settings_dict = settings_val.to_dict()
    elif hasattr(settings_val, "__dataclass_fields__"):
        from dataclasses import asdict

        settings_dict = asdict(settings_val)
    elif isinstance(settings_val, Mapping):
        settings_dict = dict(settings_val)
    elif isinstance(settings_val, dict):
        settings_dict = settings_val
    else:
        settings_dict = dict(getattr(settings_val, "__dict__", {}))
    return {
        "schema_version": method.schema_version,
        "method": method.method,
        "settings": settings_dict,
    }


def _validate_discovery_settings(settings: DiscoverySettings) -> None:
    from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
    from xic_extractor.discovery.evidence_config import (
        DiscoveryEvidenceProfile,
        DiscoveryEvidenceThresholds,
        DiscoveryEvidenceWeights,
    )
    from xic_extractor.discovery.settings import DiscoverySettings
    from xic_extractor.settings_schema import RESOLVER_MODES

    if not isinstance(settings, DiscoverySettings):
        raise TypeError("settings must be DiscoverySettings")
    profiles = settings.neutral_loss_profiles
    if not isinstance(profiles, tuple) or not profiles:
        raise ValueError("neutral_loss_profiles must be a non-empty tuple")
    if any(not isinstance(profile, NeutralLossProfile) for profile in profiles):
        raise TypeError("neutral_loss_profiles must contain NeutralLossProfile")
    tags = tuple(
        _required_text(profile.tag, field="neutral loss tag") for profile in profiles
    )
    if len(tags) != len(set(tags)):
        raise ValueError("neutral_loss_profiles contains duplicate tags")
    if settings.selected_tag_names != tags:
        raise ValueError("selected_tag_names must exactly match neutral-loss profiles")
    if settings.tag_combine_mode not in {"single", "union", "intersection"}:
        raise ValueError("unsupported tag_combine_mode")
    if settings.tag_combine_mode == "single" and len(profiles) != 1:
        raise ValueError("single tag_combine_mode requires exactly one profile")
    if settings.tag_combine_mode in {"union", "intersection"} and len(profiles) < 2:
        raise ValueError(
            f"{settings.tag_combine_mode} tag_combine_mode "
            "requires at least two profiles"
        )
    for profile in profiles:
        value = profile.neutral_loss_da
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("neutral_loss_da must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError("neutral_loss_da must be finite and positive")
    canonical_masses = tuple(
        float(profile.neutral_loss_da).hex() for profile in profiles
    )
    if len(canonical_masses) != len(set(canonical_masses)):
        raise ValueError(
            "neutral_loss_profiles contains distinct tags with the same canonical mass"
        )
    positive_fields = (
        "nl_tolerance_ppm",
        "precursor_mz_tolerance_ppm",
        "product_mz_tolerance_ppm",
        "product_search_ppm",
        "ms2_precursor_tol_da",
        "nl_min_intensity_ratio",
        "ms1_search_padding_min",
        "blank_ppm_tol",
        "blank_fold_change_threshold",
    )
    for field in positive_fields:
        value = getattr(settings, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{field} must be finite and positive")
    if not isinstance(settings.enable_blank_qualification, bool):
        raise TypeError("enable_blank_qualification must be a boolean")
    if not isinstance(settings.mass_recalibration, MassRecalibration):
        raise TypeError("mass_recalibration must be a MassRecalibration policy")
    if settings.blank_library_path is not None:
        if not isinstance(settings.blank_library_path, str):
            raise TypeError("blank_library_path must be text or None")
        if not settings.blank_library_path:
            raise ValueError("blank_library_path must be non-empty text or None")
    for field in ("rt_min", "rt_max"):
        value = getattr(settings, field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric or None")
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"{field} must be finite and non-negative")
    if (
        settings.rt_min is not None
        and settings.rt_max is not None
        and settings.rt_min > settings.rt_max
    ):
        raise ValueError("rt_min must not exceed rt_max")
    _required_text(settings.resolver_mode, field="resolver_mode")
    if settings.resolver_mode not in RESOLVER_MODES:
        raise ValueError("unsupported resolver_mode")
    evidence = settings.evidence_profile
    if not isinstance(evidence, DiscoveryEvidenceProfile):
        raise TypeError("evidence_profile must be DiscoveryEvidenceProfile")
    if evidence.name != "default":
        raise ValueError("unsupported Discovery evidence profile")
    if not isinstance(evidence.weights, DiscoveryEvidenceWeights):
        raise TypeError("evidence weights must be DiscoveryEvidenceWeights")
    if not isinstance(evidence.thresholds, DiscoveryEvidenceThresholds):
        raise TypeError("evidence thresholds must be DiscoveryEvidenceThresholds")
    for key in _WEIGHT_KEYS:
        _integer(getattr(evidence.weights, key), field=key)
    for key in _THRESHOLD_FLOAT_KEYS:
        value = getattr(evidence.thresholds, key)
        if not math.isfinite(float(value)):
            raise ValueError(f"{key} must be finite")
    if evidence.thresholds.scan_support_target <= 0:
        raise ValueError("scan_support_target must be positive")


_canonical_json_bytes = canonical_json_bytes
_unique_mapping = json_unique_mapping_hook
_require_keys = require_keys
_mapping = require_mapping
_required_text = require_text
_optional_text = require_optional_text
_float_text = canonical_float_hex
_float_from_text = parse_canonical_float_hex


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, allow_blank=True)


def _integer(value: object, *, field: str) -> int:
    return require_integer(value, field=field, type_error_factory=TypeError)


__all__ = [
    "CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION",
    "PROCESSING_RUN_SPEC_SCHEMA_VERSION",
    "CidNlDiscoveryMethodSpec",
    "DiscoveryMethodSpec",
    "GenericDiscoveryMethodSpec",
    "GenericMethodSettings",
    "ProcessingAcquisitionInput",
    "ProcessingRunSpec",
    "canonical_cid_nl_method_bytes",
    "canonical_processing_run_spec_bytes",
    "compile_processing_run_spec",
    "normalize_generic_method_settings",
]
