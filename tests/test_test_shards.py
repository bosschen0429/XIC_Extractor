from pathlib import Path

import pytest
from pre_commit.clientlib import load_config

from tools.testing import test_shards
from tools.testing.test_shards import (
    SHARD_PATTERNS,
    SNAPSHOT_TESTS,
    assign_shards,
    check_coverage,
    discover_snapshot_tests,
    discover_test_files,
    shard_for_path,
)

# Files whose path matches more than one shard glob. First match wins, so the
# left-hand shard is the owner and the file runs exactly once. Locking the set
# here means a new overlap has to be an explicit decision.
_ALLOWED_SHARD_OVERLAPS: dict[Path, tuple[str, ...]] = {
    Path("tests/test_config_hash.py"): ("docs-config", "targeted-core"),
    Path("tests/test_config_io.py"): ("docs-config", "targeted-core"),
    Path("tests/test_discovery_method_section.py"): ("gui", "alignment-core"),
    Path("tests/test_discovery_samples_section.py"): ("gui", "alignment-core"),
    Path("tests/test_discovery_worker.py"): ("gui", "alignment-core"),
    Path("tests/test_matrix_identity_blast_radius.py"): (
        "alignment-core",
        "product-gates-evidence",
    ),
    Path("tests/test_ms1_scan_index.py"): ("alignment-core", "product-gates-evidence"),
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
    Path("tests/test_targeted_manual_validation.py"): (
        "targeted-core",
        "product-gates-evidence",
    ),
    Path("tests/test_targeted_peak_selection.py"): (
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


def test_test_shard_coverage_hook_routes_staged_tests_to_owner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(".pre-commit-config.yaml")
    hooks = {
        hook["id"]: hook
        for repository in config["repos"]
        for hook in repository["hooks"]
    }

    hook = hooks["test-shard-coverage"]
    assert hook["entry"] == (
        "uv run --cache-dir .uv-cache python -m "
        "tools.testing.test_shards --check"
    )
    assert hook["files"] == r"^(tests/.*\.py|tools/testing/test_shards\.py)$"
    assert hook["pass_filenames"] is False

    monkeypatch.chdir(tmp_path)
    test_root = Path("tests")
    test_root.mkdir()
    assigned = test_root / "test_finalized_application_result.py"
    assigned.write_text("def test_assigned(): pass\n", encoding="utf-8")

    assert test_shards.main(["--check"]) == 0
    capsys.readouterr()

    probe = test_root / "test_unassigned_shard_probe.py"
    probe.write_text("def test_probe(): pass\n", encoding="utf-8")

    assert test_shards.main(["--check"]) == 1
    assert probe.as_posix() in capsys.readouterr().err


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
            Path("tests/test_standard_peak_t5d_contraction.py"),
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
        Path("tests/test_standard_peak_t5d_contraction.py"),
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


def test_snapshot_marker_cannot_grow_without_a_recorded_decision() -> None:
    # The marker removes a test from CI, so it gets the ratchet the shard
    # budgets have. Marking a test without recording it here fails, which is
    # the gate the file-level exclusion never had.
    assert discover_snapshot_tests() == SNAPSHOT_TESTS


def test_recorded_snapshot_tests_still_exist() -> None:
    # A registry entry for a renamed or deleted test would silently stop
    # protecting anything.
    discovered = discover_snapshot_tests()
    for path, names in SNAPSHOT_TESTS.items():
        assert path in discovered, path
        for name in names:
            assert name in discovered[path], f"{path}::{name}"


@pytest.mark.parametrize(
    "source",
    (
        "@pytest.mark.ledger_snapshot\nasync def test_async_snapshot():\n    pass\n",
        (
            "class TestSnapshots:\n"
            "    @pytest.mark.ledger_snapshot\n"
            "    def test_method_snapshot(self):\n"
            "        pass\n"
        ),
        (
            "@pytest.mark.ledger_snapshot\n"
            "class TestSnapshotClass:\n"
            "    def test_member(self):\n"
            "        pass\n"
        ),
        (
            "pytestmark = pytest.mark.ledger_snapshot\n\n"
            "def test_module_snapshot():\n"
            "    pass\n"
        ),
    ),
    ids=("async", "method", "class", "module"),
)
def test_snapshot_inventory_rejects_unsupported_marker_placements(
    tmp_path: Path,
    source: str,
) -> None:
    root = tmp_path / "tests"
    root.mkdir()
    (root / "test_marker_shape.py").write_text(
        "import pytest\n\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="ledger_snapshot.*top-level synchronous test function",
    ):
        discover_snapshot_tests(root)


def test_snapshot_runner_intersects_caller_marker_with_snapshot_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    monkeypatch.setattr(
        test_shards,
        "_pytest_command",
        lambda: ["python", "-m", "pytest"],
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda command: commands.append(command) or 0,
    )

    assert test_shards._run_snapshots(("-m", "slow"), Path("tests")) == 0

    assert len(commands) == 1
    command = commands[0]
    pytest_args = command[3:]
    assert pytest_args.count("-m") == 1
    marker_index = pytest_args.index("-m")
    assert pytest_args[marker_index + 1] == "(slow) and (ledger_snapshot)"


def test_ci_shard_intersects_caller_marker_with_snapshot_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "assign_shards",
        lambda _files: {"docs-config": (Path("tests/test_regular.py"),)},
    )
    monkeypatch.setattr(test_shards, "discover_test_files", lambda _root: ())
    monkeypatch.setattr(
        test_shards,
        "_pytest_command",
        lambda: ["python", "-m", "pytest"],
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda command: commands.append(command) or 0,
    )

    assert (
        test_shards._run_shard(
            "docs-config", ("-m", "slow"), Path("tests"), all_markers=True
        )
        == 0
    )

    assert len(commands) == 1
    command = commands[0]
    pytest_args = command[3:]
    assert pytest_args.count("-m") == 1
    marker_index = pytest_args.index("-m")
    assert pytest_args[marker_index + 1] == "(slow) and (not ledger_snapshot)"


@pytest.mark.parametrize(
    ("pytest_args", "expected"),
    (
        (("-m", "slow"), ("-m", "(slow) and (owned)")),
        (("-mslow",), ("-m", "(slow) and (owned)")),
        (("-m=slow",), ("-m", "(slow) and (owned)")),
        (
            ("-m", "slow", "-mfast", "-m=unit"),
            ("-m", "(slow) and (fast) and (unit) and (owned)"),
        ),
        (("-q",), ("-m", "owned", "-q")),
    ),
)
def test_marker_intersection_handles_every_pytest_option_shape(
    pytest_args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert test_shards._intersect_marker_selection(
        pytest_args,
        required="owned",
    ) == expected


@pytest.mark.parametrize(
    "pytest_args",
    (
        ("-m=",),
        ("-m",),
        ("-m", ""),
        ("-m", "-q"),
    ),
)
def test_snapshot_runner_rejects_malformed_marker_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
    pytest_args: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        test_shards,
        "discover_test_files",
        lambda _root: (Path("tests/test_snapshot.py"),),
    )
    monkeypatch.setattr(
        test_shards.subprocess,
        "call",
        lambda _command: pytest.fail("malformed marker reached pytest"),
    )

    assert test_shards._run_snapshots(pytest_args, Path("tests")) == 2


@pytest.mark.parametrize(
    ("path", "kept"),
    (
        (".codex/hooks/xic_pre_tool_guard.py", {"agent_hooks"}),
        (".codex/hooks.json", {"agent_hooks"}),
        (".codex/rules/xic-safety.rules", {"agent_hooks"}),
        ("tests/test_config_codex_hooks.py", {"agent_hooks"}),
        ("pyproject.toml", {"agent_hooks", "packaging_build"}),
        ("uv.lock", {"packaging_build"}),
        ("xic_extractor.spec", {"packaging_build"}),
        (".github/workflows/build.yml", {"packaging_build"}),
        (".github/workflows/ci.yml", {"packaging_build"}),
        ("tests/test_test_shards.py", {"packaging_build"}),
        ("tests/test_pyinstaller_spec.py", {"packaging_build"}),
        (
            "xic_extractor/discovery/data/default_background_library_15min.json.gz",
            {"packaging_build"},
        ),
        ("xic_inspector/api.py", {"packaging_build"}),
        ("gui/pipeline_worker.py", {"packaging_build"}),
        ("tools/diagnostics/targeted_istd_benchmark_loaders.py", {"packaging_build"}),
        ("xic_extractor/presets/data/dna_dr.toml", {"packaging_build"}),
        ("docs/product/backfill.md", {"product_governance"}),
        (
            "scripts/check_productization_state.py",
            {"product_governance", "packaging_build"},
        ),
        ("README.md", set()),
        ("pyproject.toml.example", set()),
        (".codex/skills/foo/SKILL.md", set()),
        (".codex/hooks.jsonx", set()),
        ("tests/test_config_codex_hooks_extra.py", set()),
        ("xic_extractor/applications/untargeted/application.py", {"packaging_build"}),
    ),
)
def test_relevance_rules_keep_only_markers_whose_paths_changed(
    path: str, kept: set[str]
) -> None:
    skipped = set(test_shards.irrelevant_markers([path]))

    assert set(test_shards.RELEVANCE_RULES) - skipped == kept


def _capture_shard_marker(monkeypatch: pytest.MonkeyPatch, **run_kwargs) -> str:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        test_shards,
        "assign_shards",
        lambda _files: {"docs-config": (Path("tests/test_regular.py"),)},
    )
    monkeypatch.setattr(test_shards, "discover_test_files", lambda _root: ())
    monkeypatch.setattr(
        test_shards, "_pytest_command", lambda: ["python", "-m", "pytest"]
    )
    monkeypatch.setattr(
        test_shards.subprocess, "call", lambda command: commands.append(command) or 0
    )

    assert test_shards._run_shard("docs-config", (), Path("tests"), **run_kwargs) == 0

    pytest_args = commands[0][3:]
    return pytest_args[pytest_args.index("-m") + 1]


def test_local_shard_skips_markers_unrelated_to_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_shards, "_local_change_base", lambda: "base")
    monkeypatch.setattr(
        test_shards,
        "changed_paths",
        lambda base, head=None: ["docs/product/backfill.md"],
    )

    expression = _capture_shard_marker(monkeypatch)

    assert expression == (
        "not ledger_snapshot and not packaging_build and not agent_hooks"
    )
    assert _capture_shard_marker(monkeypatch, all_markers=True) == "not ledger_snapshot"


def test_local_shard_runs_every_marker_when_git_cannot_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_shards, "_local_change_base", lambda: None)

    assert _capture_shard_marker(monkeypatch) == "not ledger_snapshot"


def test_ci_reads_relevance_rules_from_the_shard_owner() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tools.testing.test_shards --marker-expression" in workflow
    for pattern in test_shards.RELEVANCE_RULES.values():
        assert pattern.pattern[:30] not in workflow
