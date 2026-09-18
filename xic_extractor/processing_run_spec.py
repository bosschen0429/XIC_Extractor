"""Canonical immutable run identity for CID-NL Discovery."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterator,
    Literal,
    Mapping,
    Protocol,
    cast,
    runtime_checkable,
)

if TYPE_CHECKING:
    from xic_extractor.discovery.models import DiscoverySettings

from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
    deserialize_signal_processing_profile,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)

PROCESSING_RUN_SPEC_SCHEMA_VERSION = "processing_run_spec_v2"
PROCESSING_RUN_SPEC_ENVELOPE_VERSION = "processing_run_spec_envelope_v1"
PROCESSING_RUN_SPEC_ID_PREFIX = "processing-run.v1:"
CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION = "cid_nl_discovery_method_v2"
CID_NL_DISCOVERY_METHOD_ID_PREFIX = "cid-nl-discovery-method.v1:"

_RUN_CONTENT_KEYS = (
    "schema_version",
    "acquisition_inputs",
    "discovery_method",
    "signal_processing_profile",
)
_RUN_ENVELOPE_KEYS = (
    "schema_version",
    "run_spec_hash",
    "run_spec_id",
    "content",
)
_ACQUISITION_INPUT_KEYS = (
    "input_ordinal",
    "adapter_input_key",
    "sample_stem",
    "acquisition_source",
)
_ACQUISITION_SOURCE_KEYS = (
    "acquisition_source_id",
    "content_sha256",
    "byte_size",
    "acquisition_format",
    "observation_namespace",
)
_METHOD_ENVELOPE_KEYS = (
    "method_hash",
    "method_id",
    "content",
)
_METHOD_CONTENT_KEYS = (
    "schema_version",
    "method",
    "settings",
)
_SETTINGS_KEYS = (
    "neutral_loss_profiles",
    "selected_tag_names",
    "tag_combine_mode",
    "nl_tolerance_ppm",
    "precursor_mz_tolerance_ppm",
    "product_mz_tolerance_ppm",
    "product_search_ppm",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "seed_rt_gap_min",
    "ms1_search_padding_min",
    "rt_min",
    "rt_max",
    "resolver_mode",
    "evidence_profile",
    "enable_blank_qualification",
    "blank_library_path",
    "blank_ppm_tol",
    "blank_fold_change_threshold",
)
_EVIDENCE_PROFILE_KEYS = ("name", "weights", "thresholds")
_WEIGHT_KEYS = (
    "ms1_peak_present",
    "ms1_peak_absent",
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
    "legacy_trace_quality_high",
    "legacy_trace_quality_low",
)
_THRESHOLD_FLOAT_KEYS = (
    "rt_aligned_max_min",
    "rt_near_max_min",
    "rt_shifted_max_min",
    "product_intensity_high_min",
    "product_intensity_med_min",
    "area_high_min",
    "area_med_min",
    "ms1_support_strong_area_min",
    "ms1_support_moderate_area_min",
    "scan_support_high_score_min",
    "scan_support_low_score_max",
)
_THRESHOLD_KEYS = (
    *_THRESHOLD_FLOAT_KEYS[:9],
    "scan_support_target",
    *_THRESHOLD_FLOAT_KEYS[9:],
)
_PROFILE_ENVELOPE_KEYS = ("profile_hash", "profile_id", "content")


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
        if (
            isinstance(self.input_ordinal, bool)
            or not isinstance(self.input_ordinal, int)
            or self.input_ordinal < 0
        ):
            raise ValueError("input_ordinal must be a non-negative integer")
        _required_text(self.adapter_input_key, field="adapter_input_key")
        _required_text(self.sample_stem, field="sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise TypeError("acquisition_source must be an AcquisitionSourceIdentity")
        if self.blank_library_sha256 is not None:
            digest = self.blank_library_sha256
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("blank_library_sha256 must be a lowercase SHA-256")
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
    """Protocol for any method and Discovery settings identity."""

    @property
    def settings(self) -> Any: ...

    @property
    def method(self) -> str: ...

    @property
    def schema_version(self) -> str: ...

    @property
    def method_hash(self) -> str: ...

    @property
    def method_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CidNlDiscoveryMethodSpec:
    """Complete CID-NL method and Discovery settings identity."""

    settings: DiscoverySettings
    method: str = "cid_nl"
    schema_version: str = CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION
    _legacy_blank_settings: bool = dataclass_field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version not in {
            "cid_nl_discovery_method_v1",
            CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION,
        }:
            raise ValueError("unsupported CID-NL Discovery method schema_version")
        if self.method != "cid_nl":
            raise ValueError("Discovery method must be 'cid_nl'")
        _validate_discovery_settings(self.settings)
        if (
            self.schema_version == "cid_nl_discovery_method_v1"
            and not self._legacy_blank_settings
        ):
            if (
                self.settings.enable_blank_qualification
                or self.settings.blank_library_path is not None
                or self.settings.blank_ppm_tol != 10.0
                or self.settings.blank_fold_change_threshold != 3.0
            ):
                raise ValueError("original v1 method cannot carry blank settings")

    @property
    def method_hash(self) -> str:
        return hashlib.sha256(canonical_cid_nl_method_bytes(self)).hexdigest()

    @property
    def method_id(self) -> str:
        return f"{CID_NL_DISCOVERY_METHOD_ID_PREFIX}{self.method_hash}"


@dataclass(frozen=True, slots=True)
class ProcessingRunSpec:
    """One immutable run identity compiled before any RAW handle is opened."""

    acquisition_inputs: tuple[ProcessingAcquisitionInput, ...]
    discovery_method: DiscoveryMethodSpec | CidNlDiscoveryMethodSpec
    signal_processing_profile: SignalProcessingProfile
    schema_version: str = PROCESSING_RUN_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {
            "processing_run_spec_v1",
            PROCESSING_RUN_SPEC_SCHEMA_VERSION,
        }:
            raise ValueError("unsupported ProcessingRunSpec schema_version")
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
        if not (
            isinstance(
                self.discovery_method,
                (CidNlDiscoveryMethodSpec, DiscoveryMethodSpec),
            )
            or (
                hasattr(self.discovery_method, "method_hash")
                and hasattr(self.discovery_method, "method_id")
                and hasattr(self.discovery_method, "method")
            )
        ):
            raise TypeError("discovery_method must be a DiscoveryMethodSpec")
        if isinstance(self.discovery_method, CidNlDiscoveryMethodSpec):
            expected_method_schema = (
                "cid_nl_discovery_method_v1"
                if self.schema_version == "processing_run_spec_v1"
                else CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION
            )
            if self.discovery_method.schema_version != expected_method_schema:
                raise ValueError("run and method schema versions disagree")
        if self.schema_version == "processing_run_spec_v1" and any(
            item.blank_library_sha256 is not None for item in self.acquisition_inputs
        ):
            raise ValueError("v1 run cannot carry blank library bindings")
        if not isinstance(self.signal_processing_profile, SignalProcessingProfile):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_processing_run_spec_bytes(self)

    @property
    def run_spec_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def run_spec_id(self) -> str:
        return f"{PROCESSING_RUN_SPEC_ID_PREFIX}{self.run_spec_hash}"

    @property
    def profile_id(self) -> str:
        return self.signal_processing_profile.profile_id


def compile_processing_run_spec(
    acquisition_inputs: tuple[ProcessingAcquisitionInput, ...],
    *,
    discovery_settings: DiscoverySettings | None = None,
    discovery_method: DiscoveryMethodSpec | None = None,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> ProcessingRunSpec:
    """Compile already-identified ordered inputs into one run identity."""

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


def serialize_processing_run_spec(spec: ProcessingRunSpec) -> bytes:
    if not isinstance(spec, ProcessingRunSpec):
        raise TypeError("spec must be a ProcessingRunSpec")
    return _canonical_json_bytes(
        {
            "schema_version": PROCESSING_RUN_SPEC_ENVELOPE_VERSION,
            "run_spec_hash": spec.run_spec_hash,
            "run_spec_id": spec.run_spec_id,
            "content": _run_content(spec),
        }
    )


def deserialize_processing_run_spec(
    payload: bytes | str,
    *,
    expected_run_spec_hash: str | None = None,
    expected_run_spec_id: str | None = None,
) -> ProcessingRunSpec:
    raw, content = _decode_json_object(payload, label="ProcessingRunSpec")
    _require_keys(content, _RUN_ENVELOPE_KEYS, label="ProcessingRunSpec envelope")
    if content["schema_version"] != PROCESSING_RUN_SPEC_ENVELOPE_VERSION:
        raise ValueError("unsupported ProcessingRunSpec envelope schema_version")
    run_content = _mapping(content["content"], label="ProcessingRunSpec content")
    spec = _run_spec_from_content(run_content)
    if content["run_spec_hash"] != spec.run_spec_hash:
        raise ValueError("run_spec_hash does not match canonical content")
    if content["run_spec_id"] != spec.run_spec_id:
        raise ValueError("run_spec_id does not match canonical content")
    if (
        expected_run_spec_hash is not None
        and spec.run_spec_hash != expected_run_spec_hash
    ):
        raise ValueError("run_spec_hash does not match expected run")
    if expected_run_spec_id is not None and spec.run_spec_id != expected_run_spec_id:
        raise ValueError("run_spec_id does not match expected run")
    if serialize_processing_run_spec(spec) != raw:
        raise ValueError("ProcessingRunSpec payload is not canonical")
    return spec


def _run_content(spec: ProcessingRunSpec) -> dict[str, object]:
    return {
        "schema_version": spec.schema_version,
        "acquisition_inputs": [
            {
                "input_ordinal": item.input_ordinal,
                "adapter_input_key": item.adapter_input_key,
                "sample_stem": item.sample_stem,
                "acquisition_source": {
                    "acquisition_source_id": (
                        item.acquisition_source.acquisition_source_id
                    ),
                    "content_sha256": item.acquisition_source.content_sha256,
                    "byte_size": item.acquisition_source.byte_size,
                    "acquisition_format": item.acquisition_source.acquisition_format,
                    "observation_namespace": (
                        item.acquisition_source.observation_namespace
                    ),
                },
                **(
                    {"blank_library_sha256": item.blank_library_sha256}
                    if spec.schema_version == PROCESSING_RUN_SPEC_SCHEMA_VERSION
                    else {}
                ),
            }
            for item in spec.acquisition_inputs
        ],
        "discovery_method": {
            "method_hash": spec.discovery_method.method_hash,
            "method_id": spec.discovery_method.method_id,
            "content": _method_content(spec.discovery_method),
        },
        "signal_processing_profile": {
            "profile_hash": spec.signal_processing_profile.profile_hash,
            "profile_id": spec.signal_processing_profile.profile_id,
            "content": json.loads(
                canonical_signal_processing_profile_bytes(
                    spec.signal_processing_profile
                ).decode("utf-8")
            ),
        },
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
            str(k): _normalize_generic_setting_value(v)
            for k, v in merged.items()
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
    """Generic Discovery method identity for non-CID-NL or composite methods."""

    settings: Any
    method: str
    schema_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "settings", normalize_generic_method_settings(self.settings)
        )

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(_method_content(self))

    @property
    def method_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def method_id(self) -> str:
        return f"{self.method}-discovery-method.v1:{self.method_hash}"


DiscoveryMethodDecoder = Callable[[Mapping[str, object]], DiscoveryMethodSpec]
DiscoveryMethodEncoder = Callable[[DiscoveryMethodSpec], Mapping[str, object]]

_DISCOVERY_METHOD_DECODERS: dict[str, DiscoveryMethodDecoder] = {}
_DISCOVERY_METHOD_ENCODERS: dict[str, DiscoveryMethodEncoder] = {}


def register_discovery_method_codec(
    method: str,
    *,
    decoder: DiscoveryMethodDecoder,
    encoder: DiscoveryMethodEncoder | None = None,
) -> None:
    """Register custom serialization codec for a Discovery method."""
    method_name = str(method).strip()
    if not method_name:
        raise ValueError("method name cannot be empty")
    _DISCOVERY_METHOD_DECODERS[method_name] = decoder
    if encoder is not None:
        _DISCOVERY_METHOD_ENCODERS[method_name] = encoder


def unregister_discovery_method_codec(method: str) -> None:
    """Unregister custom serialization codec for a Discovery method."""
    method_name = str(method).strip()
    _DISCOVERY_METHOD_DECODERS.pop(method_name, None)
    _DISCOVERY_METHOD_ENCODERS.pop(method_name, None)


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
                "seed_rt_gap_min": _float_text(settings.seed_rt_gap_min),
                "ms1_search_padding_min": _float_text(settings.ms1_search_padding_min),
                "rt_min": _float_text(settings.rt_min),
                "rt_max": _float_text(settings.rt_max),
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
            },
        }
        if (
            method.schema_version == "cid_nl_discovery_method_v1"
            and not method._legacy_blank_settings
        ):
            settings_content = cast(dict[str, object], content["settings"])
            for key in _SETTINGS_KEYS[-4:]:
                del settings_content[key]
        return content
    if method.method in _DISCOVERY_METHOD_ENCODERS:
        return dict(_DISCOVERY_METHOD_ENCODERS[method.method](method))
    if hasattr(method, "to_content_dict"):
        return dict(method.to_content_dict())
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


def _run_spec_from_content(content: Mapping[str, object]) -> ProcessingRunSpec:
    _require_keys(content, _RUN_CONTENT_KEYS, label="ProcessingRunSpec content")
    schema_version = _text(content["schema_version"], field="schema_version")
    if schema_version not in {
        "processing_run_spec_v1",
        PROCESSING_RUN_SPEC_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported ProcessingRunSpec schema_version")
    input_values = _list(content["acquisition_inputs"], label="acquisition_inputs")
    inputs: list[ProcessingAcquisitionInput] = []
    for value in input_values:
        item = _mapping(value, label="acquisition input")
        _require_keys(
            item,
            _ACQUISITION_INPUT_KEYS
            + (
                ("blank_library_sha256",)
                if schema_version == PROCESSING_RUN_SPEC_SCHEMA_VERSION
                else ()
            ),
            label="acquisition input",
        )
        source_value = _mapping(item["acquisition_source"], label="acquisition source")
        _require_keys(
            source_value, _ACQUISITION_SOURCE_KEYS, label="acquisition source"
        )
        inputs.append(
            ProcessingAcquisitionInput(
                input_ordinal=_integer(item["input_ordinal"], field="input_ordinal"),
                adapter_input_key=_text(
                    item["adapter_input_key"], field="adapter_input_key"
                ),
                sample_stem=_text(item["sample_stem"], field="sample_stem"),
                acquisition_source=AcquisitionSourceIdentity(
                    acquisition_source_id=_text(
                        source_value["acquisition_source_id"],
                        field="acquisition_source_id",
                    ),
                    content_sha256=_text(
                        source_value["content_sha256"], field="content_sha256"
                    ),
                    byte_size=_integer(source_value["byte_size"], field="byte_size"),
                    acquisition_format=_text(
                        source_value["acquisition_format"],
                        field="acquisition_format",
                    ),
                    observation_namespace=_text(
                        source_value["observation_namespace"],
                        field="observation_namespace",
                    ),
                ),
                blank_library_sha256=_optional_text(
                    item.get("blank_library_sha256"),
                    field="blank_library_sha256",
                ),
            )
        )
    method_envelope = _mapping(content["discovery_method"], label="discovery_method")
    _require_keys(method_envelope, _METHOD_ENVELOPE_KEYS, label="discovery_method")
    method_content = _mapping(
        method_envelope["content"], label="discovery method content"
    )
    method = _method_from_content(method_content)
    if method_envelope["method_hash"] != method.method_hash:
        raise ValueError("method_hash does not match canonical method content")
    if method_envelope["method_id"] != method.method_id:
        raise ValueError("method_id does not match canonical method content")
    profile_envelope = _mapping(
        content["signal_processing_profile"], label="signal_processing_profile"
    )
    _require_keys(
        profile_envelope, _PROFILE_ENVELOPE_KEYS, label="signal_processing_profile"
    )
    profile_content = _mapping(
        profile_envelope["content"], label="signal processing profile content"
    )
    profile = deserialize_signal_processing_profile(
        _canonical_json_bytes(profile_content),
        expected_profile_hash=_text(
            profile_envelope["profile_hash"], field="profile_hash"
        ),
        expected_profile_id=_text(profile_envelope["profile_id"], field="profile_id"),
    )
    return ProcessingRunSpec(
        acquisition_inputs=tuple(inputs),
        discovery_method=method,
        signal_processing_profile=profile,
        schema_version=schema_version,
    )


def _method_from_content(content: Mapping[str, object]) -> DiscoveryMethodSpec:
    _require_keys(content, _METHOD_CONTENT_KEYS, label="discovery method content")
    method_name = str(content["method"])
    if method_name in _DISCOVERY_METHOD_DECODERS:
        decoded = _DISCOVERY_METHOD_DECODERS[method_name](content)
        if not isinstance(decoded, DiscoveryMethodSpec):
            raise TypeError(
                f"Registered decoder for '{method_name}' must return a "
                "DiscoveryMethodSpec"
            )
        return decoded

    if method_name != "cid_nl":
        return GenericDiscoveryMethodSpec(
            settings=content["settings"],
            method=method_name,
            schema_version=str(content["schema_version"]),
        )

    from xic_extractor.discovery.evidence_config import (
        DiscoveryEvidenceProfile,
        DiscoveryEvidenceThresholds,
        DiscoveryEvidenceWeights,
    )
    from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile

    settings_value = _mapping(content["settings"], label="Discovery settings")
    legacy_original = (
        content["schema_version"] == "cid_nl_discovery_method_v1"
        and tuple(settings_value) == _SETTINGS_KEYS[:-4]
    )
    _require_keys(
        settings_value,
        _SETTINGS_KEYS[:-4] if legacy_original else _SETTINGS_KEYS,
        label="Discovery settings",
    )
    profile_values = _list(
        settings_value["neutral_loss_profiles"], label="neutral_loss_profiles"
    )
    profiles: list[NeutralLossProfile] = []
    for value in profile_values:
        profile_value = _mapping(value, label="neutral loss profile")
        _require_keys(
            profile_value,
            ("tag", "neutral_loss_da"),
            label="neutral loss profile",
        )
        profiles.append(
            NeutralLossProfile(
                tag=_text(profile_value["tag"], field="neutral loss tag"),
                neutral_loss_da=_float_from_text(
                    profile_value["neutral_loss_da"], field="neutral_loss_da"
                ),
            )
        )
    evidence_value = _mapping(
        settings_value["evidence_profile"], label="evidence_profile"
    )
    _require_keys(evidence_value, _EVIDENCE_PROFILE_KEYS, label="evidence_profile")
    weights_value = _mapping(evidence_value["weights"], label="evidence weights")
    _require_keys(weights_value, _WEIGHT_KEYS, label="evidence weights")
    thresholds_value = _mapping(
        evidence_value["thresholds"], label="evidence thresholds"
    )
    _require_keys(thresholds_value, _THRESHOLD_KEYS, label="evidence thresholds")
    weights = DiscoveryEvidenceWeights(
        **{key: _integer(weights_value[key], field=key) for key in _WEIGHT_KEYS}
    )
    thresholds = DiscoveryEvidenceThresholds(
        rt_aligned_max_min=_float_from_text(
            thresholds_value["rt_aligned_max_min"], field="rt_aligned_max_min"
        ),
        rt_near_max_min=_float_from_text(
            thresholds_value["rt_near_max_min"], field="rt_near_max_min"
        ),
        rt_shifted_max_min=_float_from_text(
            thresholds_value["rt_shifted_max_min"], field="rt_shifted_max_min"
        ),
        product_intensity_high_min=_float_from_text(
            thresholds_value["product_intensity_high_min"],
            field="product_intensity_high_min",
        ),
        product_intensity_med_min=_float_from_text(
            thresholds_value["product_intensity_med_min"],
            field="product_intensity_med_min",
        ),
        area_high_min=_float_from_text(
            thresholds_value["area_high_min"], field="area_high_min"
        ),
        area_med_min=_float_from_text(
            thresholds_value["area_med_min"], field="area_med_min"
        ),
        ms1_support_strong_area_min=_float_from_text(
            thresholds_value["ms1_support_strong_area_min"],
            field="ms1_support_strong_area_min",
        ),
        ms1_support_moderate_area_min=_float_from_text(
            thresholds_value["ms1_support_moderate_area_min"],
            field="ms1_support_moderate_area_min",
        ),
        scan_support_target=_integer(
            thresholds_value["scan_support_target"], field="scan_support_target"
        ),
        scan_support_high_score_min=_float_from_text(
            thresholds_value["scan_support_high_score_min"],
            field="scan_support_high_score_min",
        ),
        scan_support_low_score_max=_float_from_text(
            thresholds_value["scan_support_low_score_max"],
            field="scan_support_low_score_max",
        ),
    )
    evidence_profile = DiscoveryEvidenceProfile(
        name=cast(
            Literal["default"],
            _text(evidence_value["name"], field="evidence profile name"),
        ),
        weights=weights,
        thresholds=thresholds,
    )
    selected_values = _list(
        settings_value["selected_tag_names"], label="selected_tag_names"
    )
    settings = DiscoverySettings(
        neutral_loss_profiles=tuple(profiles),
        selected_tag_names=tuple(
            _text(value, field="selected_tag_names") for value in selected_values
        ),
        tag_combine_mode=cast(
            Literal["single", "union", "intersection"],
            _text(settings_value["tag_combine_mode"], field="tag_combine_mode"),
        ),
        nl_tolerance_ppm=_float_from_text(
            settings_value["nl_tolerance_ppm"], field="nl_tolerance_ppm"
        ),
        precursor_mz_tolerance_ppm=_float_from_text(
            settings_value["precursor_mz_tolerance_ppm"],
            field="precursor_mz_tolerance_ppm",
        ),
        product_mz_tolerance_ppm=_float_from_text(
            settings_value["product_mz_tolerance_ppm"],
            field="product_mz_tolerance_ppm",
        ),
        product_search_ppm=_float_from_text(
            settings_value["product_search_ppm"], field="product_search_ppm"
        ),
        ms2_precursor_tol_da=_float_from_text(
            settings_value["ms2_precursor_tol_da"], field="ms2_precursor_tol_da"
        ),
        nl_min_intensity_ratio=_float_from_text(
            settings_value["nl_min_intensity_ratio"],
            field="nl_min_intensity_ratio",
        ),
        seed_rt_gap_min=_float_from_text(
            settings_value["seed_rt_gap_min"], field="seed_rt_gap_min"
        ),
        ms1_search_padding_min=_float_from_text(
            settings_value["ms1_search_padding_min"],
            field="ms1_search_padding_min",
        ),
        rt_min=_float_from_text(settings_value["rt_min"], field="rt_min"),
        rt_max=_float_from_text(settings_value["rt_max"], field="rt_max"),
        resolver_mode=_text(settings_value["resolver_mode"], field="resolver_mode"),
        evidence_profile=evidence_profile,
        enable_blank_qualification=_boolean(
            settings_value.get("enable_blank_qualification", False),
            field="enable_blank_qualification",
        ),
        blank_library_path=_optional_text(
            settings_value.get("blank_library_path"),
            field="blank_library_path",
        ),
        blank_ppm_tol=_float_from_text(
            settings_value.get("blank_ppm_tol", _float_text(10.0)),
            field="blank_ppm_tol",
        ),
        blank_fold_change_threshold=_float_from_text(
            settings_value.get("blank_fold_change_threshold", _float_text(3.0)),
            field="blank_fold_change_threshold",
        ),
    )
    return CidNlDiscoveryMethodSpec(
        settings=settings,
        method=_text(content["method"], field="method"),
        schema_version=_text(content["schema_version"], field="schema_version"),
        _legacy_blank_settings=(
            content["schema_version"] == "cid_nl_discovery_method_v1"
            and not legacy_original
        ),
    )


def _validate_discovery_settings(settings: DiscoverySettings) -> None:
    from xic_extractor.discovery.evidence_config import (
        DiscoveryEvidenceProfile,
        DiscoveryEvidenceThresholds,
        DiscoveryEvidenceWeights,
    )
    from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
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
        "seed_rt_gap_min",
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
    if settings.blank_library_path is not None:
        if not isinstance(settings.blank_library_path, str):
            raise TypeError("blank_library_path must be text or None")
        if not settings.blank_library_path:
            raise ValueError("blank_library_path must be non-empty text or None")
    for field in ("rt_min", "rt_max"):
        value = getattr(settings, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric")
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"{field} must be finite and non-negative")
    if settings.rt_min > settings.rt_max:
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


def _decode_json_object(
    payload: bytes | str,
    *,
    label: str,
) -> tuple[bytes, Mapping[str, object]]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise TypeError(f"{label} payload must be bytes or str")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} payload must be UTF-8") from exc
    try:
        value = json.loads(decoded, object_pairs_hook=_unique_mapping)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} payload must be valid JSON") from exc
    return raw, _mapping(value, label=label)


def _require_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    actual = tuple(value)
    if actual != keys:
        raise ValueError(f"{label} fields must be exactly {keys}; got {actual}")


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be one JSON array")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def _required_text(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if not text:
        raise ValueError(f"{field} must be non-empty text")
    return text


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field=field)


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _float_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("canonical numeric fields must be numbers")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("canonical numeric fields must be finite")
    return (0.0 if number == 0.0 else number).hex()


def _float_from_text(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical hexadecimal text")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical hexadecimal text") from exc
    if not math.isfinite(number) or _float_text(number) != value:
        raise ValueError(f"{field} must be canonical finite hexadecimal text")
    return number


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _unique_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON payload has duplicate field: {key}")
        result[key] = value
    return result


__all__ = [
    "CID_NL_DISCOVERY_METHOD_ID_PREFIX",
    "CID_NL_DISCOVERY_METHOD_SCHEMA_VERSION",
    "PROCESSING_RUN_SPEC_ENVELOPE_VERSION",
    "PROCESSING_RUN_SPEC_ID_PREFIX",
    "PROCESSING_RUN_SPEC_SCHEMA_VERSION",
    "CidNlDiscoveryMethodSpec",
    "DiscoveryMethodDecoder",
    "DiscoveryMethodEncoder",
    "DiscoveryMethodSpec",
    "GenericDiscoveryMethodSpec",
    "GenericMethodSettings",
    "ProcessingAcquisitionInput",
    "ProcessingRunSpec",
    "canonical_cid_nl_method_bytes",
    "canonical_processing_run_spec_bytes",
    "compile_processing_run_spec",
    "deserialize_processing_run_spec",
    "normalize_generic_method_settings",
    "register_discovery_method_codec",
    "serialize_processing_run_spec",
    "unregister_discovery_method_codec",
]
