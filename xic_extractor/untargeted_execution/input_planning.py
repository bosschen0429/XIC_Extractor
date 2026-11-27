"""Prepare indexed Untargeted inputs and exact subject worksets."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.alignment.cid_nl_grouping import alignment_subject_worksets
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    UntargetedCandidateExclusion,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak, representative_event_rt
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlComposition,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
    mint_cid_nl_subject_membership,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.processing_run_spec import validate_cid_nl_discovery_settings
from xic_extractor.sample_metadata import (
    SampleMetadata,
)
from xic_extractor.untargeted_execution.partition.request import (
    UntargetedPartitionContext,
    compose_partition_context,
)

PARTITION_CANDIDATE_BUDGET = 2048
"""Discovery candidates per partition. Every partition reopens each RAW once
per phase, so fewer partitions remove reopens; the largest partition's
grouping is held whole in one worker and in the parent."""


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
            dict[str, tuple[DiscoveryPeak, ...]],
        ],
    ]
    dll_dir: Path


@dataclass(frozen=True, slots=True)
class PreparedUntargetedAlignmentWorksets:
    """One plan's complete Alignment worksets and partition lookup indexes."""

    worksets: tuple[tuple[CidNlMethodSubject, ...], ...]
    memberships_by_subject: dict[str, tuple[tuple[str, str], ...]]

    def subject_batch(
        self,
        subjects: tuple[CidNlMethodSubject, ...],
    ) -> UntargetedCidNlMethodSubjectBatch:
        """Bind whole prepared worksets to their exact candidate memberships."""

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


def pack_subject_partitions(
    main: PreparedUntargetedAlignmentWorksets,
    *,
    max_partition_jobs: int,
    sample_count: int,
) -> tuple[tuple[CidNlMethodSubject, ...], ...]:
    """Pack whole worksets into partitions of bounded size and one mass bin."""
    ordered = sorted(
        main.worksets,
        key=lambda workset: (
            min(subject.precursor_mz for subject in workset),
            min(subject.method_subject_id for subject in workset),
        ),
    )

    def by_id(subjects: list[CidNlMethodSubject]) -> tuple[CidNlMethodSubject, ...]:
        return tuple(sorted(subjects, key=lambda subject: subject.method_subject_id))

    subjects_per_partition = max(1, max_partition_jobs // sample_count)
    packed: list[tuple[CidNlMethodSubject, ...]] = []
    current: list[CidNlMethodSubject] = []
    current_candidate_count = 0
    current_mass_bin: int | None = None
    for unit in ordered:
        mass_bin = int(min(subject.precursor_mz for subject in unit) // 100.0)
        candidate_count = sum(
            len(main.memberships_by_subject.get(subject.method_subject_id, ()))
            for subject in unit
        )
        if len(unit) > subjects_per_partition:
            raise ValueError(
                "max_partition_jobs cannot contain one alignment subject workset"
            )
        if current and (
            len(current) + len(unit) > subjects_per_partition
            or current_mass_bin != mass_bin
            or current_candidate_count + candidate_count > PARTITION_CANDIDATE_BUDGET
        ):
            packed.append(by_id(current))
            current = []
            current_candidate_count = 0
        current.extend(unit)
        current_candidate_count += candidate_count
        current_mass_bin = mass_bin
    if current:
        packed.append(by_id(current))
    return tuple(packed)


def prepare_untargeted_alignment_worksets(
    plan: UntargetedInputPlan,
    *,
    known_istd_reference: KnownIstdReference | None = None,
) -> PreparedUntargetedAlignmentWorksets:
    """Prepare the complete workset closure once before partition materialization."""

    worksets = alignment_subject_worksets(
        plan.runtime_subjects,
        known_istd_reference=known_istd_reference,
    )
    memberships: dict[str, list[tuple[str, str]]] = {
        subject.method_subject_id: [] for workset in worksets for subject in workset
    }
    for membership in plan.subject_batch.candidate_memberships:
        subject_memberships = memberships.get(membership[1])
        if subject_memberships is not None:
            subject_memberships.append(membership)
    return PreparedUntargetedAlignmentWorksets(
        worksets=worksets,
        memberships_by_subject={
            subject_id: tuple(sorted(subject_memberships))
            for subject_id, subject_memberships in memberships.items()
        },
    )


def is_supported_alignment_discovery(settings: DiscoverySettings) -> bool:
    """Return whether settings select a supported discovery method for alignment."""

    try:
        validate_cid_nl_discovery_settings(settings)
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
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    dll_dir: Path,
    profile: SignalProcessingProfile,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...],
    injections: InjectionSequence | None = None,
) -> UntargetedInputPlan:
    """Keep whole-batch IDs, not whole-batch candidates or physical graphs."""
    composition = cid_nl_composition(settings)
    feature_ids = source.feature_candidate_ids()
    precursor_by_id = {cid: source.locations[cid].peak_mz for cid in feature_ids}

    def locate(candidate: DiscoveryPeak) -> DiscoveryPeak:
        located = replace(
            candidate,
            located_events=source.located_events(
                candidate, neutral_loss_by_tag=settings.neutral_loss_by_tag
            ),
        )
        representative_event_rt(located)
        return located

    def load_partition(candidate_ids: tuple[str, ...]):
        candidates = source.load_candidates(candidate_ids)
        located = {
            stem: tuple(locate(candidate) for candidate in values)
            for stem, values in candidates.items()
        }
        return located, {stem: () for stem in source}

    ordered = _subject_candidates(
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
    )

    def bind(
        pairs: tuple[tuple[float | None, str], ...],
    ) -> UntargetedCidNlMethodSubjectBatch:
        return mint_cid_nl_subject_membership(
            candidates=pairs,
            precursor_by_id=precursor_by_id,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            composition=composition,
        )

    subject_batch = bind(ordered)
    expectations = {
        path.stem: expectation
        for path, expectation in zip(
            raw_paths, acquisition_source_expectations, strict=True
        )
    }
    context = compose_partition_context(
        method_subject=subject_batch,
        settings=settings,
        paths_by_stem={path.stem: path for path in raw_paths},
        metadata_by_stem={sample.raw_stem: sample for sample in sample_metadata},
        source_expectations=expectations,
        peak_map={stem: () for stem in source},
        receipt_map=source.batch.peak_receipts,
        signal_processing_profile=profile,
        injections=injections,
    )
    return UntargetedInputPlan(
        subject_batch=subject_batch,
        context=context,
        runtime_subjects=subject_batch.subjects,
        excluded_candidates=(),
        partition_input_loader=load_partition,
        dll_dir=dll_dir,
    )
