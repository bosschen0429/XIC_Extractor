"""Bind RAW files and blank libraries into a Discovery run specification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.discovery.blank_qualification import (
    resolve_background_library,
    serialize_background_library,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.workers import iter_thread_results
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    IdentityContractError,
    build_local_acquisition_source_identity,
)
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    validate_cid_nl_discovery_settings,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    bind_expected_path_identity,
    stat_identity,
)


def compile_discovery_processing_run_spec(
    raw_paths: tuple[Path, ...],
    *,
    discovery_settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> ProcessingRunSpec:
    """Validate settings, fingerprint each RAW file, then compile."""

    validate_cid_nl_discovery_settings(discovery_settings)
    expectations = fingerprint_acquisition_sources(raw_paths)
    libraries: dict[str, str | None] = {}
    inputs = tuple(
        ProcessingAcquisitionInput(
            raw_path=path,
            acquisition_source=expectation.source,
            blank_library_content=_blank_library_content(
                path, discovery_settings, libraries
            ),
        )
        for path, expectation in zip(raw_paths, expectations, strict=True)
    )
    for field, values in (
        ("sample_stem", tuple(path.stem for path in raw_paths)),
        (
            "acquisition_source_id",
            tuple(item.source.acquisition_source_id for item in expectations),
        ),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate ProcessingRunSpec {field}")
    return ProcessingRunSpec(
        acquisition_inputs=inputs,
        settings=discovery_settings,
        signal_processing_profile=signal_processing_profile,
    )


def fingerprint_acquisition_sources(
    raw_paths: tuple[Path, ...],
    *,
    fingerprint_threads: int = 1,
    verified_acquisition_sources: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
) -> tuple[AcquisitionSourceExpectation, ...]:
    """Fingerprint each RAW file and retain its exact local path state."""

    def fingerprint(path: Path) -> AcquisitionSourceExpectation:
        if verified_acquisition_sources is not None:
            expectation = verified_acquisition_sources[path.stem]
            bind_expected_path_identity(
                path, expectation.source, expectation.path_identity
            )
            return expectation
        before = stat_identity(path.stat())
        source = build_local_acquisition_source_identity(
            path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        if stat_identity(path.stat()) != before:
            raise IdentityContractError(
                "acquisition source changed during run-spec fingerprinting"
            )
        return AcquisitionSourceExpectation(source=source, path_identity=before)

    return tuple(
        iter_thread_results(fingerprint, raw_paths, max_workers=fingerprint_threads)
    )


def _blank_library_content(
    raw_path: Path,
    settings: DiscoverySettings,
    cache: dict[str, str | None],
) -> str | None:
    if not settings.enable_blank_qualification:
        return None
    key = settings.blank_library_path or str(raw_path.parent)
    if key not in cache:
        library = resolve_background_library(
            raw_dir=raw_path.parent,
            library_path=settings.blank_library_path,
        )
        cache[key] = None if library is None else serialize_background_library(library)
    return cache[key]


__all__ = [
    "compile_discovery_processing_run_spec",
    "fingerprint_acquisition_sources",
]
