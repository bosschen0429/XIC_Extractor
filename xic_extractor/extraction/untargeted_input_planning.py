"""Assemble compact batch identity while loading only local physical evidence."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path

from xic_extractor.alignment.discovery_input_source import DiscoveryInputSource
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySettings,
)
from xic_extractor.discovery.physical_fact_persistence import (
    IndexedDiscoveryPhysicalFacts,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    CANDIDATE_UNIVERSE_SCHEMA_VERSION,
    compose_untargeted_real_data_context,
)
from xic_extractor.extraction.untargeted_production_batch import (
    UntargetedCidNlProductionPlan,
    _method_center_interval,
    _physical_subject_units,
    cid_nl_composition,
    mint_subject_membership_from_physical_units,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.sample_metadata import SampleMetadata


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
) -> UntargetedCidNlProductionPlan:
    """Keep whole-batch IDs, not whole-batch candidates or physical graphs."""
    composition = cid_nl_composition(settings)
    feature_ids = source.feature_candidate_ids()
    precursor_by_id = {
        cid: physical_sources[source.locations[cid].sample_stem].precursor_mz[cid]
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
        fraction = settings.precursor_mz_tolerance_ppm / 1_000_000.0
        intervals = tuple(
            (
                precursor_by_id[cid] * (1.0 - fraction) ** 2 - 1e-6,
                precursor_by_id[cid] * (1.0 + fraction) ** 2 + 1e-6,
            )
            for cid in candidate_ids
        )
        support = source.load_review_support(
            intervals,
            sample_stems=frozenset(
                stem for stem, values in candidates.items() if values
            ),
        )
        return candidates, records, support

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
        local_units = _physical_subject_units(
            tuple(candidate for values in candidates.values() for candidate in values),
            physical_records_by_candidate=records,
            composition=composition,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        )
        units.extend(
            (loss, tuple(candidate.candidate_id for candidate in group))
            for loss, group in local_units
        )

    # Only same-source, same-apex, overlapping m/z cohorts can share a physical
    # core. Isolated entries require no curve hydration during planning.
    for (_, requirement, _), ids in buckets.items():
        cohort: list[str] = []
        upper = float("-inf")
        for cid in sorted(ids, key=lambda key: (precursor_by_id[key], key)):
            lower, next_upper = _method_center_interval(
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
    subject_batch = mint_subject_membership_from_physical_units(
        subject_units=tuple(units),
        precursor_by_id=precursor_by_id,
        settings=settings,
        composition=composition,
    )
    receipts = source.batch.candidate_receipts
    entries = []
    for stem in sorted(source):
        receipt = receipts[stem]
        assert receipt is not None
        entries.append(
            {
                "sample_stem": stem,
                "candidate_count": receipt.candidate_count,
                "candidate_ids": list(source.candidate_ids(stem)),
                "candidate_payload_sha256": receipt.candidate_payload_sha256,
                "discovery_receipt_id": receipt.discovery_receipt_id,
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
    context = compose_untargeted_real_data_context(
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
            "candidates": {
                cid: identity
                for physical_source in physical_sources.values()
                for cid, identity in physical_source.physical_identities.items()
            },
        },
        feature_candidate_ids=feature_ids,
    )
    return UntargetedCidNlProductionPlan(
        subject_batch=subject_batch,
        context=context,
        sample_job_templates=(),
        runtime_subjects=subject_batch.subjects,
        excluded_candidates=(),
        partition_input_loader=load_partition,
        dll_dir=dll_dir,
    )
