import ast
import json
from pathlib import Path

import pytest

from scripts.check_pr_body import main, validate_pr_body
from tools.testing.test_shards import SHARD_PATTERNS

# Shards deliberately excluded from .github/workflows/ci.yml. Every shard must
# appear exactly once in each test job; membership here is the only way to opt
# out, so the CI/shard contract fails closed for anything unlisted.
#
# Empty, and it should stay that way. Register-snapshot tests are excluded by
# the ledger_snapshot marker instead, which splits at the granularity the
# property has: excluding whole files also removed 175 behavioural tests from
# CI. See SNAPSHOT_MARKER in tools/testing/test_shards.py.
MANUAL_ONLY_SHARDS: frozenset[str] = frozenset()

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
HEAD_SHA = "a" * 40
OTHER_SHA = "b" * 40
RECEIPT_HEADER = (
    "| Claim | Basis | Evidence and binding | Result | What it proves | "
    "What it does not prove |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
)
RECEIPT_ROW = (
    f"| PR claims remain traceable | observed | head={HEAD_SHA}; "
    "command=python -m pytest tests/test_ci_workflow.py -q | "
    "17 focused tests passed | The receipt checker enforces traceability | "
    "It does not prove scientific or production readiness |\n"
)
PRODUCT_AUTHORITY_ACK = (
    "This PR does not grant ProductWriter or matrix/workbook authority from "
    "diagnostics, sidecars, adjudication, review packets, or lockbox labels "
    "unless an explicit contract and expected-diff are included."
)
DIRTY_FILES_ACK = "Unrelated dirty files were not reverted or rewritten."


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


def test_ci_docs_lifecycle_audit_fetches_retirement_history() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    lint_checkout = _job_block(workflow, "lint").split(
        "- uses: astral-sh/setup-uv@v8.2.0",
        1,
    )[0]

    assert "uses: actions/checkout@v6" in lint_checkout
    assert "fetch-depth: 0" in lint_checkout


def test_ci_workflow_reuses_one_environment_per_python_version() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "uv run python -m tools.testing.test_shards --check" in workflow
    for job_name, version, checkout_path in (
        ("test", "3.12", "repo-3.12-tests"),
        ("compatibility", "3.11", "repo-3.11-compatibility"),
    ):
        block = _job_block(workflow, job_name)
        assert "matrix:" not in block
        assert f"name: {job_name} (Python {version})" in block
        assert "fetch-depth: 0" in block
        assert f"path: {checkout_path}" in block
        assert f"working-directory: {checkout_path}" in block
        assert "enable-cache: true" not in block
        assert "cache-suffix:" not in block
        assert (
            f"uv sync --locked --extra dev --group dev --python {version}"
            in block
        )
        assert (
            r"& .\.venv\Scripts\python.exe "
            "-m tools.testing.test_shards $shard -- -v --tb=short -x --durations=10"
        ) in block
        assert (
            r'$env:PATH = "$venvScripts;$env:PATH"'
            in block
        )
        for shard in SHARD_PATTERNS:
            expected = 0 if shard in MANUAL_ONLY_SHARDS else 1
            assert block.count(f'"{shard}"') == expected, shard
        assert "install_seconds=" in block
        assert "test_seconds=" in block
        assert "$env:GITHUB_STEP_SUMMARY" in block
    assert "if: github.event_name == 'push'" in _job_block(
        workflow, "compatibility"
    )
    assert "${{ matrix." not in workflow
    assert "uv run pytest -v --tb=short -x" not in workflow
    assert "uv pip install" not in workflow
    assert "uv venv" not in workflow


def test_pr_gate_points_to_ci_as_single_command_owner() -> None:
    root_agent_contract = Path("AGENTS.md").read_text(encoding="utf-8")
    execution_gate = Path("docs/agent/execution-gates.md").read_text(
        encoding="utf-8"
    )
    normalized_execution_gate = " ".join(execution_gate.split())
    shard_commands = tuple(
        f"uv run python -m tools.testing.test_shards {shard} -- -v --tb=short -x"
        for shard in SHARD_PATTERNS
    )

    assert "docs/agent/execution-gates.md#pr-verification-gate" in root_agent_contract
    assert ".github/workflows/ci.yml" in execution_gate
    assert (
        "only owner of the complete hosted command surface" in normalized_execution_gate
    )
    for command in (*CORE_CI_COMMANDS, *shard_commands):
        assert command not in execution_gate
        assert command not in root_agent_contract
    assert "uv run pytest -v --tb=short -x" not in execution_gate


def _valid_pr_body() -> str:
    return f"""## Outcome

PR claims now have a proportional, SHA-bound evidence receipt and can be reviewed.

## Problem and observable loss

Scattered evidence prose made it hard to identify which head supported a claim.

## What changed

The PR template and checker now require one traceable receipt per claim.

## Scope and contracts

- Lane: workflow-only
- Product output change: no
- Public contracts at risk: none

## Review guide

Review the checker failure semantics, then confirm the CI step uses the event head.

## Verification

- Reviewed head: {HEAD_SHA}
- Validation evidence status: diagnostic_only

{RECEIPT_HEADER}{RECEIPT_ROW}
## Residual risk and rollback

- Residual risk: Custom PR prose outside the template may require normalization.
- Rollback/stop: Revert the checker step if valid receipts are falsely rejected.

## Issue and delivery linkage

- Issue linkage: Refs #321
- Acceptance closed: Template, checker, tests, and edited-event CI wiring are covered.

## Authority Check

- [x] {PRODUCT_AUTHORITY_ACK}
- [x] {DIRTY_FILES_ACK}
"""


def test_complete_valid_pr_body() -> None:
    assert validate_pr_body(_valid_pr_body(), HEAD_SHA) == ()


def test_unedited_template_and_placeholder_are_rejected() -> None:
    template = Path(".github/pull_request_template.md").read_text(encoding="utf-8")
    errors = validate_pr_body(template, HEAD_SHA)
    placeholder_errors = validate_pr_body(
        _valid_pr_body().replace("PR claims now", "TODO: PR claims now"),
        HEAD_SHA,
    )

    assert errors
    assert any("missing or empty" in error or "empty" in error for error in errors)
    assert any("placeholder" in error for error in placeholder_errors)


@pytest.mark.parametrize("punctuation", ("-", "---", "...", "[]"))
def test_required_prose_rejects_punctuation_only(punctuation: str) -> None:
    body = _valid_pr_body().replace(
        "PR claims now have a proportional, SHA-bound evidence receipt "
        "and can be reviewed.",
        punctuation,
    )

    assert any(
        "required section is empty: Outcome" == error
        for error in validate_pr_body(body, HEAD_SHA)
    )


@pytest.mark.parametrize(
    "receipt, expected_error",
    (
        ("", "exact six semantic columns"),
        (RECEIPT_HEADER, "at least one non-empty row"),
    ),
)
def test_missing_or_empty_receipt_is_rejected(
    receipt: str,
    expected_error: str,
) -> None:
    body = _valid_pr_body().replace(RECEIPT_HEADER + RECEIPT_ROW, receipt)

    assert any(
        expected_error in error for error in validate_pr_body(body, HEAD_SHA)
    )


def test_bad_receipt_basis_is_rejected() -> None:
    body = _valid_pr_body().replace("| observed |", "| opinion |")

    assert any("basis must be" in error for error in validate_pr_body(body, HEAD_SHA))


def test_same_proves_and_does_not_prove_are_rejected() -> None:
    body = _valid_pr_body().replace(
        "It does not prove scientific or production readiness",
        "The receipt checker enforces traceability",
    )

    assert any(
        "must distinguish" in error for error in validate_pr_body(body, HEAD_SHA)
    )


@pytest.mark.parametrize(
    ("original", "punctuation"),
    (
        ("PR claims remain traceable", "..."),
        ("17 focused tests passed", "---"),
    ),
)
def test_receipt_semantic_cells_reject_punctuation_only(
    original: str,
    punctuation: str,
) -> None:
    body = _valid_pr_body().replace(original, punctuation)

    assert any(
        "empty or placeholder" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_external_evidence_requires_non_empty_traceability_fields() -> None:
    valid_external = _valid_pr_body().replace(
        f"head={HEAD_SHA}; command=python -m pytest tests/test_ci_workflow.py -q",
        "input=fixture; config=default; hash=abc12345; locator=artifact://receipt",
    )
    empty_external = valid_external.replace("input=fixture", "input=")

    assert validate_pr_body(valid_external, HEAD_SHA) == ()
    assert any(
        "external input/config/hash/locator" in error
        for error in validate_pr_body(empty_external, HEAD_SHA)
    )


@pytest.mark.parametrize(
    "binding",
    (
        f"head={HEAD_SHA}",
        "command=python -m pytest tests/test_ci_workflow.py -q",
        f"head={OTHER_SHA}; command=python -m pytest tests/test_ci_workflow.py -q",
    ),
)
def test_repo_evidence_requires_matching_head_and_locator(binding: str) -> None:
    body = _valid_pr_body().replace(
        f"head={HEAD_SHA}; command=python -m pytest tests/test_ci_workflow.py -q",
        binding,
    )

    assert any(
        "bind repo evidence" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_reviewed_head_must_match_expected_sha() -> None:
    assert any(
        "does not match" in error
        for error in validate_pr_body(_valid_pr_body(), OTHER_SHA)
    )


@pytest.mark.parametrize(
    "linkage",
    ("Close #321", "Closes: #321", "Fixed #321", "Resolves #321", "Refs #321"),
)
def test_issue_linkage_accepts_owner_vocabulary(linkage: str) -> None:
    body = _replace_pr_field(_valid_pr_body(), "Issue linkage", linkage)

    assert validate_pr_body(body, HEAD_SHA) == ()


@pytest.mark.parametrize(
    "field, value",
    (
        ("Issue linkage", ""),
        ("Acceptance closed", ""),
    ),
)
def test_issue_linkage_and_acceptance_are_required(field: str, value: str) -> None:
    body = _replace_pr_field(_valid_pr_body(), field, value)

    assert any(field in error for error in validate_pr_body(body, HEAD_SHA))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("Issue linkage", "No issue: ..."),
        ("Acceptance closed", "..."),
        ("Residual risk", "None: ..."),
        ("Rollback/stop", "None: ..."),
    ),
)
def test_human_fields_reject_punctuation_only_rationale(
    field: str,
    value: str,
) -> None:
    body = _replace_pr_field(_valid_pr_body(), field, value)

    assert any(field in error for error in validate_pr_body(body, HEAD_SHA))


@pytest.mark.parametrize("field", ("Residual risk", "Rollback/stop"))
def test_risk_and_rollback_are_required(field: str) -> None:
    body = _replace_pr_field(_valid_pr_body(), field, "None")

    assert any(
        field in error and "rationale" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_authority_check_cannot_contain_unchecked_box() -> None:
    body = _valid_pr_body().replace("- [x] This PR", "- [ ] This PR")

    assert any(
        "unchecked checkbox" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_authority_check_requires_canonical_assertions() -> None:
    body = _valid_pr_body().replace(
        f"- [x] {DIRTY_FILES_ACK}",
        "The implementation looks fine.",
    )

    assert any(
        "missing required checked assertion" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_evidence_status_must_use_canonical_label() -> None:
    body = _valid_pr_body()
    body = _replace_pr_field(body, "Validation evidence status", "not_applicable")

    assert any(
        "must select an allowed option" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


@pytest.mark.parametrize(
    "private_path",
    (
        r"C:\Users\analyst\output\result.tsv",
        "D:/secret/artifact.tsv",
        r"\\server\share\artifact.tsv",
        "/home/analyst/output/result.tsv",
        "/Users/analyst/output/result.tsv",
        "/data/private/result.tsv",
        "file:///home/analyst/output/result.tsv",
        "vscode://file/C:/Users/analyst/output/result.tsv",
    ),
)
def test_public_local_path_is_rejected(private_path: str) -> None:
    body = _valid_pr_body().replace(
        "17 focused tests passed",
        f"17 focused tests passed at {private_path}",
    )

    assert any(
        "private local absolute path" in error
        for error in validate_pr_body(body, HEAD_SHA)
    )


def test_html_closing_tags_are_not_treated_as_local_paths() -> None:
    body = _valid_pr_body().replace(
        "Review the checker failure semantics, then confirm the CI step "
        "uses the event head.",
        "<details><summary>Review order</summary>Check the parser.</details>",
    )

    assert validate_pr_body(body, HEAD_SHA) == ()


@pytest.mark.parametrize(
    ("opening", "closing"),
    (("```markdown", "```"), ("```markdown", "````"), ("~~~text", "~~~~")),
)
def test_fenced_heading_is_not_parsed_as_contract_section(
    opening: str,
    closing: str,
) -> None:
    body = _valid_pr_body().replace(
        "Review the checker failure semantics, then confirm the CI step "
        "uses the event head.",
        f"""Review the checker failure semantics.

{opening}
## Outcome

Example only.
{closing}""",
    )

    assert validate_pr_body(body, HEAD_SHA) == ()


def test_github_event_path_reads_body_and_head(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "body": _valid_pr_body(),
                    "head": {"sha": HEAD_SHA},
                    "user": {"login": "contributor"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["--github-event", str(event_path)]) == 0
    assert HEAD_SHA in capsys.readouterr().out


def test_local_body_file_mode_reads_utf8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body_path = tmp_path / "body.md"
    body_path.write_text(_valid_pr_body(), encoding="utf-8")

    assert main(["--body-file", str(body_path), "--head-sha", HEAD_SHA]) == 0
    assert HEAD_SHA in capsys.readouterr().out


def test_dependabot_event_is_exempt_with_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event_path = tmp_path / "dependabot.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "body": "",
                    "head": {"sha": OTHER_SHA},
                    "user": {"login": "dependabot[bot]"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["--github-event", str(event_path)]) == 0
    output = capsys.readouterr().out
    assert "exempted" in output
    assert "machine-authored dependency PR bodies" in output


def test_non_pr_event_is_clearly_skipped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event_path = tmp_path / "push.json"
    event_path.write_text(json.dumps({"ref": "refs/heads/main"}), encoding="utf-8")

    assert main(["--github-event", str(event_path)]) == 0
    assert "skipped" in capsys.readouterr().out


def test_ci_separates_body_receipt_from_code_validation_workflow() -> None:
    code_workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    receipt_workflow = Path(
        ".github/workflows/pr-evidence-receipt.yml"
    ).read_text(encoding="utf-8")
    receipt_block = _job_block(receipt_workflow, "validate")
    checker = 'python scripts/check_pr_body.py --github-event "$GITHUB_EVENT_PATH"'

    assert 'types: ["opened", "synchronize", "reopened", "edited"]' in (
        receipt_workflow
    )
    assert "edited" not in code_workflow
    assert checker not in code_workflow
    assert receipt_workflow.count(checker) == 1
    assert receipt_block.index("uses: actions/checkout@v6") < receipt_block.index(
        checker
    )
    assert "uv sync" not in receipt_block
    for command in CORE_CI_COMMANDS:
        assert command not in receipt_workflow
    for job_name in ("lint", "typecheck", "test"):
        block = _job_block(code_workflow, job_name)
        assert "needs: pr-evidence-receipt" not in block
        assert "github.event.action" not in block


def _replace_pr_field(body: str, field: str, value: str) -> str:
    lines = body.splitlines()
    prefix = f"- {field}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix} {value}".rstrip()
            return "\n".join(lines) + "\n"
    raise AssertionError(f"missing field {field}")


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
