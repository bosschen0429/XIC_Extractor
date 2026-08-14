"""Discovery filesystem adapter for the pure ProcessingRunSpec owner."""

from __future__ import annotations

from pathlib import Path

from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.processing_run_spec import (
    CidNlDiscoveryMethodSpec,
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    compile_processing_run_spec,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


def compile_discovery_processing_run_spec(
    raw_paths: tuple[Path, ...],
    *,
    discovery_settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> ProcessingRunSpec:
    """Validate method/profile, fingerprint each path once, then compile."""

    if not isinstance(raw_paths, tuple) or not raw_paths:
        raise ValueError("raw_paths must be a non-empty Path tuple")
    if any(not isinstance(path, Path) for path in raw_paths):
        raise TypeError("raw_paths must contain Path values")

    # Fail before touching the filesystem for invalid semantic inputs.
    CidNlDiscoveryMethodSpec(settings=discovery_settings)
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")

    inputs = tuple(
        ProcessingAcquisitionInput(
            input_ordinal=ordinal,
            adapter_input_key=path.name,
            sample_stem=path.stem,
            acquisition_source=build_acquisition_source_identity(
                path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            ),
        )
        for ordinal, path in enumerate(raw_paths)
    )
    return compile_processing_run_spec(
        inputs,
        discovery_settings=discovery_settings,
        signal_processing_profile=signal_processing_profile,
    )


def processing_run_input_for_path(
    spec: ProcessingRunSpec,
    raw_path: Path,
    *,
    input_ordinal: int,
) -> ProcessingAcquisitionInput:
    """Validate a runtime path against an already-fingerprinted binding."""

    if not isinstance(spec, ProcessingRunSpec):
        raise TypeError("run_spec must be a ProcessingRunSpec")
    if not isinstance(raw_path, Path):
        raise TypeError("raw_path must be a Path")
    if (
        isinstance(input_ordinal, bool)
        or not isinstance(input_ordinal, int)
        or input_ordinal < 0
        or input_ordinal >= len(spec.acquisition_inputs)
    ):
        raise ValueError("run_spec input_ordinal is out of range")
    item = spec.acquisition_inputs[input_ordinal]
    if item.adapter_input_key != raw_path.name or item.sample_stem != raw_path.stem:
        raise ValueError("runtime RAW path does not match ProcessingRunSpec input")
    return item


def validate_processing_run_spec_call(
    spec: ProcessingRunSpec,
    raw_paths: tuple[Path, ...],
    *,
    discovery_settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
) -> tuple[ProcessingAcquisitionInput, ...]:
    """Close compatibility arguments against one supplied immutable spec."""

    if not isinstance(spec, ProcessingRunSpec):
        raise TypeError("run_spec must be a ProcessingRunSpec")
    if not isinstance(raw_paths, tuple) or not raw_paths:
        raise ValueError("raw_paths must be a non-empty Path tuple")
    if len(raw_paths) != len(spec.acquisition_inputs):
        raise ValueError("ProcessingRunSpec input cardinality does not match call")
    if discovery_settings != spec.discovery_method.settings:
        raise ValueError("Discovery settings conflict with ProcessingRunSpec")
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")
    if signal_processing_profile != spec.signal_processing_profile:
        raise ValueError("peak config conflicts with ProcessingRunSpec profile")
    return tuple(
        processing_run_input_for_path(spec, path, input_ordinal=ordinal)
        for ordinal, path in enumerate(raw_paths)
    )


__all__ = [
    "compile_discovery_processing_run_spec",
    "processing_run_input_for_path",
    "validate_processing_run_spec_call",
]
