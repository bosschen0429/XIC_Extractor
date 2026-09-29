from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from fnmatch import fnmatchcase
from importlib.util import find_spec
from pathlib import Path
from typing import Sequence

ShardPatterns = OrderedDict[str, tuple[str, ...]]

_ALIGNMENT_CORE_EXCLUDED_PATHS = frozenset(
    {
        "tests/test_discovery_method_persistence.py",
    }
)

# Register-snapshot tests are excluded from CI by MARKER, not by file. They
# assert that checked-in registers under docs/superpowers/validation/ still
# agree with the snapshot they were written against, so they pin recorded
# numbers and go red when an analysis is legitimately re-run.
#
# An earlier version of this file excluded them by moving whole test files into
# a manual-only shard. Review found the cost: those files are 212 tests, and 175
# of them are behavioural -- they mutate a copy under tmp_path and assert the
# checker rejects it. Excluding by file took all 175 out of CI too, so a
# regression that accepts caller-authored authority could merge green. The
# marker splits at the granularity the property actually has, which is per test.
#
# Which tests carry the marker was decided by measurement, not by naming: a test
# is a snapshot test when it reads a checked-in register at runtime AND does not
# redirect the checker at a tmp_path copy. Unmarked is the safe default, because
# an unmarked test still runs in CI.
#
#     uv run python -m tools.testing.test_shards --snapshots   # run only those
#
# EXIT RULE (required by the retirement contract; do not drop this block without
# resolving it): revisit at the next release cut, or as soon as any of these
# holds, and then either delete the register plus its test or promote it back
# into CI --
#   1. a register here has not been consulted in a decision for two releases;
#   2. its test has gone red only because recorded numbers moved, twice;
#   3. the register's owning milestone is retired.
SNAPSHOT_MARKER = "ledger_snapshot"

# Markers whose tests are expensive and guard a narrow set of files. A change
# that touches none of a marker's paths skips that marker's tests. CI
# (.github/workflows/ci.yml) and local shard runs both read this table, so the
# two select the same tests for the same change.
RELEVANCE_RULES: dict[str, re.Pattern[str]] = {
    "packaging_build": re.compile(
        r"^(pyproject\.toml|uv\.lock|xic_extractor\.spec|tests/test_pyinstaller_spec\.py"
        r"|assets/|config/(settings|targets)\.example\.csv|gui/main\.py"
        r"|scripts/(csv_to_excel|xlsx_to_targets)\.py|xic_extractor/presets/data/"
        r"|xic_extractor/alignment/(__init__\.py|quantitative_projection_authority\.py"
        r"|data/quantitative_projection_authority\.json))"
    ),
    "agent_hooks": re.compile(
        r"^(\.codex/(hooks/|hooks\.json$|rules/)|tests/test_config_codex_hooks\.py$"
        r"|pyproject\.toml$)"
    ),
    "product_governance": re.compile(
        r"^(scripts/check_productization_state\.py|tests/test_productization_state_index\.py"
        r"|docs/product/|docs/superpowers/(validation|schemas|productization)/"
        r"|docs/superpowers/plans/2026-06-15-productization-control-plane\.md"
        r"|tools/diagnostics/docs_(policy|scan)\.py"
        r"|xic_extractor/alignment/(production_acceptance|quant_matrix_version)\.py)"
    ),
}
# The main CI pytest session: snapshots never run, the census runs once in the
# lint job, and serial tests run after the parallel session.
CI_BASE_MARKER_EXPRESSION = (
    f"not {SNAPSHOT_MARKER} and not current_repository_census and not serial_ci"
)

# Every test carrying that marker, locked. The marker takes a test out of CI, so
# it needs the same ratchet the shard budgets have: adding one is a decision,
# and an unrecorded marker fails test_test_shards.py. Without this the exclusion
# grows silently, which is exactly how a batch of behavioural tests left CI the
# first time -- the file-level exclusion had no gate either.
#
# Add an entry only for a test that pins a recorded value from
# docs/superpowers/validation/. A rejection path, a monkeypatched dependency, or
# an invariant that survives a consistent re-analysis belongs in CI unmarked.
SNAPSHOT_TESTS: dict[str, tuple[str, ...]] = {
    "tests/test_bounded_product_lanes_contract.py": (
        "test_bounded_product_lanes_accept_current_artifacts",
        "test_limited_targeted_ms1_scope_and_effect_are_pinned",
    ),
    "tests/test_check_productization_authority.py": (
        "test_productization_authority_checker_accepts_repo_artifacts",
    ),
    "tests/test_cid_nl_default_activation_universe_closure.py": (
        "test_current_cid_nl_full_cohort_universe_closure_is_stable",
    ),
    "tests/test_cid_nl_discovery_full_scope_classification.py": (
        "test_current_cid_nl_discovery_full_scope_classification_is_stable",
    ),
    "tests/test_cid_nl_discovery_release_slice.py": (
        "test_current_cid_nl_discovery_release_slice_is_stable",
    ),
    "tests/test_productization_state_index.py": (
        "test_dependent_effect_lane_is_exact_hold_package_binding",
        "test_only_revalidated_default_scope_has_writer_authority",
        "test_productization_state_index_accepts_current_artifacts",
    ),
    "tests/test_validation_artifact_cid_nl_public_claim_catalog.py": (
        "test_current_cid_nl_public_output_claim_catalog_is_valid",
        "test_reference_rows_preserve_existing_owner_boundaries",
    ),
}

# Shards are matched in order; the first match wins.
SHARD_PATTERNS: ShardPatterns = OrderedDict(
    {
        "docs-config": (
            "tests/test_agent_sandbox_doctor.py",
            "tests/test_bootstrap_worktree.py",
            "tests/test_bounded_product_lanes_contract.py",
            "tests/test_check_productization_authority.py",
            "tests/test_config*.py",
            "tests/test_docs_*.py",
            "tests/test_multiprocessing_entrypoints.py",
            "tests/test_peak_model_selection.py",
            "tests/test_productization_*.py",
            "tests/test_pyinstaller_spec.py",
            "tests/test_presets_*.py",
            "tests/test_pt_pattern_presets.py",
            "tests/test_test_shards.py",
            "tests/test_validation_artifact_*.py",
        ),
        "gui": (
            "tests/test_discovery_worker.py",
            "tests/test_discovery_method_section.py",
            "tests/test_discovery_method_persistence.py",
            "tests/test_discovery_samples_section.py",
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
            "tests/test_acquisition_contract.py",
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
            "tests/test_dda_cid_nl_peak_support_refs.py",
            "tests/test_delivery_lineage.py",
            "tests/test_excel_*.py",
            "tests/test_extraction_*.py",
            "tests/test_execution_engine.py",
            "tests/test_execution_settings.py",
            "tests/test_execution_runtime_events.py",
            "tests/test_execution_runtime_finalization.py",
            "tests/test_execution_runtime_marker.py",
            "tests/test_execution_runtime_observability.py",
            "tests/test_execution_runtime_owner.py",
            "tests/test_execution_runtime_receipts.py",
            "tests/test_extractor*.py",
            "tests/test_gaussian15_area_pressure_audit.py",
            "tests/test_handoff_spine_runtime.py",
            "tests/test_injection_rolling.py",
            "tests/test_interval_selection.py",
            "tests/test_istd_*.py",
            "tests/test_targeted_manual_validation.py",
            "tests/test_mass_accuracy.py",
            "tests/test_neutral_loss.py",
            "tests/test_output_*.py",
            "tests/test_peak_*.py",
            "tests/test_processing_identity.py",
            "tests/test_processing_owner_references.py",
            "tests/test_raw_reader*.py",
            "tests/test_result_assembly.py",
            "tests/test_review_*.py",
            "tests/test_rt_*.py",
            "tests/test_run_extraction.py",
            "tests/test_scoring_*.py",
            "tests/test_search_scope_capture.py",
            "tests/test_shared_capture_route_census.py",
            "tests/test_signal_processing*.py",
            "tests/test_target_*.py",
            "tests/test_targeted_service_contract.py",
            "tests/test_validation_primitives.py",
            "tests/test_width.py",
            "tests/test_workbook_*.py",
            "tests/test_xic_models.py",
        ),
        "alignment-core": (
            "tests/test_feature_membership_inference.py",
            "tests/test_window_feature_membership.py",
            "tests/alignment/**/*.py",
            "tests/test_alignment_*.py",
            "tests/test_analyze_xic_request_locality.py",
            "tests/test_blank_qualification.py",
            "tests/test_build_local_ms1_feature_table_cli.py",
            "tests/test_cell_observations.py",
            "tests/test_cross_sample_*.py",
            "tests/test_delta_mass_*.py",
            "tests/test_detected_feature_sources.py",
            "tests/test_discovery_*.py",
            "tests/test_finalized_application_result.py",
            "tests/test_finalized_scientific_alignment.py",
            "tests/test_local_ms1_features.py",
            "tests/test_method_composition_extensibility.py",
            "tests/test_mode_track_closed_writer_validation_runner.py",
            "tests/test_matrix_identity_*.py",
            "tests/test_mode_track_*.py",
            "tests/test_ms1_index_backfill_audit.py",
            "tests/test_ms1_scan_index.py",
            "tests/test_overlay_trace_data.py",
            "tests/test_owner_build_route_receipt.py",
            "tests/test_owner_backfill_request_economics.py",
            "tests/test_owner_backfill_request_equivalence.py",
            "tests/test_owner_backfill_peak_fact_provenance.py",
            "tests/test_pt_pattern_*.py",
            "tests/test_pt_pattern_boundaries.py",
            "tests/test_pt_pattern_scan_proxy.py",
            "tests/test_prepare_delta_mass_detected_features.py",
            "tests/test_physical_fact_contract.py",
            "tests/test_physical_peak_evaluation_persistence.py",
            "tests/test_processing_run_spec.py",
            "tests/test_processing_run_spec_architecture.py",
            "tests/test_processing_run_spec_entrypoints.py",
            "tests/test_process_job_execution.py",
            "tests/test_quantitative_projection_authority.py",
            "tests/test_raw_superwindow_source.py",
            "tests/test_run_alignment.py",
            "tests/test_run_discovery.py",
            "tests/test_run_discovery_run_spec_cli.py",
            "tests/test_run_pt_pattern_discovery.py",
            "tests/test_run_tissue_validation.py",
            "tests/test_run_untargeted_cid_nl_mode_track.py",
            "tests/test_run_untargeted_cid_nl_real_data.py",
            "tests/test_run_untargeted_provisional_matrix.py",
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
            "tests/test_shadow_production_projection.py",
            "tests/test_shift_aware_*.py",
            "tests/test_standard_peak_*.py",
            "tests/test_tier2_*.py",
            "tests/test_xic_inspector_*.py",
        ),
        "product-gates-evidence": (
            "tests/test_area_integration_uncertainty_audit.py",
            "tests/test_evidence_*.py",
            "tests/test_matrix_*.py",
            "tests/test_model_selection_approval_registry.py",
            "tests/test_ms1_*.py",
            "tests/test_ms2_trace_evidence.py",
            "tests/test_targeted_*.py",
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
            "tests/test_artifact_paths.py",
            "tests/test_benchmark_parallel.py",
            "tests/test_build_targeted_ms1_shape_identity_supports.py",
            "tests/test_compare_alignment_workbooks.py",
            "tests/test_cross_report_evidence_consistency.py",
            "tests/test_diagnostic_*.py",
            "tests/test_instrument_qc_*.py",
            "tests/test_low_ms1_assessable_coverage_audit.py",
            "tests/test_multi_tag_adduct_audit.py",
            "tests/test_parallel*.py",
            "tests/test_run_instrument_qc.py",
            "tests/test_sample_metadata*.py",
            "tests/test_feature_quality.py",
            "tests/test_analysis_set.py",
            "tests/test_injection_timeline.py",
            "tests/test_sample_rt_calibration.py",
            "tests/test_schema_*.py",
            "tests/test_shape.py",
            "tests/test_timing.py",
            "tests/test_tissue_regression.py",
            "tests/test_validate_*.py",
            "tests/test_validation_harness.py",
        ),
    }
)


def discover_snapshot_tests(
    root: Path = Path("tests"),
) -> dict[str, tuple[str, ...]]:
    """Return every test carrying SNAPSHOT_MARKER, read from the source.

    Parsed rather than collected so the ratchet costs nothing and cannot be
    skewed by which shard happens to be running.
    """
    found: dict[str, tuple[str, ...]] = {}
    for path in discover_test_files(root):
        source = path.read_text(encoding="utf-8", errors="replace")
        if SNAPSHOT_MARKER not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        names: list[str] = []
        supported_references: set[ast.Attribute] = set()
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            references = tuple(
                reference
                for decorator in node.decorator_list
                if (
                    reference := _direct_snapshot_marker_reference(decorator)
                )
                is not None
            )
            if references:
                names.append(node.name)
                supported_references.update(references)

        unsupported = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == SNAPSHOT_MARKER
            and node not in supported_references
        )
        if unsupported:
            lines = ", ".join(str(node.lineno) for node in unsupported)
            raise ValueError(
                f"{path.as_posix()}: {SNAPSHOT_MARKER} marker at line(s) "
                f"{lines} must decorate a top-level synchronous test function"
            )
        if names:
            found[path.as_posix()] = tuple(sorted(names))
    return found


def _direct_snapshot_marker_reference(
    decorator: ast.expr,
) -> ast.Attribute | None:
    """Return a direct marker decorator, excluding marker-like arguments."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute) and target.attr == SNAPSHOT_MARKER:
        return target
    return None


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
    for shard in SHARD_PATTERNS:
        if _shard_matches_path(shard, path):
            return shard
    return None


def _shard_matches_path(shard: str, path: Path | str) -> bool:
    posix = Path(path).as_posix()
    if shard == "alignment-core" and posix in _ALIGNMENT_CORE_EXCLUDED_PATHS:
        return False
    return any(fnmatchcase(posix, pattern) for pattern in SHARD_PATTERNS[shard])


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


def irrelevant_markers(changed_paths: Sequence[str]) -> tuple[str, ...]:
    """Return the relevance-gated markers that no changed path touches."""
    return tuple(
        marker
        for marker, pattern in RELEVANCE_RULES.items()
        if not any(pattern.match(path) for path in changed_paths)
    )


def changed_paths(base: str, head: str | None = None) -> list[str] | None:
    """Paths changed from ``base``; with no ``head``, include uncommitted files.

    Returns None when git cannot answer, so callers fall back to running every
    marker instead of silently skipping tests.
    """
    diff = ["git", "diff", "--name-only", base] + ([head] if head else [])
    commands = [diff]
    if head is None:
        commands.append(["git", "ls-files", "--others", "--exclude-standard"])
    paths: list[str] = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        paths.extend(line for line in result.stdout.splitlines() if line)
    return paths


def _local_change_base() -> str | None:
    result = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/master"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _relevance_exclusion(all_markers: bool) -> str | None:
    """Marker expression that skips irrelevant gated tests for this checkout."""
    if all_markers:
        return None
    base = _local_change_base()
    paths = changed_paths(base) if base else None
    if paths is None:
        print("Could not diff against origin/master; running every marker", flush=True)
        return None
    skipped = irrelevant_markers(paths)
    if not skipped:
        return None
    print(
        "Skipping tests unrelated to this change: "
        + ", ".join(skipped)
        + " (use --all-markers to run them)",
        flush=True,
    )
    return " and ".join(f"not {marker}" for marker in skipped)


def ci_marker_expression(base: str, head: str) -> str | None:
    paths = changed_paths(base, head)
    if paths is None:
        return None
    exclusions = [f"not {marker}" for marker in irrelevant_markers(paths)]
    return " and ".join([CI_BASE_MARKER_EXPRESSION, *exclusions])


def _run_shard(
    shard: str, pytest_args: Sequence[str], root: Path, *, all_markers: bool = False
) -> int:
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
    required = f"not {SNAPSHOT_MARKER}"
    exclusion = _relevance_exclusion(all_markers)
    if exclusion:
        required = f"{required} and {exclusion}"
    owned_pytest_args = _intersect_marker_selection(
        pytest_args,
        required=required,
    )
    if owned_pytest_args is None:
        print("Invalid pytest marker selection", file=sys.stderr)
        return 2
    print(f"Running shard {shard}: {len(target_args)} test files", flush=True)
    return subprocess.call(
        [*_pytest_command(), *owned_pytest_args, *target_args]
    )


def _run_snapshots(pytest_args: Sequence[str], root: Path) -> int:
    """Run only the register-snapshot tests, the ones CI deliberately skips."""
    targets = [path.as_posix() for path in discover_test_files(root)]
    if not targets:
        print("No test files discovered", file=sys.stderr)
        return 2
    owned_pytest_args = _intersect_marker_selection(
        pytest_args,
        required=SNAPSHOT_MARKER,
    )
    if owned_pytest_args is None:
        print("Invalid pytest marker selection", file=sys.stderr)
        return 2
    print(f"Running {SNAPSHOT_MARKER} tests across {len(targets)} files", flush=True)
    return subprocess.call([*_pytest_command(), *owned_pytest_args, *targets])


def _intersect_marker_selection(
    pytest_args: Sequence[str],
    *,
    required: str,
) -> tuple[str, ...] | None:
    """Keep the runner-owned marker boundary when callers add ``-m`` filters."""
    remaining: list[str] = []
    caller_expressions: list[str] = []
    index = 0
    while index < len(pytest_args):
        argument = pytest_args[index]
        if argument == "-m":
            if index + 1 >= len(pytest_args):
                return None
            separated_expression = pytest_args[index + 1]
            if not separated_expression or separated_expression.startswith("-"):
                return None
            caller_expressions.append(separated_expression)
            index += 2
            continue
        if argument.startswith("-m") and argument != "-m":
            compact_expression = argument[2:].removeprefix("=")
            if not compact_expression:
                return None
            caller_expressions.append(compact_expression)
            index += 1
            continue
        remaining.append(argument)
        index += 1

    expression = required
    if caller_expressions:
        expression = " and ".join(
            (*(f"({item})" for item in caller_expressions), f"({required})")
        )
    return ("-m", expression, *remaining)


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
    parser.add_argument(
        "--snapshots",
        action="store_true",
        help=f"Run only the {SNAPSHOT_MARKER} tests, which the shards exclude",
    )
    parser.add_argument(
        "--all-markers",
        action="store_true",
        help="Run relevance-gated tests even when this change does not touch them",
    )
    parser.add_argument(
        "--marker-expression",
        action="store_true",
        help="Print the CI pytest marker expression for --base..--head and exit",
    )
    parser.add_argument("--base", help="Base revision for --marker-expression")
    parser.add_argument("--head", help="Head revision for --marker-expression")
    args, rest = parser.parse_known_args(argv)
    pytest_args = rest[1:] if rest[:1] == ["--"] else rest
    root = Path(args.root)
    if args.marker_expression:
        if not (args.base and args.head):
            parser.error("--marker-expression requires --base and --head")
        expression = ci_marker_expression(args.base, args.head)
        if expression is None:
            print("git diff failed", file=sys.stderr)
            return 2
        print(expression)
        return 0
    if args.list:
        return _print_summary(root)
    if args.check:
        return _check(root)
    if args.snapshots:
        return _run_snapshots(pytest_args, root)
    if args.shard is None:
        parser.error("a shard name is required unless --list or --check is used")
    return _run_shard(args.shard, pytest_args, root, all_markers=args.all_markers)


if __name__ == "__main__":
    raise SystemExit(main())
