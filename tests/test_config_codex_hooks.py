from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS_CONFIG = ROOT / ".codex" / "hooks.json"
HOOKS_DIR = ROOT / ".codex" / "hooks"
RULES_PATH = ROOT / ".codex" / "rules" / "xic-safety.rules"
OPERATING_SYSTEM = ROOT / "docs" / "agent" / "codex-operating-system.md"
HOOK_SCRIPT_PATTERN = re.compile(
    r"\.codex[\\/]+hooks[\\/]+(?P<script>[A-Za-z0-9_.-]+\.py)"
)
EXPECTED_HOOKS = {
    "UserPromptSubmit": "xic_prompt_router.py",
    "PreToolUse": "xic_pre_tool_guard.py",
    "PostToolUse": "xic_post_tool_guard.py",
}
EXPECTED_GOVERNANCE_ROWS = {
    "docs_lifecycle_retirement",
    "destructive_git_filesystem",
    "raw_background_launch",
    "execution_affecting_config",
    "product_public_surface",
    "pr_validation_gate",
}
ALLOWED_MECHANISMS = {
    "hard_guard",
    "ci_checker",
    "advisory_context",
    "human_decision",
}


def _load_hooks_config() -> dict[str, object]:
    return json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))


def _script_reference(command: object) -> Path:
    assert isinstance(command, str) and command
    matches = HOOK_SCRIPT_PATTERN.findall(command)
    assert len(matches) == 1, f"expected one repo hook script in {command!r}"
    return Path(".codex") / "hooks" / matches[0]


def _enabled_hook_entries(
    config: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    hooks = config.get("hooks")
    assert isinstance(hooks, dict)
    entries: list[tuple[str, dict[str, object]]] = []
    for event_name, groups in hooks.items():
        assert isinstance(event_name, str)
        assert isinstance(groups, list)
        for group in groups:
            assert isinstance(group, dict)
            commands = group.get("hooks")
            assert isinstance(commands, list)
            for command in commands:
                assert isinstance(command, dict)
                assert command.get("type") == "command"
                entries.append((event_name, command))
    return entries


def _minimal_payload(event_name: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": event_name,
        "turn_id": "config-smoke",
        "cwd": str(ROOT),
        "permission_mode": "default",
    }
    if event_name == "UserPromptSubmit":
        payload["prompt"] = "ordinary documentation wording"
        return payload
    payload.update(
        {
            "tool_name": "Bash",
            "tool_use_id": "config-smoke",
            "tool_input": {"command": "git status --short"},
        }
    )
    if event_name == "PostToolUse":
        payload["tool_response"] = {"stdout": "", "stderr": ""}
    return payload


def _assert_hook_contract(config: dict[str, object], *, root: Path = ROOT) -> None:
    seen: dict[str, str] = {}
    for event_name, entry in _enabled_hook_entries(config):
        posix_ref = _script_reference(entry.get("command"))
        windows_ref = _script_reference(entry.get("commandWindows"))
        assert posix_ref == windows_ref
        assert event_name not in seen, f"duplicate enabled hook event: {event_name}"
        seen[event_name] = posix_ref.name

        script = root / posix_ref
        assert script.is_file(), f"missing enabled hook script: {posix_ref.as_posix()}"
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(_minimal_payload(event_name)),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert completed.returncode == 0, (
            f"{event_name} hook did not start cleanly: {completed.stderr}"
        )
        assert completed.stdout == "", f"{event_name} no-op was not silent"

    assert seen == EXPECTED_HOOKS


def _run_pre_hook(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / "xic_pre_tool_guard.py")],
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "turn_id": "config-smoke",
                "tool_name": "Bash",
                "tool_use_id": "config-smoke",
                "cwd": str(ROOT),
                "permission_mode": "default",
                "tool_input": {"command": command},
            }
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _rule_block(tokens: tuple[str, ...]) -> str:
    pattern = "pattern = [" + ", ".join(json.dumps(token) for token in tokens) + "]"
    matches = [
        block
        for block in RULES_PATH.read_text(encoding="utf-8").split("prefix_rule(")[1:]
        if pattern in block
    ]
    assert len(matches) == 1, f"expected one rule block for {tokens!r}"
    return matches[0].split("\n)", 1)[0]


def _governance_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for line in OPERATING_SYSTEM.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) == 6:
            rows.append(cells)
    return rows


def _assert_repo_locator(locator: str) -> None:
    path_text = locator.split("#", 1)[0].split("::", 1)[0]
    assert (ROOT / path_text).exists(), f"missing governance locator: {locator}"


def test_existing_hook_behavior_fixture_runs_in_ordinary_pytest() -> None:
    subprocess.run(
        [sys.executable, str(HOOKS_DIR / "fixtures" / "assert_hook_outputs.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_all_enabled_hook_references_resolve_start_and_noop() -> None:
    _assert_hook_contract(_load_hooks_config())


def test_hook_contract_rejects_missing_reference() -> None:
    config = copy.deepcopy(_load_hooks_config())
    entry = _enabled_hook_entries(config)[0][1]
    entry["command"] = str(entry["command"]).replace(
        "xic_prompt_router.py", "missing.py"
    )
    entry["commandWindows"] = str(entry["commandWindows"]).replace(
        "xic_prompt_router.py", "missing.py"
    )
    with pytest.raises(AssertionError, match="missing enabled hook script"):
        _assert_hook_contract(config)


def test_hook_contract_rejects_cross_platform_divergence() -> None:
    config = copy.deepcopy(_load_hooks_config())
    entry = _enabled_hook_entries(config)[0][1]
    entry["commandWindows"] = str(entry["commandWindows"]).replace(
        "xic_prompt_router.py", "xic_pre_tool_guard.py"
    )
    with pytest.raises(AssertionError):
        _assert_hook_contract(config)


def test_hook_contract_rejects_nonstarting_script(tmp_path: Path) -> None:
    script = tmp_path / ".codex" / "hooks" / "broken.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(3)\n", encoding="utf-8")
    config = copy.deepcopy(_load_hooks_config())
    entry = _enabled_hook_entries(config)[0][1]
    entry["command"] = str(entry["command"]).replace(
        "xic_prompt_router.py", "broken.py"
    )
    entry["commandWindows"] = str(entry["commandWindows"]).replace(
        "xic_prompt_router.py", "broken.py"
    )
    config["hooks"] = {"UserPromptSubmit": [{"hooks": [entry]}]}

    with pytest.raises(AssertionError, match="did not start cleanly"):
        _assert_hook_contract(config, root=tmp_path)


@pytest.mark.parametrize(
    ("tokens", "decision"),
    [
        (("git", "reset", "--hard"), "forbidden"),
        (("git", "checkout", "--"), "forbidden"),
        (("git", "clean"), "prompt"),
    ],
)
def test_destructive_git_rules_are_primary_owner(
    tokens: tuple[str, ...], decision: str
) -> None:
    assert f'decision = "{decision}"' in _rule_block(tokens)


DESTRUCTIVE_TEXTS = [
    " ".join(("git", "reset", "--hard")),
    " ".join(("git", "checkout", "--")),
    " ".join(("git", "clean", "-fd")),
]


@pytest.mark.parametrize("needle", DESTRUCTIVE_TEXTS)
def test_pre_hook_does_not_block_read_only_search_for_rule_text(needle: str) -> None:
    completed = _run_pre_hook(f"rg -n {json.dumps(needle)} docs")
    assert completed.returncode == 0
    assert completed.stdout == ""


@pytest.mark.parametrize(
    "tokens",
    [
        ("git", "reset", "--hard"),
        ("git", "checkout", "--", "example.txt"),
        ("git", "clean", "-fd"),
    ],
)
def test_pre_hook_does_not_duplicate_static_destructive_prefix_policy(
    tokens: tuple[str, ...],
) -> None:
    completed = _run_pre_hook(" ".join(tokens))
    assert completed.returncode == 0
    assert completed.stdout == ""


@pytest.mark.parametrize("wrapper", ["bash -lc", "pwsh -Command"])
@pytest.mark.parametrize(
    "tokens",
    [
        ("git", "reset", "--hard"),
        ("git", "checkout", "--", "example.txt"),
        ("git", "clean", "-fd"),
    ],
)
def test_pre_hook_blocks_wrapped_destructive_commands(
    wrapper: str, tokens: tuple[str, ...]
) -> None:
    completed = _run_pre_hook(f"{wrapper} {json.dumps(' '.join(tokens))}")
    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"


def test_governance_index_closes_repository_side_residuals() -> None:
    rows = _governance_rows()
    assert {row[0] for row in rows} == EXPECTED_GOVERNANCE_ROWS
    for _row_id, owner, mechanism, locator, representative, residual in rows:
        assert mechanism in ALLOWED_MECHANISMS
        _assert_repo_locator(owner)
        _assert_repo_locator(locator)
        _assert_repo_locator(representative)
        assert residual == "none"
