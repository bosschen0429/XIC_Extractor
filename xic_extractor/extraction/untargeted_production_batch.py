"""Owner-closed production input assembly for pure Untargeted CID-NL."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, replace
from itertools import zip_longest
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.backfill.evaluation_coverage import (
    ConservativeNoRequestProofBuilder,
    OwnerBackfillPlanningPosition,
    build_owner_backfill_evaluation_coverage,
    mint_owner_backfill_planning_epoch,
    mint_owner_backfill_position_id,
)
from xic_extractor.alignment.cell_observations import (
    CellObservationRef,
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillRecoveryCandidateUniverse,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_recovery_candidate_universe,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_workflow import (
    build_initial_cid_nl_cross_sample_groups,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.mode_track import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
    UntargetedCandidateExclusion,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.workflow import (
    CaptureWorkflowArguments,
    CompletedUntargetBatchWorkflowResult,
    ModeTrackWorkflowArguments,
    UntargetWorkflowAdapter,
    UntargetWorkflowBundle,
    UntargetWorkflowCompositionRequest,
    compose_untarget_batch_after_runtime,
    compose_untarget_subject,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.execution_settings import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    adapt_current_discovery_sample_subject,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_result_store import (
    PartitionResultStore,
    SealedPartition,
)
from xic_extractor.extraction.untargeted_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeApplicationResult,
    UntargetedCidNlRealDataRuntimeJob,
    UntargetedCidNlRuntimeSampleResult,
    _RuntimeResultContext,
    build_untargeted_cid_nl_sample_runtime_jobs,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlComposition,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_alignment_subject_worksets,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
    require_capture_binding,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.sample_metadata import (
    SampleMetadata,
)


@dataclass(frozen=True, slots=True)
class UntargetedCidNlProductionBatch:
    """Partition inputs, executable jobs, and optional sparse recovery templates."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedCidNlRealDataContext
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    sample_job_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...] = ()


@dataclass(frozen=True, slots=True)
class UntargetedCidNlProductionPlan:
    """Global scientific inputs with one validated job template per sample."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedCidNlRealDataContext
    sample_job_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    runtime_subjects: tuple[CidNlMethodSubject, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    partition_input_loader: (
        Callable[
            [tuple[str, ...]],
            tuple[
                dict[str, tuple[DiscoveryCandidate, ...]],
                dict[str, DiscoveryPhysicalFactRecord],
                dict[str, tuple[DiscoveryCandidate, ...]],
            ],
        ]
        | None
    ) = None
    dll_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class PreparedCidNlAlignmentSubjectWorksets:
    """One plan's complete Alignment worksets and partition lookup indexes."""

    plan: UntargetedCidNlProductionPlan
    worksets: tuple[tuple[CidNlMethodSubject, ...], ...]
    subjects_by_id: dict[str, CidNlMethodSubject]
    workset_ids_by_subject: dict[str, frozenset[str]]
    memberships_by_subject: dict[str, tuple[tuple[str, str], ...]]

    def subject_batch(
        self,
        subjects: tuple[CidNlMethodSubject, ...],
    ) -> UntargetedCidNlMethodSubjectBatch:
        """Bind whole prepared worksets to their exact candidate memberships."""

        requested_ids: set[str] = set()
        touched_worksets: set[frozenset[str]] = set()
        for subject in subjects:
            expected = self.subjects_by_id.get(subject.method_subject_id)
            if expected is None or subject != expected:
                raise ValueError(
                    "CID-NL partition contains a foreign alignment subject"
                )
            requested_ids.add(subject.method_subject_id)
            touched_worksets.add(self.workset_ids_by_subject[subject.method_subject_id])
        if len(requested_ids) != len(subjects):
            raise ValueError("CID-NL partition contains duplicate alignment subjects")
        if any(not workset_ids <= requested_ids for workset_ids in touched_worksets):
            raise ValueError(
                "CID-NL partition cannot split one alignment subject workset"
            )
        return UntargetedCidNlMethodSubjectBatch(
            subjects=subjects,
            candidate_memberships=tuple(
                sorted(
                    membership
                    for subject in subjects
                    for membership in self.memberships_by_subject[
                        subject.method_subject_id
                    ]
                )
            ),
        )


def prepare_untargeted_cid_nl_alignment_subject_worksets(
    plan: UntargetedCidNlProductionPlan,
    *,
    known_istd_reference: KnownIstdReference | None = None,
) -> PreparedCidNlAlignmentSubjectWorksets:
    """Prepare the complete workset closure once before partition materialization."""

    if type(plan) is not UntargetedCidNlProductionPlan:
        raise TypeError("Alignment subject worksets require a production plan")
    worksets = cid_nl_alignment_subject_worksets(
        plan.runtime_subjects,
        comparison_mz_intervals=_known_istd_comparison_intervals(known_istd_reference),
    )
    subjects_by_id = {
        subject.method_subject_id: subject
        for workset in worksets
        for subject in workset
    }
    workset_ids_by_subject = {
        subject.method_subject_id: frozenset(
            member.method_subject_id for member in workset
        )
        for workset in worksets
        for subject in workset
    }
    memberships: dict[str, list[tuple[str, str]]] = {
        subject_id: [] for subject_id in subjects_by_id
    }
    for membership in plan.subject_batch.candidate_memberships:
        subject_memberships = memberships.get(membership[1])
        if subject_memberships is not None:
            subject_memberships.append(membership)
    memberships_by_subject = {
        subject_id: tuple(sorted(subject_memberships))
        for subject_id, subject_memberships in memberships.items()
    }
    if any(
        not subject_memberships
        for subject_memberships in memberships_by_subject.values()
    ):
        raise ValueError("runtime alignment subject has no candidate membership")
    return PreparedCidNlAlignmentSubjectWorksets(
        plan=plan,
        worksets=worksets,
        subjects_by_id=subjects_by_id,
        workset_ids_by_subject=workset_ids_by_subject,
        memberships_by_subject=memberships_by_subject,
    )


def _known_istd_comparison_intervals(
    reference: KnownIstdReference | None,
) -> tuple[tuple[float, float], ...]:
    """Co-locate possible known identities without assigning identity by mass."""
    if reference is None:
        return ()
    return tuple(
        (
            target.mz * (1 - target.ppm_tol / 1_000_000),
            target.mz * (1 + target.ppm_tol / 1_000_000),
        )
        for target in reference.anchor.targets
        if target.neutral_loss_da is not None
        and target.label in reference.anchor.observed_labels
    )


def is_pure_single_provider_cid_nl(settings: DiscoverySettings) -> bool:
    """Return whether settings select the production-owned pure CID-NL route."""

    if not isinstance(settings, DiscoverySettings):
        return False
    try:
        CidNlDiscoveryMethodSpec(settings=settings)
    except (TypeError, ValueError):
        return False
    return True


def is_supported_alignment_discovery(settings: Any) -> bool:
    """Return whether settings select a supported discovery method for alignment."""

    from xic_extractor.processing_run_spec import DiscoveryMethodSpec

    if isinstance(settings, DiscoveryMethodSpec):
        return True
    if isinstance(settings, DiscoverySettings):
        try:
            CidNlDiscoveryMethodSpec(settings=settings)
            return True
        except (TypeError, ValueError):
            return False
    if hasattr(settings, "tags") and getattr(settings, "tags"):
        return all(
            getattr(tag, "strategy", None) in {"neutral_loss", "delta_mass"}
            for tag in settings.tags
        )
    return False


def _method_center_interval(mz: float, ppm: float) -> tuple[float, float]:
    fraction = ppm / 1_000_000.0
    return mz / (1.0 + fraction), mz / (1.0 - fraction)


def _same_physical_core(
    left: DiscoveryPhysicalFactRecord,
    right: DiscoveryPhysicalFactRecord,
    *,
    rt_min: float,
    rt_max: float,
) -> bool:
    sentinel = object()
    matched = False
    left_core = (
        point
        for point in zip(left.rt, left.raw, strict=True)
        if rt_min <= point[0] <= rt_max
    )
    right_core = (
        point
        for point in zip(right.rt, right.raw, strict=True)
        if rt_min <= point[0] <= rt_max
    )
    for left_point, right_point in zip_longest(
        left_core,
        right_core,
        fillvalue=sentinel,
    ):
        if (
            left_point is sentinel
            or right_point is sentinel
            or left_point != right_point
        ):
            return False
        matched = True
    return matched


def _physical_subject_units(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord],
    composition: CidNlComposition | None,
    precursor_tolerance_ppm: float,
) -> tuple[tuple[float | None, tuple[DiscoveryCandidate, ...]], ...]:
    """Keep query wrappers for one proven peak in the same method workset."""

    views: dict[
        str,
        tuple[
            DiscoveryPhysicalFactRecord,
            tuple[float, float],
            tuple[float, float],
        ],
    ] = {}
    buckets: dict[tuple[str, float | None, float], list[DiscoveryCandidate]] = {}
    unproved: list[tuple[float | None, tuple[DiscoveryCandidate, ...]]] = []
    for candidate in candidates:
        requirement = (
            None if composition is not None else candidate.configured_neutral_loss_da
        )
        record = physical_records_by_candidate.get(candidate.candidate_id)
        facts = None if record is None else record.evaluation.facts
        if record is None or facts is None:
            unproved.append((requirement, (candidate,)))
            continue
        xic_fraction = record.work_item.xic_ppm_tol / 1_000_000.0
        views[candidate.candidate_id] = (
            record,
            (
                record.work_item.xic_mz * (1.0 - xic_fraction),
                record.work_item.xic_mz * (1.0 + xic_fraction),
            ),
            _method_center_interval(
                candidate.precursor_mz,
                precursor_tolerance_ppm,
            ),
        )
        buckets.setdefault(
            (
                record.work_item.acquisition_source_id,
                requirement,
                facts.apex_rt_min,
            ),
            [],
        ).append(candidate)

    proved: list[tuple[float | None, tuple[DiscoveryCandidate, ...]]] = []
    for bucket_key, bucket_candidates in sorted(buckets.items()):
        requirement = bucket_key[1]
        ordered = sorted(
            bucket_candidates,
            key=lambda item: (item.precursor_mz, item.candidate_id),
        )
        cohort: list[DiscoveryCandidate] = []
        representative_record: DiscoveryPhysicalFactRecord | None = None
        common_rt = (0.0, 0.0)
        common_xic = (0.0, 0.0)
        common_center = (0.0, 0.0)
        for candidate in ordered:
            record, xic_interval, center_interval = views[candidate.candidate_id]
            facts = record.evaluation.facts
            if facts is None:  # Kept local for type narrowing; filtered above.
                raise RuntimeError("physical proof lost its selected peak")
            if not cohort:
                cohort = [candidate]
                representative_record = record
                common_rt = (facts.left_rt_min, facts.right_rt_min)
                common_xic = xic_interval
                common_center = center_interval
                continue
            next_rt = (
                max(common_rt[0], facts.left_rt_min),
                min(common_rt[1], facts.right_rt_min),
            )
            next_xic = (
                max(common_xic[0], xic_interval[0]),
                min(common_xic[1], xic_interval[1]),
            )
            next_center = (
                max(common_center[0], center_interval[0]),
                min(common_center[1], center_interval[1]),
            )
            apex = bucket_key[2]
            joins = (
                next_rt[0] < apex < next_rt[1]
                and next_xic[0] <= next_xic[1]
                and next_center[0] <= next_center[1]
                and representative_record is not None
                and _same_physical_core(
                    representative_record,
                    record,
                    rt_min=next_rt[0],
                    rt_max=next_rt[1],
                )
            )
            if not joins:
                proved.append((requirement, tuple(cohort)))
                cohort = [candidate]
                representative_record = record
                common_rt = (facts.left_rt_min, facts.right_rt_min)
                common_xic = xic_interval
                common_center = center_interval
                continue
            cohort.append(candidate)
            common_rt = next_rt
            common_xic = next_xic
            common_center = next_center
        if cohort:
            proved.append((requirement, tuple(cohort)))

    return tuple(
        sorted(
            (*proved, *unproved),
            key=lambda unit: (
                0.0 if unit[0] is None else unit[0],
                unit[1][0].precursor_mz,
                tuple(candidate.candidate_id for candidate in unit[1]),
            ),
        )
    )


def prepare_untargeted_subject_membership(
    *,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    settings: DiscoverySettings,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord]
    | None = None,
) -> UntargetedCidNlMethodSubjectBatch:
    """Resolve exact subject membership for a closed precursor working set."""
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

    composition = cid_nl_composition(settings)
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
    subject_units = _physical_subject_units(
        candidates,
        physical_records_by_candidate=physical_records_by_candidate or {},
        composition=composition,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    return mint_subject_membership_from_physical_units(
        subject_units=tuple(
            (requirement, tuple(candidate.candidate_id for candidate in unit))
            for requirement, unit in subject_units
        ),
        precursor_by_id={
            candidate.candidate_id: candidate.precursor_mz for candidate in candidates
        },
        settings=settings,
        composition=composition,
    )


def cid_nl_composition(settings: DiscoverySettings) -> CidNlComposition | None:
    """Resolve the configured requirement shared by all precursor worksets."""
    combine_mode = settings.tag_combine_mode
    if len(settings.neutral_loss_profiles) > 1 and combine_mode == "single":
        raise ValueError("Multiple CID-NL tags require union or intersection")
    return (
        CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=combine_mode,
        )
        if len(settings.neutral_loss_profiles) > 1 and combine_mode != "single"
        else None
    )


def mint_subject_membership_from_physical_units(
    *,
    subject_units: tuple[tuple[float | None, tuple[str, ...]], ...],
    precursor_by_id: Mapping[str, float],
    settings: DiscoverySettings,
    composition: CidNlComposition | None,
) -> UntargetedCidNlMethodSubjectBatch:
    """Assign fixed query centers from compact, physically closed units."""
    subject_by_key: dict[tuple[float, float | None], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float | None]]] = []
    key_by_requirement: dict[float | None, tuple[float, float | None]] = {}
    for requirement, unit_candidates in subject_units:
        key = key_by_requirement.get(requirement)
        # A proven physical unit is indivisible. Existing fixed centers may host
        # it only when every original query remains inside the method window.
        if key is None or any(
            abs(precursor_by_id[candidate] - key[0]) / key[0] * 1_000_000.0
            > settings.precursor_mz_tolerance_ppm
            for candidate in unit_candidates
        ):
            if len(unit_candidates) == 1:
                center = precursor_by_id[unit_candidates[0]]
            else:
                center_intervals = tuple(
                    _method_center_interval(
                        precursor_by_id[candidate],
                        settings.precursor_mz_tolerance_ppm,
                    )
                    for candidate in unit_candidates
                )
                lower = max(interval[0] for interval in center_intervals)
                upper = min(interval[1] for interval in center_intervals)
                if lower > upper:
                    raise RuntimeError(
                        "physical subject unit lacks a common method center"
                    )
                center = (lower + upper) / 2.0
            key = (center, requirement)
            key_by_requirement[requirement] = key
            subject_by_key[key] = mint_untargeted_cid_nl_method_subject(
                precursor_mz=center,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=requirement,
                composition=composition,
            )
        membership_keys.extend((candidate, key) for candidate in unit_candidates)

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
    return subject_batch


def prepare_untargeted_cid_nl_production_plan(
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
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord]
    | None = None,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...]
    | None = None,
) -> UntargetedCidNlProductionPlan:
    """Seal exact candidate membership before any subject-local runtime work."""

    if not is_pure_single_provider_cid_nl(settings):
        raise ValueError(
            "Untargeted CID-NL production requires a pure single-provider route"
        )
    if not isinstance(dll_dir, Path):
        raise TypeError("dll_dir must be a Path")
    subject_batch = prepare_untargeted_subject_membership(
        candidates_by_sample=candidates_by_sample,
        settings=settings,
        physical_records_by_candidate=physical_records_by_candidate,
    )
    candidates = tuple(
        candidate for values in candidates_by_sample.values() for candidate in values
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
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records_by_candidate,
        acquisition_source_expectations=acquisition_source_expectations,
    )
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    eligible_subject_ids = {
        subject_id
        for candidate_id, subject_id in subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].ms1_peak_found
        and candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
    }
    # Keep the complete membership and fixed query centers as request identity.
    # Only subjects backed by an MS1 feature can seed cross-sample assessment.
    runtime_subjects = tuple(
        subject
        for subject in subject_batch.subjects
        if subject.method_subject_id in eligible_subject_ids
    )
    sample_ids = {
        sample.metadata.raw_stem: sample.identity.sample_identity_id
        for sample in context.samples
    }
    excluded_candidates = tuple(
        UntargetedCandidateExclusion(
            candidate_id=candidate_id,
            method_subject_id=subject_id,
            canonical_sample_identity_id=sample_ids[
                candidate_by_id[candidate_id].sample_stem
            ],
            discovery_candidate_state=(
                candidate_by_id[candidate_id].discovery_candidate_state
            ),
        )
        for candidate_id, subject_id in subject_batch.candidate_memberships
        if subject_id not in eligible_subject_ids
    )
    ordered_physical_records = tuple(
        record
        for _candidate_id, record in sorted(
            context.physical_records_by_candidate.items()
        )
    )
    physical_records_by_sample: dict[str, list[DiscoveryPhysicalFactRecord]] = {}
    for record in ordered_physical_records:
        physical_records_by_sample.setdefault(record.work_item.sample_stem, []).append(
            record
        )
    templates = tuple(
        job
        for sample in context.samples
        for job in build_untargeted_cid_nl_sample_runtime_jobs(
            processing_owners=context.processing_owners,
            sample=sample,
            subject_work_units=tuple(
                (
                    subject,
                    "untargeted-cid-nl:"
                    f"{sample.metadata.raw_stem}:{subject.method_subject_id}",
                )
                for subject in subject_batch.subjects[:1]
            ),
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.metadata.raw_stem
            ],
            dll_dir=dll_dir,
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                physical_records_by_sample.get(sample.metadata.raw_stem, ())
            ),
        )
    )
    return UntargetedCidNlProductionPlan(
        subject_batch=subject_batch,
        context=context,
        sample_job_templates=templates,
        runtime_subjects=runtime_subjects,
        excluded_candidates=excluded_candidates,
    )


def materialize_untargeted_cid_nl_partition(
    plan: UntargetedCidNlProductionPlan,
    subject_batch: UntargetedCidNlMethodSubjectBatch,
    *,
    prepared_worksets: PreparedCidNlAlignmentSubjectWorksets | None = None,
    acquisition_cache_dir: Path | None = None,
    sparse: bool = False,
) -> UntargetedCidNlProductionBatch:
    """Bind a finite subject partition to shared, already-validated inputs."""

    prepared = (
        prepare_untargeted_cid_nl_alignment_subject_worksets(plan)
        if prepared_worksets is None
        else prepared_worksets
    )
    if prepared.plan is not plan:
        raise ValueError("prepared alignment worksets crossed the production plan")
    expected_batch = (
        plan.subject_batch
        if subject_batch == plan.subject_batch
        else prepared.subject_batch(subject_batch.subjects)
    )
    if subject_batch.candidate_memberships != expected_batch.candidate_memberships:
        raise ValueError("CID-NL partition crossed prepared candidate memberships")

    if plan.partition_input_loader is not None:
        candidates, records, support = plan.partition_input_loader(
            tuple(cid for cid, _ in subject_batch.candidate_memberships)
        )
        context = replace(
            plan.context,
            candidates_by_sample=candidates,
            physical_records_by_candidate=records,
        )
        assert plan.dll_dir is not None
        templates: list[UntargetedCidNlRealDataRuntimeJob] = []
        for sample in context.samples:
            stem = sample.metadata.raw_stem
            templates.extend(
                build_untargeted_cid_nl_sample_runtime_jobs(
                    processing_owners=context.processing_owners,
                    sample=sample,
                    subject_work_units=tuple(
                        (
                            subject,
                            f"untargeted-cid-nl:{stem}:{subject.method_subject_id}",
                        )
                        for subject in subject_batch.subjects[:1]
                    ),
                    candidates=candidates[stem],
                    discovery_receipt=context.discovery_receipts_by_sample[stem],
                    settings=context.settings,
                    peak_config=context.peak_config,
                    raw_path=context.raw_paths_by_sample[stem],
                    dll_dir=plan.dll_dir,
                    signal_processing_profile=context.signal_processing_profile,
                    physical_records=tuple(
                        records[item.candidate_id] for item in candidates[stem]
                    ),
                    acquisition_source_expectation=context.acquisition_sources_by_sample[
                        stem
                    ],
                    subject_partition=subject_batch,
                    support_candidates=support[stem],
                    acquisition_cache_dir=acquisition_cache_dir,
                )
            )
    else:
        context = plan.context
        templates = list(plan.sample_job_templates)
    jobs = []
    subjects_by_id = {
        subject.method_subject_id: subject for subject in subject_batch.subjects
    }
    bound_templates = []
    for source_template in templates:
        template = copy(source_template)
        object.__setattr__(template, "acquisition_cache_dir", acquisition_cache_dir)
        bound_templates.append(template)
        subject_ids = (
            tuple(
                subject_id
                for subject_id in subjects_by_id
                if template._request_candidates_by_subject.get(subject_id)
            )
            if sparse
            else tuple(subjects_by_id)
        )
        for subject_id in subject_ids:
            subject = subjects_by_id[subject_id]
            job = copy(template)
            subject_id = subject.method_subject_id
            object.__setattr__(job, "method_subject", subject)
            object.__setattr__(
                job,
                "work_unit_key",
                f"untargeted-cid-nl:{template.sample.metadata.raw_stem}:{subject_id}",
            )
            object.__setattr__(job, "acquisition_cache_dir", acquisition_cache_dir)
            jobs.append(job)
    return UntargetedCidNlProductionBatch(
        subject_batch=subject_batch,
        context=context,
        jobs=tuple(jobs),
        sample_job_templates=tuple(bound_templates) if sparse else (),
    )


def build_untargeted_cid_nl_production_batch(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    discovery_receipts_by_sample: Mapping[str, DiscoveryCandidateUniverseReceipt],
    dll_dir: Path,
    sample_identity_namespace: str,
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord]
    | None = None,
) -> UntargetedCidNlProductionBatch:
    """Materialize a complete batch for existing direct runtime consumers."""

    plan = prepare_untargeted_cid_nl_production_plan(
        raw_paths=raw_paths,
        sample_metadata=sample_metadata,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=discovery_receipts_by_sample,
        dll_dir=dll_dir,
        sample_identity_namespace=sample_identity_namespace,
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records_by_candidate,
    )
    return materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)


def execute_untargeted_cid_nl_production_batch(
    batch: UntargetedCidNlProductionBatch,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
) -> UntargetedCidNlRealDataRuntimeApplicationResult:
    """Execute the sealed universe once and enforce its per-sample phase bound."""

    if not isinstance(batch, UntargetedCidNlProductionBatch):
        raise TypeError("batch must be an UntargetedCidNlProductionBatch")
    runtime = start_runtime_attempt(
        attempt=batch.context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in batch.jobs),
        owner_completeness_attestation=(
            f"{batch.context.processing_owners.request.processing_request_id}:"
            "cid-nl-production"
        ),
    )
    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        batch.jobs,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        progress_callback=progress_callback,
        alignment_config=alignment_config,
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


@dataclass(frozen=True, slots=True)
class UntargetedCidNlPartitionComposition:
    """Subject-local scientific closure without a whole-runtime completion claim."""

    children: tuple[FinalizedUntargetApplicationResult, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    method_assessments: tuple[CidNlMethodAssessmentRef, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class UntargetedCidNlPersistedPartition:
    """Metadata for one atomically persisted, streamed scientific partition."""

    sealed: SealedPartition
    child_count: int
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    child_order_keys: tuple[str, ...] = ()


def compose_and_persist_untargeted_cid_nl_partition(
    batch: UntargetedCidNlProductionBatch,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
    *,
    store: PartitionResultStore,
    partition_key: str,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
    should_cancel: Callable[[], object] | None = None,
    retain_memory: bool = False,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
) -> UntargetedCidNlPersistedPartition:
    """Compose one child at a time and publish only after the full partition.

    Composition inputs are prepared once.  The result store owns the trusted
    owner references and atomic file publication while this owner controls
    scientific child order and cancellation boundaries.
    """

    inputs = _build_production_composition_inputs(
        batch,
        sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    child_count = 0
    child_order_keys: list[str] = []

    def check_cancellation() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("CID-NL partition cancelled")

    def children():
        nonlocal child_count
        for bundle, captures in zip(
            inputs.bundles, inputs.completed_captures, strict=True
        ):
            check_cancellation()
            child = compose_untarget_subject(
                cast(UntargetWorkflowCompositionRequest, bundle.request),
                bundle.adapter,
                completed_captures=captures,
            )
            child_count += 1
            child_order_keys.append(
                min(
                    provider.method_subject_id
                    for provider in child.scientific_core.scope.evidence_providers
                )
            )
            yield child
            del child

    metadata_payload = UntargetedCidNlPartitionComposition(
        children=(),
        excluded_candidates=inputs.request.excluded_candidates,
        capture_bindings=inputs.request.scope.capture_bindings,
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        initial_edges=inputs.initial_edges,
        capture_evidence=inputs.capture_evidence,
    )
    sealed = store.append_children(
        partition_key,
        children=children(),
        before_publish=check_cancellation,
        metadata_payload=metadata_payload,
        retain_memory=retain_memory,
    )
    return UntargetedCidNlPersistedPartition(
        sealed=sealed,
        child_count=child_count,
        excluded_candidates=inputs.request.excluded_candidates,
        capture_bindings=inputs.request.scope.capture_bindings,
        child_order_keys=tuple(child_order_keys),
    )


@dataclass(frozen=True, slots=True)
class _ProductionCompositionInputs:
    request: UntargetWorkflowCompositionRequest
    bundles: tuple[UntargetWorkflowBundle, ...]
    method_assessments: tuple[CidNlMethodAssessmentRef, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    completed_captures: tuple[tuple[SampleLocalCapture, ...], ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


def compose_untargeted_cid_nl_production_result(
    batch: UntargetedCidNlProductionBatch,
    runtime_result: UntargetedCidNlRealDataRuntimeApplicationResult,
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
) -> CompletedUntargetBatchWorkflowResult:
    """Close exact subject-local workflows from the one completed runtime."""

    if not runtime_result.succeeded:
        raise RuntimeError("CID-NL production cannot compose a partial runtime")
    inputs = _build_production_composition_inputs(
        batch,
        runtime_result.sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    terminal = compose_untarget_batch_after_runtime(
        inputs.request,
        lambda: inputs.bundles,
        runtime_result.finalization,
    )
    if type(terminal) is not CompletedUntargetBatchWorkflowResult:
        raise RuntimeError(
            "CID-NL workflow failed atomically: "
            f"stage={getattr(terminal, 'stage', None)}; "
            f"reason={getattr(terminal, 'failure_message', None)}"
        )
    return replace(
        terminal,
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        initial_edges=inputs.initial_edges,
        capture_evidence=inputs.capture_evidence,
    )


def compose_untargeted_cid_nl_partition(
    batch: UntargetedCidNlProductionBatch,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
) -> UntargetedCidNlPartitionComposition:
    """Compose complete subject partitions before global runtime finalization."""
    inputs = _build_production_composition_inputs(
        batch,
        sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    return UntargetedCidNlPartitionComposition(
        children=tuple(
            compose_untarget_subject(
                cast(UntargetWorkflowCompositionRequest, bundle.request),
                bundle.adapter,
                completed_captures=captures,
            )
            for bundle, captures in zip(
                inputs.bundles, inputs.completed_captures, strict=True
            )
        ),
        excluded_candidates=inputs.request.excluded_candidates,
        capture_bindings=inputs.request.scope.capture_bindings,
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        initial_edges=inputs.initial_edges,
        capture_evidence=inputs.capture_evidence,
    )


def _build_production_composition_inputs(
    batch: UntargetedCidNlProductionBatch,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None,
) -> _ProductionCompositionInputs:
    if type(sample_results) is not tuple or any(
        type(result) is not UntargetedCidNlRuntimeSampleResult
        for result in sample_results
    ):
        raise TypeError("sample_results must be an immutable typed runtime population")
    if not isinstance(alignment_config, AlignmentConfig):
        raise TypeError("alignment_config must be an AlignmentConfig")
    result_context = _RuntimeResultContext(
        tuple(result.job for result in sample_results)
    )
    result_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in sample_results
    }
    jobs_by_key = {
        (job.sample.metadata.raw_stem, job.method_subject.method_subject_id): job
        for job in batch.jobs
    }
    subject_ids = {
        subject.method_subject_id for subject in batch.subject_batch.subjects
    }
    templates_by_stem = {
        job.sample.metadata.raw_stem: job for job in batch.sample_job_templates
    }
    extra_keys = set(result_by_key) - set(jobs_by_key)
    dense_keys = (
        {
            (sample.metadata.raw_stem, subject.method_subject_id)
            for sample in batch.context.samples
            for subject in batch.subject_batch.subjects
        }
        if not batch.sample_job_templates
        else set(jobs_by_key)
    )
    if (
        len(result_by_key) != len(sample_results)
        or len(jobs_by_key) != len(batch.jobs)
        or set(jobs_by_key) != dense_keys
        or not set(jobs_by_key) <= set(result_by_key)
        or any(
            result_by_key[key].job is not job for key, job in jobs_by_key.items()
        )
        or any(
            key[0] not in templates_by_stem
            or key[1] not in subject_ids
            or result_by_key[key].job.sample is not templates_by_stem[key[0]].sample
            or result_by_key[key].job.processing_owners
            is not batch.context.processing_owners
            for key in extra_keys
        )
    ):
        raise RuntimeError(
            "CID-NL production runtime lost exact sample/subject results"
        )

    results_by_subject: dict[str, list[UntargetedCidNlRuntimeSampleResult]] = {}
    for result in sample_results:
        results_by_subject.setdefault(
            result.job.method_subject.method_subject_id, []
        ).append(result)
    for results in results_by_subject.values():
        results.sort(key=lambda result: result.job.sample.identity.sample_identity_id)
    final_method_assessments = [
        assessment
        for result in sample_results
        for assessment in result.effective_assessments
    ]

    capture_by_candidate = _runtime_captures_by_candidate(batch, sample_results)
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
    }
    ordered_samples = tuple(
        sorted(batch.context.samples, key=lambda item: item.identity.sample_identity_id)
    )
    unsupported_subjects = {}
    for subject in batch.subject_batch.subjects:
        subject_assessments = tuple(
            assessment
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
        )
        evaluated_unsupported = tuple(
            ref.content.technical_state == "evaluated"
            and ref.content.method_result in {"HOLD", "REJECT"}
            for ref in subject_assessments
        )
        if any(evaluated_unsupported) and all(
            is_unsupported or is_unsearched_cid_nl_discovery_assessment(ref)
            for ref, is_unsupported in zip(
                subject_assessments, evaluated_unsupported, strict=True
            )
        ):
            unsupported_subjects[subject.method_subject_id] = subject_assessments
    bindings = tuple(
        require_capture_binding(
            capture_by_candidate[candidate_id].scope,
            candidate_id,
        )
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
        and method_subject_id not in unsupported_subjects
    )
    exclusions = tuple(
        UntargetedCandidateExclusion(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            canonical_sample_identity_id=batch.context.sample(
                candidate_by_id[candidate_id].sample_stem
            ).identity.sample_identity_id,
            discovery_candidate_state=(
                candidate_by_id[candidate_id].discovery_candidate_state
            ),
            reason_code=(
                "composite_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                and unsupported_subjects[method_subject_id][
                    0
                ].content.method_subject.composition
                is not None
                else "cid_nl_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                else "discovery_candidate_not_search_eligible"
            ),
            composite_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is not None
            ),
            method_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is None
            ),
        )
        for candidate_id, method_subject_id in (
            batch.subject_batch.candidate_memberships
        )
        if candidate_by_id[candidate_id].discovery_candidate_state
        not in MS1_FEATURE_BACKED_STATES
        or method_subject_id in unsupported_subjects
    )
    # Partitioning changes execution ownership. Scientific composition keeps
    # the global request scope and closes the exact subject views below.
    request_batch = cast(
        UntargetedCidNlMethodSubjectBatch, batch.context.method_subject
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=batch.context.processing_owners.request,
        samples=batch.context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=batch.context.peak_config,
            signal_processing_profile=batch.context.signal_processing_profile,
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in request_batch.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id
            for candidate_id, _method_subject_id in (
                request_batch.candidate_memberships
            )
        ),
        capture_bindings=bindings,
    )
    parent_request = UntargetWorkflowCompositionRequest(
        scope=scope,
        processing_request=batch.context.processing_owners.request,
        processing_attempt=batch.context.processing_owners.attempt,
        excluded_candidates=exclusions,
    )

    bundles = []
    completed_captures = []
    initial_groups: list[CrossSamplePeakGroupHypothesis] = []
    initial_edges: list[CrossSampleNeighborhoodEdge] = []
    bindings_by_subject: dict[str, list[CaptureCandidateBinding]] = {}
    for binding in bindings:
        bindings_by_subject.setdefault(binding.method_subject_id, []).append(binding)
    sample_by_id = {
        sample.identity.sample_identity_id: sample for sample in batch.context.samples
    }
    trajectory_policy = ModeTrackTrajectoryPolicy(
        trajectory_policy_version="anchor-trajectory",
        band_policy_version="fixed-band",
        prediction_half_width_min=2 / 60,
        max_interpolation_gap=10,
        max_extrapolation_gap=10,
    )
    sample_universe = frozenset(
        sample.metadata.raw_stem
        for sample in batch.context.processing_owners.sample_resolutions
    )
    injection_order = {
        sample.metadata.raw_stem: sample.metadata.injection_order
        for sample in batch.context.processing_owners.sample_resolutions
        if sample.metadata.injection_order is not None
    }
    if known_istd_reference is not None:
        known_istd_reference = known_istd_reference.prepare_batch(
            sample_universe=sample_universe,
            injection_order=injection_order,
        )
    raw_sample_stems = frozenset(batch.context.raw_paths_by_sample)
    empty_family_ids: frozenset[str] = frozenset()
    active_subjects = tuple(
        subject
        for subject in batch.subject_batch.subjects
        if bindings_by_subject.get(subject.method_subject_id)
    )
    for subject_workset in cid_nl_alignment_subject_worksets(
        active_subjects,
        comparison_mz_intervals=_known_istd_comparison_intervals(known_istd_reference),
    ):
        child_bindings = tuple(
            sorted(
                (
                    binding
                    for subject in subject_workset
                    for binding in bindings_by_subject[subject.method_subject_id]
                ),
                key=lambda binding: binding.candidate_id,
            )
        )
        child_scope = UntargetedSearchScope(
            schema_version=scope.schema_version,
            request=scope.request,
            samples=scope.samples,
            method_constraints=scope.method_constraints,
            evidence_providers=tuple(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=subject.method_subject_id,
                )
                for subject in subject_workset
            ),
            declared_candidate_ids=tuple(
                binding.candidate_id for binding in child_bindings
            ),
            capture_bindings=child_bindings,
        )
        assessments = tuple(
            (
                result.job.sample,
                assessment,
            )
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
        )
        assessments = tuple(
            sorted(assessments, key=lambda pair: pair[0].identity.sample_identity_id)
        )
        capture_inputs_list = []
        captures_list = []
        assessment_by_sample_subject = {
            (
                result.job.sample.identity.sample_identity_id,
                subject.method_subject_id,
            ): result.adapter_result.assessment_input.assessment
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
        }
        hypotheses_by_sample_subject = {
            key: compose_peak_hypotheses_from_assessment_universe(
                processing_owners=batch.context.processing_owners,
                sample=sample_by_id[key[0]],
                assessment=assessment_by_sample_subject[key],
            )
            for key in dict.fromkeys(
                (binding.sample_identity_id, binding.method_subject_id)
                for binding in child_bindings
            )
        }
        retained_hypotheses_by_assessment = {
            (
                key[0],
                assessment_by_sample_subject[
                    key
                ].artifact.content_identity.artifact_content_id,
            ): hypotheses
            for key, hypotheses in hypotheses_by_sample_subject.items()
        }
        for binding in child_bindings:
            sample = sample_by_id[binding.sample_identity_id]
            trace = capture_by_candidate[binding.candidate_id].trace
            captured = capture_peak_hypotheses(
                scope=child_scope,
                candidate_id=binding.candidate_id,
                trace=trace,
            )
            if captured.selected_hypothesis is None:
                captures_list.append(captured)
                capture_inputs_list.append(
                    CaptureWorkflowArguments(
                        candidate_id=binding.candidate_id,
                        trace=trace,
                    )
                )
                continue
            observation = mint_captured_peak_observation(
                captured,
                processing_owners=batch.context.processing_owners,
                sample=sample,
            )
            hypotheses = hypotheses_by_sample_subject[
                (binding.sample_identity_id, binding.method_subject_id)
            ]
            matches = tuple(
                hypothesis
                for hypothesis in hypotheses
                if observation in hypothesis.peak_observation_refs
            )
            if len(matches) != 1:
                raise RuntimeError(
                    "CID-NL runtime assessment lost its exact captured observation"
                )
            owner_capture = bind_owner_backed_capture_hypothesis(
                captured,
                observation=observation,
                hypothesis=matches[0],
            )
            captures_list.append(owner_capture)
            capture_inputs_list.append(
                CaptureWorkflowArguments(
                    candidate_id=binding.candidate_id,
                    trace=trace,
                    owner_observation=observation,
                    owner_hypothesis=matches[0],
                )
            )
        captures = tuple(captures_list)
        capture_inputs = tuple(capture_inputs_list)
        (
            assignment_scope,
            grouping_result,
            retained_candidates,
        ) = build_initial_cid_nl_cross_sample_groups(
            processing_owners=batch.context.processing_owners,
            captures=captures,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            known_istd_reference=known_istd_reference,
            retained_hypotheses_by_assessment=(retained_hypotheses_by_assessment),
        )
        initial_groups.extend(assignment_scope.groups)
        if batch.sample_job_templates:
            unsearched = []
            for sample in ordered_samples:
                for subject in subject_workset:
                    key = (sample.metadata.raw_stem, subject.method_subject_id)
                    if key in result_by_key:
                        continue
                    unsearched_assessment = adapt_current_discovery_sample_subject(
                        processing_owners=batch.context.processing_owners,
                        sample=sample,
                        method_subject=subject,
                        candidates=(),
                        settings=batch.context.settings,
                        peak_config=batch.context.peak_config,
                        raw=None,
                        signal_processing_profile=batch.context.signal_processing_profile,
                    ).assessment_input.assessment
                    unsearched.append((sample, unsearched_assessment))
                    final_method_assessments.append(unsearched_assessment)
            assessments = (*assessments, *unsearched)
        subject_order = {
            subject.method_subject_id: index
            for index, subject in enumerate(subject_workset)
        }
        assessments = tuple(sorted(
            assessments,
            key=lambda pair: (
                pair[0].identity.sample_identity_id,
                subject_order[pair[1].content.method_subject.method_subject_id],
            ),
        ))
        retained_cell_hypotheses: dict[tuple[str, str], list[PeakHypothesis]] = {}
        for candidate in retained_candidates:
            assessment = candidate.direct_support_assessment
            if assessment is None:
                continue
            retained_cell_hypotheses.setdefault(
                (
                    candidate.canonical_sample_identity_id,
                    assessment.artifact.content_identity.artifact_content_id,
                ),
                [],
            ).append(candidate.peak_hypothesis)
        predecessor_cells = compose_cid_nl_predecessor_cells(
            processing_owners=batch.context.processing_owners,
            groups=assignment_scope.groups,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            drift_lookup=grouping_result.drift_lookup,
            sample_universe=sample_universe,
            injection_order=injection_order,
            recovery_assessments_by_group={
                (
                    group.group_hypothesis_id,
                    sample_result.job.sample.identity.sample_identity_id,
                    sample_result.job.method_subject.method_subject_id,
                ): recovery.assessment
                for subject in subject_workset
                for sample_result in results_by_subject.get(
                    subject.method_subject_id, ()
                )
                if sample_result.recovery_attempts
                for group in assignment_scope.groups
                if group.group_center_rt is not None and (
                    recovery := sample_result.recovery_for_group(group.group_center_rt)
                )
                is not None
            },
            retained_hypotheses_by_assessment={
                key: tuple(hypotheses)
                for key, hypotheses in retained_cell_hypotheses.items()
            },
        )
        cell_by_key = {}
        cells_by_group: dict[str, list[CellObservationRef]] = {}
        for cell in predecessor_cells:
            group_id = cell.content.group_hypothesis_id
            cell_by_key[(group_id, cell.content.canonical_sample_identity_id)] = cell
            cells_by_group.setdefault(group_id, []).append(cell)
        repair_groups = grouping_result.final_population
        no_request_proofs = {}
        for group in repair_groups:
            facts = assess_conservative_backfill_group(
                tuple(cells_by_group[group.group_hypothesis_id]),
                sample_population=batch.context.samples,
            )
            proof_builder = None
            for sample in batch.context.samples:
                key = (group.group_hypothesis_id, sample.identity.sample_identity_id)
                cell = cell_by_key[key]
                if (
                    not (
                        cell.content.method_result == "HOLD"
                        or is_unsearched_cid_nl_discovery_assessment(
                            cell.method_assessment
                        )
                    )
                    or cell.peak_observation is not None
                ):
                    continue
                completion = None
                universes: tuple[
                    ConservativeBackfillRecoveryCandidateUniverse, ...
                ] = ()
                if facts.may_attempt_recovery:
                    if cell.method_assessment.candidate_peaks:
                        continue
                    method_subject_id = (
                        cell.method_assessment.content.method_subject.method_subject_id
                    )
                    sample_result = result_by_key.get(
                        (sample.metadata.raw_stem, method_subject_id)
                    )
                    if (
                        sample_result is None
                        or facts.group_context.group.group_center_rt is None
                        or sample_result.recovery_for_group(
                            facts.group_context.group.group_center_rt
                        )
                        is None
                    ):
                        continue
                    completion = result_context.project_completed_recovery(
                        sample_result, group_context=facts.group_context
                    )
                    universes = (
                        mint_conservative_backfill_recovery_candidate_universe(
                            processing_owners=batch.context.processing_owners,
                            sample=sample,
                            group_facts=(facts,),
                            candidate_assessment=completion.recovery_assessment,
                        ),
                    )
                (decision,) = map_conservative_backfill_candidates(
                    (facts,), sample=sample, candidate_universes=universes
                )
                if proof_builder is None:
                    proof_builder = ConservativeNoRequestProofBuilder(
                        processing_owners=batch.context.processing_owners,
                        group_identity=group,
                        group_facts=facts,
                    )
                no_request_proofs[key] = proof_builder.mint(
                    sample=sample,
                    decision=decision,
                    completion=completion,
                )
        positions = tuple(
            OwnerBackfillPlanningPosition(
                position_id=mint_owner_backfill_position_id(
                    processing_request_id=group.processing_request_id,
                    group_hypothesis_id=group.group_hypothesis_id,
                    sample_identity_id=sample.identity.sample_identity_id,
                ),
                group_identity=group,
                sample=sample,
                planning_state="not_planned",
                planning_reason=(
                    no_request_proofs[
                        (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    ].planning_reason
                    if (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    in no_request_proofs
                    else "local_owner_not_supersedable"
                    if cell.content.outside_group is not None
                    else "review_only"
                    if is_unsearched_cid_nl_discovery_assessment(cell.method_assessment)
                    else "retained_unresolved_candidates"
                    if (
                        cell.content.technical_state == "evaluated"
                        and cell.content.method_result == "HOLD"
                        and cell.peak_observation is None
                        and len(cell.method_assessment.candidate_peaks) >= 2
                    )
                    else "retained_local_owner"
                ),
                primary_logical_requests=(),
                conservative_no_request_proof=no_request_proofs.get(
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ),
            )
            for group in repair_groups
            for sample in batch.context.samples
            for cell in (
                cell_by_key[
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ],
            )
        )
        epoch = mint_owner_backfill_planning_epoch(
            processing_owners=batch.context.processing_owners,
            group_identities=repair_groups,
            samples=batch.context.samples,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
            backend="raw",
            window_strategy="exact",
            superwindow_span_factor=2,
            validation_mode="primary",
            result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
            scope="full-audit",
            selected_family_ids=empty_family_ids,
            positions=positions,
        )
        coverage = build_owner_backfill_evaluation_coverage(epoch, ())
        bundles.append(
            UntargetWorkflowBundle(
                request=UntargetWorkflowCompositionRequest(
                    scope=child_scope,
                    processing_request=parent_request.processing_request,
                    processing_attempt=parent_request.processing_attempt,
                ),
                adapter=UntargetWorkflowAdapter(
                    capture_inputs=capture_inputs,
                    completed_grouping=grouping_result,
                    mode_track=ModeTrackWorkflowArguments(
                        sample_assessments=assessments,
                        alignment_config=alignment_config,
                        trajectory_policy=trajectory_policy,
                        retain_observation_only_candidates=True,
                    ),
                    closed_cell_coverage=coverage,
                    predecessor_cell_observations=predecessor_cells,
                ),
            )
        )
        completed_captures.append(captures)
    return _ProductionCompositionInputs(
        request=parent_request,
        bundles=tuple(bundles),
        method_assessments=tuple(final_method_assessments),
        initial_groups=tuple(initial_groups),
        initial_edges=tuple(initial_edges),
        completed_captures=tuple(completed_captures),
        capture_evidence=tuple(
            evidence
            for result in sample_results
            for evidence in (
                *result.adapter_result.capture_evidence,
                *(
                    item
                    for attempt in result.recovery_attempts
                    for item in attempt.capture_evidence
                ),
            )
        ),
    )


def _runtime_captures_by_candidate(
    batch: UntargetedCidNlProductionBatch,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
) -> dict[str, SampleLocalCapture]:
    captures: dict[str, SampleLocalCapture] = {}
    for result in sample_results:
        for capture in result.adapter_result.captures:
            if capture.candidate_id in captures:
                raise RuntimeError("runtime capture duplicated a candidate")
            captures[capture.candidate_id] = capture
    partition_ids = {key for key, _ in batch.subject_batch.candidate_memberships}
    expected = {
        candidate.candidate_id
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
        if candidate.candidate_id in partition_ids
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    }
    if set(captures) != expected:
        raise RuntimeError("runtime capture does not close capture-eligible candidates")
    return captures


build_untargeted_production_batch = build_untargeted_cid_nl_production_batch
execute_untargeted_production_batch = execute_untargeted_cid_nl_production_batch
compose_untargeted_production_result = compose_untargeted_cid_nl_production_result
prepare_untargeted_production_plan = prepare_untargeted_cid_nl_production_plan
UntargetedProductionBatch = UntargetedCidNlProductionBatch
UntargetedProductionPlan = UntargetedCidNlProductionPlan
materialize_untargeted_partition = materialize_untargeted_cid_nl_partition


__all__ = [
    "UntargetedCidNlPartitionComposition",
    "UntargetedCidNlPersistedPartition",
    "PreparedCidNlAlignmentSubjectWorksets",
    "UntargetedCidNlProductionBatch",
    "UntargetedCidNlProductionPlan",
    "UntargetedProductionBatch",
    "UntargetedProductionPlan",
    "build_untargeted_cid_nl_production_batch",
    "build_untargeted_production_batch",
    "compose_and_persist_untargeted_cid_nl_partition",
    "compose_untargeted_cid_nl_partition",
    "compose_untargeted_cid_nl_production_result",
    "compose_untargeted_production_result",
    "execute_untargeted_cid_nl_production_batch",
    "execute_untargeted_production_batch",
    "is_pure_single_provider_cid_nl",
    "is_supported_alignment_discovery",
    "materialize_untargeted_cid_nl_partition",
    "materialize_untargeted_partition",
    "prepare_untargeted_cid_nl_alignment_subject_worksets",
    "prepare_untargeted_cid_nl_production_plan",
    "prepare_untargeted_production_plan",
]
