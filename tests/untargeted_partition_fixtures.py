"""Dense partition replay fixtures for scientific output comparisons."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from tests.cid_nl_runtime_fixtures import (
    prepare_persisted_discovery_plan,
    representative_precursors,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    accepted_membership_risks,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
    untargeted_child_projection,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.output.alignment.tsv_writer import UntargetChildOutputs
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
)
from xic_extractor.untargeted_execution.execution import (
    materialize_untargeted_partition,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionResult,
)
from xic_extractor.untargeted_execution.partition_composition import (
    ChildProjectionContext,
    UntargetedPartitionInputs,
    compose_untargeted_partition,
    group_acquired_partition,
)

ALL_ROW_OUTPUTS = AlignmentRunOutputs(
    review_tsv=Path("review.tsv"),
    matrix_tsv=Path("matrix.tsv"),
    backfill_cell_evidence_tsv=Path("backfill.tsv"),
    cells_tsv=Path("cells.tsv"),
    status_matrix_tsv=Path("status.tsv"),
)


@dataclass(frozen=True, slots=True)
class ComposedChildOutputs(UntargetChildOutputs):
    """A child's published projection beside the child it was projected from,
    so scientific fixtures can inspect what the outputs were written from."""

    child: FinalizedUntargetApplicationResult | None = None


def project_composed_child(
    child: FinalizedUntargetApplicationResult,
    context: ChildProjectionContext,
    *,
    policy: UntargetedPublicationPolicy | None = None,
) -> ComposedChildOutputs:
    """The production projection of ``child`` into every TSV and the report."""

    outputs = untargeted_child_projection(
        ALL_ROW_OUTPUTS, policy or UntargetedPublicationPolicy()
    )(child, context)
    assert isinstance(outputs, UntargetChildOutputs)
    return ComposedChildOutputs(outputs.groups, outputs.explanation, child)


def composed_child_outputs(
    children: Iterable[FinalizedUntargetApplicationResult],
    initial_groups: Iterable[CrossSamplePeakGroupHypothesis],
    *,
    samples: tuple[SampleMetadata, ...],
    sample_rt_frame: SampleRtFrame | None = None,
    capture_evidence_retained: bool = False,
) -> tuple[ComposedChildOutputs, ...]:
    """Project in-memory children with the batch's samples and their
    partition's membership risks."""

    context = ChildProjectionContext(
        samples=samples,
        membership_risks_by_owner=accepted_membership_risks(initial_groups),
        sample_rt_frame=sample_rt_frame,
        capture_evidence_retained=capture_evidence_retained,
    )
    return tuple(project_composed_child(child, context) for child in children)


def children_of(
    batch: FinalizedUntargetBatchApplicationResult,
) -> tuple[FinalizedUntargetApplicationResult, ...]:
    """The finalized children a fixture batch's outputs were projected from."""

    children = tuple(
        output.child
        for output in batch.child_outputs
        if isinstance(output, ComposedChildOutputs)
    )
    assert len(children) == len(batch.child_outputs)
    return tuple(child for child in children if child is not None)


def has_flagged_backfill_risk(
    children: Iterable[FinalizedUntargetApplicationResult],
) -> bool:
    return any(
        risk.risk_flags
        for child in children
        for _, _, risk in (
            child.scientific_core.grouping_result.backfill_membership_risks
        )
    )


def build_dense_partition(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
) -> UntargetedPartitionInputs:
    """Prepare one dense partition from a persisted Discovery batch."""

    plan = prepare_persisted_discovery_plan(
        raw_paths=raw_paths,
        sample_metadata=sample_metadata,
        settings=settings,
        peaks_by_sample=peaks_by_sample,
        dll_dir=dll_dir,
        signal_processing_profile=signal_processing_profile,
        root=raw_paths[0].parent / "persisted",
    )
    return materialize_untargeted_partition(plan, plan.subject_batch)


def execute_partition_inline(*args, **kwargs) -> UntargetedPartitionResult:
    """Run one partition on a single in-process worker."""
    with WorkerAdmissionPool(max_workers=1, backend="thread") as pool:
        return execute_partition(*args, pool=pool, **kwargs)


def execute_dense_partition(
    batch: UntargetedPartitionInputs,
    *,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
    known_istd_reference: KnownIstdReference | None = None,
) -> UntargetedPartitionResult:
    """Run one dense partition through the production sample-batch runtime."""

    result = execute_partition_inline(
        batch.jobs,
        partition_key="dense-fixture",
        progress_callback=progress_callback,
        alignment_config=alignment_config,
        prepare_grouping=lambda results: group_acquired_partition(
            batch,
            results,
            alignment_config=alignment_config,
            known_istd_reference=known_istd_reference,
            progress_callback=progress_callback,
        ),
    )
    return result


def complete_dense_partition(
    batch, runtime_result, *, alignment_config, known_istd_reference=None
):
    if not runtime_result.succeeded:
        raise RuntimeError("cannot compose a partial dense partition")
    batch = replace(batch, prepared_grouping=runtime_result.prepared_grouping)
    partition = compose_untargeted_partition(
        batch,
        runtime_result.sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    context = batch.context
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        method_subjects=context.method_subject,
        samples=batch.context.samples,
        evidence_providers=tuple(
            EvidenceProviderBinding(CID_NL_PROVIDER_ID, subject.method_subject_id)
            for subject in batch.subject_batch.subjects
        ),
        declared_candidate_ids=tuple(
            cid for cid, _ in batch.subject_batch.candidate_memberships
        ),
        capture_bindings=partition.capture_bindings,
    )
    application_result = FinalizedUntargetBatchApplicationResult(
        scope=scope,
        processing_attempt_id=context.processing_attempt_id,
        acquisition_sources=tuple(
            context.acquisition_sources_by_sample[sample.raw_stem].source
            for sample in context.samples
        ),
        child_outputs=composed_child_outputs(
            partition.children,
            partition.initial_groups,
            samples=context.samples,
            capture_evidence_retained=bool(partition.capture_evidence),
        ),
        excluded_candidates=partition.excluded_candidates,
        has_flagged_backfill_risk=has_flagged_backfill_risk(partition.children),
    )
    return CompletedUntargetBatchWorkflowResult(
        application_result=application_result,
        method_assessments=partition.method_assessments,
        initial_groups=partition.initial_groups,
        capture_evidence=partition.capture_evidence,
    ).bind_publication_inputs(
        peaks_by_sample=context.peaks_by_sample,
        precursor_mz_by_candidate=representative_precursors(context.peaks_by_sample),
        neutral_loss_by_tag=context.settings.neutral_loss_by_tag,
        alignment_config=alignment_config,
    )


def fixture_assessment(result):
    """Inspect retained sample facts for scientific fixture expectations."""
    if result.backfill_attempt is not None:
        return result.backfill_attempt.assessment
    return result.adapter_result.assessment_input.assessment


def physical_peak_keys(
    batch: FinalizedUntargetBatchApplicationResult,
) -> tuple[str, ...]:
    """Every final member's physical peak key, in the batch's child order."""

    return tuple(
        content.physical_peak_key
        for child in children_of(batch)
        for group in child.scientific_core.final_population
        for member in group.member_subjects
        for content in member.physical_peaks
    )
