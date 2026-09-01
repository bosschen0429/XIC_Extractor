"""Owner-closed production input assembly for pure Untargeted CID-NL."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeApplicationResult,
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class UntargetedCidNlProductionBatch:
    """One complete request context and its canonical sample/subject jobs."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedCidNlRealDataContext
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]


def is_pure_single_provider_cid_nl(settings: DiscoverySettings) -> bool:
    """Return whether settings select the production-owned pure CID-NL route."""

    if not isinstance(settings, DiscoverySettings):
        return False
    return (
        settings.tag_combine_mode == "single"
        and len(settings.neutral_loss_profiles) == 1
        and len(settings.selected_tag_names) == 1
        and settings.selected_tag_names[0] == settings.neutral_loss_profiles[0].tag
    )


def build_untargeted_cid_nl_production_batch(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt,
    ],
    dll_dir: Path,
    sample_identity_namespace: str,
) -> UntargetedCidNlProductionBatch:
    """Seal exact candidate membership before any subject-local runtime work."""

    if not is_pure_single_provider_cid_nl(settings):
        raise ValueError(
            "Untargeted CID-NL production requires a pure single-provider route"
        )
    if not isinstance(dll_dir, Path):
        raise TypeError("dll_dir must be a Path")
    candidates = tuple(
        candidate
        for sample_candidates in candidates_by_sample.values()
        for candidate in sample_candidates
    )
    if not candidates:
        raise ValueError("Untargeted CID-NL production requires candidates")
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Discovery candidate identity must be globally unique")

    subject_by_key: dict[tuple[float, float], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float]]] = []
    for candidate in candidates:
        precursor_mz = float(candidate.precursor_mz)
        neutral_loss_da = float(candidate.configured_neutral_loss_da)
        if (
            not math.isfinite(precursor_mz)
            or precursor_mz <= 0.0
            or not math.isfinite(neutral_loss_da)
            or neutral_loss_da <= 0.0
        ):
            raise ValueError("Discovery candidate lacks finite CID-NL owner facts")
        key = (precursor_mz, neutral_loss_da)
        subject_by_key.setdefault(
            key,
            mint_untargeted_cid_nl_method_subject(
                precursor_mz=precursor_mz,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=neutral_loss_da,
            ),
        )
        membership_keys.append((candidate.candidate_id, key))

    subjects = tuple(
        sorted(subject_by_key.values(), key=lambda subject: subject.method_subject_id)
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            sorted(
                (
                    candidate_id,
                    subject_by_key[key].method_subject_id,
                )
                for candidate_id, key in membership_keys
            )
        ),
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=sample_metadata,
        method_subject=subject_batch,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=discovery_receipts_by_sample,
        sample_identity_namespace=sample_identity_namespace,
    )
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=dll_dir,
            work_unit_key=(
                "untargeted-cid-nl:"
                f"{sample.metadata.raw_stem}:{subject.method_subject_id}"
            ),
        )
        for sample in context.samples
        for subject in subject_batch.subjects
    )
    return UntargetedCidNlProductionBatch(
        subject_batch=subject_batch,
        context=context,
        jobs=jobs,
    )


def execute_untargeted_cid_nl_production_batch(
    batch: UntargetedCidNlProductionBatch,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedCidNlRealDataRuntimeApplicationResult:
    """Execute the sealed universe once and enforce its per-sample phase bound."""

    if not isinstance(batch, UntargetedCidNlProductionBatch):
        raise TypeError("batch must be an UntargetedCidNlProductionBatch")
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation=(
            f"{batch.context.processing_owners.request.processing_request_id}:"
            "cid-nl-production-v1"
        ),
    )
    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        batch.jobs,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        progress_callback=progress_callback,
    )
    if not result.succeeded or len(result.sample_results) != len(batch.jobs):
        raise RuntimeError("Untargeted CID-NL production did not complete atomically")
    call_cost = untargeted_cid_nl_runtime_call_cost(result.finalization)
    sample_count = len(batch.context.samples)
    if (
        call_cost["raw_open_count"] > sample_count * 2
        or call_cost["xic_batch_count"] > sample_count * 2
    ):
        raise RuntimeError(
            "Untargeted CID-NL production exceeded initial/recovery sample phases"
        )
    return result


__all__ = [
    "UntargetedCidNlProductionBatch",
    "build_untargeted_cid_nl_production_batch",
    "execute_untargeted_cid_nl_production_batch",
    "is_pure_single_provider_cid_nl",
]
