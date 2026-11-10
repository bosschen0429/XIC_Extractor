from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.analysis_set import AnalysisSet
from xic_extractor.configuration.models import ExtractionConfig
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
    injection_order: dict[str, int]
    sample_metadata: tuple[SampleMetadata, ...] = ()


def resolve_targeted_inputs(
    config: ExtractionConfig,
    *,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
) -> ResolvedTargetedInputs:
    by_path = {entry.raw_path: entry for entry in _analysis_set(config).entries}
    selected = [by_path[path] for path in raw_paths]
    return ResolvedTargetedInputs(
        injection_order=(
            injection_order
            if injection_order is not None
            else {entry.raw_stem: entry.injection_order for entry in selected}
        ),
        sample_metadata=tuple(entry.metadata for entry in selected),
    )


def resolve_targeted_raw_paths(config: ExtractionConfig) -> list[Path]:
    return sorted(_analysis_set(config).raw_paths)


def _analysis_set(config: ExtractionConfig) -> AnalysisSet:
    # 上機資訊 annotates roles; the RAW headers own order and sample names.
    return read_analysis_set(
        config.data_dir,
        config.dll_dir,
        sequence_source=config.injection_order_source,
        include_context=False,
    )


def build_targeted_acquisition_sources(
    *,
    raw_paths: Sequence[Path],
    injection_order: Mapping[str, int],
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
    actual_stems = set(injection_order)
    if actual_stems != expected_stems:
        missing = sorted(expected_stems - actual_stems)
        unexpected = sorted(actual_stems - expected_stems)
        raise IdentityContractError(
            "injection order must bind every acquisition source exactly once; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    if len(set(injection_order.values())) != len(injection_order):
        raise IdentityContractError("injection_order values must be unique")
    return sources_by_stem
