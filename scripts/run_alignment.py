from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.backfill_scope import read_family_allowlist_tsv
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
)
from xic_extractor.alignment.drift_evidence import read_targeted_istd_drift_evidence
from xic_extractor.alignment.pipeline import run_alignment
from xic_extractor.alignment.process_backend import AlignmentProcessExecutionError
from xic_extractor.alignment.raw_sources import existing_raw_paths
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.presets import PresetError, apply_to_alignment, load_preset
from xic_extractor.presets.models import Preset
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_SETTINGS_DEFAULTS,
    RESOLVER_MODES,
)

_DEFAULT_DRIFT_LOCAL_WINDOW = 40
_DEFAULT_RAW_WORKERS = 1
_DEFAULT_RAW_XIC_BATCH_SIZE = 1
_PERFORMANCE_PROFILES = {
    "validation-fast": {
        "raw_workers": 11,
        "raw_xic_batch_size": 64,
    },
}
_STANDARD_PEAK_PUBLICATION_MODES = (
    "matrix-only",
    "review-gallery",
    "deep-audit",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        preset, preset_alignment_config, preset_runtime_options = _resolve_preset(args)
    except PresetError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    standard_peak_backfill_enabled = bool(
        preset_runtime_options.get("standard_peak_backfill", False),
    )
    backfill_expansion_productization_mode = str(
        preset_runtime_options.get("backfill_expansion_productization", "off"),
    )
    if args.standard_peak_backfill_publication_mode is not None:
        if not standard_peak_backfill_enabled:
            print(
                "--standard-peak-backfill-publication-mode requires a preset "
                "that enables standard_peak_backfill",
                file=sys.stderr,
            )
            return 2
        preset_runtime_options = dict(preset_runtime_options)
        _apply_standard_peak_publication_mode_override(
            preset_runtime_options,
            args.standard_peak_backfill_publication_mode,
        )
    if args.standard_peak_evidence_cache_dir is not None and not (
        standard_peak_backfill_enabled
    ):
        print(
            "--standard-peak-evidence-cache-dir requires a preset that enables "
            "standard_peak_backfill",
            file=sys.stderr,
        )
        return 2
    if (args.sample_info is None) != (args.targeted_istd_workbook is None):
        print(
            (
                "--sample-info is required with --targeted-istd-workbook, "
                "and both must be provided together"
            ),
            file=sys.stderr,
        )
        return 2

    discovery_batch_index = args.discovery_batch_index.resolve()
    raw_dir = args.raw_dir.resolve()
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    standard_peak_evidence_cache_dir = (
        args.standard_peak_evidence_cache_dir.resolve()
        if args.standard_peak_evidence_cache_dir is not None
        else None
    )
    raw_workers, raw_xic_batch_size = _resolve_raw_execution_settings(args)
    owner_build_xic_backend = _effective_owner_build_xic_backend(
        args,
        preset_runtime_options,
    )
    identity_coherence_output_dir = (
        args.identity_coherence_output_dir.resolve()
        if args.identity_coherence_output_dir is not None
        else None
    )
    identity_coherence_controls_manifest = (
        args.identity_coherence_controls_manifest.resolve()
        if args.emit_identity_coherence_diagnostic
        and args.identity_coherence_controls_manifest is not None
        else None
    )

    if not discovery_batch_index.is_file():
        print(
            f"{discovery_batch_index}: discovery batch index does not exist",
            file=sys.stderr,
        )
        return 2
    if not raw_dir.is_dir():
        print(f"{raw_dir}: raw directory does not exist", file=sys.stderr)
        return 2
    if not dll_dir.is_dir():
        print(f"{dll_dir}: dll directory does not exist", file=sys.stderr)
        return 2
    try:
        discovery_batch = read_discovery_batch_index(discovery_batch_index)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        _validate_unique_sample_stems(discovery_batch_index, discovery_batch)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    discovery_sample_count = len(discovery_batch.sample_order)
    if (
        args.expected_sample_count is not None
        and discovery_sample_count != args.expected_sample_count
    ):
        print(
            (
                f"{discovery_batch_index}: expected {args.expected_sample_count} "
                f"discovery batch samples, found {discovery_sample_count}"
            ),
            file=sys.stderr,
        )
        return 2
    try:
        _validate_expected_85raw_contract(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        resolved_raw_paths = _validate_launch_artifacts(
            discovery_batch,
            raw_dir=raw_dir,
            require_all_raw=(
                args.preflight_only or args.expected_sample_count == 85
            ),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if (
        args.emit_identity_coherence_diagnostic
        and identity_coherence_controls_manifest is not None
        and not identity_coherence_controls_manifest.is_file()
    ):
        print(
            (
                f"{identity_coherence_controls_manifest}: identity coherence "
                "controls manifest does not exist"
            ),
            file=sys.stderr,
        )
        return 2
    if args.sample_info is not None and args.targeted_istd_workbook is not None:
        sample_info = args.sample_info.resolve()
        targeted_istd_workbook = args.targeted_istd_workbook.resolve()
        if not sample_info.is_file():
            print(f"{sample_info}: sample info does not exist", file=sys.stderr)
            return 2
        if not targeted_istd_workbook.is_file():
            print(
                f"{targeted_istd_workbook}: targeted ISTD workbook does not exist",
                file=sys.stderr,
            )
            return 2
    else:
        sample_info = None
        targeted_istd_workbook = None
    sample_column_injection_order = (
        args.sample_column_injection_order.resolve()
        if args.sample_column_injection_order is not None
        else None
    )
    if (
        sample_column_injection_order is not None
        and not sample_column_injection_order.is_file()
    ):
        print(
            f"{sample_column_injection_order}: sample-column injection-order file "
            "does not exist",
            file=sys.stderr,
        )
        return 2
    try:
        selected_family_ids, selected_family_source = _resolve_selected_families(args)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.preflight_only:
        _print_preflight_summary(
            args,
            discovery_batch_index=discovery_batch_index,
            discovery_batch=discovery_batch,
            resolved_raw_paths=resolved_raw_paths,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
            raw_workers=raw_workers,
            raw_xic_batch_size=raw_xic_batch_size,
            owner_build_xic_backend=owner_build_xic_backend,
        )
        return 0

    timing_recorder = (
        TimingRecorder(
            "alignment",
            live_output_path=(
                args.timing_live_output.resolve()
                if args.timing_live_output is not None
                else None
            ),
        )
        if args.timing_output is not None or args.timing_live_output is not None
        else None
    )
    timing_kwargs = (
        {"timing_recorder": timing_recorder}
        if timing_recorder is not None
        else {}
    )
    standard_peak_outputs = None
    backfill_expansion_outputs = None
    product_ready_publication_check_outputs = None
    try:
        drift_lookup = (
            read_targeted_istd_drift_evidence(
                targeted_workbook=targeted_istd_workbook,
                sample_info=sample_info,
                local_window=args.drift_local_window,
            )
            if sample_info is not None and targeted_istd_workbook is not None
            else None
        )
        run_kwargs = {
            "discovery_batch_index": discovery_batch_index,
            "raw_dir": raw_dir,
            "dll_dir": dll_dir,
            "output_dir": output_dir,
            "alignment_config": replace(
                preset_alignment_config,
                owner_backfill_min_detected_samples=(
                    args.owner_backfill_min_detected_samples
                ),
            ),
            "peak_config": _peak_config(
                raw_dir,
                dll_dir,
                output_dir,
                _alignment_production_resolver_mode(args.resolver_mode),
                baseline_audit_method=_baseline_audit_method(args),
                baseline_integration_method=_baseline_integration_method(args),
            ),
            "output_level": args.output_level,
            "emit_alignment_cells": (
                args.emit_alignment_cells
                or _standard_peak_backfill_requires_full_cells(
                    standard_peak_backfill_enabled=standard_peak_backfill_enabled,
                    output_level=args.output_level,
                )
            ),
            "emit_alignment_status_matrix": args.emit_alignment_status_matrix,
            "emit_alignment_integration_audit": args.emit_alignment_integration_audit,
            "emit_alignment_backfill_seed_audit": (
                args.emit_alignment_backfill_seed_audit
                or standard_peak_backfill_enabled
            ),
            "emit_alignment_backfill_candidate_audit": (
                args.emit_alignment_backfill_candidate_audit
            ),
            "raw_workers": raw_workers,
            "raw_xic_batch_size": raw_xic_batch_size,
            "owner_build_xic_backend": owner_build_xic_backend,
            "owner_backfill_xic_backend": _owner_backfill_xic_backend(
                args.owner_backfill_xic_backend
            ),
            "owner_backfill_window_strategy": args.owner_backfill_window_strategy,
            "owner_backfill_superwindow_span_factor": (
                args.owner_backfill_superwindow_span_factor
            ),
            "preconsolidate_owner_families": args.preconsolidate_owner_families,
            "emit_early_loser_shadow": args.emit_early_loser_shadow,
            "emit_identity_coherence_diagnostic": (
                args.emit_identity_coherence_diagnostic
            ),
            "identity_coherence_output_dir": identity_coherence_output_dir,
            "identity_coherence_controls_manifest": (
                identity_coherence_controls_manifest
            ),
            "drift_lookup": drift_lookup,
            "sample_column_injection_order": sample_column_injection_order,
            "backfill_scope": args.backfill_scope,
            "audit_evidence_mode": args.audit_evidence_mode,
            "selected_family_ids": selected_family_ids,
            "selected_family_source": selected_family_source,
            **timing_kwargs,
        }
        profiler = cProfile.Profile() if args.profile == "cprofile" else None
        try:
            if profiler is None:
                outputs = run_alignment(**run_kwargs)
            else:
                outputs = profiler.runcall(run_alignment, **run_kwargs)
            if standard_peak_backfill_enabled:
                standard_peak_outputs = run_standard_peak_backfill_preset(
                    alignment_dir=output_dir,
                    raw_dir=raw_dir,
                    dll_dir=dll_dir,
                    source_run_id=_preset_source_run_id(preset),
                    chunk_size=_runtime_int_option(
                        preset_runtime_options,
                        "standard_peak_backfill_chunk_size",
                    ),
                    reuse_existing=bool(
                        preset_runtime_options[
                            "standard_peak_backfill_reuse_existing"
                        ],
                    ),
                    write_gallery=bool(
                        preset_runtime_options[
                            "standard_peak_backfill_write_gallery"
                        ],
                    ),
                    publication_mode=str(
                        preset_runtime_options[
                            "standard_peak_backfill_publication_mode"
                        ],
                    ),
                    min_shape_r=_runtime_float_option(
                        preset_runtime_options,
                        "standard_peak_backfill_min_shape_r",
                    ),
                    render_workers=_standard_peak_render_workers(raw_workers),
                    chunk_workers=_standard_peak_chunk_workers(raw_workers),
                    evidence_cache_dir=standard_peak_evidence_cache_dir,
                    timing_recorder=timing_recorder,
                )
            if backfill_expansion_productization_mode == "clean-target-selective":
                backfill_expansion_outputs = (
                    run_backfill_expansion_clean_target_selective_preset_from_alignment(
                        alignment_dir=output_dir,
                        raw_dir=raw_dir,
                        dll_dir=dll_dir,
                        output_dir=(
                            output_dir / "backfill_expansion_productization_preset"
                        ),
                        reuse_existing_raw_overlay=bool(
                            preset_runtime_options[
                                "backfill_expansion_reuse_existing_raw_overlay"
                            ],
                        ),
                        reuse_existing_shift_aware=bool(
                            preset_runtime_options[
                                "backfill_expansion_reuse_existing_shift_aware"
                            ],
                        ),
                        render_shift_aware_images=bool(
                            preset_runtime_options[
                                "backfill_expansion_render_shift_aware_images"
                            ],
                        ),
                        min_shape_r=_runtime_float_option(
                            preset_runtime_options,
                            "backfill_expansion_min_shape_r",
                        ),
                        timing_recorder=timing_recorder,
                        activation_scope_expected_diff_tsv=(
                            _clean_target_selective_activation_expected_diff_tsv()
                        ),
                    )
                )
            if _is_builtin_product_ready_preset(preset):
                product_ready_publication_check_outputs = (
                    check_product_ready_preset_publication(
                        alignment_dir=output_dir,
                    )
                )
                if product_ready_publication_check_outputs.status != "pass":
                    raise ValueError(
                        "Product-ready preset publication check failed: "
                        f"{product_ready_publication_check_outputs.summary_json}",
                    )
        finally:
            if profiler is not None:
                _write_cprofile_outputs(
                    profiler,
                    _profile_output_dir(args, output_dir),
                )
    except (
        AlignmentProcessExecutionError,
        RawReaderError,
        ValueError,
        OSError,
        KeyError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if outputs.workbook is not None:
        print(f"Alignment workbook: {outputs.workbook}")
    if outputs.review_html is not None:
        print(f"Alignment review HTML: {outputs.review_html}")
    if outputs.review_tsv is not None:
        print(f"Alignment review TSV: {outputs.review_tsv}")
    if outputs.matrix_tsv is not None:
        print(f"Alignment matrix TSV: {outputs.matrix_tsv}")
    if outputs.cells_tsv is not None:
        print(f"Alignment cells TSV: {outputs.cells_tsv}")
    if outputs.integration_audit_tsv is not None:
        print(f"Alignment integration audit TSV: {outputs.integration_audit_tsv}")
    if outputs.backfill_seed_audit_tsv is not None:
        print(f"Alignment backfill seed audit TSV: {outputs.backfill_seed_audit_tsv}")
    if outputs.backfill_candidate_audit_tsv is not None:
        print(
            "Alignment backfill candidate audit TSV: "
            f"{outputs.backfill_candidate_audit_tsv}"
        )
    if outputs.status_matrix_tsv is not None:
        print(f"Alignment status matrix TSV: {outputs.status_matrix_tsv}")
    if outputs.event_to_owner_tsv is not None:
        print(f"Event to MS1 owner TSV: {outputs.event_to_owner_tsv}")
    if outputs.ambiguous_owners_tsv is not None:
        print(f"Ambiguous MS1 owners TSV: {outputs.ambiguous_owners_tsv}")
    if outputs.edge_evidence_tsv is not None:
        print(f"Owner edge evidence TSV: {outputs.edge_evidence_tsv}")
    if outputs.skipped_evidence_ledger_tsv is not None:
        print(f"Skipped evidence ledger TSV: {outputs.skipped_evidence_ledger_tsv}")
    if outputs.run_metadata_json is not None:
        print(f"Alignment run metadata JSON: {outputs.run_metadata_json}")
    if outputs.early_loser_shadow_json is not None:
        print(f"Alignment early-loser shadow JSON: {outputs.early_loser_shadow_json}")
    if outputs.identity_coherence_output_dir is not None:
        print(
            "Identity coherence diagnostic: "
            f"{outputs.identity_coherence_output_dir}"
        )
    if standard_peak_outputs is not None:
        print(
            "Standard-peak backfill preset summary JSON: "
            f"{standard_peak_outputs.summary_json}"
        )
        published_manifest = getattr(
            standard_peak_outputs,
            "published_alignment_manifest_json",
            None,
        )
        if published_manifest is not None:
            print(
                "Standard-peak backfill publication manifest: "
                f"{published_manifest}"
            )
        gallery_html = getattr(standard_peak_outputs, "gallery_html", None)
        if gallery_html is not None:
            print(f"Standard-peak backfill gallery HTML: {gallery_html}")
    if product_ready_publication_check_outputs is not None:
        print(
            "Product-ready preset publication summary JSON: "
            f"{product_ready_publication_check_outputs.summary_json}"
        )
        print(
            "Product-ready preset publication checks TSV: "
            f"{product_ready_publication_check_outputs.checks_tsv}"
        )
    if backfill_expansion_outputs is not None:
        print(
            "Backfill expansion productization summary JSON: "
            f"{backfill_expansion_outputs.summary_json}"
        )
        print(
            "Backfill expansion clean-target activation summary JSON: "
            f"{backfill_expansion_outputs.clean_target_activation_summary_json}"
        )
        print(
            "Backfill expansion product authority scope: "
            f"{backfill_expansion_outputs.product_authority_scope}"
        )
    if timing_recorder is not None:
        if args.timing_live_output is not None:
            print(f"Timing live JSON: {args.timing_live_output.resolve()}")
        if args.timing_output is not None:
            timing_path = args.timing_output.resolve()
            timing_recorder.write_json(timing_path)
            print(f"Timing JSON: {timing_path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run untargeted discovery alignment from a discovery batch index.",
    )
    parser.add_argument(
        "--discovery-batch-index",
        type=Path,
        required=True,
        help="Path to discovery_batch_index.csv from xic-discovery-cli --raw-dir.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        required=True,
        help="Authoritative directory containing Thermo RAW files for backfill.",
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Directory containing Thermo RawFileReader DLLs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "alignment",
        help="Output directory for alignment_review.tsv and alignment_matrix.tsv.",
    )
    parser.add_argument(
        "--preset",
        help=(
            "Built-in preset name or TOML path. Alignment presets may enable "
            "post-alignment standard-peak backfill publication."
        ),
    )
    parser.add_argument(
        "--standard-peak-backfill-publication-mode",
        choices=_STANDARD_PEAK_PUBLICATION_MODES,
        help=(
            "Override the preset standard-peak publication mode. matrix-only "
            "publishes the formal matrix from compact evidence without PNG "
            "gallery rendering; deep-audit preserves the legacy full overlay "
            "and gallery behavior. review-gallery keeps RAW overlay evidence "
            "compact while rendering shift-aware review evidence and the "
            "activation-synced review gallery."
        ),
    )
    parser.add_argument(
        "--standard-peak-evidence-cache-dir",
        type=Path,
        help=(
            "Optional content-keyed cache for standard-peak matrix-only overlay "
            "trace evidence. Use only for repeated method-development reruns; "
            "default one-shot runs do not use this cache."
        ),
    )
    parser.add_argument(
        "--timing-output",
        type=Path,
        help="Optional JSON path for alignment stage timing.",
    )
    parser.add_argument(
        "--timing-live-output",
        type=Path,
        help=(
            "Optional JSON path updated after each timing record. Use this as "
            "a timeout-safe heartbeat for long 85RAW validation runs."
        ),
    )
    parser.add_argument(
        "--expected-sample-count",
        type=_positive_int,
        help=(
            "Expected discovery_batch_index sample count. Use 8 or 85 for scoped "
            "validation gates to fail fast when the wrong batch index is passed."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Validate launch inputs, print the resolved alignment contract, and "
            "exit without loading discovery candidates or reading RAW files."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=("off", "cprofile"),
        default="off",
        help="Optional alignment micro-profiler. cprofile writes sidecar files.",
    )
    parser.add_argument(
        "--profile-output-dir",
        type=Path,
        help="Output directory for --profile cprofile sidecar files.",
    )
    parser.add_argument(
        "--raw-workers",
        type=_positive_int,
        help=(
            "Number of RAW worker processes for sample-local alignment backfill. "
            f"Default {_DEFAULT_RAW_WORKERS}, unless --performance-profile sets "
            "a profile value."
        ),
    )
    parser.add_argument(
        "--raw-xic-batch-size",
        type=_positive_int,
        default=None,
        help=(
            "Maximum XIC requests per RAW API batch. Default "
            f"{_DEFAULT_RAW_XIC_BATCH_SIZE} preserves the pre-batch execution "
            "shape unless --performance-profile sets a profile value."
        ),
    )
    parser.add_argument(
        "--performance-profile",
        choices=tuple(_PERFORMANCE_PROFILES),
        help=(
            "Named alignment execution profile. 'validation-fast' uses the "
            "local RAW validation fast path: raw-workers=11 and "
            "raw-xic-batch-size=64. Explicit raw flags override profile values."
        ),
    )
    parser.add_argument(
        "--owner-backfill-min-detected-samples",
        type=_positive_int,
        default=1,
        help=(
            "Only run owner-centered MS1 backfill for features detected in at "
            "least this many samples. Default 1 preserves full backfill."
        ),
    )
    parser.add_argument(
        "--owner-build-xic-backend",
        choices=("raw", "raw-super-window", "ms1-index"),
        default=None,
        help=(
            "Experimental XIC backend for sample-local owner building. "
            "Default uses the active preset value or 'raw'. "
            "'raw-super-window' merges overlapping vendor scan windows and "
            "crops back to request windows. 'ms1-index' is an explicit "
            "approximate fast mode and may change owner peaks, areas, "
            "feature rows, and matrix values."
        ),
    )
    parser.add_argument(
        "--owner-backfill-xic-backend",
        choices=("raw", "ms1-index", "ms1-index-hybrid"),
        default="raw",
        help=(
            "XIC backend for owner-centered MS1 backfill. Default 'raw' uses "
            "Thermo vendor chromatograms. 'ms1-index' is an explicit "
            "approximate fast mode and may change peak areas. "
            "'ms1-index-hybrid' uses MS1-index prefiltering but writes "
            "vendor-confirmed rescued cells."
        ),
    )
    parser.add_argument(
        "--owner-backfill-window-strategy",
        choices=("exact", "super-window"),
        default="exact",
        help=(
            "Owner-backfill RAW window strategy. Default exact preserves the "
            "current request shape. super-window is an opt-in validation "
            "optimization that merges overlapping scan windows and crops each "
            "trace back to the original request window before peak picking."
        ),
    )
    parser.add_argument(
        "--owner-backfill-superwindow-span-factor",
        type=_positive_int,
        default=2,
        help=(
            "Maximum merged scan span as a multiple of the largest original "
            "request span when --owner-backfill-window-strategy super-window "
            "is enabled. Default 2."
        ),
    )
    parser.add_argument(
        "--preconsolidate-owner-families",
        action="store_true",
        help=(
            "Experimental algorithm mode: merge identity-compatible "
            "single-sample owner families before owner-centered backfill."
        ),
    )
    parser.add_argument(
        "--emit-early-loser-shadow",
        action="store_true",
        help=(
            "Emit an owner-only pre-Backfill loser/survivor shadow and compare "
            "it with final claim/consolidation authority. The shadow never "
            "feeds Backfill, Matrix, or writers; it requires full-audit scope "
            "and cannot be combined with activated preconsolidation."
        ),
    )
    parser.add_argument(
        "--backfill-scope",
        choices=("full-audit", "production-equivalent", "selected-families"),
        default="full-audit",
        help=(
            "Owner-backfill evidence scope. Default full-audit preserves legacy "
            "audit output. production-equivalent skips only rows proven unable "
            "to affect the primary matrix. selected-families is diagnostic-only."
        ),
    )
    parser.add_argument(
        "--audit-evidence-mode",
        choices=("auto", "none", "full", "selected"),
        default="auto",
        help=(
            "Controls heavy audit evidence computation independently from artifact "
            "emission. auto preserves legacy full-audit outputs but keeps "
            "production-equivalent validation slim unless an explicit audit "
            "destination is requested."
        ),
    )
    parser.add_argument(
        "--backfill-family-list-tsv",
        type=Path,
        help=(
            "TSV allowlist for --backfill-scope selected-families. The default "
            "family id column is feature_family_id."
        ),
    )
    parser.add_argument(
        "--backfill-family-id-column",
        default="feature_family_id",
        help="Family id column in --backfill-family-list-tsv.",
    )
    parser.add_argument(
        "--backfill-family-id",
        action="append",
        help=(
            "Family id to include in --backfill-scope selected-families. May be "
            "specified multiple times."
        ),
    )
    parser.add_argument(
        "--emit-identity-coherence-diagnostic",
        action="store_true",
        help=(
            "Emit the opt-in pre-Backfill identity coherence diagnostic. "
            "This diagnostic writes sidecar outputs and does not mutate the "
            "alignment matrix."
        ),
    )
    parser.add_argument(
        "--identity-coherence-output-dir",
        type=Path,
        help=(
            "Output directory for identity coherence diagnostic sidecars. "
            "Defaults to <output-dir>/identity_coherence when the diagnostic "
            "is enabled."
        ),
    )
    parser.add_argument(
        "--identity-coherence-controls-manifest",
        type=Path,
        help=(
            "Optional controls manifest for the identity coherence diagnostic. "
            "Ignored unless --emit-identity-coherence-diagnostic is set."
        ),
    )
    parser.add_argument(
        "--sample-info",
        type=Path,
        help="Sample metadata CSV used with --targeted-istd-workbook for drift priors.",
    )
    parser.add_argument(
        "--targeted-istd-workbook",
        type=Path,
        help="Targeted ISTD workbook used with --sample-info for drift priors.",
    )
    parser.add_argument(
        "--drift-local-window",
        type=_positive_int,
        default=_DEFAULT_DRIFT_LOCAL_WINDOW,
        help=(
            "Injection-order half-window used to build targeted ISTD drift "
            f"priors. Default {_DEFAULT_DRIFT_LOCAL_WINDOW} supports sparse "
            "validation subsets while preserving sample-local rolling medians."
        ),
    )
    parser.add_argument(
        "--sample-column-injection-order",
        type=Path,
        help=(
            "CSV/XLSX with Sample_Name,Injection_Order columns, or a "
            "sample_metadata_v1 CSV/TSV projected to injection order (e.g. "
            "the instrument_qc_injection_order.csv or "
            "instrument_qc_sample_metadata.tsv derived from the method .docx). "
            "When given, final matrix sample columns are ordered earliest- to "
            "latest-injected; samples not listed keep their order at the end. "
            "Reorders columns only; values are unchanged."
        ),
    )
    parser.add_argument(
        "--resolver-mode",
        type=_resolver_mode,
        default="region_first_safe_merge",
    )
    parser.add_argument(
        "--output-level",
        choices=(
            "production",
            "machine",
            "debug",
            "validation",
            "validation-minimal",
        ),
        default="machine",
        help=(
            "Alignment artifact level. Default remains machine until "
            "owner-based validation acceptance."
        ),
    )
    parser.add_argument("--emit-alignment-cells", action="store_true")
    parser.add_argument("--emit-alignment-integration-audit", action="store_true")
    parser.add_argument(
        "--emit-baseline-audit-asls",
        action="store_true",
        help="Emit AsLS shadow columns in alignment_cell_integration_audit.tsv.",
    )
    parser.add_argument(
        "--baseline-integration-method",
        help="Alignment integration-audit baseline method. Only asls is supported.",
    )
    parser.add_argument(
        "--emit-alignment-backfill-seed-audit",
        action="store_true",
        help=(
            "Emit lightweight owner-backfill seed provenance needed by retained "
            "backfill evidence gates and seed-specific overlay galleries."
        ),
    )
    parser.add_argument(
        "--emit-alignment-backfill-candidate-audit",
        action="store_true",
        help=(
            "Emit the exhaustive owner-backfill all-candidate audit sidecar. "
            "This is a deep-debug output and is not required for normal "
            "backfill gallery overlay generation."
        ),
    )
    parser.add_argument("--emit-alignment-status-matrix", action="store_true")
    return parser.parse_args(argv)


def _validate_launch_artifacts(
    discovery_batch: DiscoveryBatchInput,
    *,
    raw_dir: Path,
    require_all_raw: bool,
) -> dict[str, Path]:
    missing_candidate_csvs = tuple(
        (sample_stem, candidate_csv)
        for sample_stem, candidate_csv in discovery_batch.candidate_csvs.items()
        if not candidate_csv.is_file()
    )
    if missing_candidate_csvs:
        sample_stem, candidate_csv = missing_candidate_csvs[0]
        suffix = _truncated_count_suffix(len(missing_candidate_csvs))
        raise ValueError(
            "candidate CSV does not exist for discovery sample "
            f"{sample_stem}: {candidate_csv}{suffix}"
        )

    resolved_raw_paths = existing_raw_paths(
        sample_order=discovery_batch.sample_order,
        raw_files=discovery_batch.raw_files,
        raw_dir=raw_dir,
    )
    missing_raw_samples = tuple(
        sample_stem
        for sample_stem in discovery_batch.sample_order
        if sample_stem not in resolved_raw_paths
    )
    if require_all_raw and missing_raw_samples:
        sample_text = ", ".join(missing_raw_samples[:5])
        suffix = _truncated_count_suffix(len(missing_raw_samples))
        raise ValueError(
            f"{raw_dir}: RAW file does not exist for discovery sample(s): "
            f"{sample_text}{suffix}"
        )
    return resolved_raw_paths


def _validate_unique_sample_stems(
    path: Path,
    discovery_batch: DiscoveryBatchInput,
) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for sample_stem in discovery_batch.sample_order:
        if sample_stem in seen and sample_stem not in duplicates:
            duplicates.append(sample_stem)
        seen.add(sample_stem)
    if duplicates:
        duplicate_text = ", ".join(duplicates[:5])
        suffix = _truncated_count_suffix(len(duplicates))
        raise ValueError(
            f"{path}: duplicate sample_stem values are not supported: "
            f"{duplicate_text}{suffix}"
        )


def _validate_expected_85raw_contract(args: argparse.Namespace) -> None:
    if args.expected_sample_count != 85:
        return

    failures: list[str] = []
    _expect_arg(
        failures,
        "--output-level",
        args.output_level,
        "validation-minimal",
    )
    _expect_arg(
        failures,
        "--backfill-scope",
        args.backfill_scope,
        "production-equivalent",
    )
    _expect_arg(failures, "--audit-evidence-mode", args.audit_evidence_mode, "none")
    _expect_arg(
        failures,
        "--performance-profile",
        args.performance_profile,
        "validation-fast",
    )
    _expect_arg(
        failures,
        "--owner-backfill-window-strategy",
        args.owner_backfill_window_strategy,
        "super-window",
    )
    _expect_arg(
        failures,
        "--owner-backfill-superwindow-span-factor",
        args.owner_backfill_superwindow_span_factor,
        2,
    )
    if args.timing_output is None:
        failures.append("--timing-output is required for canonical 85RAW runs")
    if args.timing_live_output is None:
        failures.append("--timing-live-output is required for canonical 85RAW runs")
    if not _is_repo_venv_python():
        failures.append(
            "Python executable must be under this worktree .venv for canonical "
            f"85RAW runs; got {sys.executable}"
        )

    if failures:
        joined = "\n- ".join(failures)
        raise ValueError(f"85RAW canonical launch contract failed:\n- {joined}")


def _resolve_preset(
    args: argparse.Namespace,
) -> tuple[Preset | None, AlignmentConfig, dict[str, object]]:
    if args.preset is None:
        return None, AlignmentConfig(), {"standard_peak_backfill": False}
    preset = load_preset(args.preset)
    alignment_config, runtime_options = apply_to_alignment(preset)
    return preset, alignment_config, runtime_options


def _preset_source_run_id(preset: Preset | None) -> str:
    if preset is None:
        return "alignment-preset:standard-peak-backfill"
    safe_source = preset.source.replace("\\", "/")
    return f"alignment-preset:{safe_source}:standard-peak-backfill"


def _apply_standard_peak_publication_mode_override(
    runtime_options: dict[str, object],
    publication_mode: str,
) -> None:
    runtime_options["standard_peak_backfill_publication_mode"] = publication_mode
    runtime_options["standard_peak_backfill_write_gallery"] = publication_mode in {
        "review-gallery",
        "deep-audit",
    }


def _runtime_int_option(runtime_options: dict[str, object], key: str) -> int:
    return int(cast(Any, runtime_options[key]))


def _runtime_float_option(runtime_options: dict[str, object], key: str) -> float:
    return float(cast(Any, runtime_options[key]))


def _standard_peak_backfill_requires_full_cells(
    *,
    standard_peak_backfill_enabled: bool,
    output_level: str,
) -> bool:
    if not standard_peak_backfill_enabled:
        return False
    return output_level != "validation-minimal"


def _standard_peak_render_workers(raw_workers: int) -> int:
    return max(1, min(raw_workers, os.cpu_count() or 1, 3))


def _standard_peak_chunk_workers(raw_workers: int) -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(raw_workers, max(1, cpu_count // 4), 2))


def run_standard_peak_backfill_preset(**kwargs):
    from tools.diagnostics.standard_peak_backfill_preset import (
        run_standard_peak_backfill_preset as runner,
    )

    return runner(**kwargs)


def run_backfill_expansion_clean_target_selective_preset_from_alignment(**kwargs):
    from scripts.run_backfill_expansion_full_evidence_chain import (
        run_backfill_expansion_clean_target_selective_preset_from_alignment as runner,
    )

    return runner(**kwargs)


def check_product_ready_preset_publication(**kwargs):
    from xic_extractor.diagnostics.product_ready_preset_publication_check import (
        check_product_ready_preset_publication as checker,
    )

    return checker(**kwargs)


def _is_builtin_product_ready_preset(preset: Preset | None) -> bool:
    return preset is not None and preset.source == "builtin:dna_dr_product_ready"


def _clean_target_selective_activation_expected_diff_tsv() -> Path:
    from scripts import (
        build_backfill_expansion_clean_target_selective_product_activation,
    )

    return (
        build_backfill_expansion_clean_target_selective_product_activation
        .DEFAULT_FILTERED_EXPECTED_DIFF_TSV
    )


def _expect_arg(
    failures: list[str],
    flag: str,
    actual: object,
    expected: object,
) -> None:
    if actual != expected:
        failures.append(f"{flag} must be {expected!r}; got {actual!r}")


def _is_repo_venv_python() -> bool:
    repo_venv = (_repo_root() / ".venv").resolve()
    try:
        python_executable = Path(sys.executable).resolve()
    except OSError:
        return False
    return python_executable.is_relative_to(repo_venv)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _truncated_count_suffix(count: int) -> str:
    if count <= 1:
        return ""
    hidden = count - 1
    return f" (+{hidden} more)"


def _print_preflight_summary(
    args: argparse.Namespace,
    *,
    discovery_batch_index: Path,
    discovery_batch: DiscoveryBatchInput,
    resolved_raw_paths: dict[str, Path],
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    raw_workers: int,
    raw_xic_batch_size: int,
    owner_build_xic_backend: str,
) -> None:
    sample_count = len(discovery_batch.sample_order)
    print("Alignment launch preflight OK (diagnostic_only; no validation completed)")
    print(
        "Preflight scope: shared discovery-batch parser, candidate CSV existence, "
        "RAW path existence; no candidate CSVs loaded; no RAW files opened."
    )
    print(f"Discovery batch index: {discovery_batch_index}")
    print(f"Discovery batch samples: {sample_count}")
    print(f"Candidate CSVs found: {len(discovery_batch.candidate_csvs)}")
    print(f"RAW paths found: {len(resolved_raw_paths)}")
    if args.expected_sample_count is not None:
        print(f"Expected sample count: {args.expected_sample_count}")
    print(
        "85RAW canonical contract: "
        f"{'enforced' if args.expected_sample_count == 85 else 'not requested'}"
    )
    print(f"Python executable: {sys.executable}")
    print(f"run_alignment module: {Path(__file__).resolve()}")
    print(f"Working directory: {Path.cwd().resolve()}")
    print(f"RAW dir: {raw_dir}")
    print(f"DLL dir: {dll_dir}")
    print(f"Output dir: {output_dir}")
    print(f"Output level: {args.output_level}")
    print(f"Backfill scope: {args.backfill_scope}")
    print(f"Audit evidence mode: {args.audit_evidence_mode}")
    print(f"Performance profile: {args.performance_profile or '<none>'}")
    print(f"RAW workers: {raw_workers}")
    print(f"RAW XIC batch size: {raw_xic_batch_size}")
    print(f"Owner build XIC backend: {owner_build_xic_backend}")
    print(f"Owner backfill window strategy: {args.owner_backfill_window_strategy}")
    print(
        "Owner backfill superwindow span factor: "
        f"{args.owner_backfill_superwindow_span_factor}"
    )
    print(
        "Timing JSON: "
        f"{args.timing_output.resolve() if args.timing_output else '<none>'}"
    )
    print(
        "Timing live JSON: "
        f"{args.timing_live_output.resolve() if args.timing_live_output else '<none>'}"
    )


def _resolve_raw_execution_settings(args: argparse.Namespace) -> tuple[int, int]:
    profile = _PERFORMANCE_PROFILES.get(args.performance_profile or "", {})
    raw_workers = (
        args.raw_workers
        if args.raw_workers is not None
        else profile.get("raw_workers", _DEFAULT_RAW_WORKERS)
    )
    raw_xic_batch_size = (
        args.raw_xic_batch_size
        if args.raw_xic_batch_size is not None
        else profile.get("raw_xic_batch_size", _DEFAULT_RAW_XIC_BATCH_SIZE)
    )
    return raw_workers, raw_xic_batch_size


def _resolve_selected_families(args: argparse.Namespace) -> tuple[frozenset[str], str]:
    inline_ids = frozenset(
        family_id.strip()
        for family_id in (args.backfill_family_id or ())
        if family_id.strip()
    )
    if args.backfill_scope != "selected-families":
        if args.backfill_family_list_tsv is not None or inline_ids:
            raise ValueError(
                "backfill family allowlist flags require "
                "--backfill-scope selected-families"
            )
        return frozenset(), ""

    selected = set(inline_ids)
    sources: list[str] = []
    if args.backfill_family_list_tsv is not None:
        path = args.backfill_family_list_tsv.resolve()
        if not path.is_file():
            raise ValueError(f"{path}: backfill family list TSV does not exist")
        sources.append(f"tsv:{path}")
        selected.update(
            read_family_allowlist_tsv(
                path,
                family_id_column=args.backfill_family_id_column,
            )
        )
    if inline_ids:
        sources.append("inline:" + ",".join(sorted(inline_ids)))
    if not selected:
        raise ValueError(
            "selected-families backfill scope requires --backfill-family-id "
            "or --backfill-family-list-tsv"
        )
    return frozenset(selected), ";".join(sources)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer >= 1") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be an integer >= 1")
    return parsed


def _resolver_mode(value: str) -> str:
    parsed = value.strip()
    if parsed == "arbitrated":
        raise argparse.ArgumentTypeError(ARBITRATED_RESOLVER_RETIRED_MESSAGE)
    if parsed not in RESOLVER_MODES:
        allowed = ", ".join(RESOLVER_MODES[:-1]) + f", or {RESOLVER_MODES[-1]}"
        raise argparse.ArgumentTypeError(f"resolver-mode must be {allowed}")
    return parsed


def _profile_output_dir(args: argparse.Namespace, output_dir: Path) -> Path:
    if args.profile_output_dir is not None:
        return args.profile_output_dir.resolve()
    return output_dir / "profile"


def _write_cprofile_outputs(profiler: cProfile.Profile, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = output_dir / "profile.prof"
    top_path = output_dir / "profile_top.txt"
    profiler.dump_stats(str(profile_path))
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(50)
    top_path.write_text(stream.getvalue(), encoding="utf-8")
    print(f"cProfile binary: {profile_path}")
    print(f"cProfile top functions: {top_path}")


def _owner_backfill_xic_backend(value: str) -> str:
    if value == "ms1-index":
        return "ms1_index"
    if value == "ms1-index-hybrid":
        return "ms1_index_hybrid"
    return value


def _effective_owner_build_xic_backend(
    args: argparse.Namespace,
    preset_runtime_options: dict[str, object],
) -> str:
    value = args.owner_build_xic_backend
    if value is None:
        value = str(preset_runtime_options.get("owner_build_xic_backend", "raw"))
    return _owner_build_xic_backend(value)


def _owner_build_xic_backend(value: str) -> str:
    if value == "raw-super-window":
        return "raw_superwindow"
    if value == "ms1-index":
        return "ms1_index"
    return value


def _alignment_production_resolver_mode(resolver_mode: str) -> str:
    if resolver_mode == "region_first_safe_merge":
        return "local_minimum"
    return resolver_mode


def _baseline_audit_method(args: argparse.Namespace) -> str:
    if args.emit_baseline_audit_asls:
        return "asls"
    env_method = os.environ.get("BASELINE_AUDIT_METHOD", "").strip().lower()
    if env_method in {"", "asls"}:
        return env_method
    raise ValueError("BASELINE_AUDIT_METHOD must be empty or asls")


def _baseline_integration_method(args: argparse.Namespace) -> str:
    if args.baseline_integration_method:
        method = args.baseline_integration_method.strip().lower()
        if method == "linear_edge":
            raise ValueError("linear_edge baseline integration is retired; use asls")
        if method != "asls":
            raise ValueError("--baseline-integration-method must be asls")
        return method
    env_method = os.environ.get("BASELINE_INTEGRATION_METHOD", "").strip().lower()
    if env_method in {"", "asls"}:
        return "asls"
    if env_method == "linear_edge":
        raise ValueError("linear_edge baseline integration is retired; use asls")
    raise ValueError("BASELINE_INTEGRATION_METHOD must be asls")


def _peak_config(
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    resolver_mode: str,
    baseline_audit_method: str = "",
    baseline_integration_method: str = "asls",
) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=int(defaults["smooth_window"]),
        smooth_polyorder=int(defaults["smooth_polyorder"]),
        ms1_morphology_smoothing_window_points=int(
            defaults["ms1_morphology_smoothing_window_points"]
        ),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=float(defaults["ms2_precursor_tol_da"]),
        nl_min_intensity_ratio=float(defaults["nl_min_intensity_ratio"]),
        resolver_mode=resolver_mode,
        resolver_chrom_threshold=float(defaults["resolver_chrom_threshold"]),
        resolver_min_search_range_min=float(defaults["resolver_min_search_range_min"]),
        resolver_min_relative_height=float(defaults["resolver_min_relative_height"]),
        resolver_min_absolute_height=float(defaults["resolver_min_absolute_height"]),
        resolver_min_ratio_top_edge=float(defaults["resolver_min_ratio_top_edge"]),
        resolver_peak_duration_min=float(defaults["resolver_peak_duration_min"]),
        resolver_peak_duration_max=float(defaults["resolver_peak_duration_max"]),
        resolver_min_scans=int(defaults["resolver_min_scans"]),
        baseline_audit_method=baseline_audit_method,
        baseline_integration_method=baseline_integration_method,
    )


if __name__ == "__main__":
    raise SystemExit(main())
