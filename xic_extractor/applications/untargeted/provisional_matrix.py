"""Full-universe Untargeted Discovery-to-provisional-Matrix orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill.peak_fact import BackfillWindowStrategy
from xic_extractor.alignment.config import (
    AlignmentConfig,
    BackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.applications.untargeted.application import (
    run_untargeted_application,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    RetainedAlignmentOptions,
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.execution_profile import (
    DEFAULT_ALIGNMENT_WORKERS,
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.input_planning import (
    is_pure_single_provider_cid_nl,
)


@dataclass(frozen=True, slots=True)
class UntargetedProvisionalMatrixRun:
    discovery_index: Path
    alignment_outputs: AlignmentRunOutputs


def run_untargeted_provisional_matrix_workflow(
    *,
    raw_files: tuple[Path, ...],
    dll_dir: Path,
    output_dir: Path,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_xic_batch_size: int = 1,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    owner_backfill_xic_backend: BackfillXicBackend = "raw",
    owner_backfill_window_strategy: BackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    sample_column_injection_order: Path | None = None,
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    timing_recorder: TimingRecorder | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    capture_alignment_artifact_fingerprints: bool = False,
) -> UntargetedProvisionalMatrixRun:
    """Run every fresh Discovery candidate through existing full Alignment."""

    if not is_pure_single_provider_cid_nl(discovery_settings):
        raise ValueError(
            "provisional review supports only pure single-provider CID-NL; "
            "use the dna_dr preset"
        )
    unsupported: list[str] = []
    if raw_xic_batch_size != 1:
        unsupported.append("raw_xic_batch_size")
    if owner_backfill_xic_backend != "raw":
        unsupported.append("owner_backfill_xic_backend")
    if owner_backfill_window_strategy != "exact":
        unsupported.append("owner_backfill_window_strategy")
    if owner_backfill_superwindow_span_factor != 2:
        unsupported.append("owner_backfill_superwindow_span_factor")
    if sample_column_injection_order is not None:
        unsupported.append("sample_column_injection_order")
    if unsupported:
        raise ValueError(
            "pure CID-NL retained route does not support legacy Alignment options: "
            + ", ".join(unsupported)
        )
    execution = UntargetedExecutionPolicy(raw_workers=raw_workers)
    legacy = RetainedAlignmentOptions(
        owner_build_xic_backend=owner_build_xic_backend,
        audit_evidence_mode="none",
        require_discovery_owner_closure=True,
    )

    if not raw_files:
        raise ValueError("full-universe workflow requires at least one RAW file")
    if any(not path.is_file() for path in raw_files):
        raise ValueError("full-universe workflow RAW input does not exist")
    raw_dir = raw_files[0].parent
    if any(path.parent != raw_dir for path in raw_files):
        raise ValueError("full-universe workflow requires one RAW directory")
    if not dll_dir.is_dir():
        raise ValueError("full-universe workflow Thermo DLL directory does not exist")
    if output_dir.exists():
        raise ValueError("full-universe workflow output already exists")

    output_dir.mkdir(parents=True)
    discovery_dir = output_dir / "discovery"
    alignment_dir = output_dir / "alignment"
    peak_config = _peak_config(
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=discovery_dir,
        settings=discovery_settings,
    )
    discovery = run_discovery_batch(
        raw_files,
        output_dir=discovery_dir,
        settings=discovery_settings,
        peak_config=peak_config,
        timing_recorder=timing_recorder,
        max_workers=raw_workers,
        progress_callback=lambda completed, total, sample_stem: _progress(
            progress_callback,
            "discovery_sample_complete",
            completed=completed,
            total=total,
            sample_stem=sample_stem,
        ),
    )
    _progress(progress_callback, "discovery_complete")

    outputs = run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=discovery.batch_index_csv,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=alignment_dir,
            alignment_config=alignment_config,
            peak_config=peak_config,
            discovery_settings=discovery_settings,
            sample_metadata=sample_metadata,
            execution=execution,
            publication=UntargetedPublicationPolicy(
                output_level="validation-minimal",
                capture_artifact_fingerprints=capture_alignment_artifact_fingerprints,
                emit_provisional_matrix=True,
            ),
            legacy=legacy,
        ),
        UntargetedExecutionContext(timing_recorder=timing_recorder),
    )
    if (
        outputs.provisional_matrix_tsv is None
        or outputs.provisional_identity_tsv is None
        or outputs.provisional_cell_provenance_tsv is None
        or outputs.provisional_summary_json is None
    ):
        raise RuntimeError("full-universe Alignment omitted provisional outputs")
    _progress(
        progress_callback,
        "alignment_complete",
        provisional_matrix=str(outputs.provisional_matrix_tsv),
    )
    return UntargetedProvisionalMatrixRun(
        discovery_index=discovery.batch_index_csv,
        alignment_outputs=outputs,
    )


def _peak_config(
    *,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "unused_xic_results.csv",
        diagnostics_csv=output_dir / "unused_xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        ms1_morphology_smoothing_window_points=DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
    )


def _progress(
    callback: Callable[[Mapping[str, object]], None] | None,
    event: str,
    **values: object,
) -> None:
    if callback is not None:
        callback({"event": event, **values})


__all__ = (
    "UntargetedProvisionalMatrixRun",
    "run_untargeted_provisional_matrix_workflow",
)
