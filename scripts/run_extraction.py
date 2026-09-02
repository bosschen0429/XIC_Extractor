import argparse
import csv
import multiprocessing
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from xic_extractor import extractor
from xic_extractor.config import ConfigError, ExtractionConfig, load_config
from xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff import (
    write_targeted_ms1_shape_identity_auto_diff_artifacts,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer import (
    run_build_targeted_ms1_shape_identity_supports,
)
from xic_extractor.extraction.targeted_diagnostic_application import (
    build_canonical_targeted_application_service,
)
from xic_extractor.extraction.targeted_inputs import (
    resolve_model_selection_expected_diff_approvals,
)
from xic_extractor.extraction.targeted_service import (
    build_targeted_application_request,
)
from xic_extractor.output.method_manifest import (
    MethodManifestContext,
    MethodManifestError,
    load_method_manifest_for_replay,
)
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.targeted_ms1_shape_identity_policy import (
    EXPLICIT_SUPPORT_TSV_POLICY,
    LIMITED_HMDC_MEDC_POLICY,
    LIMITED_HMDC_MEDC_TARGETS,
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
)


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    cli_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)
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
            output_mode = replay_request.output_mode
        else:
            base_dir = (args.base_dir or Path.cwd()).resolve()
            config_dir = base_dir / "config"
            settings_overrides = {}
            output_mode = "csv_only" if args.skip_excel else "excel"
            if args.data_dir is not None:
                data_dir = args.data_dir.resolve()
                if not data_dir.is_dir():
                    raise ConfigError(
                        f"{data_dir}: data_dir override must be a directory"
                    )
                settings_overrides["data_dir"] = str(data_dir)
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

        if settings_overrides:
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
        skip_excel = output_mode == "csv_only"
        run_config = (
            replace(config, keep_intermediate_csv=True) if skip_excel else config
        )
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
            excel_output_path = None if skip_excel else _excel_output_path(config)
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
    except (ConfigError, MethodManifestError, RawReaderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Processed files: {len(output.file_results)}")
    print(f"Diagnostics: {len(output.diagnostics)}")

    if skip_excel:
        print("Excel skipped.")
        return 0

    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Python XIC extraction and optional Excel conversion."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Project/base directory containing config/ and output/.",
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
        "--skip-excel",
        action="store_true",
        help="Write CSV outputs only and skip Excel conversion.",
    )
    parser.add_argument(
        "--excel",
        action="store_true",
        help="Run Excel conversion after writing CSV outputs; this is the default.",
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
            "reviewed targeted_ms1_shape_identity_v0 support TSV."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-activation-policy",
        choices=TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
        default=None,
        help=(
            "Override settings.csv targeted_ms1_shape_identity_activation_policy. "
            "Use limited_5hmdc_5medc_v1 to restrict support TSV activation to "
            "5-hmdC and 5-medC."
        ),
    )
    parser.add_argument(
        "--targeted-ms1-shape-identity-auto-limited-default",
        action="store_true",
        help=(
            "Run the bounded automatic NL_FAIL/NO_MS2 rescue workflow. This "
            "creates baseline/support/final artifacts, builds a RAW-backed "
            "targeted_ms1_shape_identity_v0 support TSV for 5-hmdC and 5-medC, "
            "and applies it with the limited_5hmdc_5medc_v1 policy."
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
    if args.skip_excel:
        conflicts.append("--skip-excel")
    if args.excel:
        conflicts.append("--excel")
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
    return (
        config.targeted_ms1_shape_identity_activation_policy == LIMITED_HMDC_MEDC_POLICY
        and config.targeted_ms1_shape_identity_support_tsv is None
    )


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
    approval_snapshot = resolve_model_selection_expected_diff_approvals(
        config,
        None,
    )
    auto_root = auto_output_dir or (
        base_dir
        / "output"
        / f"targeted_ms1_shape_identity_limited_auto_{_timestamp_text()}"
    )
    baseline_output_dir = auto_root / "baseline" / "output"
    support_dir = auto_root / "support"
    final_staging_output_dir = auto_root / "final_unverified" / "output"
    final_output_dir = auto_root / "final" / "output"
    _ensure_auto_publish_slot_available(
        final_staging_output_dir=final_staging_output_dir,
        final_output_dir=final_output_dir,
    )
    baseline_config = replace(
        config,
        output_csv=baseline_output_dir / "xic_results.csv",
        diagnostics_csv=baseline_output_dir / "xic_diagnostics.csv",
        keep_intermediate_csv=True,
        targeted_ms1_shape_identity_support_tsv=None,
        targeted_ms1_shape_identity_activation_policy=LIMITED_HMDC_MEDC_POLICY,
    )
    baseline_settings_overrides = {
        **settings_overrides,
        "targeted_ms1_shape_identity_activation_policy": LIMITED_HMDC_MEDC_POLICY,
    }
    print(f"Auto limited baseline output: {baseline_output_dir}")
    build_canonical_targeted_application_service(extractor.acquire).execute_application(
        build_targeted_application_request(
            baseline_config,
            targets,
            progress_callback=_print_progress,
            model_selection_expected_diff_approvals=approval_snapshot,
            method_manifest_context=MethodManifestContext(
                entrypoint="xic-extractor-cli-targeted-ms1-auto-baseline",
                argv=tuple(cli_argv),
                base_dir=base_dir,
                config_dir=config_dir,
                settings_overrides=baseline_settings_overrides,
                output_mode="csv_only",
            ),
            output_projection="acquisition_baseline",
        )
    ).active_output
    support_tsv = support_dir / "targeted_ms1_shape_identity_v0.tsv"
    support_outputs = run_build_targeted_ms1_shape_identity_supports(
        long_csv=baseline_config.output_csv.with_name("xic_results_long.csv"),
        raw_dir=baseline_config.data_dir,
        dll_dir=baseline_config.dll_dir,
        config_dir=config_dir,
        output_tsv=support_tsv,
        target_names=tuple(sorted(LIMITED_HMDC_MEDC_TARGETS)),
    )
    print(f"Auto limited support TSV: {support_outputs.evidence_tsv}")
    print(f"Auto limited support rows: {support_outputs.evidence_row_count}")
    final_staging_config = replace(
        config,
        output_csv=final_staging_output_dir / "xic_results.csv",
        diagnostics_csv=final_staging_output_dir / "xic_diagnostics.csv",
        keep_intermediate_csv=True,
        targeted_ms1_shape_identity_support_tsv=support_tsv,
        targeted_ms1_shape_identity_activation_policy=LIMITED_HMDC_MEDC_POLICY,
    )
    final_settings_overrides = {
        **settings_overrides,
        "targeted_ms1_shape_identity_support_tsv": str(support_tsv.resolve()),
        "targeted_ms1_shape_identity_activation_policy": LIMITED_HMDC_MEDC_POLICY,
    }
    print(f"Auto limited unverified final output: {final_staging_output_dir}")
    final_output = (
        build_canonical_targeted_application_service(extractor.acquire)
        .execute_application(
            build_targeted_application_request(
                final_staging_config,
                targets,
                progress_callback=_print_progress,
                model_selection_expected_diff_approvals=approval_snapshot,
                method_manifest_context=MethodManifestContext(
                    entrypoint="xic-extractor-cli-targeted-ms1-auto-limited-default",
                    argv=tuple(cli_argv),
                    base_dir=base_dir,
                    config_dir=config_dir,
                    settings_overrides=final_settings_overrides,
                    output_mode=output_mode,
                ),
                excel_output_path=(
                    None
                    if output_mode == "csv_only"
                    else _excel_output_path(final_staging_config)
                ),
            )
        )
        .active_output
    )
    diff_outputs = write_targeted_ms1_shape_identity_auto_diff_artifacts(
        baseline_output_dir=baseline_output_dir,
        optin_output_dir=final_staging_output_dir,
        support_tsv=support_tsv,
        output_dir=auto_root,
    )
    print(f"Auto limited expected diff: {diff_outputs.expected_diff_summary_tsv}")
    print(f"Auto limited matrix diff: {diff_outputs.matrix_diff_summary_tsv}")
    print(
        "Auto limited expected-diff gate: "
        f"{diff_outputs.gate_status} ({diff_outputs.expected_diff_row_count} rows, "
        f"{diff_outputs.matrix_diff_cell_count} matrix cells)"
    )
    _publish_verified_auto_output(
        final_staging_output_dir=final_staging_output_dir,
        final_output_dir=final_output_dir,
    )
    final_config = replace(
        final_staging_config,
        output_csv=final_output_dir / "xic_results.csv",
        diagnostics_csv=final_output_dir / "xic_diagnostics.csv",
    )
    print(f"Auto limited verified final output: {final_output_dir}")
    return final_output, final_config


def _ensure_auto_publish_slot_available(
    *,
    final_staging_output_dir: Path,
    final_output_dir: Path,
) -> None:
    if final_output_dir.exists():
        raise ConfigError(
            f"{final_output_dir}: verified auto-limited final output already "
            "exists; choose a new --targeted-ms1-shape-identity-auto-output-dir "
            "or remove the stale output"
        )
    if final_staging_output_dir.exists():
        raise ConfigError(
            f"{final_staging_output_dir}: unverified auto-limited final output "
            "already exists; inspect or remove it before rerunning"
        )


def _publish_verified_auto_output(
    *,
    final_staging_output_dir: Path,
    final_output_dir: Path,
) -> None:
    if not final_staging_output_dir.is_dir():
        raise ValueError(
            f"{final_staging_output_dir}: unverified final output directory missing"
        )
    if final_output_dir.exists():
        raise ValueError(f"{final_output_dir}: verified final output already exists")
    final_output_dir.parent.mkdir(parents=True, exist_ok=True)
    final_staging_output_dir.replace(final_output_dir)
    try:
        final_staging_output_dir.parent.rmdir()
    except OSError:
        pass


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
