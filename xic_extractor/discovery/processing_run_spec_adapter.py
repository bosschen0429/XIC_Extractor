"""Discovery filesystem adapter for the pure ProcessingRunSpec owner."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundLibrary,
    resolve_background_library,
    serialize_background_library,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    IdentityContractError,
    build_acquisition_source_identity,
)
from xic_extractor.processing_run_spec import (
    CidNlDiscoveryMethodSpec,
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    compile_processing_run_spec,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    _stat_identity,
)


class _BlankLibraryBinding(TypedDict):
    blank_library_sha256: str | None
    blank_library_content: str | None


_LibrarySnapshots = dict[int, tuple[BlankBackgroundLibrary, str, str]]


def compile_discovery_processing_run_spec(
    raw_paths: tuple[Path, ...],
    *,
    discovery_settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> ProcessingRunSpec:
    """Validate method/profile, fingerprint each path once, then compile."""

    spec, _expectations = compile_discovery_processing_run_spec_with_expectations(
        raw_paths,
        discovery_settings=discovery_settings,
        signal_processing_profile=signal_processing_profile,
    )
    return spec


def compile_discovery_processing_run_spec_with_expectations(
    raw_paths: tuple[Path, ...],
    *,
    discovery_settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
    fingerprint_workers: int = 1,
) -> tuple[ProcessingRunSpec, tuple[AcquisitionSourceExpectation, ...]]:
    """Compile a run spec and retain each fingerprint's exact local path state."""

    if not isinstance(raw_paths, tuple) or not raw_paths:
        raise ValueError("raw_paths must be a non-empty Path tuple")
    if any(not isinstance(path, Path) for path in raw_paths):
        raise TypeError("raw_paths must contain Path values")
    if type(fingerprint_workers) is not int or fingerprint_workers < 1:
        raise ValueError("fingerprint_workers must be a positive integer")

    # Fail before touching the filesystem for invalid semantic inputs.
    CidNlDiscoveryMethodSpec(settings=discovery_settings)
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")

    snapshots: dict[str, tuple[str | None, str | None]] = {}
    libraries: _LibrarySnapshots = {}
    inputs = []
    expectations = []

    def fingerprint(path: Path) -> AcquisitionSourceExpectation:
        before = _stat_identity(path.stat())
        source = build_acquisition_source_identity(
            path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        if _stat_identity(path.stat()) != before:
            raise IdentityContractError(
                "acquisition source changed during run-spec fingerprinting"
            )
        return AcquisitionSourceExpectation(source=source, path_identity=before)

    with ExitStack() as stack:
        if fingerprint_workers > 1:
            executor = stack.enter_context(
                ThreadPoolExecutor(max_workers=min(fingerprint_workers, len(raw_paths)))
            )
            verified = tuple(executor.map(fingerprint, raw_paths))
        else:
            verified = tuple(map(fingerprint, raw_paths))
    for ordinal, (path, expectation) in enumerate(
        zip(raw_paths, verified, strict=True)
    ):
        inputs.append(
            ProcessingAcquisitionInput(
                input_ordinal=ordinal,
                adapter_input_key=path.name,
                sample_stem=path.stem,
                acquisition_source=expectation.source,
                **_blank_library_binding(
                    path, discovery_settings, snapshots, libraries
                ),
            )
        )
        expectations.append(expectation)
    spec = compile_processing_run_spec(
        tuple(inputs),
        discovery_settings=discovery_settings,
        signal_processing_profile=signal_processing_profile,
    )
    return spec, tuple(expectations)


def compile_discovery_processing_run_spec_from_config(
    raw_paths: tuple[Path, ...],
    discovery_settings: DiscoverySettings,
    extraction_config: ExtractionConfig,
) -> ProcessingRunSpec:
    """Resolve the canonical profile from config, then compile one run spec."""

    profile = resolve_signal_processing_profile(
        signal_processing_method=extraction_config.signal_processing_method,
        signal_processing_window_points=(
            extraction_config.signal_processing_window_points
        ),
    )
    return compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=discovery_settings,
        signal_processing_profile=profile,
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


def _blank_library_binding(
    raw_path: Path,
    settings: DiscoverySettings,
    cache: dict[str, tuple[str | None, str | None]],
    libraries: _LibrarySnapshots,
) -> _BlankLibraryBinding:
    if not settings.enable_blank_qualification:
        return {"blank_library_sha256": None, "blank_library_content": None}
    key = settings.blank_library_path or str(raw_path.parent)
    if key not in cache:
        library = resolve_background_library(
            raw_dir=raw_path.parent,
            library_path=settings.blank_library_path,
        )
        if library is None:
            cache[key] = None, None
        else:
            # Different sample directories can select the same packaged object.
            # Retain that object so its identity cannot be recycled in this call.
            if id(library) not in libraries:
                content = serialize_background_library(library)
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                libraries[id(library)] = library, digest, content
            _, digest, content = libraries[id(library)]
            cache[key] = digest, content
    bound_digest, bound_content = cache[key]
    return {
        "blank_library_sha256": bound_digest,
        "blank_library_content": bound_content,
    }


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
    inputs = tuple(
        processing_run_input_for_path(spec, path, input_ordinal=ordinal)
        for ordinal, path in enumerate(raw_paths)
    )
    if not discovery_settings.enable_blank_qualification:
        return inputs
    hydrated: list[ProcessingAcquisitionInput] = []
    snapshots: dict[str, tuple[str | None, str | None]] = {}
    libraries: _LibrarySnapshots = {}
    for path, item in zip(raw_paths, inputs, strict=True):
        # None digest is a bound absence, not permission to select a new library.
        if item.blank_library_sha256 is None or item.blank_library_content is not None:
            hydrated.append(item)
            continue
        binding = _blank_library_binding(path, discovery_settings, snapshots, libraries)
        if binding["blank_library_sha256"] != item.blank_library_sha256:
            raise ValueError(
                "selected blank library content does not match ProcessingRunSpec"
            )
        hydrated.append(replace(item, **binding))
    return tuple(hydrated)


__all__ = [
    "compile_discovery_processing_run_spec",
    "compile_discovery_processing_run_spec_with_expectations",
    "compile_discovery_processing_run_spec_from_config",
    "processing_run_input_for_path",
    "validate_processing_run_spec_call",
]
