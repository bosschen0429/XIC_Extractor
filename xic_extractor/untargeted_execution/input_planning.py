"""Prepare eager or indexed Untargeted inputs and exact subject worksets."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    UntargetedCandidateExclusion,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
)
from xic_extractor.discovery.batch_contract import (
    CANDIDATE_UNIVERSE_SCHEMA_VERSION,
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.physical_fact_persistence import (
    IndexedDiscoveryPhysicalFacts,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalFactRecord,
    admits_cid_nl_alignment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlComposition,
    CidNlMethodSubject,
    CidNlPhysicalCandidateFact,
    CidNlPhysicalProof,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_alignment_subject_worksets,
    cid_nl_method_center_interval,
    group_cid_nl_physical_subject_units,
    mint_cid_nl_subject_membership_from_physical_units,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.sample_metadata import (
    SampleMetadata,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionJob,
    build_sample_jobs,
)
from xic_extractor.untargeted_execution.partition.request import (
    UntargetedPartitionContext,
    build_partition_context,
    compose_partition_context,
)


@dataclass(frozen=True, slots=True)
class UntargetedInputPlan:
    """Request-bound subject inputs with eager templates or a lazy partition loader."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedPartitionContext
    sample_job_templates: tuple[UntargetedPartitionJob, ...]
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
class PreparedUntargetedAlignmentWorksets:
    """One plan's complete Alignment worksets and partition lookup indexes."""

    plan: UntargetedInputPlan
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


def prepare_untargeted_alignment_worksets(
    plan: UntargetedInputPlan,
    *,
    known_istd_reference: KnownIstdReference | None = None,
) -> PreparedUntargetedAlignmentWorksets:
    """Prepare the complete workset closure once before partition materialization."""

    if type(plan) is not UntargetedInputPlan:
        raise TypeError("Alignment subject worksets require a production plan")
    worksets = alignment_subject_worksets(
        plan.runtime_subjects,
        known_istd_reference=known_istd_reference,
    )
    subjects_by_id = {
        subject.method_subject_id: subject
        for workset in worksets
        for subject in workset
    }
    workset_ids_by_subject: dict[str, frozenset[str]] = {}
    for workset in worksets:
        # One set per workset, shared by its members. Building it per subject
        # instead stores the same k ids k times.
        member_ids = frozenset(member.method_subject_id for member in workset)
        for subject in workset:
            workset_ids_by_subject[subject.method_subject_id] = member_ids
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
    return PreparedUntargetedAlignmentWorksets(
        plan=plan,
        worksets=worksets,
        subjects_by_id=subjects_by_id,
        workset_ids_by_subject=workset_ids_by_subject,
        memberships_by_subject=memberships_by_subject,
    )


def alignment_subject_worksets(
    subjects: tuple[CidNlMethodSubject, ...],
    *,
    known_istd_reference: KnownIstdReference | None = None,
) -> tuple[tuple[CidNlMethodSubject, ...], ...]:
    """Close compatible subjects including the supplied known-identity intervals."""
    return cid_nl_alignment_subject_worksets(
        subjects,
        comparison_mz_intervals=_known_istd_comparison_intervals(known_istd_reference),
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
    except TypeError, ValueError:
        return False
    return True


def is_supported_alignment_discovery(settings: DiscoverySettings) -> bool:
    """Return whether settings select a supported discovery method for alignment."""

    try:
        CidNlDiscoveryMethodSpec(settings=settings)
    except TypeError, ValueError:
        return False
    return True


def _physical_subject_candidate_fact(
    candidate: DiscoveryCandidate,
    record: DiscoveryPhysicalFactRecord,
) -> CidNlPhysicalCandidateFact:
    facts = record.evaluation.facts
    proof = None
    if facts is not None:
        trace = record.acquired_trace.trace
        proof = CidNlPhysicalProof(
            acquisition_source_id=record.work_item.acquisition_source_id,
            apex_rt_min=facts.apex_rt_min,
            left_rt_min=facts.left_rt_min,
            right_rt_min=facts.right_rt_min,
            xic_mz=record.work_item.xic_mz,
            xic_ppm_tol=record.work_item.xic_ppm_tol,
            rt=trace.rt,
            raw=trace.intensity,
        )
    return CidNlPhysicalCandidateFact(
        candidate_id=candidate.candidate_id,
        physical_mz=candidate.physical_mz,
        configured_neutral_loss_da=candidate.configured_neutral_loss_da,
        proof=proof,
    )


def prepare_untargeted_subject_membership(
    *,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    settings: DiscoverySettings,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord],
) -> UntargetedCidNlMethodSubjectBatch:
    """Resolve exact subject membership for a closed precursor working set."""
    candidates = tuple(
        candidate
        for sample_candidates in candidates_by_sample.values()
        for candidate in sample_candidates
        if admits_cid_nl_alignment(
            candidate, physical_records_by_candidate.get(candidate.candidate_id)
        )
    )
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Discovery candidate identity must be globally unique")

    composition = cid_nl_composition(settings)
    for candidate in candidates:
        precursor_mz = float(candidate.physical_mz)
        neutral_loss_da = float(candidate.configured_neutral_loss_da)
        if (
            not math.isfinite(precursor_mz)
            or precursor_mz <= 0.0
            or not math.isfinite(neutral_loss_da)
            or neutral_loss_da <= 0.0
        ):
            raise ValueError("Discovery candidate lacks finite CID-NL owner facts")
    physical_records = physical_records_by_candidate
    subject_units = group_cid_nl_physical_subject_units(
        tuple(
            _physical_subject_candidate_fact(
                candidate, physical_records[candidate.candidate_id]
            )
            for candidate in candidates
        ),
        composition=composition,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    return mint_cid_nl_subject_membership_from_physical_units(
        subject_units=subject_units,
        precursor_by_id={
            candidate.candidate_id: candidate.physical_mz for candidate in candidates
        },
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
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


def prepare_eager_untargeted_plan(
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
    signal_processing_profile: SignalProcessingProfile,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord],
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...]
    | None = None,
) -> UntargetedInputPlan:
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
    context = build_partition_context(
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
        feature_candidate_ids=tuple(
            cid for cid, _ in subject_batch.candidate_memberships
        ),
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
        for job in build_sample_jobs(
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
    return UntargetedInputPlan(
        subject_batch=subject_batch,
        context=context,
        sample_job_templates=templates,
        runtime_subjects=subject_batch.subjects,
        excluded_candidates=(),
    )


def prepare_indexed_untargeted_plan(
    *,
    source: DiscoveryInputSource,
    physical_sources: Mapping[str, IndexedDiscoveryPhysicalFacts],
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    dll_dir: Path,
    profile: SignalProcessingProfile,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...],
    sample_identity_namespace: str,
    restore_candidate: Callable[
        [DiscoveryCandidate, DiscoveryPhysicalFactRecord], DiscoveryCandidate
    ],
) -> UntargetedInputPlan:
    """Keep whole-batch IDs, not whole-batch candidates or physical graphs."""
    composition = cid_nl_composition(settings)
    feature_ids = tuple(
        cid
        for cid in source.feature_candidate_ids()
        if cid
        in physical_sources[source.locations[cid].sample_stem].resolved_candidate_ids
    )
    precursor_by_id = {
        cid: source.ms1_apex_mz_by_candidate.get(
            cid, source.locations[cid].precursor_mz
        )
        for cid in feature_ids
    }

    def load_inputs(candidate_ids: tuple[str, ...]):
        candidates = source.load_candidates(candidate_ids)
        records: dict[str, DiscoveryPhysicalFactRecord] = {}
        restored: dict[str, tuple[DiscoveryCandidate, ...]] = {}
        for stem, values in candidates.items():
            if not values:
                restored[stem] = ()
                continue
            local_records = physical_sources[stem].load_candidates(
                tuple(candidate.candidate_id for candidate in values)
            )
            records.update(local_records)
            restored[stem] = tuple(
                restore_candidate(candidate, local_records[candidate.candidate_id])
                for candidate in values
            )
        return restored, records

    def load_partition(candidate_ids: tuple[str, ...]):
        candidates, records = load_inputs(candidate_ids)
        return candidates, records, {stem: () for stem in source}

    units: list[tuple[float | None, tuple[str, ...]]] = []
    buckets: dict[tuple[str, float | None, float], list[str]] = defaultdict(list)
    source_ids = {
        path.stem: expectation.source.acquisition_source_id
        for path, expectation in zip(
            raw_paths, acquisition_source_expectations, strict=True
        )
    }
    for cid in feature_ids:
        location = source.locations[cid]
        requirement = (
            None if composition is not None else location.configured_neutral_loss_da
        )
        apex = physical_sources[location.sample_stem].physical_apices.get(cid)
        if apex is None:
            units.append((requirement, (cid,)))
        else:
            buckets[source_ids[location.sample_stem], requirement, apex].append(cid)

    def close_physical_cohort(requirement: float | None, ids: list[str]) -> None:
        if len(ids) == 1:
            units.append((requirement, (ids[0],)))
            return
        candidates, records = load_inputs(tuple(ids))
        local_units = group_cid_nl_physical_subject_units(
            tuple(
                _physical_subject_candidate_fact(
                    candidate, records.get(candidate.candidate_id)
                )
                for values in candidates.values()
                for candidate in values
            ),
            composition=composition,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        )
        units.extend(local_units)

    # Only same-source, same-apex, overlapping m/z cohorts can share a physical
    # core. Isolated entries require no curve hydration during planning.
    for (_, requirement, _), ids in buckets.items():
        cohort: list[str] = []
        upper = float("-inf")
        for cid in sorted(ids, key=lambda key: (precursor_by_id[key], key)):
            lower, next_upper = cid_nl_method_center_interval(
                precursor_by_id[cid], settings.precursor_mz_tolerance_ppm
            )
            if cohort and lower > upper:
                close_physical_cohort(requirement, cohort)
                cohort = []
            cohort.append(cid)
            upper = max(upper, next_upper)
        if cohort:
            close_physical_cohort(requirement, cohort)
    units.sort(
        key=lambda unit: (
            0.0 if unit[0] is None else unit[0],
            precursor_by_id[unit[1][0]],
            unit[1],
        )
    )
    subject_batch = mint_cid_nl_subject_membership_from_physical_units(
        subject_units=tuple(units),
        precursor_by_id=precursor_by_id,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        composition=composition,
    )
    receipts = source.batch.candidate_receipts
    entries = []
    for stem in sorted(source):
        receipt = receipts[stem]
        entries.append(
            {
                "sample_stem": stem,
                "candidate_count": receipt.candidate_count,
                "candidate_ids": list(source.candidate_ids(stem)),
                "candidate_artifact_schema_version": (
                    receipt.candidate_artifact_schema_version
                ),
                "candidate_artifact_sha256": receipt.candidate_artifact_sha256,
                "candidate_artifact_byte_size": receipt.candidate_artifact_byte_size,
                "acquisition_source_id": (
                    receipt.acquisition_source.acquisition_source_id
                ),
            }
        )
    expectations = {
        path.stem: expectation
        for path, expectation in zip(
            raw_paths, acquisition_source_expectations, strict=True
        )
    }
    context = compose_partition_context(
        method_subject=subject_batch,
        settings=settings,
        peak_config=peak_config,
        paths_by_stem={path.stem: path for path in raw_paths},
        metadata_by_stem={sample.raw_stem: sample for sample in sample_metadata},
        sources_by_stem={stem: item.source for stem, item in expectations.items()},
        source_expectations=expectations,
        candidate_map={stem: () for stem in source},
        receipt_map={
            stem: receipt for stem, receipt in receipts.items() if receipt is not None
        },
        physical_records={},
        sample_identity_namespace=sample_identity_namespace,
        signal_processing_profile=profile,
        candidate_universe={
            "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
            "samples": entries,
        },
        physical_capture={
            "schema_version": "physical_capture_inputs",
            "profile": json.loads(canonical_signal_processing_profile_bytes(profile)),
        },
        feature_candidate_ids=feature_ids,
    )
    return UntargetedInputPlan(
        subject_batch=subject_batch,
        context=context,
        sample_job_templates=(),
        runtime_subjects=subject_batch.subjects,
        excluded_candidates=(),
        partition_input_loader=load_partition,
        dll_dir=dll_dir,
    )
