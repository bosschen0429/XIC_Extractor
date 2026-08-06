from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, asdict, dataclass
from pathlib import Path
from typing import cast, final

from xic_extractor.alignment import quant_matrix_version as quant_matrix
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ModeTrackConservativeBackfillGroupFacts,
    assess_mode_track_conservative_backfill_groups,
)
from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.identity_coherence.mode_track_producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.production_acceptance import (
    build_quant_matrix_version_from_checked_manifest,
    load_checked_production_acceptance_manifest,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlModeTrackProjection,
    build_untargeted_cid_nl_mode_track_projection,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import RuntimeMetricAvailability
from xic_extractor.execution_runtime_owner import (
    CoordinatorTerminalRecord,
    RuntimeArtifactIoSample,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
    discovery_settings_payload,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    verify_persisted_peak_observation_claims,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_extractor.tabular_io import numeric_equal, render_delimited_rows

SAMPLE_STEM = "TumorBC2297_DNA"
PRECURSOR_MZ = 349.215
NEUTRAL_LOSS_DA = 116.0474
VALIDATION_STATUS = "diagnostic_only"
_HEARTBEAT_SCHEMA_VERSION = "issue180_m9_validation_heartbeat_v1"
_RUNTIME_TELEMETRY_UNAVAILABLE_REASON = "not_collected_by_issue180_m9_validation"
RUN_CONTEXT_FIELDS = frozenset(
    (
        "code_sha processing_request_id processing_attempt_id call_cost "
        "candidate_census"
    ).split()
)


@dataclass(frozen=True)
class ModeTrackValidationRun:
    projection: UntargetedCidNlModeTrackProjection
    facts: tuple[ModeTrackConservativeBackfillGroupFacts, ...]
    call_cost: Mapping[str, int]
    discovery_index: Path
    settings: DiscoverySettings
    owners: ProcessingOwnerReferences
    started: float


@dataclass(frozen=True, slots=True, order=True)
class _GateCReviewSubject:
    group_hypothesis_id: str
    sample_stem: str
    sample_identity_id: str
    peak_observation_content_id: str
    raw_area: str


_VALIDATED_GATE_C_REVIEW_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class _ValidatedGateCReviewSet:
    subjects: tuple[_GateCReviewSubject, ...]
    manual_review_sha256: str
    manual_review_replay_sha256: str
    _mint_token: InitVar[object]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("_ValidatedGateCReviewSet is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _VALIDATED_GATE_C_REVIEW_MINT_TOKEN:
            raise ValueError("Gate C review set must be minted by its validator")
        if not self.subjects or len(self.subjects) != len(set(self.subjects)):
            raise ValueError("Gate C validated review subject census is malformed")
        for value in (
            self.manual_review_sha256,
            self.manual_review_replay_sha256,
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError("Gate C validated review digest is malformed")


def gate_c_launch_contract() -> dict[str, object]:
    return {
        "schema_version": "issue180_m9_gate_c_launch_contract_v2",
        "validation_status": VALIDATION_STATUS,
        "precursor_mz": PRECURSOR_MZ,
        "neutral_loss_da": NEUTRAL_LOSS_DA,
        "alignment_config": {"anchor_min_seed_events": 1},
        "trajectory_policy": asdict(_mode_track_trajectory_policy()),
        "call_budget": {
            "discovery_raw_open_count_max": 85,
            "m7_raw_open_count_max": 85,
            "total_raw_open_count_max": 170,
            "m7_xic_batch_count_max": 85,
        },
        "product_authority": "none_before_checked_c2",
    }


def run_gate_b(*, raw_file: Path, dll_dir: Path, output_dir: Path) -> Path:
    if raw_file.stem != SAMPLE_STEM:
        raise ValueError(f"expected the existing {SAMPLE_STEM}.raw canary")
    metadata = SampleMetadata(
        sample_name=SAMPLE_STEM,
        raw_stem=SAMPLE_STEM,
        injection_order=1,
        sample_role="study_sample",
        batch_id="issue180-m9-gate-b",
        matrix_type="breast_tissue",
        group="tumor",
    )
    run = _run_mode_track_batch(
        raw_files=(raw_file,),
        sample_metadata=(metadata,),
        dll_dir=dll_dir,
        output_dir=output_dir,
        source_relpath=f"output/{output_dir.name}/mode_track_source.json",
        identity_namespace="issue180-m9-gate-b",
    )
    (output_dir / "mode_track_source.json").write_bytes(
        run.projection.source_artifact_bytes
    )
    rts = sorted(cast(float, fact.group.group_center_rt) for fact in run.facts)
    if len(rts) != 2 or any(
        abs(a - b) > 0.1 for a, b in zip(rts, (12.4123, 13.0354), strict=True)
    ):
        raise RuntimeError("Gate B two-track census changed")
    if (
        sum(f.direct_support_count_s for f in run.facts) != 1
        or sum(len(f.exact_owner_candidate_assignments) for f in run.facts) != 1
        or run.call_cost["discovery_raw_open_count"] != 1
        or run.call_cost["m7_raw_open_count"] != 1
        or run.call_cost["total_raw_open_count"] != 2
        or run.projection.pending_acceptance_rows
        or run.projection.expected_diff_rows
    ):
        raise RuntimeError("Gate B support/candidate/write census changed")
    receipt = output_dir / "gate_b_receipt.json"
    _write_json(
        receipt,
        {
            "schema_version": "issue180_m9_gate_b_receipt_v1",
            "validation_status": VALIDATION_STATUS,
            "sample_stem": SAMPLE_STEM,
            "precursor_mz": PRECURSOR_MZ,
            "neutral_loss_da": NEUTRAL_LOSS_DA,
            "fresh_owner_bound_discovery_index_sha256": _sha256(run.discovery_index),
            "sample_metadata": asdict(metadata),
            "settings_sha256": _json_sha256(discovery_settings_payload(run.settings)),
            "call_cost": dict(run.call_cost),
            "track_facts": [_fact_row(fact) for fact in run.facts],
            "pending_acceptance_count": 0,
            "expected_diff_count": 0,
            "source_artifact_sha256": run.projection.source_artifact_sha256,
            "product_authority": run.projection.product_authority,
            "elapsed_seconds": time.perf_counter() - run.started,
        },
    )
    _write_json(output_dir / "timing.live.json", _heartbeat("completed", run.started))
    return receipt


def run_gate_c(
    *,
    raw_files: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    dll_dir: Path,
    manifest_root: Path,
    output_dir: Path,
    code_sha: str,
    metadata_sha256: str,
) -> Path:
    if len(raw_files) != 85 or len(sample_metadata) != 85:
        raise ValueError("Gate C requires exactly 85 RAW and metadata rows")
    proposal_dir = output_dir / "proposal"
    source_relpath = (
        proposal_dir.resolve().relative_to(manifest_root.resolve())
        / "mode_track_source.json"
    ).as_posix()
    run = _run_mode_track_batch(
        raw_files=raw_files,
        sample_metadata=sample_metadata,
        dll_dir=dll_dir,
        output_dir=output_dir,
        source_relpath=source_relpath,
        identity_namespace="issue180-m9-gate-c",
    )
    _validate_gate_c_call_budget(run.call_cost)
    context = {
        "code_sha": code_sha,
        "processing_request_id": run.owners.request.processing_request_id,
        "processing_attempt_id": run.owners.attempt.processing_attempt_id,
        "call_cost": dict(run.call_cost),
        "candidate_census": {
            "metadata_sha256": metadata_sha256,
            "discovery_index_sha256": _sha256(run.discovery_index),
            "track_count": len(run.facts),
            "pending": len(run.projection.pending_acceptance_rows),
        },
    }
    receipt = freeze_gate_c_proposal(
        projection=run.projection,
        manifest_root=manifest_root,
        output_dir=proposal_dir,
        run_context=context,
    )
    _write_json(output_dir / "timing.live.json", _heartbeat("completed", run.started))
    return receipt


def run_gate_c_fresh_finalization(
    *,
    raw_files: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    dll_dir: Path,
    manifest_root: Path,
    replay_output_dir: Path,
    final_output_dir: Path,
    proposal_dir: Path,
    manual_review_path: Path,
    manual_review_replay_path: Path,
    review_transfer_receipt_path: Path,
    manifest_path: Path,
    code_sha: str,
    metadata_sha256: str,
) -> Path:
    """Recompute M2 through its owner and close Gate C in the same process."""

    if len(raw_files) != 85 or len(sample_metadata) != 85:
        raise ValueError("Gate C requires exactly 85 RAW and metadata rows")
    manifest_root = manifest_root.resolve()
    replay_output_dir = replay_output_dir.resolve()
    final_output_dir = final_output_dir.resolve()
    transaction_staging_dir = _gate_c_transaction_staging_dir(final_output_dir)
    if (
        replay_output_dir.exists()
        or final_output_dir.exists()
        or transaction_staging_dir.exists()
    ):
        raise ValueError("Gate C C2 replay or final output already exists")
    for target in (replay_output_dir, final_output_dir):
        if not target.is_relative_to(manifest_root):
            raise ValueError("Gate C C2 outputs must stay under manifest root")

    _validate_review_transfer_receipt(
        receipt_path=review_transfer_receipt_path,
        code_sha=code_sha,
        proposal_dir=proposal_dir,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
        manifest_path=manifest_path,
        manifest_root=manifest_root,
    )

    source_path = replay_output_dir / "fresh_mode_track_source.json"
    run = _run_mode_track_batch(
        raw_files=raw_files,
        sample_metadata=sample_metadata,
        dll_dir=dll_dir,
        output_dir=replay_output_dir,
        source_relpath=source_path.relative_to(manifest_root).as_posix(),
        identity_namespace="issue180-m9-gate-c",
    )
    _validate_gate_c_call_budget(run.call_cost)
    source_path.write_bytes(run.projection.source_artifact_bytes)
    scientific_rows = _mode_track_scientific_projection_rows(run.facts)
    proposal_path = proposal_dir / "proposal.json"
    proposal_receipt_path = proposal_dir / "proposal_receipt.json"
    fresh_receipt_path = replay_output_dir / "fresh_owner_receipt.json"
    _write_json(
        fresh_receipt_path,
        {
            "schema_version": "issue180_m9_gate_c_fresh_owner_receipt_v1",
            "validation_status": VALIDATION_STATUS,
            "code_sha": code_sha,
            "metadata_sha256": metadata_sha256,
            "processing_request_id": run.owners.request.processing_request_id,
            "processing_attempt_id": run.owners.attempt.processing_attempt_id,
            "call_cost": dict(run.call_cost),
            "call_cost_authority": "same_attempt_audit_only",
            "scientific_projection_sha256": _json_sha256(scientific_rows),
            "fresh_source_sha256": _sha256(source_path),
            "c1_proposal_sha256": _sha256(proposal_path),
            "c1_proposal_receipt_sha256": _sha256(proposal_receipt_path),
            "audit_review_transfer_receipt_sha256": _sha256(
                review_transfer_receipt_path
            ),
            "eligibility_authority": "live_typed_m2_facts_same_process_only",
            "writer_input_authority": "fresh_projection_only",
        },
    )
    validated_review = _rehydrate_gate_c_review_set(
        proposal_dir=proposal_dir,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
        manifest_root=manifest_root,
        fresh_projection=run.projection,
    )
    transaction_receipt_path = _finalize_gate_c_transaction(
        projection=run.projection,
        validated_review=validated_review,
        manifest_root=manifest_root,
        final_output_dir=final_output_dir,
        code_sha=code_sha,
        metadata_sha256=metadata_sha256,
        fresh_owner_receipt_path=fresh_receipt_path,
        review_transfer_receipt_path=review_transfer_receipt_path,
    )
    _write_json(
        replay_output_dir / "timing.live.json",
        _heartbeat("completed", run.started),
    )
    return transaction_receipt_path


def _gate_c_transaction_staging_dir(final_output_dir: Path) -> Path:
    return final_output_dir.with_name(f"{final_output_dir.name}.transaction-staging")


def _finalize_gate_c_transaction(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    validated_review: _ValidatedGateCReviewSet,
    manifest_root: Path,
    final_output_dir: Path,
    code_sha: str,
    metadata_sha256: str,
    fresh_owner_receipt_path: Path,
    review_transfer_receipt_path: Path,
) -> Path:
    """Publish the QMV, final receipt, and transaction receipt in one rename."""

    transaction_staging_dir = _gate_c_transaction_staging_dir(final_output_dir)
    if final_output_dir.exists() or transaction_staging_dir.exists():
        raise ValueError("Gate C final or transaction staging output already exists")
    final_receipt_path = finalize_gate_c_fresh_projection(
        projection=projection,
        validated_review=validated_review,
        manifest_root=manifest_root,
        output_dir=transaction_staging_dir,
    )
    transaction_receipt_path = transaction_staging_dir / "transaction_receipt.json"
    _write_json(
        transaction_receipt_path,
        {
            "schema_version": "issue180_m9_gate_c_transaction_receipt_v2",
            "validation_status": VALIDATION_STATUS,
            "code_sha": code_sha,
            "metadata_sha256": metadata_sha256,
            "fresh_owner_receipt_sha256": _sha256(fresh_owner_receipt_path),
            "final_receipt_sha256": _sha256(final_receipt_path),
            "audit_review_transfer_receipt_sha256": _sha256(
                review_transfer_receipt_path
            ),
            "product_authority": "checked_qmv_owner_only",
        },
    )
    transaction_staging_dir.replace(final_output_dir)
    return final_output_dir / "transaction_receipt.json"


def _run_mode_track_batch(
    *,
    raw_files: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    dll_dir: Path,
    output_dir: Path,
    source_relpath: str,
    identity_namespace: str,
) -> ModeTrackValidationRun:
    if output_dir.exists() or not raw_files or len(raw_files) != len(sample_metadata):
        raise ValueError("validation batch output or sample universe is invalid")
    if not dll_dir.is_dir() or any(not path.is_file() for path in raw_files):
        raise ValueError("RAW or Thermo DLL input does not exist")
    output_dir.mkdir(parents=True)
    started = time.perf_counter()
    heartbeat_path = output_dir / "timing.live.json"
    _write_json(heartbeat_path, _heartbeat("started", started))
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=NEUTRAL_LOSS_DA,
        )
    )
    discovery_dir = output_dir / "discovery"
    peak_config = _peak_config(raw_files[0].parent, dll_dir, discovery_dir, settings)
    discovery = run_discovery_batch(
        raw_files,
        output_dir=discovery_dir,
        settings=settings,
        peak_config=peak_config,
    )
    _write_json(heartbeat_path, _heartbeat("discovery_complete", started))
    batch = read_discovery_batch_index(
        discovery.batch_index_csv,
        require_owner_closure=True,
    )
    candidates_by_sample = {}
    receipts_by_sample = {}
    for raw_file in raw_files:
        receipt = batch.candidate_receipts[raw_file.stem]
        if receipt is None:
            raise ValueError("fresh Discovery did not mint every owner receipt")
        receipts_by_sample[raw_file.stem] = receipt
        candidates_by_sample[raw_file.stem] = read_owner_bound_discovery_candidates_csv(
            batch.candidate_csvs[raw_file.stem],
            receipt=receipt,
            require_successor_columns=True,
        )
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=PRECURSOR_MZ,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=NEUTRAL_LOSS_DA,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_files,
        sample_metadata=sample_metadata,
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=receipts_by_sample,
        sample_identity_namespace=identity_namespace,
    )
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=context.method_subject,
            candidates=context.candidates_by_sample[raw_file.stem],
            discovery_receipt=receipts_by_sample[raw_file.stem],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=raw_file,
            dll_dir=dll_dir,
            work_unit_key=f"untargeted-cid-nl:{raw_file.stem}",
        )
        for raw_file, sample in zip(raw_files, context.samples, strict=True)
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation=f"{identity_namespace}:v1",
    )
    runtime_result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=_coarse_runtime_telemetry,
        recovery_marker=OwnerPathRuntimeMarker(
            output_dir / "runtime.json",
            identity_namespace,
        ),
    )
    if not runtime_result.succeeded or len(runtime_result.sample_results) != len(jobs):
        raise RuntimeError("M7 validation batch did not complete atomically")
    m7_call_cost = untargeted_cid_nl_runtime_call_cost(runtime_result.finalization)
    if m7_call_cost["raw_open_count"] > len(raw_files):
        raise RuntimeError("M7 validation batch exceeded one RAW open per sample")
    call_cost = _gate_c_workload_call_cost(
        discovery_raw_open_count=len(raw_files),
        m7_call_cost=m7_call_cost,
    )
    _write_json(heartbeat_path, _heartbeat("m7_complete", started))
    sample_assessments = tuple(
        (sample, result.adapter_result.assessment_input.assessment)
        for sample, result in zip(
            context.samples, runtime_result.sample_results, strict=True
        )
    )
    _, model_set, batches = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=context.processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=_mode_track_trajectory_policy(),
    )
    facts = assess_mode_track_conservative_backfill_groups(
        processing_owners=context.processing_owners,
        model_set=model_set,
        assignment_batches=batches,
        sample_assessments=sample_assessments,
    )
    projection = build_untargeted_cid_nl_mode_track_projection(
        processing_owners=context.processing_owners,
        mode_track_facts=facts,
        source_artifact_relpath=source_relpath,
    )
    return ModeTrackValidationRun(
        projection=projection,
        facts=facts,
        call_cost=call_cost,
        discovery_index=discovery.batch_index_csv,
        settings=settings,
        owners=context.processing_owners,
        started=started,
    )


def _gate_c_workload_call_cost(
    *,
    discovery_raw_open_count: int,
    m7_call_cost: Mapping[str, int],
) -> dict[str, int]:
    """Report Discovery and M7 costs without conflating their owners."""

    m7_raw_open_count = m7_call_cost["raw_open_count"]
    return {
        "discovery_raw_open_count": discovery_raw_open_count,
        "m7_raw_open_count": m7_raw_open_count,
        "total_raw_open_count": discovery_raw_open_count + m7_raw_open_count,
        "m7_xic_batch_count": m7_call_cost["xic_batch_count"],
        "m7_logical_xic_request_count": m7_call_cost["logical_xic_request_count"],
        "m7_adapter_chromatogram_call_count": m7_call_cost[
            "adapter_chromatogram_call_count"
        ],
        "m7_trace_point_count": m7_call_cost["trace_point_count"],
    }


def _validate_gate_c_call_budget(call_cost: Mapping[str, int]) -> None:
    if (
        call_cost["discovery_raw_open_count"] > 85
        or call_cost["m7_raw_open_count"] > 85
        or call_cost["total_raw_open_count"] > 170
        or call_cost["m7_xic_batch_count"] > 85
    ):
        raise RuntimeError("Gate C exceeded the sample-batched RAW call budget")


def _mode_track_trajectory_policy() -> ModeTrackTrajectoryPolicy:
    return ModeTrackTrajectoryPolicy(
        trajectory_policy_version="anchor-trajectory-v1",
        band_policy_version="fixed-band-v1",
        prediction_half_width_min=2 / 60,
        max_interpolation_gap=10,
        max_extrapolation_gap=10,
    )


def _fact_row(fact: ModeTrackConservativeBackfillGroupFacts) -> dict[str, object]:
    return {
        "group_hypothesis_id": fact.group.group_hypothesis_id,
        "group_center_rt": fact.group.group_center_rt,
        "sample_count_n": fact.sample_count_n,
        "minimum_support_k": fact.minimum_support_k,
        "direct_support_count_s": fact.direct_support_count_s,
        "candidate_count": len(fact.exact_owner_candidate_assignments),
        "may_attempt_recovery": fact.may_attempt_recovery,
        "reason_codes": list(fact.reason_codes),
    }


def _mode_track_scientific_projection_rows(
    facts: Sequence[ModeTrackConservativeBackfillGroupFacts],
) -> list[dict[str, object]]:
    """Render only cross-attempt-stable scientific facts from the live M2 owner."""

    rows: list[dict[str, object]] = [
        {
            "group_hypothesis_id": fact.group.group_hypothesis_id,
            "group_center_mz": fact.group.group_center_mz,
            "group_center_rt": fact.group.group_center_rt,
            "sample_population_ids": [
                sample.identity.sample_identity_id for sample in fact.sample_population
            ],
            "activation_eligible_sample_ids": list(fact.activation_eligible_sample_ids),
            "direct_support": [
                {
                    "sample_identity_id": assignment.canonical_sample_identity_id,
                    "peak_observation_content_id": (
                        assignment.peak_observation_content_id
                    ),
                }
                for assignment in fact.direct_support_assignments
            ],
            "exact_owner_candidates": [
                {
                    "sample_identity_id": assignment.canonical_sample_identity_id,
                    "peak_observation_content_id": (
                        assignment.peak_observation_content_id
                    ),
                }
                for assignment in fact.exact_owner_candidate_assignments
            ],
            "sample_count_n": fact.sample_count_n,
            "minimum_support_k": fact.minimum_support_k,
            "direct_support_count_s": fact.direct_support_count_s,
            "group_frozen": fact.group_frozen,
            "may_attempt_recovery": fact.may_attempt_recovery,
            "reason_codes": list(fact.reason_codes),
        }
        for fact in facts
    ]
    group_ids = [row["group_hypothesis_id"] for row in rows]
    if not rows or len(group_ids) != len(set(group_ids)):
        raise ValueError("fresh M2 scientific projection group census is malformed")
    return rows


def _validate_fresh_mode_track_projection(
    *,
    persisted_rows: Sequence[Mapping[str, object]],
    fresh_facts: Sequence[ModeTrackConservativeBackfillGroupFacts],
) -> list[dict[str, object]]:
    expected = _mode_track_scientific_projection_rows(fresh_facts)
    loaded = [dict(row) for row in persisted_rows]
    if loaded != expected:
        raise ValueError("Gate C fresh M2 scientific projection differs from C1")
    return expected


def _validate_fresh_proposed_keys(
    proposal: Mapping[str, object],
    fresh_facts: Sequence[ModeTrackConservativeBackfillGroupFacts],
) -> None:
    expected: dict[tuple[object, object, object, object], tuple[str, str]] = {}
    for fact in fresh_facts:
        decision = "proposed" if fact.may_attempt_recovery else "rejected"
        reason = (
            "m2_activation_passed"
            if fact.may_attempt_recovery
            else "m2_support_below_threshold"
        )
        for candidate in fact.exact_owner_candidate_assignments:
            fresh_key = (
                fact.group.group_hypothesis_id,
                candidate.canonical_sample_identity_id,
                candidate.sample.metadata.raw_stem,
                candidate.peak_observation_content_id,
            )
            if fresh_key in expected or any(value is None for value in fresh_key):
                raise ValueError("fresh M2 candidate identity is duplicate")
            expected[fresh_key] = (decision, reason)

    decisions = _required_mapping_rows(proposal, "candidate_decision_rows")
    observed: dict[tuple[object, object, object, object], tuple[object, object]] = {}
    for row in decisions:
        persisted_key = (
            row.get("group_hypothesis_id"),
            row.get("sample_identity_id"),
            row.get("sample_stem"),
            row.get("peak_observation_content_id"),
        )
        if persisted_key in observed:
            raise ValueError("Gate C persisted candidate decision is duplicate")
        observed[persisted_key] = (row.get("decision"), row.get("reason_code"))
    if observed != expected:
        raise ValueError("Gate C persisted candidate decisions differ from fresh M2")


def _candidate_decision_rows(
    facts: Sequence[ModeTrackConservativeBackfillGroupFacts],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fact in facts:
        for assignment in fact.exact_owner_candidate_assignments:
            rows.append(
                {
                    "group_hypothesis_id": fact.group.group_hypothesis_id,
                    "sample_identity_id": assignment.canonical_sample_identity_id,
                    "sample_stem": assignment.sample.metadata.raw_stem,
                    "assignment_id": assignment.assignment_id,
                    "peak_hypothesis_id": assignment.peak_hypothesis_id,
                    "peak_observation_content_id": (
                        assignment.peak_observation_content_id
                    ),
                    "decision": (
                        "proposed" if fact.may_attempt_recovery else "rejected"
                    ),
                    "reason_code": (
                        "m2_activation_passed"
                        if fact.may_attempt_recovery
                        else "m2_support_below_threshold"
                    ),
                }
            )
    return rows


def freeze_gate_c_proposal(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    manifest_root: Path,
    output_dir: Path,
    run_context: Mapping[str, object],
) -> Path:
    if set(run_context) != RUN_CONTEXT_FIELDS:
        raise ValueError("Gate C run context fields changed")
    manifest_root, output_dir = manifest_root.resolve(), output_dir.resolve()
    if not output_dir.is_relative_to(manifest_root):
        raise ValueError("proposal output must stay under manifest root")
    source_path = manifest_root / projection.source_artifact_relpath
    if source_path.parent.resolve() != output_dir:
        raise ValueError("projection source path must be inside final proposal output")
    if not projection.pending_acceptance_rows:
        raise ValueError("Gate C proposal must contain at least one pending row")
    staging = output_dir.with_name(f"{output_dir.name}.staging")
    if output_dir.exists() or staging.exists():
        raise ValueError("Gate C proposal or staging output already exists")
    staging.mkdir(parents=True)
    staged_source = staging / source_path.name
    staged_source.write_bytes(projection.source_artifact_bytes)
    fact_rows = [_fact_row(fact) for fact in projection.mode_track_facts]
    decision_rows = _candidate_decision_rows(projection.mode_track_facts)
    scientific_rows = _mode_track_scientific_projection_rows(
        projection.mode_track_facts
    )
    proposal = {
        "schema_version": "issue180_m9_gate_c_proposal_v2",
        "matrix_header": list(projection.matrix_header),
        "input_quant_matrix_rows": list(map(dict, projection.input_quant_matrix_rows)),
        "input_matrix_identity_rows": list(
            map(dict, projection.input_matrix_identity_rows)
        ),
        "pending_acceptance_rows": list(map(dict, projection.pending_acceptance_rows)),
        "expected_diff_rows": list(map(dict, projection.expected_diff_rows)),
        "mode_track_fact_rows": fact_rows,
        "candidate_decision_rows": decision_rows,
        "mode_track_scientific_projection_rows": scientific_rows,
        "source_artifact_relpath": projection.source_artifact_relpath,
        "source_artifact_sha256": projection.source_artifact_sha256,
    }
    proposal_path = staging / "proposal.json"
    _write_json(proposal_path, proposal)
    context = dict(run_context)
    supplied_census = context.get("candidate_census")
    if not isinstance(supplied_census, dict):
        raise ValueError("Gate C run context candidate census is missing")
    proposed_count = sum(row["decision"] == "proposed" for row in decision_rows)
    expected_supplied = {
        "track_count": len(fact_rows),
        "pending": proposed_count,
    }
    if any(
        key in supplied_census and supplied_census[key] != value
        for key, value in expected_supplied.items()
    ):
        raise ValueError("Gate C run context candidate census changed")
    supplied_census.update(
        {
            **expected_supplied,
            "rejected": len(decision_rows) - proposed_count,
            "exact_owner_candidate_count": len(decision_rows),
            "mode_track_facts_sha256": _json_sha256(fact_rows),
            "candidate_decisions_sha256": _json_sha256(decision_rows),
        }
    )
    context["candidate_census"] = supplied_census
    receipt = {
        "schema_version": "issue180_m9_gate_c_proposal_receipt_v2",
        "proposal_sha256": _sha256(proposal_path),
        "source_file_sha256": _sha256(staged_source),
        "run_context": context,
    }
    _validate_proposal_payload(proposal, receipt, staged_source)
    _publish_json_marker(staging / "proposal_receipt.json", receipt)
    staging.replace(output_dir)
    return output_dir / "proposal_receipt.json"


def transfer_gate_c_review(
    *,
    reviewed_packet_dir: Path,
    proposal_dir: Path,
    manifest_root: Path,
    output_dir: Path,
    code_sha: str,
) -> Path:
    """Transfer a validated human review only across exact physical subjects."""

    manifest_root = manifest_root.resolve()
    reviewed_packet_dir = reviewed_packet_dir.resolve()
    proposal_dir = proposal_dir.resolve()
    output_dir = output_dir.resolve()
    for source in (reviewed_packet_dir, proposal_dir):
        if not source.is_relative_to(manifest_root):
            raise ValueError("Gate C review-transfer input escapes manifest root")
    if not output_dir.is_relative_to(manifest_root) or output_dir.exists():
        raise ValueError("Gate C review-transfer output is invalid")

    old_proposal_dir = reviewed_packet_dir / "proposal"
    old_proposal_path = old_proposal_dir / "proposal.json"
    old_receipt_path = old_proposal_dir / "proposal_receipt.json"
    old_review_path = reviewed_packet_dir / "manual_review_v1.json"
    old_replay_path = reviewed_packet_dir / "manual_review_observation_replay_v1.json"
    old_manifest_path = reviewed_packet_dir / "checked_manifest_v1.tsv"
    new_proposal_path = proposal_dir / "proposal.json"
    new_receipt_path = proposal_dir / "proposal_receipt.json"
    old_proposal = _read_json_object(old_proposal_path)
    old_receipt = _read_json_object(old_receipt_path)
    new_proposal = _read_json_object(new_proposal_path)
    new_receipt = _read_json_object(new_receipt_path)
    old_source = manifest_root / str(old_proposal.get("source_artifact_relpath", ""))
    new_source = manifest_root / str(new_proposal.get("source_artifact_relpath", ""))
    _validate_reviewed_packet_for_transfer(
        proposal=old_proposal,
        receipt=old_receipt,
        proposal_path=old_proposal_path,
        review_path=old_review_path,
        replay_path=old_replay_path,
        manifest_path=old_manifest_path,
        source_path=old_source,
        manifest_root=manifest_root,
    )
    if new_receipt.get("proposal_sha256") != _sha256(new_proposal_path):
        raise ValueError("Gate C new proposal hash changed")
    _validate_proposal_payload(new_proposal, new_receipt, new_source)

    old_subjects = _review_subject_rows(old_proposal, old_source)
    new_subjects = _review_subject_rows(new_proposal, new_source)
    if old_subjects != new_subjects:
        raise ValueError("Gate C review subjects differ; fresh review is required")

    staging = output_dir.with_name(f"{output_dir.name}.staging")
    if staging.exists():
        raise ValueError("Gate C review-transfer staging output already exists")
    staging.mkdir(parents=True)
    old_replay = _read_json_object(old_replay_path)
    transferred_replay = {
        **old_replay,
        "proposal_sha256": new_receipt["proposal_sha256"],
        "source_artifact_sha256": new_proposal["source_artifact_sha256"],
        "rows": _required_mapping_rows(old_replay, "rows"),
    }
    replay_path = staging / "manual_review_observation_replay_v1.json"
    _write_json(replay_path, transferred_replay)

    new_source_rows = _required_rows(_read_json_object(new_source), "rows")
    candidate_hashes = {
        (row["group_hypothesis_id"], row["sample_stem"]): _json_sha256(row)
        for row in new_source_rows
    }
    new_context = cast(dict[str, object], new_receipt["run_context"])
    old_review = _read_json_object(old_review_path)
    transferred_review_rows = []
    for row in _required_mapping_rows(old_review, "rows"):
        key = (str(row.get("peak_hypothesis_id", "")), str(row.get("sample_stem", "")))
        transferred_review_rows.append(
            {
                **row,
                "proposal_sha256": new_receipt["proposal_sha256"],
                "source_artifact_sha256": new_proposal["source_artifact_sha256"],
                "processing_request_id": new_context["processing_request_id"],
                "processing_attempt_id": new_context["processing_attempt_id"],
                "candidate_key_sha256": candidate_hashes[key],
            }
        )
    final_replay_path = output_dir / replay_path.name
    final_replay_relpath = final_replay_path.relative_to(manifest_root).as_posix()
    transferred_review: dict[str, object] = {
        "schema_version": "issue180_m9_gate_c_manual_review_v1",
        "observation_replay_relpath": final_replay_relpath,
        "observation_replay_sha256": _sha256(replay_path),
        "rows": transferred_review_rows,
    }
    review_path = staging / "manual_review_v1.json"
    _write_json(review_path, transferred_review)

    pending = _required_rows(new_proposal, "pending_acceptance_rows")
    manifest_sha = quant_matrix.production_acceptance_manifest_sha256(pending)
    checked_rows = [{**row, "manifest_sha256": manifest_sha} for row in pending]
    manifest_path = staging / "checked_manifest_v1.tsv"
    manifest_path.write_text(
        render_delimited_rows(
            checked_rows,
            quant_matrix.PRODUCTION_ACCEPTANCE_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    _validate_manual_review_replay(
        transferred_replay, new_proposal, new_receipt, pending, new_source
    )
    _validate_manual_review(
        transferred_review,
        new_proposal,
        new_receipt,
        pending,
        new_source,
        replay_relpath=final_replay_relpath,
        replay_sha256=_sha256(replay_path),
    )
    checked = load_checked_production_acceptance_manifest(
        manifest_path=manifest_path,
        repo_root=manifest_root,
    )
    _validate_pending_manifest_equality(pending, checked.rows)
    transfer_receipt = {
        "schema_version": "issue180_m9_gate_c_review_transfer_receipt_v1",
        "validation_status": VALIDATION_STATUS,
        "code_sha": code_sha,
        "review_subject_count": len(new_subjects),
        "review_subjects_sha256": _json_sha256(new_subjects),
        "reviewed_proposal_receipt_sha256": _sha256(old_receipt_path),
        "reviewed_manual_review_sha256": _sha256(old_review_path),
        "reviewed_replay_sha256": _sha256(old_replay_path),
        "new_proposal_receipt_sha256": _sha256(new_receipt_path),
        "transferred_manual_review_sha256": _sha256(review_path),
        "transferred_replay_sha256": _sha256(replay_path),
        "checked_manifest_sha256": _sha256(manifest_path),
        "transfer_rule": "exact_group_sample_identity_observation_content_area",
        "product_authority": "none_review_input_only",
    }
    _write_json(staging / "review_transfer_receipt.json", transfer_receipt)
    staging.replace(output_dir)
    return output_dir / "review_transfer_receipt.json"


def _validate_review_transfer_receipt(
    *,
    receipt_path: Path,
    code_sha: str,
    proposal_dir: Path,
    manual_review_path: Path,
    manual_review_replay_path: Path,
    manifest_path: Path,
    manifest_root: Path,
) -> Mapping[str, object]:
    manifest_root = manifest_root.resolve()
    paths = (
        receipt_path.resolve(),
        proposal_dir.resolve(),
        manual_review_path.resolve(),
        manual_review_replay_path.resolve(),
        manifest_path.resolve(),
    )
    if any(not path.is_relative_to(manifest_root) for path in paths):
        raise ValueError("Gate C review-transfer inputs must stay under manifest root")

    receipt = _read_json_object(receipt_path)
    required_fields = {
        "schema_version",
        "validation_status",
        "code_sha",
        "review_subject_count",
        "review_subjects_sha256",
        "reviewed_proposal_receipt_sha256",
        "reviewed_manual_review_sha256",
        "reviewed_replay_sha256",
        "new_proposal_receipt_sha256",
        "transferred_manual_review_sha256",
        "transferred_replay_sha256",
        "checked_manifest_sha256",
        "transfer_rule",
        "product_authority",
    }
    if set(receipt) != required_fields or (
        receipt.get("schema_version")
        != "issue180_m9_gate_c_review_transfer_receipt_v1"
        or receipt.get("validation_status") != VALIDATION_STATUS
        or receipt.get("code_sha") != code_sha
        or receipt.get("transfer_rule")
        != "exact_group_sample_identity_observation_content_area"
        or receipt.get("product_authority") != "none_review_input_only"
    ):
        raise ValueError("Gate C review-transfer receipt contract changed")

    proposal_path = proposal_dir / "proposal.json"
    proposal_receipt_path = proposal_dir / "proposal_receipt.json"
    proposal = _read_json_object(proposal_path)
    proposal_receipt = _read_json_object(proposal_receipt_path)
    source_path = manifest_root / str(proposal.get("source_artifact_relpath", ""))
    _validate_proposal_payload(proposal, proposal_receipt, source_path)
    review_subjects = _review_subject_rows(proposal, source_path)
    expected_bindings = {
        "review_subject_count": len(review_subjects),
        "review_subjects_sha256": _json_sha256(review_subjects),
        "new_proposal_receipt_sha256": _sha256(proposal_receipt_path),
        "transferred_manual_review_sha256": _sha256(manual_review_path),
        "transferred_replay_sha256": _sha256(manual_review_replay_path),
        "checked_manifest_sha256": _sha256(manifest_path),
    }
    if any(receipt.get(key) != value for key, value in expected_bindings.items()):
        raise ValueError("Gate C review-transfer receipt bindings changed")
    for key in (
        "reviewed_proposal_receipt_sha256",
        "reviewed_manual_review_sha256",
        "reviewed_replay_sha256",
    ):
        value = receipt.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Gate C reviewed packet digest is malformed")
    return receipt


def _validate_reviewed_packet_for_transfer(
    *,
    proposal: Mapping[str, object],
    receipt: Mapping[str, object],
    proposal_path: Path,
    review_path: Path,
    replay_path: Path,
    manifest_path: Path,
    source_path: Path,
    manifest_root: Path,
) -> None:
    proposal_schema = proposal.get("schema_version")
    receipt_schema = receipt.get("schema_version")
    if proposal_schema not in {
        "issue180_m9_gate_c_proposal_v1",
        "issue180_m9_gate_c_proposal_v2",
    } or receipt_schema not in {
        "issue180_m9_gate_c_proposal_receipt_v1",
        "issue180_m9_gate_c_proposal_receipt_v2",
    }:
        raise ValueError("Gate C reviewed packet schema is unsupported")
    if receipt.get("proposal_sha256") != _sha256(proposal_path) or receipt.get(
        "source_file_sha256"
    ) != _sha256(source_path):
        raise ValueError("Gate C reviewed packet hash changed")
    if proposal_schema == "issue180_m9_gate_c_proposal_v2":
        _validate_proposal_payload(proposal, receipt, source_path)
    elif (
        str(proposal.get("source_artifact_sha256", "")).upper()
        != hashlib.sha256(source_path.read_bytes()).hexdigest().upper()
    ):
        raise ValueError("Gate C reviewed source SHA changed")
    pending = _required_rows(proposal, "pending_acceptance_rows")
    review = _read_json_object(review_path)
    replay = _read_json_object(replay_path)
    _validate_manual_review_replay(replay, proposal, receipt, pending, source_path)
    _validate_manual_review(
        review,
        proposal,
        receipt,
        pending,
        source_path,
        replay_relpath=replay_path.relative_to(manifest_root).as_posix(),
        replay_sha256=_sha256(replay_path),
    )
    checked = load_checked_production_acceptance_manifest(
        manifest_path=manifest_path,
        repo_root=manifest_root,
    )
    _validate_pending_manifest_equality(pending, checked.rows)


def _review_subject_rows(
    proposal: Mapping[str, object],
    source_path: Path,
) -> list[dict[str, object]]:
    return _review_subject_rows_from_payload(
        pending=_required_rows(proposal, "pending_acceptance_rows"),
        source=_read_json_object(source_path),
    )


def _projection_review_subject_rows(
    projection: UntargetedCidNlModeTrackProjection,
) -> list[dict[str, object]]:
    source = json.loads(
        projection.source_artifact_bytes,
        object_pairs_hook=_unique_json_mapping,
    )
    if not isinstance(source, dict):
        raise ValueError("Gate C fresh source must be a JSON object")
    return _review_subject_rows_from_payload(
        pending=projection.pending_acceptance_rows,
        source=source,
    )


def _review_subject_rows_from_payload(
    *,
    pending: Sequence[Mapping[str, str]],
    source: Mapping[str, object],
) -> list[dict[str, object]]:
    pending_keys = {
        (row["peak_hypothesis_id"], row["sample_stem"])
        for row in pending
    }
    source_rows = _required_mapping_rows(source, "rows")
    subjects: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for row in source_rows:
        key = (row.get("group_hypothesis_id"), row.get("sample_stem"))
        if key not in pending_keys:
            continue
        physical_ids = row.get("physical_ids")
        if (
            key in seen
            or not isinstance(physical_ids, list)
            or len(physical_ids) < 3
            or not row.get("sample_identity_id")
            or not physical_ids[2]
            or row.get("quant_value") is None
        ):
            raise ValueError("Gate C review subject is malformed")
        seen.add(key)
        subjects.append(
            {
                "group_hypothesis_id": key[0],
                "sample_stem": key[1],
                "sample_identity_id": row["sample_identity_id"],
                "peak_observation_content_id": physical_ids[2],
                "raw_area": row["quant_value"],
            }
        )
    if seen != pending_keys:
        raise ValueError("Gate C review subject census is incomplete")
    return sorted(
        subjects,
        key=lambda row: (str(row["group_hypothesis_id"]), str(row["sample_stem"])),
    )


def _rehydrate_gate_c_review_set(
    *,
    proposal_dir: Path,
    manual_review_path: Path,
    manual_review_replay_path: Path,
    manifest_root: Path,
    fresh_projection: UntargetedCidNlModeTrackProjection,
) -> _ValidatedGateCReviewSet:
    proposal_path = proposal_dir / "proposal.json"
    receipt_path = proposal_dir / "proposal_receipt.json"
    proposal = _read_json_object(proposal_path)
    receipt = _read_json_object(receipt_path)
    source_path = manifest_root / str(proposal.get("source_artifact_relpath", ""))
    if receipt.get("proposal_sha256") != _sha256(proposal_path):
        raise ValueError("Gate C proposal hash changed")
    if receipt.get("source_file_sha256") != _sha256(source_path):
        raise ValueError("Gate C source file hash changed")
    _validate_proposal_payload(proposal, receipt, source_path)
    _validate_fresh_mode_track_projection(
        persisted_rows=_required_mapping_rows(
            proposal, "mode_track_scientific_projection_rows"
        ),
        fresh_facts=fresh_projection.mode_track_facts,
    )
    _validate_fresh_proposed_keys(proposal, fresh_projection.mode_track_facts)
    pending = _required_rows(proposal, "pending_acceptance_rows")
    replay_relpath = _manifest_relative_path(
        manual_review_replay_path,
        manifest_root=manifest_root,
    )
    replay = _read_json_object(manual_review_replay_path)
    review = _read_json_object(manual_review_path)
    _validate_manual_review_replay(replay, proposal, receipt, pending, source_path)
    _validate_manual_review(
        review,
        proposal,
        receipt,
        pending,
        source_path,
        replay_relpath=replay_relpath,
        replay_sha256=_sha256(manual_review_replay_path),
    )
    persisted_subjects = _review_subject_rows(proposal, source_path)
    fresh_subjects = _projection_review_subject_rows(fresh_projection)
    if persisted_subjects != fresh_subjects:
        raise ValueError("Gate C reviewed subjects differ from fresh C2 owner")
    subjects = tuple(
        _GateCReviewSubject(
            group_hypothesis_id=str(row["group_hypothesis_id"]),
            sample_stem=str(row["sample_stem"]),
            sample_identity_id=str(row["sample_identity_id"]),
            peak_observation_content_id=str(row["peak_observation_content_id"]),
            raw_area=str(row["raw_area"]),
        )
        for row in fresh_subjects
    )
    return _ValidatedGateCReviewSet(
        subjects=subjects,
        manual_review_sha256=_sha256(manual_review_path),
        manual_review_replay_sha256=_sha256(manual_review_replay_path),
        _mint_token=_VALIDATED_GATE_C_REVIEW_MINT_TOKEN,
    )


def finalize_gate_c_fresh_projection(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    validated_review: _ValidatedGateCReviewSet,
    manifest_root: Path,
    output_dir: Path,
) -> Path:
    if not isinstance(projection, UntargetedCidNlModeTrackProjection) or not isinstance(
        validated_review, _ValidatedGateCReviewSet
    ):
        raise TypeError("Gate C finalization requires fresh typed owner authority")
    manifest_root = manifest_root.resolve()
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(manifest_root):
        raise ValueError("Gate C final output must stay under manifest root")
    source_path = manifest_root / projection.source_artifact_relpath
    if (
        not source_path.resolve().is_relative_to(manifest_root)
        or not source_path.is_file()
        or _sha256(source_path).upper() != projection.source_artifact_sha256.upper()
    ):
        raise ValueError("Gate C fresh projection source binding changed")

    fresh_subjects = tuple(
        _GateCReviewSubject(
            group_hypothesis_id=str(row["group_hypothesis_id"]),
            sample_stem=str(row["sample_stem"]),
            sample_identity_id=str(row["sample_identity_id"]),
            peak_observation_content_id=str(row["peak_observation_content_id"]),
            raw_area=str(row["raw_area"]),
        )
        for row in _projection_review_subject_rows(projection)
    )
    if validated_review.subjects != fresh_subjects:
        raise ValueError("Gate C validated review differs from fresh C2 subjects")

    pending = list(map(dict, projection.pending_acceptance_rows))
    manifest_sha = quant_matrix.production_acceptance_manifest_sha256(pending)
    checked_rows = [{**row, "manifest_sha256": manifest_sha} for row in pending]
    staging = output_dir.with_name(f"{output_dir.name}.staging")
    if output_dir.exists() or staging.exists():
        raise ValueError("Gate C final or staging output already exists")
    staging.mkdir(parents=True)
    manifest_path = staging / "checked_manifest_v1.tsv"
    manifest_path.write_text(
        render_delimited_rows(
            checked_rows,
            quant_matrix.PRODUCTION_ACCEPTANCE_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    checked = load_checked_production_acceptance_manifest(
        manifest_path=manifest_path,
        repo_root=manifest_root,
    )
    _validate_pending_manifest_equality(pending, checked.rows)
    result = build_quant_matrix_version_from_checked_manifest(
        matrix_header=projection.matrix_header,
        input_quant_matrix_rows=projection.input_quant_matrix_rows,
        input_matrix_identity_rows=projection.input_matrix_identity_rows,
        checked_manifest=checked,
        expected_diff_rows=projection.expected_diff_rows,
    )
    rollback_status, rollback_rows = _validate_gate_c_fresh_terminal(
        projection, pending, result
    )
    result_payload = {
        key: list(map(dict, value)) for key, value in vars(result).items()
    }
    scientific_rows = _mode_track_scientific_projection_rows(
        projection.mode_track_facts
    )
    final_receipt = {
        "schema_version": "issue180_m9_gate_c_final_receipt_v3",
        "validation_status": VALIDATION_STATUS,
        "validated_review_subjects_sha256": _json_sha256(
            [asdict(subject) for subject in validated_review.subjects]
        ),
        "manual_review_sha256": validated_review.manual_review_sha256,
        "manual_review_replay_sha256": (
            validated_review.manual_review_replay_sha256
        ),
        "checked_manifest_file_sha256": checked.manifest_file_sha256,
        "result_qmv_sha256": _json_sha256(result_payload),
        "baseline_qmv_sha256": _json_sha256(
            list(map(dict, projection.input_quant_matrix_rows))
        ),
        "rollback_qmv_sha256": _json_sha256(rollback_rows),
        "adopted_cell_count": len(pending),
        "rollback_status": rollback_status,
        "fresh_source_sha256": projection.source_artifact_sha256,
        "fresh_scientific_projection_sha256": _json_sha256(scientific_rows),
        "writer_authority": "fresh_projection_plus_validated_review_only",
    }
    _write_json(staging / "result_qmv.json", result_payload)
    _publish_json_marker(staging / "final_receipt.json", final_receipt)
    staging.replace(output_dir)
    return output_dir / "final_receipt.json"


def _validate_proposal_payload(
    proposal: Mapping[str, object],
    receipt: Mapping[str, object],
    source_path: Path,
) -> None:
    if proposal.get("schema_version") != "issue180_m9_gate_c_proposal_v2":
        raise ValueError("Gate C proposal schema changed")
    if receipt.get("schema_version") != "issue180_m9_gate_c_proposal_receipt_v2":
        raise ValueError("Gate C proposal receipt schema changed")
    source_bytes = source_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest().upper()
    if proposal.get("source_artifact_sha256") != source_sha:
        raise ValueError("Gate C owner source SHA changed")
    source = _read_json_object(source_path)
    pending = _required_rows(proposal, "pending_acceptance_rows")
    expected = _required_rows(proposal, "expected_diff_rows")
    fact_rows = _required_rows(proposal, "mode_track_fact_rows")
    decision_rows = _required_rows(proposal, "candidate_decision_rows")
    if len(pending) != len(expected) or not pending:
        raise ValueError("Gate C proposal candidate/diff census changed")
    source_rows = _required_rows(source, "rows")
    source_keys = {
        (row["group_hypothesis_id"], row["sample_stem"]) for row in source_rows
    }
    pending_keys = {(row["peak_hypothesis_id"], row["sample_stem"]) for row in pending}
    if source_keys != pending_keys:
        raise ValueError("Gate C source and pending product keys differ")
    fact_group_ids = [row.get("group_hypothesis_id") for row in fact_rows]
    if not fact_group_ids or len(fact_group_ids) != len(set(fact_group_ids)):
        raise ValueError("Gate C ModeTrack fact census is incomplete")
    decision_keys = [
        (
            row.get("group_hypothesis_id"),
            row.get("sample_stem"),
            row.get("assignment_id"),
        )
        for row in decision_rows
    ]
    if len(decision_keys) != len(set(decision_keys)) or any(
        row.get("decision") not in {"proposed", "rejected"} for row in decision_rows
    ):
        raise ValueError("Gate C exact-owner decision census is malformed")
    proposed_keys = {
        (row["group_hypothesis_id"], row["sample_stem"])
        for row in decision_rows
        if row["decision"] == "proposed"
    }
    if proposed_keys != pending_keys:
        raise ValueError("Gate C proposed decision and pending keys differ")
    context = receipt.get("run_context")
    owner_keys = ("code_sha", "processing_request_id", "processing_attempt_id")
    if not isinstance(context, dict) or not all(context.get(key) for key in owner_keys):
        raise ValueError("Gate C proposal receipt owner context is incomplete")
    census = context.get("candidate_census")
    expected_census = {
        "track_count": len(fact_rows),
        "pending": len(proposed_keys),
        "rejected": len(decision_rows) - len(proposed_keys),
        "exact_owner_candidate_count": len(decision_rows),
        "mode_track_facts_sha256": _json_sha256(fact_rows),
        "candidate_decisions_sha256": _json_sha256(decision_rows),
    }
    if not isinstance(census, dict) or any(
        census.get(key) != value for key, value in expected_census.items()
    ):
        raise ValueError("Gate C receipt census does not bind proposal evidence")


def _validate_manual_review(
    review: Mapping[str, object],
    proposal: Mapping[str, object],
    receipt: Mapping[str, object],
    pending: Sequence[Mapping[str, str]],
    source_path: Path,
    *,
    replay_relpath: str,
    replay_sha256: str,
) -> None:
    if review.get("schema_version") != "issue180_m9_gate_c_manual_review_v1":
        raise ValueError("Gate C manual review schema changed")
    rows = review.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Gate C manual review rows are missing")
    if (
        review.get("observation_replay_relpath") != replay_relpath
        or review.get("observation_replay_sha256") != replay_sha256
    ):
        raise ValueError("Gate C manual review replay binding changed")
    source_rows = _required_rows(_read_json_object(source_path), "rows")
    candidate_hashes = {
        (row["group_hypothesis_id"], row["sample_stem"]): _json_sha256(row)
        for row in source_rows
    }
    context = cast(dict[str, object], receipt["run_context"])
    expected = {(row["peak_hypothesis_id"], row["sample_stem"]) for row in pending}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Gate C manual review row is malformed")
        key = (str(row.get("peak_hypothesis_id", "")), str(row.get("sample_stem", "")))
        if key in seen or key not in expected:
            raise ValueError("Gate C manual review key is duplicate or foreign")
        seen.add(key)
        bound = {
            "proposal_sha256": receipt["proposal_sha256"],
            "source_artifact_sha256": proposal["source_artifact_sha256"],
            "processing_request_id": context["processing_request_id"],
            "processing_attempt_id": context["processing_attempt_id"],
            "verdict": "pass",
            "candidate_key_sha256": candidate_hashes[key],
        }
        if any(row.get(name) != value for name, value in bound.items()) or any(
            not row.get(name)
            for name in ("reviewer_id", "reviewed_at", "evidence_locator")
        ):
            raise ValueError("Gate C manual review is unbound or not passing")
    if seen != expected:
        raise ValueError("Gate C manual review does not cover every pending row")


def _validate_manual_review_replay(
    replay: Mapping[str, object],
    proposal: Mapping[str, object],
    receipt: Mapping[str, object],
    pending: Sequence[Mapping[str, str]],
    source_path: Path,
) -> None:
    if (
        replay.get("schema_version") != "issue180_m9_gate_c_observation_replay_v1"
        or replay.get("validation_status") != VALIDATION_STATUS
        or replay.get("proposal_sha256") != receipt["proposal_sha256"]
        or str(replay.get("source_artifact_sha256", "")).upper()
        != str(proposal["source_artifact_sha256"]).upper()
    ):
        raise ValueError("Gate C physical replay authority binding changed")
    rows = replay.get("rows")
    if not isinstance(rows, list) or replay.get("row_count") != len(rows):
        raise ValueError("Gate C physical replay row census is malformed")
    source_rows = _required_rows(_read_json_object(source_path), "rows")
    source_by_key = {
        (row["group_hypothesis_id"], row["sample_stem"]): row for row in source_rows
    }
    pending_by_key = {
        (row["peak_hypothesis_id"], row["sample_stem"]): row for row in pending
    }
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Gate C physical replay row is malformed")
        key = (str(row.get("peak_hypothesis_id", "")), str(row.get("sample_stem", "")))
        source = source_by_key.get(key)
        accepted = pending_by_key.get(key)
        if key in seen or source is None or accepted is None:
            raise ValueError("Gate C physical replay key is duplicate or foreign")
        seen.add(key)
        observation = row.get("observation_content")
        physical_ids = source.get("physical_ids")
        if not isinstance(observation, dict):
            raise ValueError("Gate C physical replay observation content is malformed")
        try:
            verified_observation = verify_persisted_peak_observation_claims(
                observation
            )
        except PeakObservationContractError as exc:
            raise ValueError(
                "Gate C physical replay observation content is owner-invalid"
            ) from exc
        if (
            not isinstance(physical_ids, list)
            or len(physical_ids) < 3
            or row.get("feature_family_id") != accepted.get("feature_family_id")
            or row.get("peak_observation_content_id") != physical_ids[2]
            or verified_observation.peak_observation_content_id != physical_ids[2]
            or verified_observation.canonical_sample_identity_id
            != source.get("sample_identity_id")
            or row.get("proposal_quant_value") != accepted.get("quant_value")
            or source.get("quant_value") != accepted.get("quant_value")
            or row.get("area_parity") != "pass"
        ):
            raise ValueError("Gate C physical replay candidate binding changed")
        if not numeric_equal(
            verified_observation.area_raw_counts_seconds,
            accepted.get("quant_value"),
        ):
            raise ValueError("Gate C physical replay raw-area parity changed")
        source_observation = source.get("peak_observation")
        if isinstance(source_observation, dict) and (
            source_observation.get("content") != observation
        ):
            raise ValueError("Gate C physical replay observation payload changed")
    if seen != set(pending_by_key):
        raise ValueError("Gate C physical replay does not cover every pending row")


def _validate_pending_manifest_equality(
    pending: Sequence[Mapping[str, str]],
    checked: Sequence[Mapping[str, str]],
) -> None:
    accepted = [
        {key: value for key, value in row.items() if key != "manifest_sha256"}
        for row in checked
    ]
    proposed = [
        {key: value for key, value in row.items() if key != "manifest_sha256"}
        for row in pending
    ]
    if accepted != proposed:
        raise ValueError("checked manifest differs from fresh C2 projection")


def _validate_gate_c_fresh_terminal(
    projection: UntargetedCidNlModeTrackProjection,
    pending: Sequence[Mapping[str, str]],
    result: quant_matrix.QuantMatrixVersionRows,
) -> tuple[str, list[dict[str, str]]]:
    header = projection.matrix_header
    baseline = list(map(dict, projection.input_quant_matrix_rows))
    identities = projection.input_matrix_identity_rows
    return _validate_gate_c_terminal_rows(
        header=header,
        baseline=baseline,
        identities=identities,
        pending=pending,
        result=result,
    )


def _validate_gate_c_terminal_rows(
    *,
    header: Sequence[str],
    baseline: Sequence[Mapping[str, str]],
    identities: Sequence[Mapping[str, str]],
    pending: Sequence[Mapping[str, str]],
    result: quant_matrix.QuantMatrixVersionRows,
) -> tuple[str, list[dict[str, str]]]:
    accepted = {(row["peak_hypothesis_id"], row["sample_stem"]) for row in pending}
    if len(result.quant_matrix_rows) != len(baseline):
        raise ValueError("Gate C Matrix row count changed")
    for identity, before, after in zip(
        identities, baseline, result.quant_matrix_rows, strict=True
    ):
        if any(
            after.get(column, "") != before.get(column, "") for column in ("Mz", "RT")
        ):
            raise ValueError("Gate C terminal Matrix row identity changed")
        group_id = identity["peak_hypothesis_id"]
        expected = {
            sample
            for sample in header[2:]
            if before.get(sample, "") or (group_id, sample) in accepted
        }
        actual = {sample for sample in header[2:] if after.get(sample, "")}
        if actual != expected:
            raise ValueError("Gate C terminal Matrix contains an unauthorized value")
    detected_only_rows = quant_matrix.detected_only_matrix_rows_from_quant_version(
        quant_matrix_rows=result.quant_matrix_rows,
        cell_provenance_rows=result.cell_provenance_rows,
        matrix_identity_rows=identities,
    )
    rollback_rows = [
        {column: row.get(column, "") for column in header} for row in detected_only_rows
    ]
    baseline_detected_rows = [
        row for row in baseline if any(row.get(sample, "") for sample in header[2:])
    ]
    if rollback_rows != baseline_detected_rows:
        raise ValueError("Gate C result-derived rollback differs from baseline")
    return "baseline_exact", list(map(dict, baseline))


def _manifest_relative_path(path: Path, *, manifest_root: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(manifest_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("Gate C physical replay escapes manifest root") from exc
    if not resolved.is_file():
        raise ValueError("Gate C physical replay must be a file")
    return relative.as_posix()


def _read_json_object(path: Path) -> dict[str, object]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_json_mapping,
    )
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _unique_json_mapping(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Gate C JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _new_output_dir(output_dir: Path) -> Path:
    if output_dir.exists():
        raise ValueError(f"output already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    return output_dir


def _publish_json_marker(path: Path, payload: object) -> None:
    temporary = path.with_suffix(".tmp")
    _write_json(temporary, payload)
    temporary.replace(path)


def _required_rows(value: Mapping[str, object], key: str) -> list[dict[str, str]]:
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Gate C {key} rows are malformed")
    return rows


def _required_mapping_rows(
    value: Mapping[str, object], key: str
) -> list[dict[str, object]]:
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Gate C {key} rows are malformed")
    return rows


def _required_strings(value: Mapping[str, object], key: str) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ValueError(f"Gate C {key} must contain strings")
    return items


def _heartbeat(event: str, started: float) -> dict[str, object]:
    return {
        "schema_version": _HEARTBEAT_SCHEMA_VERSION,
        "event": event,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _peak_config(
    data_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "unused_xic_results.csv",
        diagnostics_csv=output_dir / "unused_xic_diagnostics.csv",
        smooth_window=int(defaults["smooth_window"]),
        smooth_polyorder=int(defaults["smooth_polyorder"]),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
    )


def _coarse_runtime_telemetry(
    coordinator: CoordinatorTerminalRecord,
) -> RuntimeTelemetrySamples:
    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    reason = _RUNTIME_TELEMETRY_UNAVAILABLE_REASON
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=(0,) * event_count,
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            reason=reason,
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=worker_key,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                reason=reason,
            )
            for worker_key in worker_keys
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-file", required=True, type=Path)
    parser.add_argument("--dll-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    receipt_path = run_gate_b(
        raw_file=args.raw_file.resolve(),
        dll_dir=args.dll_dir.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
