import ast
from pathlib import Path

from tools.testing.test_shards import SHARD_PATTERNS

CORE_CI_COMMANDS = (
    "uv run ruff check xic_extractor tests",
    "uv run lint-imports --no-cache",
    "uv run python scripts/check_architecture_census.py",
    "uv run python scripts/check_diagnostics_index.py",
    (
        "uv run python tools/diagnostics/docs_management_audit.py "
        "--repo-only --fail-on-completed-transient --json"
    ),
    "uv run mypy xic_extractor",
    "uv run python -m tools.testing.test_shards --check",
)


def test_ci_workflow_runs_ruff_and_mypy() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    for command in CORE_CI_COMMANDS:
        assert command in workflow


def test_ci_workflow_cancels_obsolete_runs_and_avoids_duplicate_branch_pushes() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "concurrency:" in workflow
    assert "cancel-in-progress: true" in workflow
    assert 'branches: ["master", "main", "feature/*", "fix/*"]' not in workflow
    assert _event_branches(workflow, "push") == ("master", "main")
    assert _event_branches(workflow, "pull_request") == ("master", "main")


def test_ci_workflow_uses_uv_cache_for_hosted_static_jobs() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    for job_name in ("lint", "typecheck"):
        block = _job_block(workflow, job_name)
        assert "runs-on: ubuntu-latest" in block
        assert "uses: astral-sh/setup-uv@v8.2.0" in block
        assert "enable-cache: true" in block
        assert "cache-dependency-glob:" in block
        assert "pyproject.toml" in block
        assert "uv.lock" in block


def test_ci_workflow_runs_sharded_tests() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    shard_command = (
        "uv run python -m tools.testing.test_shards "
        "${{ matrix.shard }} -- -v --tb=short -x"
    )

    assert "uv run python -m tools.testing.test_shards --check" in workflow
    assert _matrix_shard_blocks(workflow) == (
        tuple(SHARD_PATTERNS),
        tuple(SHARD_PATTERNS),
    )
    assert "name: test / ${{ matrix.shard }} (Python 3.12)" in workflow
    assert "name: compatibility / ${{ matrix.shard }} (Python 3.11)" in workflow
    assert "if: github.event_name == 'push'" in workflow
    assert "repo-3.12-${{ matrix.shard }}" in workflow
    assert "repo-3.11-${{ matrix.shard }}" in workflow
    assert "repo-${{ matrix.python-version }}-${{ matrix.shard }}" not in workflow
    assert 'python-version: ["3.11", "3.12"]' not in workflow
    assert shard_command in workflow
    assert "install_seconds=" in workflow
    assert "test_seconds=" in workflow
    assert "$env:GITHUB_STEP_SUMMARY" in workflow
    assert "uv run pytest -v --tb=short -x" not in workflow


def test_pr_gate_owner_matches_ci_commands_and_root_points_to_it() -> None:
    root_agent_contract = Path("AGENTS.md").read_text(encoding="utf-8")
    execution_gate = Path("docs/agent/execution-gates.md").read_text(
        encoding="utf-8"
    )
    shard_commands = tuple(
        f"uv run python -m tools.testing.test_shards {shard} -- -v --tb=short -x"
        for shard in SHARD_PATTERNS
    )

    assert (
        "docs/agent/execution-gates.md#pr-verification-gate"
        in root_agent_contract
    )
    for command in (*CORE_CI_COMMANDS, *shard_commands):
        assert command in execution_gate
        assert command not in root_agent_contract
    assert "uv run pytest -v --tb=short -x" not in execution_gate


def _matrix_shard_blocks(workflow: str) -> tuple[tuple[str, ...], ...]:
    lines = workflow.splitlines()
    blocks: list[tuple[str, ...]] = []
    for index, line in enumerate(lines):
        if line.strip() != "shard:":
            continue
        shards: list[str] = []
        for follow in lines[index + 1 :]:
            stripped = follow.strip()
            if stripped.startswith("- "):
                shards.append(stripped[2:])
                continue
            if stripped:
                break
        blocks.append(tuple(shards))
    if not blocks:
        raise AssertionError("missing test shard matrix")
    return tuple(blocks)


def _job_block(workflow: str, job_name: str) -> str:
    lines = workflow.splitlines()
    header = f"  {job_name}:"
    for index, line in enumerate(lines):
        if line != header:
            continue
        block: list[str] = []
        for follow in lines[index:]:
            if follow.startswith("  ") and not follow.startswith("    ") and block:
                break
            block.append(follow)
        return "\n".join(block)
    raise AssertionError(f"missing job {job_name}")


def _event_branches(workflow: str, event_name: str) -> tuple[str, ...]:
    lines = workflow.splitlines()
    event_header = f"  {event_name}:"
    for index, line in enumerate(lines):
        if line != event_header:
            continue
        for follow in lines[index + 1 :]:
            if follow.startswith("  ") and not follow.startswith("    "):
                break
            stripped = follow.strip()
            if not stripped.startswith("branches:"):
                continue
            parsed = ast.literal_eval(stripped.split(":", 1)[1].strip())
            if not isinstance(parsed, list):
                raise AssertionError(f"{event_name} branches must be a YAML list")
            return tuple(str(value) for value in parsed)
        break
    raise AssertionError(f"missing branches for {event_name}")
