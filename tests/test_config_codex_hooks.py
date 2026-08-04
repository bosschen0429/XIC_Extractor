from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS_CONFIG = ROOT / ".codex" / "hooks.json"
PROJECT_CONFIG = ROOT / ".codex" / "config.toml"
HOOKS_DIR = ROOT / ".codex" / "hooks"
RULES_PATH = ROOT / ".codex" / "rules" / "xic-safety.rules"
OPERATING_SYSTEM = ROOT / "docs" / "agent" / "codex-operating-system.md"
PARAMETER_SETTINGS = ROOT / "docs" / "agent" / "parameter-settings.md"
SUBAGENT_ROUTING = ROOT / "docs" / "agent" / "subagent-routing.md"
HOOK_COMMAND_PATTERNS = {
    "command": re.compile(
        r'^python3 "\$\(git rev-parse --show-toplevel\)/\.codex/hooks/'
        r'(?P<script>[A-Za-z0-9_.-]+\.py)"$'
    ),
    "commandWindows": re.compile(
        r'^python "\$\(git rev-parse --show-toplevel\)\\\.codex\\hooks\\'
        r'(?P<script>[A-Za-z0-9_.-]+\.py)"$'
    ),
}
EXPECTED_HOOKS = {
    "UserPromptSubmit": "xic_prompt_router.py",
    "PreToolUse": "xic_pre_tool_guard.py",
    "PostToolUse": "xic_post_tool_guard.py",
}
EXPECTED_MATCHERS = {
    "UserPromptSubmit": None,
    "PreToolUse": "Bash|apply_patch|Edit|Write",
    "PostToolUse": "Bash",
}
EXPECTED_GOVERNANCE_ROWS = {
    "docs_lifecycle_retirement",
    "destructive_git_filesystem",
    "raw_background_launch",
    "execution_affecting_config",
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


def _project_shell_environment() -> dict[str, str]:
    config = tomllib.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment.update(config["shell_environment_policy"]["set"])
    return environment


def _script_reference(command: object, field: str) -> Path:
    assert isinstance(command, str) and command
    match = HOOK_COMMAND_PATTERNS[field].fullmatch(command)
    assert match is not None, f"invalid {field} hook command shape: {command!r}"
    return Path(".codex") / "hooks" / match.group("script")


def _enabled_hook_entries(
    config: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    hooks = config.get("hooks")
    assert isinstance(hooks, dict)
    assert set(hooks) == set(EXPECTED_MATCHERS)
    entries: list[tuple[str, dict[str, object]]] = []
    for event_name, groups in hooks.items():
        assert isinstance(event_name, str)
        assert isinstance(groups, list)
        for group in groups:
            assert isinstance(group, dict)
            assert group.get("matcher") == EXPECTED_MATCHERS[event_name], (
                f"unexpected {event_name} matcher: {group.get('matcher')!r}"
            )
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
        posix_ref = _script_reference(entry.get("command"), "command")
        windows_ref = _script_reference(entry.get("commandWindows"), "commandWindows")
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


def _run_configured_hook(
    event_name: str, entry: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell is not None, "PowerShell is required by commandWindows"
        argv = [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            str(entry["commandWindows"]),
        ]
    else:
        argv = ["/bin/sh", "-c", str(entry["command"])]
    return subprocess.run(
        argv,
        input=json.dumps(_minimal_payload(event_name)),
        cwd=ROOT,
        env=_project_shell_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


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


def _run_post_hook(
    command: str, tool_response: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / "xic_post_tool_guard.py")],
        input=json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "turn_id": "config-smoke",
                "tool_name": "Bash",
                "tool_use_id": "config-smoke",
                "cwd": str(ROOT),
                "permission_mode": "default",
                "tool_input": {"command": command},
                "tool_response": tool_response,
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
    owner_text, _, symbol = locator.partition("::")
    path_text, _, fragment = owner_text.partition("#")
    path = ROOT / path_text
    assert path.exists(), f"missing governance locator: {locator}"
    if fragment:
        headings = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                continue
            title = line.lstrip("#").strip().lower()
            slug = re.sub(r"[^\w\- ]", "", title)
            headings.append(re.sub(r"\s+", "-", slug))
        assert fragment in headings, f"missing governance heading: {locator}"
    if symbol:
        source = path.read_text(encoding="utf-8")
        assert re.search(rf"\b{re.escape(symbol)}\b", source), (
            f"missing governance symbol: {locator}"
        )


def test_existing_hook_behavior_fixture_runs_in_ordinary_pytest() -> None:
    subprocess.run(
        [sys.executable, str(HOOKS_DIR / "fixtures" / "assert_hook_outputs.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_project_config_isolates_git_from_unreadable_user_home() -> None:
    config = tomllib.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))

    assert config == {
        "shell_environment_policy": {
            "set": {"GIT_CONFIG_GLOBAL": "NUL"},
        }
    }


def test_subagent_routing_uses_current_project_sandbox_contract() -> None:
    routing = SUBAGENT_ROUTING.read_text(encoding="utf-8")

    assert "permission profile `workspace-github`" in routing
    assert 'approval_policy = "on-request"' in routing
    assert "`GIT_CONFIG_GLOBAL=NUL`" in routing
    assert "intentionally not added" not in routing
    assert (
        ".venv\\Scripts\\python.exe -m scripts.agent_sandbox_doctor "
        '--command-only --command "<command>"'
    ) in routing


def test_elevated_git_ownership_recovery_stays_invocation_scoped() -> None:
    settings = PARAMETER_SETTINGS.read_text(encoding="utf-8")

    assert "$xicRepoRoot = (Resolve-Path .).Path.Replace('\\', '/')" in settings
    assert 'git -c "safe.directory=$xicRepoRoot" add -- <task-files...>' in settings
    assert "不要寫入 global `safe.directory`" in settings


def test_all_enabled_hook_references_resolve_start_and_noop() -> None:
    _assert_hook_contract(_load_hooks_config())


def test_enabled_hook_commands_start_on_current_platform() -> None:
    for event_name, entry in _enabled_hook_entries(_load_hooks_config()):
        completed = _run_configured_hook(event_name, entry)
        assert completed.returncode == 0, (
            f"configured {event_name} command failed: {completed.stderr}"
        )
        assert completed.stdout == ""


def test_configured_hook_command_rejects_missing_runner() -> None:
    config = copy.deepcopy(_load_hooks_config())
    event_name, entry = _enabled_hook_entries(config)[0]
    field = "commandWindows" if os.name == "nt" else "command"
    entry[field] = str(entry[field]).replace("python", "missing-codex-runner", 1)
    completed = _run_configured_hook(event_name, entry)
    assert completed.returncode != 0


@pytest.mark.parametrize("field", ["command", "commandWindows"])
def test_hook_contract_rejects_invalid_platform_command_shape(field: str) -> None:
    config = copy.deepcopy(_load_hooks_config())
    entry = _enabled_hook_entries(config)[0][1]
    entry[field] = str(entry[field]).replace("python", "missing-codex-runner", 1)
    with pytest.raises(AssertionError, match=f"invalid {field} hook command shape"):
        _assert_hook_contract(config)


def test_hook_contract_rejects_narrowed_matcher() -> None:
    config = copy.deepcopy(_load_hooks_config())
    groups = config["hooks"]
    assert isinstance(groups, dict)
    pre_groups = groups["PreToolUse"]
    assert isinstance(pre_groups, list)
    pre_groups[0]["matcher"] = "Bash"
    with pytest.raises(AssertionError, match="unexpected PreToolUse matcher"):
        _enabled_hook_entries(config)


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


def test_git_add_has_no_blanket_allow_rule() -> None:
    rules = RULES_PATH.read_text(encoding="utf-8")

    assert 'pattern = ["git", "add"]' not in rules
    assert 'decision = "prompt"' in _rule_block(("git", "push"))


@pytest.mark.parametrize(
    "tool_response",
    [
        {"stdout": "collected 0 items", "stderr": ""},
        "ERROR: not found: tests/test_example.py::test_missing",
    ],
)
def test_post_hook_rejects_empty_or_invalid_pytest_targets(
    tool_response: object,
) -> None:
    completed = _run_post_hook("python -m pytest tests -q", tool_response)

    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    assert output["decision"] == "block"
    assert "zero tests or used an invalid node id" in output["reason"]


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


@pytest.mark.parametrize(
    "command",
    [
        "git -C C:\\repo push origin HEAD",
        "git --git-dir C:\\repo\\.git push origin HEAD",
        "pwsh -Command " + json.dumps("git --no-pager push origin HEAD"),
        "env -u CI git -C C:\\repo push origin HEAD",
        "bash -lc "
        + json.dumps("FOO=1 command git --git-dir C:\\repo\\.git push origin HEAD"),
        "Write-Host ready\ngit -C C:\\repo push origin HEAD",
        "pwsh -Command 'Write-Host ready\ngit -C C:\\repo push origin HEAD'",
    ],
)
def test_pre_hook_rewrites_optioned_git_push_to_policy_visible_form(
    command: str,
) -> None:
    completed = _run_pre_hook(command)

    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "direct `git push`" in hook_output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "git -C C:\\repo status --short",
        "env -u CI git -C C:\\repo status --short",
    ],
)
def test_pre_hook_preserves_read_only_git_global_option_commands(
    command: str,
) -> None:
    completed = _run_pre_hook(command)

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_leaves_direct_git_push_to_execpolicy_prompt() -> None:
    completed = _run_pre_hook("git push origin HEAD")

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert 'decision = "prompt"' in _rule_block(("git", "push"))


WRAPPERS = [
    "bash -lc",
    "bash -xc",
    "/bin/bash -lc",
    "env bash -lc",
    "pwsh -Command",
    "pwsh -NoProfile -Command",
    "cmd /c",
]


@pytest.mark.parametrize(
    "wrapper",
    ["env -u CI bash -lc", "env --unset=CI /bin/bash -lc"],
)
def test_pre_hook_locates_wrapped_shell_after_env_options(wrapper: str) -> None:
    completed = _run_pre_hook(f"{wrapper} {json.dumps(DESTRUCTIVE_TEXTS[0])}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "git_invocation",
    [
        "command git",
        "command -p git",
        "exec git",
        "exec -a safe-name git",
        "/usr/bin/git",
        "git -C /tmp",
    ],
)
def test_pre_hook_normalizes_nested_git_invocations(git_invocation: str) -> None:
    payload = " ".join((git_invocation, "reset", "--hard"))
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("prefix", ["FOO=1 ", ">/tmp/xic-hook-log "])
def test_pre_hook_normalizes_simple_command_prefixes(prefix: str) -> None:
    payload = prefix + DESTRUCTIVE_TEXTS[0]
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize(
    "tokens",
    [
        ("git", "reset", "--hard"),
        ("git", "checkout", "--", "example.txt"),
        ("git", "clean", "-fd"),
    ],
)
def test_pre_hook_preserves_wrapped_destructive_owner_semantics(
    wrapper: str, tokens: tuple[str, ...]
) -> None:
    completed = _run_pre_hook(f"{wrapper} {json.dumps(' '.join(tokens))}")
    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "flags",
    [
        ("-fd",),
        ("-f", "-d"),
        ("--force", "-d"),
        ("-f",),
        ("--force",),
    ],
)
def test_pre_hook_denies_all_wrapped_forced_clean_forms(
    flags: tuple[str, ...],
) -> None:
    payload = " ".join(("git", "clean", *flags))
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_hook_allows_wrapped_clean_dry_run() -> None:
    payload = " ".join(("git", "clean", "-n", "-d"))
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    assert completed.returncode == 0
    assert completed.stdout == ""


@pytest.mark.parametrize(
    "wrapper", ["sh -c", "/bin/bash -lc", "pwsh -NoProfile -Command"]
)
def test_pre_hook_does_not_deny_wrapped_read_only_search(wrapper: str) -> None:
    needle = " ".join(("git", "reset", "--hard"))
    payload = f"rg -n {json.dumps(needle)} docs"
    completed = _run_pre_hook(f"{wrapper} {json.dumps(payload)}")
    assert completed.returncode == 0
    assert completed.stdout == ""


@pytest.mark.parametrize(
    ("wrapper", "prefix", "tokens"),
    [
        ("bash -lc", "cd /tmp && ", ("git", "reset", "--hard")),
        ("pwsh -Command", "Write-Host ok; ", ("git", "checkout", "--", "file")),
        ("bash -lc", "sh -c ", ("git", "reset", "--hard")),
    ],
)
def test_pre_hook_blocks_compound_and_nested_wrapped_destructive_commands(
    wrapper: str, prefix: str, tokens: tuple[str, ...]
) -> None:
    nested = " ".join(tokens)
    payload = prefix + (json.dumps(nested) if prefix == "sh -c " else nested)
    completed = _run_pre_hook(f"{wrapper} {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "payload",
    [
        "if true; then " + DESTRUCTIVE_TEXTS[0] + "; fi",
        "while true; do " + DESTRUCTIVE_TEXTS[2] + "; done",
        "if " + DESTRUCTIVE_TEXTS[0] + "; then :; fi",
        "until " + DESTRUCTIVE_TEXTS[2] + "; do :; done",
    ],
)
def test_pre_hook_blocks_control_prefixed_destructive_commands(payload: str) -> None:
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("wrapper", ["bash -lc", "pwsh -Command"])
def test_pre_hook_blocks_multiline_wrapped_destructive_commands(wrapper: str) -> None:
    payload = "Write-Host ok\n" + DESTRUCTIVE_TEXTS[0]
    completed = _run_pre_hook(f"{wrapper} '{payload}'")
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_hook_preserves_quoted_newline_in_wrapped_search() -> None:
    needle = "prefix\n" + DESTRUCTIVE_TEXTS[0]
    payload = f"rg -n {json.dumps(needle)} docs"
    completed = _run_pre_hook(f"bash -lc '{payload}'")
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_preserves_quoted_newline_in_git_search() -> None:
    needle = "prefix\ngit -C C:\\repo push origin HEAD"
    completed = _run_pre_hook(f"rg -n {json.dumps(needle)} docs")
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_preserves_quoted_separators_in_wrapped_search() -> None:
    needle = "prefix; " + " ".join(("git", "reset", "--hard"))
    payload = f"rg -n {json.dumps(needle)} docs"
    completed = _run_pre_hook(f"bash -lc {json.dumps(payload)}")
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_blocks_wrapped_raw_launch_without_preset_token() -> None:
    payload = (
        "Write-Host ok; Start-Process python -ArgumentList "
        "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT "
        "--dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR'"
    )
    completed = _run_pre_hook(f"pwsh -Command {json.dumps(payload)}")
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "Background RAW launch" in hook_output["permissionDecisionReason"]


def test_pre_hook_correlates_raw_signals_across_powershell_statements() -> None:
    payload = (
        "$rawArgs='-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT'; "
        "Start-Process python -ArgumentList $rawArgs"
    )
    completed = _run_pre_hook(f'pwsh -Command "{payload}"')
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "Background RAW launch" in hook_output["permissionDecisionReason"]


def test_pre_hook_propagates_raw_argument_aliases() -> None:
    payload = (
        "$a='-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT'; "
        "$b=$a; Start-Process python -ArgumentList $b"
    )
    completed = _run_pre_hook(f'pwsh -Command "{payload}"')
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "Background RAW launch" in hook_output["permissionDecisionReason"]


def test_pre_hook_resolves_raw_assignment_state_at_launch_time() -> None:
    raw_then_safe = (
        "$a='-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT'; "
        "Start-Process python -ArgumentList $a; $a='-c print(1)'"
    )
    completed = _run_pre_hook(f'pwsh -Command "{raw_then_safe}"')
    output = json.loads(completed.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    safe_then_raw = (
        "$a='-c print(1)'; Start-Process python -ArgumentList $a; "
        "$a='-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT'"
    )
    completed = _run_pre_hook(f'pwsh -Command "{safe_then_raw}"')
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_blocks_raw_launch_inside_powershell_control_block() -> None:
    payload = (
        "if ($true) { Start-Process python -ArgumentList "
        "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT' }"
    )
    completed = _run_pre_hook(f'pwsh -Command "{payload}"')
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "Background RAW launch" in hook_output["permissionDecisionReason"]


def test_pre_hook_blocks_raw_launch_on_assignment_rhs() -> None:
    payload = (
        "$proc = Start-Process python -PassThru -ArgumentList "
        "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT'"
    )
    completed = _run_pre_hook(f'pwsh -Command "{payload}"')
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "Background RAW launch" in hook_output["permissionDecisionReason"]


def test_pre_hook_does_not_treat_start_process_text_as_a_raw_launch() -> None:
    completed = _run_pre_hook('bash -lc "echo Start-Process run_alignment"')
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_does_not_cross_correlate_unrelated_raw_text() -> None:
    payload = (
        "Write-Host run_alignment; Start-Process python -ArgumentList '-c print(1)'"
    )
    completed = _run_pre_hook(f'pwsh -Command "{payload}"')
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_pre_hook_emits_config_advisory_for_agent_contract() -> None:
    completed = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "xic_pre_tool_guard.py")],
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "turn_id": "config-smoke",
                "tool_name": "apply_patch",
                "tool_use_id": "config-smoke",
                "cwd": str(ROOT),
                "permission_mode": "default",
                "tool_input": {
                    "patch": (
                        "*** Update File: docs/agent/product-validation-contract.md\n"
                    )
                },
            }
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    output = json.loads(completed.stdout)
    message = output["hookSpecificOutput"]["additionalContext"]
    assert "Execution-affecting XIC agent config" in message


def test_governance_index_closes_repository_side_residuals() -> None:
    rows = _governance_rows()
    assert {row[0] for row in rows} == EXPECTED_GOVERNANCE_ROWS
    for _row_id, owner, mechanism, locator, representative, residual in rows:
        assert mechanism in ALLOWED_MECHANISMS
        _assert_repo_locator(owner)
        _assert_repo_locator(locator)
        _assert_repo_locator(representative)
        assert residual == "none"


def test_governance_locator_rejects_stale_fragment_and_symbol() -> None:
    with pytest.raises(AssertionError, match="missing governance heading"):
        _assert_repo_locator("docs/agent/codex-operating-system.md#missing-heading")
    with pytest.raises(AssertionError, match="missing governance symbol"):
        _assert_repo_locator("tools/diagnostics/docs_policy.py::missing_symbol")
