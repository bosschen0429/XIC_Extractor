import re
from pathlib import Path

CORE_CI_COMMANDS = (
    "uv run ruff check xic_extractor tests",
    "uv run lint-imports --no-cache",
    (
        "uv run pytest tests/test_docs_architecture_census.py "
        "-m current_repository_census --noconftest -o addopts= "
        "--basetemp=${{ runner.temp }}/xic-architecture-census -q"
    ),
    "uv run python scripts/check_diagnostics_index.py",
    (
        "uv run python tools/diagnostics/docs_management_audit.py "
        "--repo-only --fail-on-completed-transient --json"
    ),
    "uv run mypy xic_extractor",
    "uv run python -m tools.testing.test_shards --check",
)


def test_ci_runs_static_checks_and_shard_validation() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for command in CORE_CI_COMMANDS:
        assert command in workflow


def test_ci_cancels_obsolete_runs_and_limits_push_branches() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "concurrency:" in workflow
    assert "cancel-in-progress: true" in workflow
    assert _event_branches(workflow, "push") == ("master", "main")
    assert _event_branches(workflow, "pull_request") == ("master", "main")


def test_ci_uses_uv_cache_for_hosted_static_jobs() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for job_name in ("lint", "typecheck"):
        block = _job_block(workflow, job_name)
        assert "runs-on: ubuntu-latest" in block
        assert "uses: astral-sh/setup-uv@v8.2.0" in block
        assert "enable-cache: true" in block
        assert "cache-dependency-glob:" in block
        assert "pyproject.toml" in block
        assert "uv.lock" in block


def test_ci_uses_one_test_job_per_supported_python_version() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for job_name, version in (("test", "3.12"), ("compatibility", "3.11")):
        block = _job_block(workflow, job_name)
        assert f"name: {job_name} (Python {version})" in block
        assert f"uv sync --locked --extra dev --group dev --python {version}" in block
        assert "matrix:" not in block


def test_ci_fetches_history_for_docs_lifecycle_audit() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    lint_checkout = _job_block(workflow, "lint").split(
        "- uses: astral-sh/setup-uv@v8.2.0", 1
    )[0]
    assert "fetch-depth: 0" in lint_checkout


def test_pr_gate_points_to_ci_as_command_owner() -> None:
    root_contract = Path("AGENTS.md").read_text(encoding="utf-8")
    execution_gate = Path("docs/agent/execution-gates.md").read_text(
        encoding="utf-8"
    )
    assert "docs/agent/execution-gates.md#pr-verification" in root_contract
    assert ".github/workflows/ci.yml" in execution_gate
    for command in CORE_CI_COMMANDS:
        assert command not in execution_gate
        assert command not in root_contract


def _job_block(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"missing job {job_name}"
    return match.group("body")


def _event_branches(workflow: str, event_name: str) -> tuple[str, ...]:
    match = re.search(
        rf"(?ms)^  {re.escape(event_name)}:\n"
        r"(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|^jobs:)",
        workflow,
    )
    assert match is not None, f"missing event {event_name}"
    branch_match = re.search(r"branches:\s*\[([^]]+)\]", match.group("body"))
    assert branch_match is not None
    return tuple(
        item.strip().strip("\"'") for item in branch_match.group(1).split(",")
    )
