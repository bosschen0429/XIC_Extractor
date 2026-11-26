from __future__ import annotations

import argparse
import cProfile
import io
import math
import pstats
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from zipfile import BadZipFile

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
)
from xic_extractor.applications.untargeted.application import (
    AlignmentProcessExecutionError,
    run_untargeted_application,
    run_untargeted_grouping,
)
from xic_extractor.applications.untargeted.istd_reference import (
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    AlignmentReplayOptions,
    UntargetedExecutionContext,
    UntargetedRequest,
    validate_retained_processing_profile,
)
from xic_extractor.configuration.execution_profile import (
    DEFAULT_WORKER_BACKEND,
    WORKER_BACKENDS,
    ExecutionProfile,
    UntargetedExecutionPolicy,
    load_execution_profile,
)
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.paths import targets_template_path
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
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
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.raw_sequence import read_analysis_set
from xic_extractor.sample_metadata import SampleMetadataError
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "regroup":
        from tools.diagnostics.grouping_experiment import regroup_main

        return regroup_main(argv[1:])

    if argv and argv[0] in {"matrix", "pair-batches", "backfill"}:
        return _run_stage_replay(argv)

    args = _parse_args(argv)
    try:
        execution_profile = load_execution_profile(
            alignment_workers=args.raw_workers, backend=args.raw_worker_backend
        )
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
    signal_processing_profile = resolve_signal_processing_profile(
        signal_processing_method=args.signal_processing_method,
        signal_processing_window_points=args.signal_processing_window_points,
    )
    try:
        execution = UntargetedExecutionPolicy(
            profile=execution_profile,
            max_partition_jobs=args.max_partition_jobs,
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
    timing_recorder = (
        TimingRecorder("alignment", process_memory_sampler=ProcessTreeMemorySampler())
        if not args.preflight_only and (args.timing_output or args.timing_live_output)
        else None
    )
    try:
        with (
            timing_recorder.stage("alignment.read_batch_index")
            if timing_recorder
            else nullcontext()
        ):
            discovery_batch = read_discovery_batch_index(discovery_batch_index)
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
        resolved_raw_paths = _validate_launch_artifacts(
            discovery_batch,
            raw_dir=raw_dir,
            require_all_raw=(
                args.preflight_only
                or args.require_all_raw
                or args.stop_after == "grouping"
            ),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sample_metadata = None
    if args.sample_metadata is not None:
        metadata_path = args.sample_metadata.resolve()
        try:
            analysis_set = read_analysis_set(
                raw_dir, dll_dir, analysis="untargeted", sequence_source=metadata_path
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
    if args.preflight_only:
        _print_preflight_summary(
            args,
            discovery_batch_index=discovery_batch_index,
            discovery_batch=discovery_batch,
            resolved_raw_paths=resolved_raw_paths,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
        )
        return 0

    if timing_recorder is not None:
        timing_recorder.live_output_path = (
            args.timing_live_output.resolve()
            if args.timing_live_output is not None
            else None
        )
        timing_recorder.start_process_memory()
    try:
        request = UntargetedRequest(
            discovery_batch_index=discovery_batch,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
            alignment_config=replace(
                recipe.alignment_config, backfill_enabled=args.backfill
            ),
            discovery_settings=recipe.discovery_settings,
            signal_processing_profile=signal_processing_profile,
            sample_metadata=sample_metadata,
            known_istd_reference_workbook=args.known_istd_reference_workbook,
            targets_csv=args.targets_csv,
            known_istd_reference=known_istd_reference,
            execution=execution,
            publication=UntargetedPublicationPolicy(
                output_level=args.output_level,
                emit_alignment_cells=args.emit_alignment_cells,
                emit_alignment_explanation=args.emit_alignment_explanation,
                explanation_full_audit=args.explanation_full_audit,
                explanation_curve_mz_ranges=tuple(args.explanation_curve_mz_range),
                emit_alignment_status_matrix=args.emit_alignment_status_matrix,
            ),
            replay=AlignmentReplayOptions(
                grouping=args.save_grouping_inputs,
                backfill=args.save_backfill_inputs,
                pairing=args.save_pairing_inputs,
                matrix=args.save_matrix_inputs,
            ),
        )
        context = UntargetedExecutionContext(timing_recorder=timing_recorder)
        profiler = cProfile.Profile() if args.profile == "cprofile" else None
        try:
            if profiler is not None:
                profiler.enable()
            if args.stop_after == "grouping":
                grouping_output = run_untargeted_grouping(
                    request,
                    context,
                    save_snapshot=args.save_grouping_inputs,
                    save_backfill_inputs=args.save_backfill_inputs,
                    save_pairing_inputs=args.save_pairing_inputs,
                )
            else:
                outputs = run_untargeted_application(request, context)
        finally:
            if profiler is not None:
                profiler.disable()
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
                coverage = timing_recorder.wall_coverage()
                if coverage["unattributed_fraction"] > 0.05:
                    print(
                        "Timing warning: "
                        f"{coverage['unattributed_wall_sec'] / 60:.1f} min "
                        f"({coverage['unattributed_fraction']:.0%}) of wall time "
                        "is outside every recorded stage",
                        file=sys.stderr,
                    )

    if args.stop_after == "grouping":
        print(f"Grouping results: {grouping_output}")
        if args.save_pairing_inputs:
            print(f"Batch pairing inputs: {grouping_output / 'pairing-inputs'}")
        if args.save_backfill_inputs:
            print(f"Backfill inputs: {grouping_output / 'backfill-inputs'}")
        return 0
    if outputs.review_tsv is not None:
        print(f"Alignment review TSV: {outputs.review_tsv}")
    if outputs.evidence is not None:
        print(f"Alignment explanation: {outputs.evidence.report_path}")
    if outputs.matrix_tsv is not None:
        print(f"Alignment matrix TSV: {outputs.matrix_tsv}")
    if outputs.cells_tsv is not None:
        print(f"Alignment cells TSV: {outputs.cells_tsv}")
    if outputs.status_matrix_tsv is not None:
        print(f"Alignment status matrix TSV: {outputs.status_matrix_tsv}")
    if outputs.run_metadata_json is not None:
        print(f"Alignment run metadata JSON: {outputs.run_metadata_json}")
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


def _run_stage_replay(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="run_alignment")
    stages = parser.add_subparsers(dest="stage", required=True)
    backfill = stages.add_parser(
        "backfill", description="Rerun Backfill on a local grouping checkpoint."
    )
    backfill.add_argument(
        "--inputs",
        required=True,
        type=Path,
        help="Directory captured with --save-backfill-inputs",
    )
    backfill.add_argument(
        "--save-matrix-inputs",
        action="store_true",
        help="Save finalized values for Matrix replay.",
    )
    pairing = stages.add_parser(
        "pair-batches", description="Match finished batches without RAW or Backfill."
    )
    pairing.add_argument(
        "--inputs",
        required=True,
        nargs="+",
        type=Path,
        help="One captured pairing input per batch (at least two).",
    )
    matrix = stages.add_parser(
        "matrix",
        description="Write only the Matrix from finalized cell values.",
    )
    matrix.add_argument(
        "--backfill",
        required=True,
        nargs="+",
        type=Path,
        help="Completed matrix-cells.pickle.gz file(s)",
    )
    matrix.add_argument(
        "--pairing",
        type=Path,
        help="Completed matrix-join.pickle.gz (required for multiple batches)",
    )
    for stage in (backfill, pairing, matrix):
        stage.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.stage == "backfill":
            from xic_extractor.diagnostics.backfill_replay import replay_backfill

            label = "Backfill decisions"
            path = replay_backfill(
                args.inputs,
                args.output_dir,
                execution=ExecutionProfile(alignment_workers=1, backend="thread"),
                save_matrix_inputs=args.save_matrix_inputs,
            )
        elif args.stage == "pair-batches":
            from xic_extractor.diagnostics.batch_pairing import replay_pairing

            label = "Batch correspondence"
            path = replay_pairing(args.inputs, args.output_dir)
        else:
            from xic_extractor.diagnostics.matrix_replay import replay_matrix

            label = "Matrix"
            path = replay_matrix(args.backfill, args.pairing, args.output_dir)
    except (OSError, ValueError, RawReaderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish finalized pure CID-NL Alignment "
            "from an owner-closed discovery batch index. To recompute only grouping "
            "from a saved snapshot, use: %(prog)s regroup --help. "
            "To rerun only Backfill, use: %(prog)s backfill --help. "
            "To match finished batches, use: %(prog)s pair-batches --help. "
            "To write only the Matrix, use: %(prog)s matrix --help."
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
            "writes alignment_matrix.tsv and alignment_rt_anchors.tsv."
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
            "xic_extractor/configuration/data/targets.xlsx unless a workbook "
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
        choices=WORKER_BACKENDS,
        default=DEFAULT_WORKER_BACKEND,
        help=(
            "Backend for persisted-input indexing and RAW sample execution "
            f"('process' or 'thread', default: '{DEFAULT_WORKER_BACKEND}'). "
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
            "Expected discovery_batch_index sample count. This only checks the "
            "size of the input batch; it does not select a validation tier."
        ),
    )
    parser.add_argument(
        "--require-all-raw",
        action="store_true",
        help="Check that all indexed RAWs exist before acquisition.",
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
        help="Canonical Gaussian window: odd integer 3-999 (default 9).",
    )
    parser.add_argument(
        "--output-level",
        choices=(
            "production",
            "machine",
            "debug",
            "validation-minimal",
        ),
        default="production",
        help=(
            "Alignment artifact level. Default production writes only "
            "alignment_matrix.tsv; use machine/debug or explicit "
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
    parser.add_argument(
        "--stop-after",
        choices=("grouping",),
        help=(
            "Stop after initial assessment, RT calibration and grouping; write "
            "group, member and decision tables without Backfill or Matrix publication."
        ),
    )
    parser.add_argument(
        "--save-grouping-inputs",
        action="store_true",
        help="Save grouping-inputs.json.gz for regroup.",
    )
    parser.add_argument(
        "--save-backfill-inputs",
        action="store_true",
        help="Save local inputs for Backfill replay.",
    )
    parser.add_argument(
        "--save-pairing-inputs",
        action="store_true",
        help="Save one input per declared batch for pair-batches.",
    )
    parser.add_argument(
        "--save-matrix-inputs",
        action="store_true",
        help="Save finalized Matrix inputs and, for multiple batches, the frozen join.",
    )
    parser.add_argument("--emit-alignment-cells", action="store_true")
    parser.add_argument(
        "--emit-alignment-explanation",
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
    parser.add_argument("--emit-alignment-status-matrix", action="store_true")
    args = parser.parse_args(argv)
    if args.save_matrix_inputs and args.stop_after == "grouping":
        parser.error("--save-matrix-inputs requires a complete Alignment run")
    if args.save_backfill_inputs and not args.backfill:
        parser.error("--save-backfill-inputs cannot capture a --no-backfill plan")
    return args


def _validate_launch_artifacts(
    discovery_batch: DiscoveryBatchInput,
    *,
    raw_dir: Path,
    require_all_raw: bool,
) -> dict[str, Path]:
    missing_peak_csvs = tuple(
        (raw_stem, peak_csv)
        for raw_stem, peak_csv in discovery_batch.peak_csvs.items()
        if not peak_csv.is_file()
    )
    if missing_peak_csvs:
        raw_stem, peak_csv = missing_peak_csvs[0]
        suffix = _truncated_count_suffix(len(missing_peak_csvs))
        raise ValueError(
            "peak CSV does not exist for discovery sample "
            f"{raw_stem}: {peak_csv}{suffix}"
        )

    if not require_all_raw:
        return {}

    resolved_raw_paths = existing_raw_paths(
        sample_order=discovery_batch.sample_order,
        raw_files=discovery_batch.raw_files,
        raw_dir=raw_dir,
    )
    missing_raw_samples = tuple(
        raw_stem
        for raw_stem in discovery_batch.sample_order
        if raw_stem not in resolved_raw_paths
    )
    if missing_raw_samples:
        sample_text = ", ".join(missing_raw_samples[:5])
        suffix = _truncated_count_suffix(len(missing_raw_samples))
        raise ValueError(
            f"{raw_dir}: RAW file does not exist for discovery sample(s): "
            f"{sample_text}{suffix}"
        )
    return resolved_raw_paths


def _resolve_preset(args: argparse.Namespace) -> tuple[Preset, BoundCidNlRecipe]:
    if args.preset is None:
        raise PresetError(
            "Alignment supports only pure single-provider CID-NL; use "
            "--preset dna_dr with an owner-closed discovery batch index"
        )
    preset = load_preset(args.preset)
    recipe_overrides: dict[str, object] = {}
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
) -> None:
    sample_count = len(discovery_batch.sample_order)
    print("Alignment launch preflight OK (diagnostic_only; no validation completed)")
    print(
        "Preflight scope: owner-closed discovery batch, candidate CSV existence, "
        "RAW path existence; no candidate CSVs loaded; no RAW files opened."
    )
    print(f"Discovery batch index: {discovery_batch_index}")
    print(f"Discovery batch samples: {sample_count}")
    print(f"Candidate CSVs found: {len(discovery_batch.peak_csvs)}")
    print(f"RAW paths found: {len(resolved_raw_paths)}")
    if args.expected_sample_count is not None:
        print(f"Expected sample count: {args.expected_sample_count}")
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
    print(
        "Timing JSON: "
        f"{args.timing_output.resolve() if args.timing_output else '<none>'}"
    )
    print(
        "Timing live JSON: "
        f"{args.timing_live_output.resolve() if args.timing_live_output else '<none>'}"
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
