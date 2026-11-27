from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_sequence import read_analysis_set
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True)
class ResolvedTargetedInputs:
    raw_paths: tuple[Path, ...]
    injections: InjectionSequence
    sample_metadata: tuple[SampleMetadata, ...] = ()


def resolve_targeted_inputs(
    config: ExtractionConfig,
    *,
    injection_order: dict[str, int] | None,
) -> ResolvedTargetedInputs:
    analysis_set = read_analysis_set(
        config.data_dir,
        config.dll_dir,
        analysis="targeted",
        sequence_source=config.injection_order_source,
        include_context=False,
    )
    entries = sorted(analysis_set.analyzed, key=lambda entry: entry.raw_path)
    return ResolvedTargetedInputs(
        raw_paths=tuple(entry.raw_path for entry in entries),
        injections=(
            analysis_set.injections
            if injection_order is None
            else analysis_set.injections.with_orders(injection_order)
        ),
        sample_metadata=tuple(entry.metadata for entry in entries),
    )


def build_targeted_acquisition_sources(
    *,
    raw_paths: Sequence[Path],
    injections: InjectionSequence,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, AcquisitionSourceIdentity]:
    """Bind each Targeted RAW to its local file revision, keyed by RAW stem."""

    sources_by_stem: dict[str, AcquisitionSourceIdentity] = {}
    for raw_path in raw_paths:
        stem = raw_path.stem
        if stem in sources_by_stem:
            raise IdentityContractError(f"ambiguous acquisition source stem: {stem!r}")
        sources_by_stem[stem] = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            should_cancel=should_cancel,
        )
    expected_stems = set(sources_by_stem)
    actual_stems = set(injections.stems)
    if actual_stems != expected_stems:
        missing = sorted(expected_stems - actual_stems)
        unexpected = sorted(actual_stems - expected_stems)
        raise IdentityContractError(
            "injection order must bind every acquisition source exactly once; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    if not injections.has_unique_orders:
        raise IdentityContractError("injection_order values must be unique")
    return sources_by_stem
