import argparse
import csv
import multiprocessing
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.applications.targeted.limited_identity_workflow import (
    execute_limited_identity_workflow,
    requires_limited_identity_workflow,
)
from xic_extractor.applications.targeted.output_boundary import (
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.loader import (
    load_config,
    prepare_targeted_config,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.output.method_manifest import (
    MethodManifestContext,
    MethodManifestError,
    load_method_manifest_for_replay,
)
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.targeted_ms1_shape_identity_policy import (
    EXPLICIT_SUPPORT_TSV_POLICY,
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
)


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    cli_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)
    prepared = None
    try:
        if args.replay_manifest is None:
            _validate_targeted_ms1_shape_identity_auto_args(args)
        replay_request = None
        if args.replay_manifest is not None:
            _reject_replay_overrides(args)
            replay_request = load_method_manifest_for_replay(args.replay_manifest)
            base_dir = replay_request.base_dir
            config_dir = replay_request.config_dir
            settings_overrides = replay_request.settings_overrides
            output_mode = "excel"
        else:
            base_dir = (args.base_dir or Path.cwd()).resolve()
            config_dir = base_dir / "config"
            settings_overrides = {}
            output_mode = "excel"
            if args.data_dir is not None:
                data_dir = args.data_dir.resolve()
                if not data_dir.is_dir():
                    raise ConfigError(
                        f"{data_dir}: data_dir override must be a directory"
                    )
                settings_overrides["data_dir"] = str(data_dir)
            if args.injection_order_source is not None:
                settings_overrides["injection_order_source"] = str(
                    args.injection_order_source.resolve()
                )
            if args.model_selection_expected_diff_approvals is not None:
                settings_overrides[
                    "model_selection_expected_diff_approval_registry"
                ] = str(args.model_selection_expected_diff_approvals.resolve())
            if args.targeted_ms1_shape_identity_support_tsv is not None:
                settings_overrides["targeted_ms1_shape_identity_support_tsv"] = str(
                    args.targeted_ms1_shape_identity_support_tsv.resolve()
                )
                if args.targeted_ms1_shape_identity_activation_policy is None:
                    settings_overrides[
                        "targeted_ms1_shape_identity_activation_policy"
                    ] = EXPLICIT_SUPPORT_TSV_POLICY
            if args.targeted_ms1_shape_identity_activation_policy is not None:
                settings_overrides["targeted_ms1_shape_identity_activation_policy"] = (
                    args.targeted_ms1_shape_identity_activation_policy
                )

        if replay_request is None and args.method_dir is not None:
            if args.parallel_mode is not None:
                settings_overrides["parallel_mode"] = args.parallel_mode
            if args.parallel_workers is not None:
                settings_overrides["parallel_workers"] = str(args.parallel_workers)
            prepared = prepare_targeted_config(
                config_dir,
                method_dir=args.method_dir.resolve(),
                settings_overrides=settings_overrides,
                output_dir=base_dir / "output",
            )
            config, targets = prepared.config, list(prepared.targets)
            config_dir = prepared.input_config_dir
            settings_overrides = {}
        elif replay_request is not None:
            config, targets = load_config(
                config_dir,
                settings_overrides=settings_overrides or None,
                output_dir=replay_request.manifest_path.parent,
            )
        elif settings_overrides:
            config, targets = load_config(
                config_dir,
                settings_overrides=settings_overrides,
            )
        else:
            config, targets = load_config(config_dir)
        if replay_request is None and args.parallel_mode is not None:
            config = replace(config, parallel_mode=args.parallel_mode)
        if replay_request is None and args.parallel_workers is not None:
            config = replace(config, parallel_workers=args.parallel_workers)
        if replay_request is not None:
            config = replace(
                config,
                parallel_mode=replay_request.parallel_mode,
                parallel_workers=replay_request.parallel_workers,
            )
        run_config = config
        if _should_run_targeted_ms1_shape_identity_auto_limited_default(
            args,
            config,
            replay_request=replay_request,
        ):
            try:
                auto_result = _run_targeted_ms1_shape_identity_auto_limited_default(
                    run_config,
                    targets,
                    cli_argv=cli_argv,
                    base_dir=base_dir,
                    config_dir=config_dir,
                    settings_overrides=settings_overrides,
                    output_mode=output_mode,
                    auto_output_dir=(
                        None
                        if args.targeted_ms1_shape_identity_auto_output_dir is None
                        else args.targeted_ms1_shape_identity_auto_output_dir.resolve()
                    ),
                )
            except (OSError, ValueError, csv.Error) as exc:
                raise ConfigError(
                    "targeted MS1 shape identity auto-limited workflow failed; "
                    f"verified final output was not published: {exc}"
                ) from exc
            output, _ = auto_result
        else:
            excel_output_path = _excel_output_path(config)
            output = (
                build_canonical_targeted_application_service(extractor.acquire)
                .execute_application(
                    build_targeted_application_request(
                        run_config,
                        targets,
                        progress_callback=_print_progress,
                        method_manifest_context=MethodManifestContext(
                            entrypoint=(
                                "xic-extractor-cli-replay"
                                if replay_request is not None
                                else "xic-extractor-cli"
                            ),
                            argv=cli_argv,
                            base_dir=base_dir,
                            config_dir=config_dir,
                            settings_overrides=settings_overrides,
                            output_mode=output_mode,
                        ),
                        excel_output_path=excel_output_path,
                        replace_existing_outputs=replay_request is not None,
                    )
                )
                .active_output
            )
    except (
        ConfigError,
        MethodManifestError,
        RawReaderError,
        TargetedOutputBoundaryError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    finally:
        if prepared is not None:
            prepared.cleanup()

    print(f"Processed files: {len(output.file_results)}")
    print(f"Diagnostics: {len(output.diagnostics)}")

    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Targeted XIC extraction and deliver one XLSX workbook."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Project/base directory containing config/ and output/.",
    )
    parser.add_argument(
        "--method-dir",
        type=Path,
        default=None,
        help=(
            "Select a complete reusable Targeted method directory (settings.csv "
            "and targets.csv). Batch/machine settings come from --base-dir; "
            "resource-bound portable methods are not yet supported."
        ),
    )
    parser.add_argument(
        "--replay-manifest",
        type=Path,
        default=None,
        help=(
            "Replay a previous xic-extractor-cli run from method_manifest.json. "
            "Replay mode does not accept runtime override flags."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override settings.csv data_dir for validation subsets.",
    )
    parser.add_argument(
        "--injection-order-source",
        type=Path,
        default=None,
        help="Explicit injection-order CSV/XLSX for this RAW batch.",
    )
    parser.add_argument(
        "--parallel-mode",
        choices=("serial", "process"),
        default=None,
        help="Override settings.csv parallel_mode.",
    )
    parser.add_argument(
        "--parallel-workers",
        type=_positive_int,
        default=None,
        help="Override settings.csv parallel_workers.",
    )
    parser.add_argument(
        "--model-selection-expected-diff-approvals",
        type=Path,
        default=None,
        help=(
            "Override settings.csv model_selection_expected_diff_approval_registry "
            "with a durable expected-diff approval TSV."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-support-tsv",
        type=Path,
        default=None,
        help=(
            "Override settings.csv targeted_ms1_shape_identity_support_tsv with a "
            "reviewed targeted_ms1_shape_identity support TSV."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-activation-policy",
        choices=TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
        default=None,
        help=(
            "Override settings.csv targeted_ms1_shape_identity_activation_policy. "
            "Use limited_5hmdc_5medc to restrict support TSV activation to "
            "5-hmdC and 5-medC."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-auto-limited-default",
        action="store_true",
        help=(
            "Run the bounded automatic NL_FAIL/NO_MS2 rescue workflow. This "
            "creates baseline/support/final artifacts, builds a RAW-backed "
            "targeted_ms1_shape_identity support TSV for 5-hmdC and 5-medC, "
            "and applies it with the limited_5hmdc_5medc policy."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-auto-output-dir",
        type=Path,
        default=None,
        help=(
            "Output root for --targeted-ms1-shape-identity-auto-limited-default. "
            "Defaults to output/targeted_ms1_shape_identity_limited_auto_<timestamp>."
        ),
    )
    return parser.parse_args(argv)


def _reject_replay_overrides(args: argparse.Namespace) -> None:
    conflicts = []
    if args.base_dir is not None:
        conflicts.append("--base-dir")
    if args.data_dir is not None:
        conflicts.append("--data-dir")
    if args.method_dir is not None:
        conflicts.append("--method-dir")
    if args.injection_order_source is not None:
        conflicts.append("--injection-order-source")
    if args.parallel_mode is not None:
        conflicts.append("--parallel-mode")
    if args.parallel_workers is not None:
        conflicts.append("--parallel-workers")
    if args.model_selection_expected_diff_approvals is not None:
        conflicts.append("--model-selection-expected-diff-approvals")
    if args.targeted_ms1_shape_identity_support_tsv is not None:
        conflicts.append("--targeted-ms1-shape-identity-support-tsv")
    if args.targeted_ms1_shape_identity_activation_policy is not None:
        conflicts.append("--targeted-ms1-shape-identity-activation-policy")
    if args.targeted_ms1_shape_identity_auto_limited_default:
        conflicts.append("--targeted-ms1-shape-identity-auto-limited-default")
    if args.targeted_ms1_shape_identity_auto_output_dir is not None:
        conflicts.append("--targeted-ms1-shape-identity-auto-output-dir")
    if conflicts:
        joined = ", ".join(conflicts)
        raise ConfigError(f"--replay-manifest cannot be combined with {joined}")


def _validate_targeted_ms1_shape_identity_auto_args(
    args: argparse.Namespace,
) -> None:
    if (
        args.targeted_ms1_shape_identity_auto_output_dir is not None
        and not args.targeted_ms1_shape_identity_auto_limited_default
    ):
        raise ConfigError(
            "--targeted-ms1-shape-identity-auto-output-dir requires "
            "--targeted-ms1-shape-identity-auto-limited-default"
        )
    if not args.targeted_ms1_shape_identity_auto_limited_default:
        return
    conflicts = []
    if args.targeted_ms1_shape_identity_support_tsv is not None:
        conflicts.append("--targeted-ms1-shape-identity-support-tsv")
    if args.targeted_ms1_shape_identity_activation_policy is not None:
        conflicts.append("--targeted-ms1-shape-identity-activation-policy")
    if conflicts:
        joined = ", ".join(conflicts)
        raise ConfigError(
            "--targeted-ms1-shape-identity-auto-limited-default cannot be "
            f"combined with {joined}"
        )


def _should_run_targeted_ms1_shape_identity_auto_limited_default(
    args: argparse.Namespace,
    config: ExtractionConfig,
    *,
    replay_request,
) -> bool:
    if replay_request is not None:
        return False
    if args.targeted_ms1_shape_identity_auto_limited_default:
        return True
    return requires_limited_identity_workflow(config)


def _run_targeted_ms1_shape_identity_auto_limited_default(
    config: ExtractionConfig,
    targets,
    *,
    cli_argv: Sequence[str],
    base_dir: Path,
    config_dir: Path,
    settings_overrides: dict[str, str],
    output_mode: str,
    auto_output_dir: Path | None,
):
    output_root = auto_output_dir or (
        base_dir
        / "output"
        / f"targeted_ms1_shape_identity_limited_auto_{_timestamp_text()}"
    )
    result = execute_limited_identity_workflow(
        build_targeted_application_request(
            config,
            targets,
            progress_callback=_print_progress,
            method_manifest_context=MethodManifestContext(
                entrypoint="xic-extractor-cli",
                argv=tuple(cli_argv),
                base_dir=base_dir,
                config_dir=config_dir,
                settings_overrides=settings_overrides,
                output_mode=output_mode,
            ),
            excel_output_path=_excel_output_path(config),
        ),
        application=build_canonical_targeted_application_service(extractor.acquire),
        output_root=output_root,
    )
    print(f"Auto limited support rows: {result.support_row_count}")
    print(
        f"Auto limited expected-diff gate: {result.gate_status} "
        f"({result.expected_diff_row_count} rows, "
        f"{result.matrix_diff_cell_count} matrix cells)"
    )
    print(
        f"Auto limited verified final output: {result.final_config.output_csv.parent}"
    )
    return result.final_execution.active_output, result.final_config


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("parallel-workers must be >= 1")
    return parsed


def _print_progress(current: int, total: int, filename: str) -> None:
    print(f"{current}/{total} {filename}")


def _excel_output_path(config: ExtractionConfig) -> Path:
    timestamp = _timestamp_text()
    return config.output_csv.parent / f"xic_results_{timestamp}.xlsx"


def _timestamp_text() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


if __name__ == "__main__":
    raise SystemExit(main())
