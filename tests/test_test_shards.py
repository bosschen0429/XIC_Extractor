from pathlib import Path

from tools.testing import test_shards
from tools.testing.test_shards import (
    SHARD_PATTERNS,
    assign_shards,
    check_coverage,
    discover_test_files,
    shard_for_path,
)

_ALLOWED_SHARD_OVERLAPS = {
    Path("tests/test_config_hash.py"): ("docs-config", "targeted-core"),
    Path("tests/test_config_io.py"): ("docs-config", "targeted-core"),
    Path("tests/test_discovery_method_section.py"): ("gui", "alignment-core"),
    Path("tests/test_discovery_worker.py"): ("gui", "alignment-core"),
    Path("tests/test_matrix_identity_blast_radius.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_matrix_identity_projection.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_ms1_index_backfill_audit.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_ms1_scan_index.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_peak_model_selection.py"): ("docs-config", "targeted-core"),
    Path("tests/test_pt_pattern_presets.py"): ("docs-config", "alignment-core"),
    Path("tests/test_target_pair_expected_diff_approval_registry_tool.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_target_pair_rt_auto_reselection.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_target_pair_rt_calibration.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_target_pair_rt_candidate_plot_review.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_targeted_service_contract.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_untargeted_view.py"): ("gui", "alignment-core"),
    Path("tests/test_validate_ms1_scan_index_xic.py"): (
        "alignment-core",
        "diagnostics-tools",
    ),
}


def test_test_shards_cover_every_test_file() -> None:
    assert check_coverage() == ()


def test_test_shards_do_not_run_empty_primary_shards() -> None:
    assigned = assign_shards(discover_test_files())

    for shard in SHARD_PATTERNS:
        assert assigned[shard], shard


def test_test_shards_keep_changed_shard_contracts() -> None:
    assigned = assign_shards(
        (
            Path("tests/test_discovery_worker.py"),
            Path("tests/test_discovery_method_section.py"),
            Path("tests/test_discovery_method_persistence.py"),
            Path("tests/test_pyinstaller_spec.py"),
            Path("tests/test_peak_model_selection.py"),
            Path("tests/test_alignment_pipeline_outputs.py"),
            Path("tests/test_processing_run_spec.py"),
            Path("tests/test_processing_run_spec_architecture.py"),
            Path("tests/test_processing_run_spec_entrypoints.py"),
            Path("tests/test_physical_peak_evaluation_persistence.py"),
            Path("tests/test_run_discovery_run_spec_cli.py"),
            Path("tests/test_process_job_execution.py"),
            Path("tests/test_standard_peak_backfill_preset.py"),
            Path("tests/test_targeted_ms1_shape_identity.py"),
        )
    )

    assert assigned["gui"] == (
        Path("tests/test_discovery_worker.py"),
        Path("tests/test_discovery_method_section.py"),
        Path("tests/test_discovery_method_persistence.py"),
    )
    assert assigned["docs-config"] == (
        Path("tests/test_pyinstaller_spec.py"),
        Path("tests/test_peak_model_selection.py"),
    )
    assert assigned["alignment-core"] == (
        Path("tests/test_alignment_pipeline_outputs.py"),
        Path("tests/test_processing_run_spec.py"),
        Path("tests/test_processing_run_spec_architecture.py"),
        Path("tests/test_processing_run_spec_entrypoints.py"),
        Path("tests/test_physical_peak_evaluation_persistence.py"),
        Path("tests/test_run_discovery_run_spec_cli.py"),
        Path("tests/test_process_job_execution.py"),
    )
    exact_alignment_paths = (
        Path("tests/test_processing_run_spec.py"),
        Path("tests/test_processing_run_spec_architecture.py"),
        Path("tests/test_processing_run_spec_entrypoints.py"),
        Path("tests/test_physical_peak_evaluation_persistence.py"),
        Path("tests/test_run_discovery_run_spec_cli.py"),
    )
    for path in exact_alignment_paths:
        assert _matching_shards(path) == ("alignment-core",)
        assert shard_for_path(path) == "alignment-core"
    discovery_method_persistence_path = Path(
        "tests/test_discovery_method_persistence.py"
    )
    assert _matching_shards(discovery_method_persistence_path) == ("gui",)
    assert shard_for_path(discovery_method_persistence_path) == "gui"
    assert assigned["product-gates-activation"] == (
        Path("tests/test_standard_peak_backfill_preset.py"),
    )
    assert assigned["product-gates-evidence"] == (
        Path("tests/test_targeted_ms1_shape_identity.py"),
    )


def test_test_shards_lock_allowed_overlaps_and_first_match_owner() -> None:
    observed = {
        path: matches
        for path in discover_test_files()
        if len(matches := _matching_shards(path)) > 1
    }

    assert observed == _ALLOWED_SHARD_OVERLAPS
    for path, matches in observed.items():
        assert shard_for_path(path) == matches[0]


def test_test_shards_prefer_uv_runner_when_pytest_is_not_importable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(test_shards, "find_spec", lambda name: None)
    monkeypatch.setattr(
        test_shards.shutil,
        "which",
        lambda name: "C:/tools/uv.exe" if name == "uv" else "C:/global/pytest.exe",
    )

    assert test_shards._pytest_command() == ["C:/tools/uv.exe", "run", "pytest"]


def _matching_shards(path: Path) -> tuple[str, ...]:
    return tuple(
        shard
        for shard in SHARD_PATTERNS
        if test_shards._shard_matches_path(shard, path)
    )
