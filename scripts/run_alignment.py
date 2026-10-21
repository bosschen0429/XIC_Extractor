from __future__ import annotations

import argparse
import cProfile
import io
import math
import os
import pstats
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from zipfile import BadZipFile

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
)
from xic_extractor.alignment.owner_build_route_receipt import OwnerBuildRouteReceipt
from xic_extractor.applications.untargeted.application import (
    AlignmentProcessExecutionError,
    run_untargeted_application,
)
from xic_extractor.applications.untargeted.istd_reference import (
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    RetainedAlignmentOptions,
    UntargetedExecutionContext,
    UntargetedRequest,
    validate_retained_processing_profile,
)
from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.configuration.execution_profile import (
    DEFAULT_ALIGNMENT_WORKER_BACKEND,
    UntargetedExecutionPolicy,
    load_execution_profile,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.configuration.paths import targets_template_path
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
)
from xic_extractor.execution.process_memory import ProcessTreeMemorySampler
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.presets import (
    BoundCidNlRecipe,
    PresetError,
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.presets.models import Preset
from xic_extractor.raw_reader import RawReaderError, read_analysis_set
from xic_extractor.sample_metadata import SampleMetadataError
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        execution_profile = load_execution_profile(alignment_workers=args.raw_workers)
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.raw_workers = execution_profile.alignment_workers
    try:
        preset, recipe = _resolve_preset(args)
    except PresetError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    discovery_batch_index = args.discovery_batch_index.resolve()
    raw_dir = args.raw_dir.resolve()
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    sample_metadata = None
    if args.sample_metadata is not None:
        metadata_path = args.sample_metadata.resolve()
        try:
            # 上機資訊 annotates; the RAW owner supplies names and order.
            analysis_set = read_analysis_set(
                raw_dir, dll_dir, sequence_source=metadata_path
            )
            sample_metadata = tuple(entry.metadata for entry in analysis_set.analyzed)
        except (SampleMetadataError, RawReaderError, ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        batches = sorted({row.batch_id for row in sample_metadata})
        print(
            f"Sample metadata: {metadata_path} "
            f"({len(sample_metadata)} samples; {len(batches)} batches: "
            f"{', '.join(batch or '<unspecified>' for batch in batches)})"
        )
    else:
        print(
            "Sample metadata: RAW-derived metadata "
            f"(single-directory batch fallback: {raw_dir.name})"
        )
    signal_processing_profile = resolve_signal_processing_profile(
        signal_processing_method=args.signal_processing_method,
        signal_processing_window_points=args.signal_processing_window_points,
    )
    peak_config_resolver_mode = recipe.discovery_settings.resolver_mode
    peak_config_nl_min_intensity_ratio = (
        recipe.discovery_settings.nl_min_intensity_ratio
    )
    owner_build_xic_backend = recipe.owner_build_xic_backend
    if preset.name == "DNA dR Owner Backfill (compat)":
        print(
            "Preset readiness authority: not_evaluated "
            "(historical compatibility name only)"
        )
    try:
        execution = UntargetedExecutionPolicy(
            raw_workers=args.raw_workers,
            raw_worker_backend=args.raw_worker_backend,
            retain_acquisition_cache=args.retain_acquisition_cache,
            acquisition_cache_dir=args.acquisition_cache_dir,
            max_partition_jobs=args.max_partition_jobs,
        )
        legacy = RetainedAlignmentOptions(
            owner_build_xic_backend=owner_build_xic_backend,
        )
        validate_retained_processing_profile(signal_processing_profile)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
        known_istd_reference = _resolve_istd_input(args)
    except (ValueError, OSError, BadZipFile, KeyError, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
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
        _validate_expected_full_cohort_contract(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        resolved_raw_paths = _validate_launch_artifacts(
            discovery_batch,
            raw_dir=raw_dir,
            require_all_raw=(args.preflight_only or args.expected_sample_count == 85),
        )
    except ValueError as exc:
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
            owner_build_xic_backend=owner_build_xic_backend,
        )
        return 0

    timing_recorder = (
        TimingRecorder(
            "alignment",
            process_memory_sampler=ProcessTreeMemorySampler(),
            live_output_path=(
                args.timing_live_output.resolve()
                if args.timing_live_output is not None
                else None
            ),
        )
        if args.timing_output is not None or args.timing_live_output is not None
        else None
    )
    if timing_recorder is not None:
        timing_recorder.start_process_memory()
    try:
        request = UntargetedRequest(
            discovery_batch_index=discovery_batch_index,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
            alignment_config=replace(
                recipe.alignment_config, backfill_enabled=args.backfill
            ),
            peak_config=_peak_config(
                raw_dir,
                dll_dir,
                output_dir,
                peak_config_resolver_mode,
                baseline_integration_method=_baseline_integration_method(args),
                signal_processing_profile=signal_processing_profile,
                nl_min_intensity_ratio=peak_config_nl_min_intensity_ratio,
            ),
            discovery_settings=recipe.discovery_settings,
            signal_processing_profile=signal_processing_profile,
            sample_metadata=sample_metadata,
            known_istd_reference_workbook=args.known_istd_reference_workbook,
            targets_csv=args.targets_csv,
            known_istd_reference=known_istd_reference,
            execution=execution,
            legacy=legacy,
            publication=UntargetedPublicationPolicy(
                output_level=args.output_level,
                emit_alignment_cells=args.emit_alignment_cells,
                emit_cid_nl_explanation=args.emit_cid_nl_explanation,
                explanation_full_audit=args.explanation_full_audit,
                explanation_curve_mz_ranges=tuple(args.explanation_curve_mz_range),
                emit_alignment_status_matrix=args.emit_alignment_status_matrix,
                emit_alignment_backfill_seed_audit=args.emit_alignment_backfill_seed_audit,
            ),
        )
        context = UntargetedExecutionContext(timing_recorder=timing_recorder)
        profiler = cProfile.Profile() if args.profile == "cprofile" else None
        try:
            if profiler is None:
                outputs = run_untargeted_application(request, context)
            else:
                outputs = profiler.runcall(run_untargeted_application, request, context)
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
    finally:
        if timing_recorder is not None:
            timing_recorder.stop_process_memory(scope="foreground")
            if args.timing_output is not None:
                timing_path = args.timing_output.resolve()
                timing_recorder.write_json(timing_path)
                print(f"Timing JSON: {timing_path}")

    if outputs.review_tsv is not None:
        print(f"Alignment review TSV: {outputs.review_tsv}")
    if outputs.matrix_tsv is not None:
        print(f"Alignment matrix TSV: {outputs.matrix_tsv}")
    if outputs.cells_tsv is not None:
        print(f"Alignment cells TSV: {outputs.cells_tsv}")
    if outputs.backfill_seed_audit_tsv is not None:
        print(f"Alignment backfill seed audit TSV: {outputs.backfill_seed_audit_tsv}")
    if outputs.status_matrix_tsv is not None:
        print(f"Alignment status matrix TSV: {outputs.status_matrix_tsv}")
    if outputs.run_metadata_json is not None:
        print(f"Alignment run metadata JSON: {outputs.run_metadata_json}")
    owner_build_route_receipt = outputs.owner_build_route_receipt
    if not isinstance(owner_build_route_receipt, OwnerBuildRouteReceipt):
        print("Alignment owner did not return its route receipt", file=sys.stderr)
        return 2
    _print_owner_build_route_receipt(owner_build_route_receipt)
    if timing_recorder is not None:
        if args.timing_live_output is not None:
            print(f"Timing live JSON: {args.timing_live_output.resolve()}")
    return 0


def _resolve_istd_input(args: argparse.Namespace) -> KnownIstdReference | None:
    """Resolve the CLI's normal ISTD input before any RAW acquisition."""
    if (
        sum(
            (
                args.targets_csv is not None,
                args.known_istd_reference_workbook is not None,
                args.allow_uncorrected,
            )
        )
        > 1
    ):
        raise ValueError(
            "--targets-csv, --known-istd-reference-workbook and "
            "--allow-uncorrected are mutually exclusive"
        )
    if args.allow_uncorrected:
        print("ISTD alignment: disabled (explicit --allow-uncorrected)")
        return None
    if args.known_istd_reference_workbook is not None:
        args.known_istd_reference_workbook = (
            args.known_istd_reference_workbook.resolve()
        )
        reference = read_known_istd_reference(args.known_istd_reference_workbook)
        print("ISTD alignment: enabled (explicit workbook)")
        print(f"Known ISTD reference workbook: {reference.workbook_path}")
        print(f"Known ISTD reference SHA-256: {reference.workbook_sha256}")
        print(f"Known ISTD labels loaded: {len(reference.anchor.observed_labels)}")
        return reference
    source = "explicit targets CSV" if args.targets_csv is not None else "GUI default"
    args.targets_csv = (args.targets_csv or targets_template_path()).resolve()
    if not args.targets_csv.is_file():
        raise ValueError(f"{args.targets_csv}: targets CSV file does not exist")
    from xic_extractor.configuration.targets import read_istd_targets

    selection = read_istd_targets(args.targets_csv)
    print(f"ISTD alignment: enabled ({source})")
    print(
        f"Targets CSV: {args.targets_csv} "
        f"({len(selection.targets)} ISTD loaded, "
        f"{selection.skipped_count} non-ISTD rows skipped)"
    )
    return None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish finalized pure CID-NL Alignment "
            "from an owner-closed discovery batch index."
        ),
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
        "--sample-metadata",
        type=Path,
        help=(
            "Sample metadata or sequence file preserving batch IDs, roles and "
            "injection orders. Required to preserve multiple batches in a "
            "flattened RAW directory; otherwise metadata is derived from RAW "
            "with the directory name as a single batch."
        ),
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
        help=(
            "Output directory for selected Alignment artifacts. The default "
            "writes only alignment_matrix.tsv."
        ),
    )
    parser.add_argument(
        "--blank-library",
        type=Path,
        help="Use the same explicit background library binding as Discovery.",
    )
    parser.add_argument(
        "--known-istd-reference-workbook",
        type=Path,
        help=(
            "Report whose Targets ISTDs and XIC Results RT align known SUPPORT owners."
        ),
    )
    parser.add_argument(
        "--targets-csv",
        type=Path,
        help=(
            "Targets CSV containing ISTD definitions. When provided, ISTD drift "
            "anchors are extracted directly in-memory from RAW files without "
            "requiring an external Excel workbook. Defaults to the GUI template "
            "xic_extractor/configuration/data/targets.csv unless a workbook "
            "or --allow-uncorrected "
            "is specified. The resolved source and ISTD count are printed."
        ),
    )
    parser.add_argument(
        "--allow-uncorrected",
        action="store_true",
        help=(
            "Explicitly run without ISTD RT alignment. Mutually exclusive with "
            "--targets-csv and --known-istd-reference-workbook."
        ),
    )
    parser.add_argument(
        "--preset",
        help=(
            "Required built-in pure CID-NL preset name or TOML path. Use "
            "dna_dr for the DNA CID-NL recipe."
        ),
    )
    parser.add_argument(
        "--raw-workers",
        type=_positive_int,
        help=(
            "Worker budget for source fingerprinting, persisted-input indexing, "
            "and RAW sample phases within each bounded CID-NL partition "
            "(default: shared execution profile alignment_workers, normally 4)."
        ),
    )
    parser.add_argument(
        "--raw-worker-backend",
        choices=("process", "thread"),
        default=DEFAULT_ALIGNMENT_WORKER_BACKEND,
        help=(
            "Backend for persisted-input indexing and RAW sample execution "
            f"('process' or 'thread', default: '{DEFAULT_ALIGNMENT_WORKER_BACKEND}'). "
            "Fingerprinting uses threads."
        ),
    )
    parser.add_argument(
        "--max-partition-jobs",
        type=_positive_int,
        default=100_000,
        help=("Maximum job budget per bounded CID-NL partition (default: 100000)."),
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
            "a timeout-safe heartbeat for long FULL COHORT validation runs."
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
        "--owner-build-xic-backend",
        choices=("raw", "raw-super-window", "ms1-index"),
        default=None,
        help=(
            "Configured backend provenance recorded in the owner route receipt. "
            "The finalized CID-NL owner determines the effective execution route."
        ),
    )
    parser.add_argument(
        "--signal-processing-method",
        choices=(DEFAULT_SIGNAL_PROCESSING_PROFILE.method,),
        default=DEFAULT_SIGNAL_PROCESSING_PROFILE.method,
        help=(
            "Canonical signal smoothing method. Gaussian is the only supported "
            "method; AsLS is the fixed baseline estimator."
        ),
    )
    parser.add_argument(
        "--signal-processing-window-points",
        type=_signal_processing_window_points,
        default=DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points,
        metavar="ODD_POINTS",
        help="Canonical Gaussian window: odd integer 3-999 (default 15).",
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
        default="production",
        help=(
            "Alignment artifact level. Default production writes only "
            "alignment_matrix.tsv; use machine/debug/validation or explicit "
            "emit flags for additional artifacts."
        ),
    )
    parser.add_argument(
        "--backfill",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Revisit missing group cells (default: enabled); "
            "--no-backfill leaves them unsearched."
        ),
    )
    parser.add_argument("--emit-alignment-cells", action="store_true")
    parser.add_argument(
        "--retain-acquisition-cache",
        action="store_true",
        help=(
            "keep .cid_nl_acquisition_cache after a successful run so a repeat "
            "run skips rereading the RAW files; a failed attempt keeps it "
            "either way"
        ),
    )
    parser.add_argument(
        "--acquisition-cache-dir",
        type=Path,
        help=(
            "keep the RAW acquisition cache in this directory instead of the "
            "output directory; runs share it and it is never removed, so a "
            "repeat run into a new output directory skips rereading the RAW files"
        ),
    )
    parser.add_argument(
        "--emit-cid-nl-explanation",
        action="store_true",
        help=(
            "write a compact decision review without full audit or curve arrays; "
            "debug and validation levels include it already"
        ),
    )
    parser.add_argument(
        "--explanation-full-audit",
        action="store_true",
        help="Explicitly retain the expensive full explanation and all curves",
    )
    parser.add_argument(
        "--explanation-curve-mz-range",
        action="append",
        default=[],
        type=_mz_range,
        metavar="LOWER:UPPER",
        help="Retain curves only in this m/z range (repeatable); implies explanation",
    )
    parser.add_argument(
        "--baseline-integration-method",
        help="Baseline integration method. Only asls is supported.",
    )
    parser.add_argument(
        "--emit-alignment-backfill-seed-audit",
        action="store_true",
        help=(
            "Emit lightweight owner-backfill seed provenance needed by retained "
            "backfill evidence gates and seed-specific overlay galleries."
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


def _validate_expected_full_cohort_contract(args: argparse.Namespace) -> None:
    if args.expected_sample_count != 85:
        return

    failures: list[str] = []
    if args.output_level not in {"production", "validation-minimal"}:
        failures.append(
            "--output-level must be 'production' or 'validation-minimal'; "
            f"got {args.output_level!r}"
        )
    if args.timing_output is None:
        failures.append("--timing-output is required for canonical FULL COHORT runs")
    if args.timing_live_output is None:
        failures.append(
            "--timing-live-output is required for canonical FULL COHORT runs"
        )
    if not _is_repo_venv_python():
        failures.append(
            "Python executable must be under this worktree .venv for canonical "
            f"FULL COHORT runs; got {sys.executable}"
        )

    if failures:
        joined = "\n- ".join(failures)
        raise ValueError(f"FULL COHORT canonical launch contract failed:\n- {joined}")


def _resolve_preset(args: argparse.Namespace) -> tuple[Preset, BoundCidNlRecipe]:
    if args.preset is None:
        raise PresetError(
            "Alignment supports only pure single-provider CID-NL; use "
            "--preset dna_dr with an owner-closed discovery batch index"
        )
    preset = load_preset(args.preset)
    recipe_overrides: dict[str, object] = {}
    if args.owner_build_xic_backend is not None:
        recipe_overrides["owner_build_xic_backend"] = args.owner_build_xic_backend
    recipe = bind_cid_nl_recipe(
        preset, "align_only", recipe_overrides=recipe_overrides or None
    )
    if args.blank_library is not None:
        blank_library = args.blank_library.resolve()
        if not blank_library.is_file():
            raise PresetError(f"{blank_library}: blank library does not exist")
        recipe = replace(
            recipe,
            discovery_settings=replace(
                recipe.discovery_settings,
                enable_blank_qualification=True,
                blank_library_path=str(blank_library),
            ),
        )
    if not is_supported_alignment_discovery(recipe.discovery_settings):
        raise PresetError(
            f"Alignment does not support discovery settings in preset {preset.name}"
        )
    return preset, recipe


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
    owner_build_xic_backend: str,
) -> None:
    sample_count = len(discovery_batch.sample_order)
    print("Alignment launch preflight OK (diagnostic_only; no validation completed)")
    print(
        "Preflight scope: owner-closed discovery batch, candidate CSV existence, "
        "RAW path existence; no candidate CSVs loaded; no RAW files opened."
    )
    print(f"Discovery batch index: {discovery_batch_index}")
    print(f"Discovery batch samples: {sample_count}")
    print(f"Candidate CSVs found: {len(discovery_batch.candidate_csvs)}")
    print(f"RAW paths found: {len(resolved_raw_paths)}")
    if args.expected_sample_count is not None:
        print(f"Expected sample count: {args.expected_sample_count}")
    print(
        "FULL COHORT canonical contract: "
        f"{'enforced' if args.expected_sample_count == 85 else 'not requested'}"
    )
    print(f"Python executable: {sys.executable}")
    print(f"run_alignment module: {Path(__file__).resolve()}")
    print(f"Working directory: {Path.cwd().resolve()}")
    print(f"RAW dir: {raw_dir}")
    print(f"DLL dir: {dll_dir}")
    print(f"Output dir: {output_dir}")
    print(f"Output level: {args.output_level}")
    print(f"Alignment worker budget: {args.raw_workers}")
    print(f"Backfill: {'enabled' if args.backfill else 'disabled (not searched)'}")
    print(f"Input indexing / RAW backend: {args.raw_worker_backend}")
    print("Source fingerprint backend: thread")
    _print_owner_build_route_preflight(owner_build_xic_backend)
    print(
        "Timing JSON: "
        f"{args.timing_output.resolve() if args.timing_output else '<none>'}"
    )
    print(
        "Timing live JSON: "
        f"{args.timing_live_output.resolve() if args.timing_live_output else '<none>'}"
    )


def _print_owner_build_route_receipt(receipt: OwnerBuildRouteReceipt) -> None:
    print(
        "Configured owner-build XIC backend: "
        f"{receipt.configured_owner_build_xic_backend}"
    )
    if receipt.effective_owner_build_xic_backend is None:
        print(f"Effective owner-build route: {receipt.effective_owner_build_route}")
    else:
        print(
            "Effective owner-build route: "
            f"{receipt.effective_owner_build_route}:"
            f"{receipt.effective_owner_build_xic_backend}"
        )
    print("Effective route validation: owner_validated")
    if receipt.processing_run_spec_id is None:
        print("ProcessingRunSpec identity: <none; CID-NL Alignment CLI is align-only>")
    else:
        print(f"ProcessingRunSpec id: {receipt.processing_run_spec_id}")
        print(f"ProcessingRunSpec hash: {receipt.processing_run_spec_hash}")


def _print_owner_build_route_preflight(owner_build_xic_backend: str) -> None:
    print(f"Configured owner-build XIC backend: {owner_build_xic_backend}")
    print("Effective owner-build route: <not executed>")
    print("Effective route validation: not_executed")
    print("ProcessingRunSpec identity: <none; CID-NL Alignment CLI is align-only>")


def _mz_range(value: str) -> tuple[float, float]:
    try:
        lower, upper = map(float, value.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use LOWER:UPPER") from exc
    if not math.isfinite(lower + upper) or not 0 < lower <= upper:
        raise argparse.ArgumentTypeError(
            "m/z bounds must be positive, finite and ordered"
        )
    return lower, upper


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer >= 1") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be an integer >= 1")
    return parsed


def _signal_processing_window_points(value: str) -> int:
    try:
        parsed = int(value)
        resolve_signal_processing_profile(signal_processing_window_points=parsed)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
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
    baseline_integration_method: str = "asls",
    signal_processing_profile: SignalProcessingProfile | None = None,
    nl_min_intensity_ratio: float | None = None,
) -> ExtractionConfig:
    profile = signal_processing_profile or resolve_signal_processing_profile()
    return ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        ms1_morphology_smoothing_window_points=profile.window_points,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=DEFAULT_CID_NL_MASS_POLICY.ms2_precursor_tol_da,
        nl_min_intensity_ratio=(
            DEFAULT_CID_NL_MASS_POLICY.nl_min_intensity_ratio
            if nl_min_intensity_ratio is None
            else nl_min_intensity_ratio
        ),
        resolver_mode=resolver_mode,
        baseline_integration_method=baseline_integration_method,
    )


if __name__ == "__main__":
    raise SystemExit(main())
