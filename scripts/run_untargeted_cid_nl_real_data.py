"""Foreground real-data runtime runner from Discovery receipts to QMV."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.alignment.production_acceptance import REQUIRED_COLUMNS
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    build_untargeted_cid_nl_golden_path,
    build_untargeted_cid_nl_quant_matrix_version,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.csv_reader import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.execution._observability import RuntimeMetricAvailability
from xic_extractor.execution.attempt import (
    RuntimeArtifactIoSample,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_partition.execution import (
    execute_attempt,
    runtime_call_cost,
)
from xic_extractor.extraction.untargeted_partition.models import UntargetedPartitionJob
from xic_extractor.extraction.untargeted_partition.request import (
    build_partition_context,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.sample_metadata import load_sample_metadata
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    RESOLVER_MODES,
)
from xic_extractor.tabular_io import write_delimited_rows

VALIDATION_STATUS = "diagnostic_only"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.perf_counter()
    output_dir = args.output_dir.resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"{output_dir}: output directory already exists", file=sys.stderr)
        return 2
    timing_live_path = (
        args.timing_live_output.resolve()
        if args.timing_live_output is not None
        else output_dir / "timing.live.json"
    )
    heartbeat = _Heartbeat(timing_live_path, started=started)
    heartbeat.write(event="started")
    try:
        summary = _run(args, output_dir=output_dir, heartbeat=heartbeat)
    except Exception as exc:
        heartbeat.write(event="failed", error=f"{type(exc).__name__}: {exc}")
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started
    summary["elapsed_seconds"] = elapsed
    summary_path = output_dir / "real_data_runtime_summary.json"
    _write_json(summary_path, summary)
    timing_path = (
        args.timing_output.resolve()
        if args.timing_output is not None
        else output_dir / "timing.json"
    )
    timing_payload = {
        "schema_version": "untargeted_cid_nl_real_data_runtime_timing",
        "validation_status": VALIDATION_STATUS,
        "elapsed_seconds": elapsed,
        "sample_count": summary["sample_count"],
        "runtime_succeeded": summary["runtime_succeeded"],
        "terminal_state": summary["terminal_state"],
        "call_cost": summary["call_cost"],
    }
    _write_json(timing_path, timing_payload)
    heartbeat.write(event="completed", terminal_state=summary["terminal_state"])
    print(f"Real-data runtime summary: {summary_path}")
    print(f"Real-data runtime timing: {timing_path}")
    return 0


def _run(
    args: argparse.Namespace,
    *,
    output_dir: Path,
    heartbeat: "_Heartbeat",
) -> dict[str, object]:
    batch_index = read_discovery_batch_index(
        args.discovery_batch_index.resolve(),
        require_owner_closure=True,
    )
    selected_stems = _selected_stems(batch_index.sample_order, args.sample_stem)
    if args.expected_sample_count is not None and len(selected_stems) != (
        args.expected_sample_count
    ):
        raise ValueError(
            f"expected {args.expected_sample_count} samples, "
            f"found {len(selected_stems)}"
        )
    raw_paths_by_sample = existing_raw_paths(
        sample_order=selected_stems,
        raw_files={stem: batch_index.raw_files[stem] for stem in selected_stems},
        raw_dir=args.raw_dir.resolve(),
    )
    missing_raw = tuple(
        stem for stem in selected_stems if stem not in raw_paths_by_sample
    )
    if missing_raw:
        raise ValueError(f"missing RAW files for samples: {', '.join(missing_raw)}")
    discovery_receipts_by_sample = {}
    candidates_by_sample = {}
    for stem in selected_stems:
        receipt = batch_index.candidate_receipts[stem]
        if receipt is None:
            raise ValueError(f"missing Discovery owner receipt for {stem}")
        discovery_receipts_by_sample[stem] = receipt
        candidates_by_sample[stem] = read_owner_bound_discovery_candidates_csv(
            batch_index.candidate_csvs[stem],
            receipt=receipt,
        )
    metadata_rows = load_sample_metadata(args.sample_metadata.resolve())
    metadata_by_stem = {row.raw_stem: row for row in metadata_rows}
    missing_metadata = tuple(
        stem for stem in selected_stems if stem not in metadata_by_stem
    )
    if missing_metadata:
        raise ValueError(f"missing sample metadata for: {', '.join(missing_metadata)}")
    selected_metadata = tuple(metadata_by_stem[stem] for stem in selected_stems)
    if any(row.excluded for row in selected_metadata):
        excluded = tuple(row.raw_stem for row in selected_metadata if row.excluded)
        raise ValueError(
            "excluded samples require an explicit smaller validation selection: "
            + ", ".join(excluded)
        )
    settings = _discovery_settings(args)
    peak_config = _peak_config(
        args.raw_dir.resolve(),
        args.dll_dir.resolve(),
        output_dir,
        settings,
        args,
    )
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=args.precursor_mz,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=args.neutral_loss_da,
    )
    context = build_partition_context(
        raw_paths=tuple(raw_paths_by_sample[stem] for stem in selected_stems),
        sample_metadata=selected_metadata,
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=discovery_receipts_by_sample,
        sample_identity_namespace=args.sample_identity_namespace,
    )
    jobs = tuple(
        UntargetedPartitionJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=context.method_subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=args.dll_dir.resolve(),
            work_unit_key=f"untargeted-cid-nl:{sample.metadata.raw_stem}",
        )
        for sample in context.samples
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-runner",
    )
    runtime_result = execute_attempt(
        runtime,
        jobs,
        telemetry_provider=_coarse_runtime_telemetry,
        recovery_marker=OwnerPathRuntimeMarker(
            output_dir / "runtime.json",
            "untargeted-cid-nl-real-data-runtime:runner",
        ),
        progress_callback=heartbeat.progress,
    )
    if not runtime_result.succeeded:
        raise RuntimeError("Real-data atomic runtime did not succeed")
    call_cost = runtime_call_cost(runtime_result.finalization)

    has_direct_support = any(
        item.adapter_result.assessment_input.assessment.content.method_result
        == "SUPPORT"
        for item in runtime_result.sample_results
    )
    projection = None
    qmv = None
    source_relpath = None
    pending_manifest_path = None
    backfill_decision_by_sample_id = {}
    if has_direct_support:
        manifest_root = args.manifest_root.resolve()
        source_path = output_dir / "untargeted_cid_nl_source.json"
        try:
            source_relpath = source_path.resolve().relative_to(manifest_root).as_posix()
        except ValueError as exc:
            raise ValueError(
                "output-dir must be inside manifest-root for source-artifact validation"
            ) from exc
        projection = build_untargeted_cid_nl_golden_path(
            processing_owners=context.processing_owners,
            method_subject=context.method_subject,
            sample_inputs=tuple(
                item.adapter_result.assessment_input
                for item in runtime_result.sample_results
            ),
            backfill_inputs=tuple(
                item.backfill_input
                for item in runtime_result.sample_results
                if item.backfill_input is not None
            ),
            source_artifact_relpath=source_relpath,
        )
        source_path.write_bytes(projection.source_artifact_bytes)
        pending_manifest_path = (
            output_dir / "pending_production_acceptance_manifest.tsv"
        )
        write_delimited_rows(
            pending_manifest_path,
            projection.pending_acceptance_rows,
            REQUIRED_COLUMNS,
            delimiter="\t",
            encoding="utf-8",
            extrasaction="raise",
        )
        checked_manifest_path = args.production_acceptance_manifest
        if checked_manifest_path is None and not projection.pending_acceptance_rows:
            checked_manifest_path = pending_manifest_path
        if checked_manifest_path is not None:
            qmv = build_untargeted_cid_nl_quant_matrix_version(
                projection,
                production_acceptance_manifest_path=checked_manifest_path.resolve(),
                manifest_root=manifest_root,
            )
            _write_qmv(output_dir, projection.matrix_header, qmv)
        backfill_decision_by_sample_id = {
            decision.target_cell.content.canonical_sample_identity_id: decision
            for decision in projection.backfill_decisions
        }
    sample_rows = []
    for item in runtime_result.sample_results:
        adapter = item.adapter_result
        assessment = adapter.assessment_input.assessment
        acquisition = (
            item.backfill_attempt.acquisition
            if item.backfill_attempt is not None
            else adapter.acquisition
        )
        backfill_decision = backfill_decision_by_sample_id.get(
            item.job.sample.identity.sample_identity_id
        )
        sample_rows.append(
            {
                "sample_stem": item.job.sample.metadata.raw_stem,
                "sample_role": item.job.sample.metadata.sample_role,
                "technical_state": assessment.content.technical_state,
                "method_result": assessment.content.method_result,
                "reason_codes": list(assessment.content.reason_codes),
                "source_candidate_count": len(adapter.source_candidate_ids),
                "strict_scan_count": adapter.strict_scan_count,
                "candidate_peak_count": len(assessment.candidate_peaks),
                "backfill_attempted": item.backfill_attempt is not None,
                "backfill_candidate_count": item.result_ref.backfill_candidate_count,
                "backfill_disposition": (
                    backfill_decision.disposition
                    if backfill_decision is not None
                    else None
                ),
                "logical_xic_request_count": (
                    acquisition.logical_xic_request_count
                    if acquisition is not None
                    else 0
                ),
                "xic_batch_count": (
                    acquisition.xic_batch_count if acquisition is not None else 0
                ),
                "adapter_chromatogram_call_count": (
                    acquisition.adapter_chromatogram_call_count
                    if acquisition is not None
                    else 0
                ),
                "trace_point_count": (
                    acquisition.trace_point_count if acquisition is not None else 0
                ),
                "discovery_receipt_id": (
                    item.job.discovery_receipt.discovery_receipt_id
                ),
                "discovery_candidate_artifact_sha256": (
                    item.job.discovery_receipt.candidate_artifact_sha256
                ),
                "discovery_acquisition_source_id": (
                    item.job.discovery_receipt.acquisition_source.acquisition_source_id
                ),
                "result_artifact_id": item.result_ref.artifact.evidence_artifact_id,
            }
        )
    if projection is None:
        terminal_state = "assessments_complete_no_direct_support"
    elif qmv is not None:
        terminal_state = "quant_matrix_version_built"
    else:
        terminal_state = "prepared_requires_production_acceptance_manifest"
    return {
        "schema_version": "untargeted_cid_nl_real_data_runtime_summary",
        "validation_status": VALIDATION_STATUS,
        "terminal_state": terminal_state,
        "runtime_succeeded": runtime_result.succeeded,
        "processing_request_id": (
            context.processing_owners.request.processing_request_id
        ),
        "processing_attempt_id": (
            context.processing_owners.attempt.processing_attempt_id
        ),
        "method_subject": context.method_subject.request_payload(),
        "sample_count": len(runtime_result.sample_results),
        "call_cost": call_cost,
        "samples": sample_rows,
        "source_artifact_relpath": source_relpath,
        "source_artifact_sha256": (
            projection.source_artifact_sha256 if projection is not None else None
        ),
        "pending_acceptance_count": (
            len(projection.pending_acceptance_rows) if projection is not None else 0
        ),
        "pending_acceptance_manifest": (
            str(pending_manifest_path) if pending_manifest_path is not None else None
        ),
        "quant_matrix_version_built": qmv is not None,
        "telemetry_precision": "coarse_event_order_only",
    }


def _write_qmv(output_dir: Path, matrix_header, qmv) -> None:
    write_delimited_rows(
        output_dir / "quant_matrix_version.csv",
        qmv.quant_matrix_rows,
        matrix_header,
        delimiter=",",
        encoding="utf-8",
        extrasaction="raise",
    )
    write_delimited_rows(
        output_dir / "cell_provenance.tsv",
        qmv.cell_provenance_rows,
        CELL_PROVENANCE_COLUMNS,
        delimiter="\t",
        encoding="utf-8",
        extrasaction="raise",
    )
    write_delimited_rows(
        output_dir / "row_summary.tsv",
        qmv.row_summary_rows,
        ROW_SUMMARY_COLUMNS,
        delimiter="\t",
        encoding="utf-8",
        extrasaction="raise",
    )
    write_delimited_rows(
        output_dir / "expected_diff_summary.tsv",
        qmv.expected_diff_summary_rows,
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        delimiter="\t",
        encoding="utf-8",
        extrasaction="raise",
    )


def _selected_stems(
    sample_order: tuple[str, ...],
    requested: list[str] | None,
) -> tuple[str, ...]:
    if not requested:
        return sample_order
    requested_set = set(requested)
    missing = requested_set.difference(sample_order)
    if missing:
        raise ValueError(f"unknown requested samples: {', '.join(sorted(missing))}")
    return tuple(stem for stem in sample_order if stem in requested_set)


def _discovery_settings(args: argparse.Namespace) -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag=args.neutral_loss_tag,
            neutral_loss_da=args.neutral_loss_da,
        ),
        tag_combine_mode="single",
        nl_tolerance_ppm=args.nl_tolerance_ppm,
        precursor_mz_tolerance_ppm=args.precursor_mz_tolerance_ppm,
        product_mz_tolerance_ppm=args.product_mz_tolerance_ppm,
        product_search_ppm=args.product_search_ppm,
        ms2_precursor_tol_da=args.ms2_precursor_tol_da,
        nl_min_intensity_ratio=args.nl_min_intensity_ratio,
        seed_rt_gap_min=args.seed_rt_gap_min,
        ms1_search_padding_min=args.ms1_search_padding_min,
        rt_min=args.rt_min,
        rt_max=args.rt_max,
        resolver_mode=args.resolver_mode,
    )


def _peak_config(
    data_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    args: argparse.Namespace,
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "unused_xic_results.csv",
        diagnostics_csv=output_dir / "unused_xic_diagnostics.csv",
        smooth_window=args.smooth_window,
        smooth_polyorder=args.smooth_polyorder,
        ms1_morphology_smoothing_window_points=(
            args.ms1_morphology_smoothing_window_points
        ),
        peak_rel_height=args.peak_rel_height,
        peak_min_prominence_ratio=args.peak_min_prominence_ratio,
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
    )


def _coarse_runtime_telemetry(coordinator) -> RuntimeTelemetrySamples:
    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    reason = "not_collected_by_real_data_runtime_foreground_runner"
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


class _Heartbeat:
    def __init__(self, path: Path, *, started: float) -> None:
        self._path = path
        self._started = started
        self._completed_samples: list[str] = []

    def progress(self, event: Mapping[str, object]) -> None:
        sample_stem = event.get("sample_stem")
        if isinstance(sample_stem, str):
            self._completed_samples.append(sample_stem)
        event_name = event.get("event")
        if not isinstance(event_name, str):
            raise ValueError("heartbeat progress event requires an event name")
        self.write(
            event=event_name,
            **{key: value for key, value in event.items() if key != "event"},
        )

    def write(self, *, event: str, **values: object) -> None:
        _write_json(
            self._path,
            {
                "schema_version": "untargeted_cid_nl_real_data_runtime_heartbeat",
                "validation_status": VALIDATION_STATUS,
                "event": event,
                "elapsed_seconds": time.perf_counter() - self._started,
                "completed_samples": list(self._completed_samples),
                **values,
            },
        )


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    parser = argparse.ArgumentParser(
        description=(
            "Replay current Discovery receipts through one real Untargeted "
            "CID-NL subject and the golden-path projection."
        )
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--sample-metadata", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, default=Path.cwd())
    parser.add_argument("--sample-stem", action="append")
    parser.add_argument("--expected-sample-count", type=_positive_int)
    parser.add_argument("--precursor-mz", type=_positive_float, required=True)
    parser.add_argument(
        "--precursor-mz-tolerance-ppm",
        type=_positive_float,
        default=20.0,
    )
    parser.add_argument("--neutral-loss-tag", default="DNA_dR")
    parser.add_argument("--neutral-loss-da", type=_positive_float, default=116.0474)
    parser.add_argument(
        "--nl-tolerance-ppm",
        type=_positive_float,
        default=20.0,
        help=(
            "Per-ion precursor/product m/z ppm used for the quadrature "
            "neutral-loss window."
        ),
    )
    parser.add_argument(
        "--product-mz-tolerance-ppm",
        type=_positive_float,
        default=20.0,
    )
    parser.add_argument("--product-search-ppm", type=_positive_float, default=50.0)
    parser.add_argument(
        "--ms2-precursor-tol-da",
        type=_positive_float,
        default=float(defaults["ms2_precursor_tol_da"]),
    )
    parser.add_argument("--nl-min-intensity-ratio", type=_positive_float, default=0.01)
    parser.add_argument("--seed-rt-gap-min", type=_positive_float, default=0.20)
    parser.add_argument("--ms1-search-padding-min", type=_positive_float, default=0.35)
    parser.add_argument("--rt-min", type=_nonnegative_float, default=0.0)
    parser.add_argument("--rt-max", type=_positive_float, default=999.0)
    parser.add_argument(
        "--resolver-mode",
        choices=RESOLVER_MODES,
        default="local_minimum",
    )
    parser.add_argument(
        "--smooth-window",
        type=_positive_int,
        default=15,
    )
    parser.add_argument(
        "--smooth-polyorder",
        type=_positive_int,
        default=3,
    )
    parser.add_argument(
        "--ms1-morphology-smoothing-window-points",
        type=_positive_int,
        default=int(defaults["ms1_morphology_smoothing_window_points"]),
    )
    parser.add_argument(
        "--peak-rel-height",
        type=_positive_float,
        default=0.95,
    )
    parser.add_argument(
        "--peak-min-prominence-ratio",
        type=_positive_float,
        default=0.10,
    )
    parser.add_argument(
        "--sample-identity-namespace",
        default="study:untargeted-cid-nl-real-data-runtime",
    )
    parser.add_argument("--production-acceptance-manifest", type=Path)
    parser.add_argument("--timing-output", type=Path)
    parser.add_argument("--timing-live-output", type=Path)
    args = parser.parse_args(argv)
    if args.rt_min >= args.rt_max:
        parser.error("rt-min must be smaller than rt-max")
    if args.smooth_polyorder >= args.smooth_window:
        parser.error("smooth-polyorder must be smaller than smooth-window")
    return args


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be finite and > 0")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError("value must be finite and >= 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
