from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

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
from xic_extractor.alignment.quant_matrix_version import QuantMatrixVersionRows
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
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

SAMPLE_STEM = "TumorBC2297_DNA"
PRECURSOR_MZ = 349.215
NEUTRAL_LOSS_DA = 116.0474
VALIDATION_STATUS = "diagnostic_only"
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


def gate_c_launch_contract() -> dict[str, object]:
    return {
        "schema_version": "issue180_m9_gate_c_launch_contract_v1",
        "validation_status": VALIDATION_STATUS,
        "precursor_mz": PRECURSOR_MZ,
        "neutral_loss_da": NEUTRAL_LOSS_DA,
        "alignment_config": {"anchor_min_seed_events": 1},
        "trajectory_policy": asdict(_mode_track_trajectory_policy()),
        "call_budget": {"raw_open_count_max": 85, "xic_batch_count_max": 85},
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
        or run.call_cost["raw_open_count"] != 1
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
    if run.call_cost["raw_open_count"] > 85 or run.call_cost["xic_batch_count"] > 85:
        raise RuntimeError("Gate C exceeded the sample-batched RAW call budget")
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
    call_cost = untargeted_cid_nl_runtime_call_cost(runtime_result.finalization)
    if call_cost["raw_open_count"] > len(raw_files):
        raise RuntimeError("M7 validation batch exceeded one RAW open per sample")
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
    staging = _new_output_dir(output_dir)
    staged_source = staging / source_path.name
    staged_source.write_bytes(projection.source_artifact_bytes)
    fact_rows = [_fact_row(fact) for fact in projection.mode_track_facts]
    decision_rows = _candidate_decision_rows(projection.mode_track_facts)
    proposal = {
        "schema_version": "issue180_m9_gate_c_proposal_v1",
        "matrix_header": list(projection.matrix_header),
        "input_quant_matrix_rows": list(map(dict, projection.input_quant_matrix_rows)),
        "input_matrix_identity_rows": list(
            map(dict, projection.input_matrix_identity_rows)
        ),
        "pending_acceptance_rows": list(map(dict, projection.pending_acceptance_rows)),
        "expected_diff_rows": list(map(dict, projection.expected_diff_rows)),
        "mode_track_fact_rows": fact_rows,
        "candidate_decision_rows": decision_rows,
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
        "schema_version": "issue180_m9_gate_c_proposal_receipt_v1",
        "proposal_sha256": _sha256(proposal_path),
        "source_file_sha256": _sha256(staged_source),
        "run_context": context,
    }
    _validate_proposal_payload(proposal, receipt, staged_source)
    _publish_json_marker(staging / "proposal_receipt.json", receipt)
    return output_dir / "proposal_receipt.json"


def finalize_gate_c_proposal(
    *,
    proposal_dir: Path,
    manual_review_path: Path,
    manifest_path: Path,
    manifest_root: Path,
    output_dir: Path,
) -> Path:
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
    pending = _required_rows(proposal, "pending_acceptance_rows")
    review = _read_json_object(manual_review_path)
    _validate_manual_review(review, proposal, receipt, pending, source_path)
    checked = load_checked_production_acceptance_manifest(
        manifest_path=manifest_path,
        repo_root=manifest_root,
    )
    _validate_pending_manifest_equality(pending, checked.rows)
    result = build_quant_matrix_version_from_checked_manifest(
        matrix_header=_required_strings(proposal, "matrix_header"),
        input_quant_matrix_rows=_required_rows(proposal, "input_quant_matrix_rows"),
        input_matrix_identity_rows=_required_rows(
            proposal, "input_matrix_identity_rows"
        ),
        checked_manifest=checked,
        expected_diff_rows=_required_rows(proposal, "expected_diff_rows"),
    )
    _validate_gate_c_terminal(proposal, pending, result)
    result_payload = {
        key: list(map(dict, value)) for key, value in vars(result).items()
    }
    final_receipt = {
        "schema_version": "issue180_m9_gate_c_final_receipt_v1",
        "validation_status": VALIDATION_STATUS,
        "proposal_receipt_sha256": _sha256(receipt_path),
        "manual_review_sha256": _sha256(manual_review_path),
        "checked_manifest_file_sha256": checked.manifest_file_sha256,
        "result_qmv_sha256": _json_sha256(result_payload),
        "baseline_qmv_sha256": _json_sha256(
            _required_rows(proposal, "input_quant_matrix_rows")
        ),
        "adopted_cell_count": len(pending),
        "rollback_status": "baseline_exact",
    }
    staging = _new_output_dir(output_dir)
    _write_json(staging / "result_qmv.json", result_payload)
    _publish_json_marker(staging / "final_receipt.json", final_receipt)
    return output_dir / "final_receipt.json"


def _validate_proposal_payload(
    proposal: Mapping[str, object],
    receipt: Mapping[str, object],
    source_path: Path,
) -> None:
    if proposal.get("schema_version") != "issue180_m9_gate_c_proposal_v1":
        raise ValueError("Gate C proposal schema changed")
    source_bytes = source_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest().upper()
    if proposal.get("source_artifact_sha256") != source_sha:
        raise ValueError("Gate C owner source SHA changed")
    source = json.loads(source_bytes)
    pending = _required_rows(proposal, "pending_acceptance_rows")
    expected = _required_rows(proposal, "expected_diff_rows")
    fact_rows = _required_rows(proposal, "mode_track_fact_rows")
    decision_rows = _required_rows(proposal, "candidate_decision_rows")
    if len(pending) != len(expected) or not pending:
        raise ValueError("Gate C proposal candidate/diff census changed")
    source_keys = {
        (row["group_hypothesis_id"], row["sample_stem"])
        for row in source.get("rows", ())
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
        row.get("decision") not in {"proposed", "rejected"}
        for row in decision_rows
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
) -> None:
    if review.get("schema_version") != "issue180_m9_gate_c_manual_review_v1":
        raise ValueError("Gate C manual review schema changed")
    rows = review.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Gate C manual review rows are missing")
    source_rows = json.loads(source_path.read_bytes())["rows"]
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
        raise ValueError("checked manifest differs from C1 proposal")


def _validate_gate_c_terminal(
    proposal: Mapping[str, object],
    pending: Sequence[Mapping[str, str]],
    result: QuantMatrixVersionRows,
) -> None:
    header = _required_strings(proposal, "matrix_header")
    baseline = _required_rows(proposal, "input_quant_matrix_rows")
    identities = _required_rows(proposal, "input_matrix_identity_rows")
    accepted = {(row["peak_hypothesis_id"], row["sample_stem"]) for row in pending}
    if len(result.quant_matrix_rows) != len(baseline):
        raise ValueError("Gate C Matrix row count changed")
    for identity, before, after in zip(
        identities, baseline, result.quant_matrix_rows, strict=True
    ):
        group_id = identity["peak_hypothesis_id"]
        expected = {
            sample
            for sample in header[2:]
            if before.get(sample, "") or (group_id, sample) in accepted
        }
        actual = {sample for sample in header[2:] if after.get(sample, "")}
        if actual != expected:
            raise ValueError("Gate C terminal Matrix contains an unauthorized value")


def _read_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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


def _required_strings(value: Mapping[str, object], key: str) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ValueError(f"Gate C {key} must contain strings")
    return items


def _heartbeat(event: str, started: float) -> dict[str, object]:
    return {
        "schema_version": "issue180_m9_gate_b_heartbeat_v1",
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
    reason = "not_collected_by_issue180_gate_b"
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
