from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.controls import (
    IdentityControlsConfig,
    IdentityDecoySource,
    evaluate_identity_controls,
    read_identity_controls_manifest,
)
from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceConfig,
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.identity_coherence.output import (
    IdentityCoherenceOutputContext,
    IdentityCoherenceOutputPaths,
    IdentityCoherenceOutputRecord,
    write_identity_coherence_outputs,
)
from xic_extractor.alignment.identity_coherence.record_builder import (
    IdentityCoherenceSeedTracePlan,
    build_cell_candidate_evidence,
    build_identity_coherence_output_record,
    candidate_pool_for_seed_source,
    trace_requests_for_seed_source,
)
from xic_extractor.alignment.identity_coherence.schema import SeedGateClass
from xic_extractor.alignment.identity_coherence.source_mapping import (
    IdentityCoherenceSeedSource,
    assignment_status_by_candidate_id,
    build_identity_coherence_seed_sources,
    candidate_decision_id,
    candidate_identity_family_id,
    candidate_is_non_seed_pool_member,
    candidate_request_id,
)
from xic_extractor.alignment.identity_coherence.trace_retrieval import (
    IdentityCoherenceRawSource,
    retrieve_identity_coherence_trace,
    retrieve_identity_coherence_traces,
    trace_request_for_candidate,
)
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.discovery.models import DiscoveryCandidate

__all__ = [
    "IdentityCoherenceDiagnosticRun",
    "IdentityCoherenceRawSource",
    "IdentityCoherenceSeedSource",
    "build_cell_candidate_evidence",
    "build_identity_coherence_seed_sources",
    "candidate_decision_id",
    "candidate_identity_family_id",
    "candidate_is_non_seed_pool_member",
    "candidate_request_id",
    "retrieve_identity_coherence_trace",
    "retrieve_identity_coherence_traces",
    "run_identity_coherence_diagnostic",
    "trace_request_for_candidate",
]


@dataclass(frozen=True)
class IdentityCoherenceDiagnosticRun:
    records: tuple[IdentityCoherenceOutputRecord, ...]
    control_rows: tuple[Mapping[str, object], ...]
    trace_results: tuple[IdentityCoherenceTraceResult, ...]
    context: IdentityCoherenceOutputContext
    paths: IdentityCoherenceOutputPaths


def run_identity_coherence_diagnostic(
    *,
    candidates: Sequence[DiscoveryCandidate],
    ownership: OwnershipBuildResult,
    sample_order: Sequence[str],
    raw_sources: Mapping[str, IdentityCoherenceRawSource],
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    raw_workers: int,
    raw_xic_batch_size: int,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    fragment_profile_id: str,
    fragment_profile_hash: str,
    config: IdentityCoherenceConfig | None = None,
    controls_manifest_path: Path | None = None,
    controls_config: IdentityControlsConfig | None = None,
) -> IdentityCoherenceDiagnosticRun:
    identity_config = config or IdentityCoherenceConfig()
    identity_controls_config = controls_config or IdentityControlsConfig()
    sources = build_identity_coherence_seed_sources(
        candidates=candidates,
        ownership=ownership,
        alignment_config=alignment_config,
        fragment_profile_id=fragment_profile_id,
        fragment_profile_hash=fragment_profile_hash,
        config=identity_config,
    )
    assignment_by_candidate_id = assignment_status_by_candidate_id(ownership)
    records_by_source_index: dict[int, IdentityCoherenceOutputRecord] = {}
    trace_plans: list[IdentityCoherenceSeedTracePlan] = []
    trace_requests: list[IdentityCoherenceTraceRequest] = []
    for source_index, source in enumerate(sources):
        if source.seed_gate.seed_gate_class != SeedGateClass.COHERENT_SEED:
            records_by_source_index[source_index] = (
                build_identity_coherence_output_record(
                    source,
                    candidate_pool=(),
                    trace_results=(),
                    assignment_by_candidate_id=assignment_by_candidate_id,
                    sample_order=tuple(sample_order),
                    config=identity_config,
                )
            )
            continue
        candidate_pool = candidate_pool_for_seed_source(
            source,
            candidates,
            alignment_config=alignment_config,
        )
        source_trace_requests = trace_requests_for_seed_source(
            source,
            candidate_pool,
            alignment_config=alignment_config,
        )
        trace_plans.append(
            IdentityCoherenceSeedTracePlan(
                source_index=source_index,
                source=source,
                candidate_pool=candidate_pool,
                trace_requests=source_trace_requests,
            )
        )
        trace_requests.extend(source_trace_requests)

    trace_results = retrieve_identity_coherence_traces(
        tuple(trace_requests),
        raw_sources=raw_sources,
        raw_paths=raw_paths,
        dll_dir=dll_dir,
        raw_workers=raw_workers,
        raw_xic_batch_size=raw_xic_batch_size,
    )
    trace_offset = 0
    for plan in trace_plans:
        source_trace_count = len(plan.trace_requests)
        source_trace_results = trace_results[
            trace_offset : trace_offset + source_trace_count
        ]
        records_by_source_index[plan.source_index] = (
            build_identity_coherence_output_record(
                plan.source,
                candidate_pool=plan.candidate_pool,
                trace_results=source_trace_results,
                assignment_by_candidate_id=assignment_by_candidate_id,
                sample_order=tuple(sample_order),
                config=identity_config,
            )
        )
        trace_offset += source_trace_count
    records = tuple(
        records_by_source_index[source_index] for source_index in range(len(sources))
    )

    decoy_sources = tuple(
        IdentityDecoySource(
            source_record=record,
            seed_evidence=source.seed_evidence,
            owner_like=source.owner,
            owner_assignment_status=source.owner_assignment_status,
        )
        for source, record in zip(sources, records, strict=True)
        if record.seed_gate.seed_gate_class == SeedGateClass.COHERENT_SEED
    )
    if controls_manifest_path is None:
        control_rows: tuple[Mapping[str, object], ...] = ()
        manifest_path = "not_provided"
    else:
        entries = read_identity_controls_manifest(controls_manifest_path)
        evaluation = evaluate_identity_controls(
            entries,
            records=records,
            decoy_sources=decoy_sources,
            config=identity_controls_config,
            seed_gate_config=identity_config.seed_gate,
        )
        control_rows = evaluation.rows
        manifest_path = str(controls_manifest_path)

    context = IdentityCoherenceOutputContext(
        command="xic-align-cli",
        mode=(
            "inline_pre_backfill_process"
            if raw_workers > 1
            else "inline_pre_backfill_serial"
        ),
        input_source="run_alignment.pre_backfill_ownership",
        control_manifest_path=manifest_path,
        raw_xic_request_count=sum(
            result.raw_xic_request_count for result in trace_results
        ),
        xic_point_count=sum(result.xic_point_count for result in trace_results),
        projected_full_cohort_identity_request_count=None,
        max_projected_full_cohort_identity_xic_requests=(
            identity_config.engineering.max_projected_full_cohort_identity_xic_requests
        ),
        max_infrastructure_blocked_fraction=(
            identity_config.engineering.max_infrastructure_blocked_fraction
        ),
        firewall_fixture_status="not_assessed",
        spawn_payload_smoke_status="not_assessed",
    )
    paths = write_identity_coherence_outputs(
        output_dir,
        records,
        context=context,
        control_rows=control_rows,
    )
    return IdentityCoherenceDiagnosticRun(
        records=records,
        control_rows=control_rows,
        trace_results=trace_results,
        context=context,
        paths=paths,
    )
