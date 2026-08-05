"""Task-scoped real-RAW fixture for Issue 180 Gate B.

This is an explicit validation harness, not a public CLI or product writer.
It creates one fresh owner-bound Discovery request and keeps the complete
M7 -> M8 -> M2 -> M6 projection transaction in memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

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
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
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


def run_gate_b(*, raw_file: Path, dll_dir: Path, output_dir: Path) -> Path:
    """Run the bounded one-RAW Gate B transaction and write its receipt."""

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

    assessment = (
        runtime_result.sample_results[0]
        .adapter_result.assessment_input.assessment
    )
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

    group_rts = tuple(sorted(fact.group.group_center_rt for fact in facts))
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
        ms1_morphology_smoothing_window_points=int(
            defaults["ms1_morphology_smoothing_window_points"]
        ),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
        resolver_chrom_threshold=float(defaults["resolver_chrom_threshold"]),
        resolver_min_search_range_min=float(
            defaults["resolver_min_search_range_min"]
        ),
        resolver_min_relative_height=float(defaults["resolver_min_relative_height"]),
        resolver_min_absolute_height=float(defaults["resolver_min_absolute_height"]),
        resolver_min_ratio_top_edge=float(defaults["resolver_min_ratio_top_edge"]),
        resolver_peak_duration_min=float(defaults["resolver_peak_duration_min"]),
        resolver_peak_duration_max=float(defaults["resolver_peak_duration_max"]),
        resolver_min_scans=int(defaults["resolver_min_scans"]),
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
