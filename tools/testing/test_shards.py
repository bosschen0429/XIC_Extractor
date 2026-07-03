from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import OrderedDict
from fnmatch import fnmatchcase
from importlib.util import find_spec
from pathlib import Path
from typing import Sequence

ShardPatterns = OrderedDict[str, tuple[str, ...]]

SHARD_PATTERNS: ShardPatterns = OrderedDict(
    {
        "docs-config": (
            "tests/test_agent_sandbox_doctor.py",
            "tests/test_bounded_product_lanes_contract.py",
            "tests/test_check_productization_authority.py",
            "tests/test_ci_workflow.py",
            "tests/test_config*.py",
            "tests/test_docs_*.py",
            "tests/test_handoff_*.py",
            "tests/test_multiprocessing_entrypoints.py",
            "tests/test_peak_model_selection.py",
            "tests/test_productization_*.py",
            "tests/test_pyinstaller_spec.py",
            "tests/test_presets_*.py",
            "tests/test_pt_pattern_presets.py",
            "tests/test_retire_docs.py",
            "tests/test_score_retirement_legacy_authority.py",
            "tests/test_test_shards.py",
            "tests/test_validation_artifact_*.py",
        ),
        "gui": (
            "tests/test_discovery_worker.py",
            "tests/test_discovery_method_section.py",
            "tests/test_gui_*.py",
            "tests/test_main_window_layout.py",
            "tests/test_messages.py",
            "tests/test_method_manifest.py",
            "tests/test_pipeline_worker.py",
            "tests/test_results_section.py",
            "tests/test_settings_*.py",
            "tests/test_targets_section.py",
            "tests/test_untargeted_view.py",
            "tests/test_wheel_guard.py",
            "tests/test_xlsx_to_targets.py",
        ),
        "targeted-core": (
            "tests/test_add_istd_rt_trend.py",
            "tests/test_baseline*.py",
            "tests/test_boundary_*.py",
            "tests/test_candidate_*.py",
            "tests/test_chrom_*.py",
            "tests/test_compare_resolvers.py",
            "tests/test_config_hash.py",
            "tests/test_config_io.py",
            "tests/test_csv_*.py",
            "tests/test_csv_to_excel.py",
            "tests/test_cwt_*.py",
            "tests/test_excel_*.py",
            "tests/test_extraction_*.py",
            "tests/test_extractor*.py",
            "tests/test_gaussian15_area_pressure_audit.py",
            "tests/test_injection_rolling.py",
            "tests/test_interval_selection.py",
            "tests/test_istd_*.py",
            "tests/test_local_minimum_param_sweep.py",
            "tests/test_neutral_loss.py",
            "tests/test_output_*.py",
            "tests/test_peak_*.py",
            "tests/test_raw_reader.py",
            "tests/test_result_assembly.py",
            "tests/test_review_*.py",
            "tests/test_rt_*.py",
            "tests/test_run_extraction.py",
            "tests/test_scoring_*.py",
            "tests/test_signal_processing*.py",
            "tests/test_subthreshold_sensitivity_report.py",
            "tests/test_target_*.py",
            "tests/test_width.py",
            "tests/test_workbook_*.py",
            "tests/test_xic_models.py",
        ),
        "alignment-core": (
            "tests/alignment/**/*.py",
            "tests/test_alignment_*.py",
            "tests/test_analyze_xic_request_locality.py",
            "tests/test_build_local_ms1_feature_table_cli.py",
            "tests/test_delta_mass_*.py",
            "tests/test_detected_feature_sources.py",
            "tests/test_discovery_*.py",
            "tests/test_family_ms1_alignment_experiment*.py",
            "tests/test_local_ms1_features.py",
            "tests/test_family_ms1_backfill_review_report.py",
            "tests/test_family_ms1_overlay*.py",
            "tests/test_matrix_identity_*.py",
            "tests/test_ms1_index_backfill_audit.py",
            "tests/test_ms1_scan_index.py",
            "tests/test_overlay_trace_data.py",
            "tests/test_owner_backfill_request_economics.py",
            "tests/test_pre_backfill_consolidation.py",
            "tests/test_pt_pattern_*.py",
            "tests/test_pt_pattern_boundaries.py",
            "tests/test_pt_pattern_scan_proxy.py",
            "tests/test_prepare_delta_mass_detected_features.py",
            "tests/test_run_alignment.py",
            "tests/test_run_alignment_validation.py",
            "tests/test_run_discovery.py",
            "tests/test_run_pt_pattern_discovery.py",
            "tests/test_scan_retention_times.py",
            "tests/test_shared_peak_identity_*.py",
            "tests/test_untargeted_*.py",
            "tests/test_validate_ms1_scan_index_xic.py",
        ),
        "product-gates-activation": (
            "tests/test_backfill_*.py",
            "tests/test_cid_*.py",
            "tests/test_dna_*.py",
            "tests/test_product_ready_*.py",
            "tests/test_provisional_*.py",
            "tests/test_quant_*.py",
            "tests/test_retained_*.py",
            "tests/test_run_backfill_expansion_full_evidence_chain.py",
            "tests/test_seed_aware_backfill_review.py",
            "tests/test_shadow_production_projection.py",
            "tests/test_shift_aware_*.py",
            "tests/test_standard_peak_*.py",
            "tests/test_tier2_*.py",
            "tests/test_trace_overlay_recovery_contract.py",
        ),
        "product-gates-evidence": (
            "tests/test_area_integration_uncertainty_audit.py",
            "tests/test_evidence_*.py",
            "tests/test_lockbox_*.py",
            "tests/test_matrix_*.py",
            "tests/test_model_selection_approval_registry.py",
            "tests/test_ms1_*.py",
            "tests/test_ms2_trace_evidence.py",
            "tests/test_p1_*.py",
            "tests/test_p7_*.py",
            "tests/test_paired_*.py",
            "tests/test_production_*.py",
            "tests/test_region_*.py",
            "tests/test_row_*.py",
            "tests/test_selected_*.py",
            "tests/test_single_dr_production_gate_decision_report.py",
            "tests/test_target_pair_*.py",
            "tests/test_targeted_*.py",
        ),
        "diagnostics-tools": (
            "tests/test_auto_accept_policy.py",
            "tests/test_benchmark_parallel.py",
            "tests/test_build_targeted_ms1_shape_identity_supports.py",
            "tests/test_changed_row_mode_overlay_review.py",
            "tests/test_compare_alignment_workbooks.py",
            "tests/test_cross_report_evidence_consistency.py",
            "tests/test_diagnostic_*.py",
            "tests/test_generate_stubs.py",
            "tests/test_instrument_qc_*.py",
            "tests/test_low_ms1_assessable_coverage_audit.py",
            "tests/test_multi_tag_adduct_audit.py",
            "tests/test_parallel*.py",
            "tests/test_run_instrument_qc.py",
            "tests/test_sample_metadata.py",
            "tests/test_schema_*.py",
            "tests/test_shape.py",
            "tests/test_timing.py",
            "tests/test_tissue_regression.py",
            "tests/test_validate_*.py",
            "tests/test_validation_harness.py",
        ),
    }
)


def discover_test_files(root: Path = Path("tests")) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("test_*.py")
                if "__pycache__" not in path.parts
            ),
            key=lambda path: path.as_posix(),
        )
    )


def assign_shards(files: Sequence[Path]) -> dict[str, tuple[Path, ...]]:
    assigned: dict[str, list[Path]] = {name: [] for name in SHARD_PATTERNS}
    leftovers: list[Path] = []
    for path in files:
        shard = shard_for_path(path)
        if shard is None:
            leftovers.append(path)
        else:
            assigned[shard].append(path)
    if leftovers:
        assigned["misc"] = leftovers
    return {name: tuple(paths) for name, paths in assigned.items()}


def shard_for_path(path: Path | str) -> str | None:
    posix = Path(path).as_posix()
    for shard, patterns in SHARD_PATTERNS.items():
        if any(fnmatchcase(posix, pattern) for pattern in patterns):
            return shard
    return None


def shard_names(*, include_misc: bool = True) -> tuple[str, ...]:
    names = tuple(SHARD_PATTERNS)
    if not include_misc:
        return names
    assigned = assign_shards(discover_test_files())
    return tuple(name for name in (*names, "misc") if assigned.get(name))


def check_coverage(root: Path = Path("tests")) -> tuple[Path, ...]:
    files = discover_test_files(root)
    return tuple(path for path in files if shard_for_path(path) is None)


def _print_summary(root: Path) -> int:
    assigned = assign_shards(discover_test_files(root))
    total = 0
    for shard, files in assigned.items():
        total += len(files)
        print(f"{shard}: {len(files)} files")
    print(f"total: {total} files")
    return 0


def _check(root: Path) -> int:
    unassigned = check_coverage(root)
    if unassigned:
        print("Unassigned test files:", file=sys.stderr)
        for path in unassigned:
            print(f"- {path.as_posix()}", file=sys.stderr)
        return 1
    _print_summary(root)
    return 0


def _run_shard(shard: str, pytest_args: Sequence[str], root: Path) -> int:
    assigned = assign_shards(discover_test_files(root))
    if shard not in assigned:
        available = ", ".join(sorted(assigned))
        print(
            f"Unknown shard {shard!r}; available shards: {available}",
            file=sys.stderr,
        )
        return 2
    targets = assigned[shard]
    if not targets:
        print(f"Shard {shard!r} has no test files", file=sys.stderr)
        return 2
    target_args = [path.as_posix() for path in targets]
    print(f"Running shard {shard}: {len(target_args)} test files", flush=True)
    return subprocess.call([*_pytest_command(), *pytest_args, *target_args])


def _pytest_command() -> list[str]:
    if find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    uv_exe = shutil.which("uv")
    if uv_exe:
        return [uv_exe, "run", "pytest"]
    pytest_exe = shutil.which("pytest")
    if pytest_exe:
        return [pytest_exe]
    return [sys.executable, "-m", "pytest"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", nargs="?", help="Shard name to run")
    parser.add_argument("--root", default="tests", help="Test root directory")
    parser.add_argument("--list", action="store_true", help="List shard file counts")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if any test file is not assigned to a shard",
    )
    args, rest = parser.parse_known_args(argv)
    pytest_args = rest[1:] if rest[:1] == ["--"] else rest
    root = Path(args.root)
    if args.list:
        return _print_summary(root)
    if args.check:
        return _check(root)
    if args.shard is None:
        parser.error("a shard name is required unless --list or --check is used")
    return _run_shard(args.shard, pytest_args, root)


if __name__ == "__main__":
    raise SystemExit(main())
