from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
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


def run_gate_b(*, raw_file: Path, dll_dir: Path, output_dir: Path) -> Path:
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    if raw_file.stem != SAMPLE_STEM or not raw_file.is_file():
        raise ValueError(f"expected the existing {SAMPLE_STEM}.raw canary")
    if not dll_dir.is_dir():
        raise ValueError("Thermo DLL directory does not exist")
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
    peak_config = _peak_config(
        raw_file.parent,
        dll_dir,
        discovery_dir,
        settings,
    )
    discovery = run_discovery_batch(
        (raw_file,),
        output_dir=discovery_dir,
        settings=settings,
        peak_config=peak_config,
    )
    _write_json(heartbeat_path, _heartbeat("discovery_complete", started))
    batch = read_discovery_batch_index(
        discovery.batch_index_csv,
        require_owner_closure=True,
    )
    receipt = batch.candidate_receipts[SAMPLE_STEM]
    if receipt is None:
        raise ValueError("fresh Discovery did not mint an owner receipt")
    candidates = read_owner_bound_discovery_candidates_csv(
        batch.candidate_csvs[SAMPLE_STEM],
        receipt=receipt,
        require_successor_columns=True,
    )
    metadata = SampleMetadata(
        sample_name=SAMPLE_STEM,
        raw_stem=SAMPLE_STEM,
        injection_order=1,
        sample_role="study_sample",
        batch_id="issue180-m9-gate-b",
        matrix_type="breast_tissue",
        group="tumor",
    )
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=PRECURSOR_MZ,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=NEUTRAL_LOSS_DA,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=(raw_file,),
        sample_metadata=(metadata,),
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={SAMPLE_STEM: candidates},
        discovery_receipts_by_sample={SAMPLE_STEM: receipt},
        sample_identity_namespace="issue180-m9-gate-b",
    )
    sample = context.samples[0]
    job = UntargetedCidNlRealDataRuntimeJob(
        processing_owners=context.processing_owners,
        sample=sample,
        method_subject=context.method_subject,
        candidates=context.candidates_by_sample[SAMPLE_STEM],
        discovery_receipt=receipt,
        settings=context.settings,
        peak_config=context.peak_config,
        raw_path=raw_file,
        dll_dir=dll_dir,
        work_unit_key=f"untargeted-cid-nl:{SAMPLE_STEM}",
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=(job.work_unit_key,),
        owner_completeness_attestation="issue180-m9-gate-b:v1",
    )
    runtime_result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        (job,),
        telemetry_provider=_coarse_runtime_telemetry,
        recovery_marker=OwnerPathRuntimeMarker(
            output_dir / "runtime.json",
            "issue180-m9-gate-b",
        ),
    )
    if not runtime_result.succeeded or len(runtime_result.sample_results) != 1:
        raise RuntimeError("M7 canary did not complete atomically")
    call_cost = untargeted_cid_nl_runtime_call_cost(runtime_result.finalization)
    if call_cost["raw_open_count"] != 1:
        raise RuntimeError("M7 canary did not preserve one sample-batched RAW open")
    _write_json(heartbeat_path, _heartbeat("m7_complete", started))
    assessment = runtime_result.sample_results[
        0
    ].adapter_result.assessment_input.assessment
    sample_assessments = ((sample, assessment),)
    _, model_set, batches = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=context.processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=2 / 60,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    facts = assess_mode_track_conservative_backfill_groups(
        processing_owners=context.processing_owners,
        model_set=model_set,
        assignment_batches=batches,
        sample_assessments=sample_assessments,
    )
    source_relpath = f"output/{output_dir.name}/mode_track_source.json"
    projection = build_untargeted_cid_nl_mode_track_projection(
        processing_owners=context.processing_owners,
        mode_track_facts=facts,
        source_artifact_relpath=source_relpath,
    )
    source_path = output_dir / "mode_track_source.json"
    source_path.write_bytes(projection.source_artifact_bytes)
    group_rts = tuple(sorted(cast(float, fact.group.group_center_rt) for fact in facts))
    if len(group_rts) != 2 or any(
        abs(observed - expected) > 0.1
        for observed, expected in zip(group_rts, (12.4123, 13.0354), strict=True)
    ):
        raise RuntimeError("Gate B did not retain the expected two-track census")
    if sum(fact.direct_support_count_s for fact in facts) != 1:
        raise RuntimeError("Gate B direct-SUPPORT census changed")
    if sum(len(fact.exact_owner_candidate_assignments) for fact in facts) != 1:
        raise RuntimeError("Gate B exact-owner candidate census changed")
    fact_rows = [
        {
            "group_hypothesis_id": fact.group.group_hypothesis_id,
            "group_center_rt": fact.group.group_center_rt,
            "sample_count_n": fact.sample_count_n,
            "minimum_support_k": fact.minimum_support_k,
            "direct_support_count_s": fact.direct_support_count_s,
            "candidate_count": len(fact.exact_owner_candidate_assignments),
            "may_attempt_recovery": fact.may_attempt_recovery,
        }
        for fact in facts
    ]
    if projection.pending_acceptance_rows or projection.expected_diff_rows:
        raise RuntimeError("one-sample S<K canary must remain non-writing")
    receipt_path = output_dir / "gate_b_receipt.json"
    _write_json(
        receipt_path,
        {
            "schema_version": "issue180_m9_gate_b_receipt_v1",
            "validation_status": VALIDATION_STATUS,
            "sample_stem": SAMPLE_STEM,
            "precursor_mz": PRECURSOR_MZ,
            "neutral_loss_da": NEUTRAL_LOSS_DA,
            "fresh_owner_bound_discovery_index_sha256": _sha256(
                discovery.batch_index_csv
            ),
            "sample_metadata": asdict(metadata),
            "settings_sha256": _json_sha256(discovery_settings_payload(settings)),
            "call_cost": call_cost,
            "track_facts": fact_rows,
            "pending_acceptance_count": len(projection.pending_acceptance_rows),
            "expected_diff_count": len(projection.expected_diff_rows),
            "source_artifact_sha256": projection.source_artifact_sha256,
            "product_authority": projection.product_authority,
            "elapsed_seconds": time.perf_counter() - started,
        },
    )
    _write_json(heartbeat_path, _heartbeat("completed", started))
    return receipt_path


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
    proposal = {
        "schema_version": "issue180_m9_gate_c_proposal_v1",
        "matrix_header": list(projection.matrix_header),
        "input_quant_matrix_rows": list(map(dict, projection.input_quant_matrix_rows)),
        "input_matrix_identity_rows": list(
            map(dict, projection.input_matrix_identity_rows)
        ),
        "pending_acceptance_rows": list(map(dict, projection.pending_acceptance_rows)),
        "expected_diff_rows": list(map(dict, projection.expected_diff_rows)),
        "source_artifact_relpath": projection.source_artifact_relpath,
        "source_artifact_sha256": projection.source_artifact_sha256,
    }
    proposal_path = staging / "proposal.json"
    _write_json(proposal_path, proposal)
    receipt = {
        "schema_version": "issue180_m9_gate_c_proposal_receipt_v1",
        "proposal_sha256": _sha256(proposal_path),
        "source_file_sha256": _sha256(staged_source),
        "run_context": dict(run_context),
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
    if len(pending) != len(expected) or not pending:
        raise ValueError("Gate C proposal candidate/diff census changed")
    source_keys = {
        (row["group_hypothesis_id"], row["sample_stem"])
        for row in source.get("rows", ())
    }
    pending_keys = {(row["peak_hypothesis_id"], row["sample_stem"]) for row in pending}
    if source_keys != pending_keys:
        raise ValueError("Gate C source and pending product keys differ")
    context = receipt.get("run_context")
    owner_keys = ("code_sha", "processing_request_id", "processing_attempt_id")
    if not isinstance(context, dict) or not all(context.get(key) for key in owner_keys):
        raise ValueError("Gate C proposal receipt owner context is incomplete")


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
