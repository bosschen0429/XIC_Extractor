"""Prepare indexed Untargeted inputs and exact subject worksets."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    UntargetedCandidateExclusion,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.physical_fact_persistence import (
    IndexedDiscoveryPhysicalFacts,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalFactRecord,
    validate_peak_physical_measurement,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlComposition,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_alignment_subject_worksets,
    mint_cid_nl_subject_membership,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.sample_metadata import (
    SampleMetadata,
)
from xic_extractor.untargeted_execution.partition.request import (
    UntargetedPartitionContext,
    compose_partition_context,
)


@dataclass(frozen=True, slots=True)
class UntargetedInputPlan:
    """Request-bound subject inputs with a lazy partition loader."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedPartitionContext
    runtime_subjects: tuple[CidNlMethodSubject, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    partition_input_loader: Callable[
        [tuple[str, ...]],
        tuple[
            dict[str, tuple[DiscoveryPeak, ...]],
            dict[str, DiscoveryPhysicalFactRecord],
            dict[str, tuple[DiscoveryPeak, ...]],
        ],
    ]
    dll_dir: Path


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


def is_supported_alignment_discovery(settings: DiscoverySettings) -> bool:
    """Return whether settings select a supported discovery method for alignment."""

    try:
        CidNlDiscoveryMethodSpec(settings=settings)
    except TypeError, ValueError:
        return False
    return True


def _subject_candidates(
    candidates: list[tuple[float | None, str]],
    precursor_by_id: Mapping[str, float],
) -> tuple[tuple[float | None, str], ...]:
    """Discovery peaks in subject order; each is one physical peak."""
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                0.0 if item[0] is None else item[0],
                precursor_by_id[item[1]],
                item[1],
            ),
        )
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
) -> UntargetedInputPlan:
    """Keep whole-batch IDs, not whole-batch candidates or physical graphs."""
    composition = cid_nl_composition(settings)
    feature_ids = tuple(
        cid
        for cid in source.feature_candidate_ids()
        if cid
        in physical_sources[source.locations[cid].sample_stem].resolved_candidate_ids
    )
    precursor_by_id = {cid: source.locations[cid].peak_mz for cid in feature_ids}

    def load_inputs(candidate_ids: tuple[str, ...]):
        candidates = source.load_candidates(candidate_ids)
        records: dict[str, DiscoveryPhysicalFactRecord] = {}
        for stem, values in candidates.items():
            if not values:
                continue
            local_records = physical_sources[stem].load_candidates(
                tuple(candidate.peak_id for candidate in values)
            )
            for candidate in values:
                validate_peak_physical_measurement(
                    candidate, local_records[candidate.peak_id]
                )
            records.update(local_records)
        return candidates, records

    def load_partition(candidate_ids: tuple[str, ...]):
        candidates, records = load_inputs(candidate_ids)
        located = {
            stem: tuple(
                replace(
                    candidate,
                    located_events=source.located_events(
                        candidate, neutral_loss_by_tag=settings.neutral_loss_by_tag
                    ),
                )
                for candidate in values
            )
            for stem, values in candidates.items()
        }
        return located, records, {stem: () for stem in source}

    subject_batch = mint_cid_nl_subject_membership(
        candidates=_subject_candidates(
            [
                (
                    None
                    if composition is not None
                    else settings.neutral_loss_by_tag[
                        source.locations[cid].neutral_loss_tag
                    ],
                    cid,
                )
                for cid in feature_ids
            ],
            precursor_by_id,
        ),
        precursor_by_id=precursor_by_id,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        composition=composition,
    )
    receipts = source.batch.peak_receipts
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
        source_expectations=expectations,
        peak_map={stem: () for stem in source},
        receipt_map={
            stem: receipt for stem, receipt in receipts.items() if receipt is not None
        },
        physical_records={},
        signal_processing_profile=profile,
    )
    return UntargetedInputPlan(
        subject_batch=subject_batch,
        context=context,
        runtime_subjects=subject_batch.subjects,
        excluded_candidates=(),
        partition_input_loader=load_partition,
        dll_dir=dll_dir,
    )
