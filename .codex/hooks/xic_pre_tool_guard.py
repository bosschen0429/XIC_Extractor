# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys

from xic_hook_policy import (
    PRODUCT_SURFACE_PATHS,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    PRODUCTIZATION_STATUS_INDEX_REL,
    git_commit_uses_autostage_or_pathspec,
    is_git_commit_command,
    is_shell_command_event,
    mentions_any_path,
    run_docs_placement_guard,
)

WRAPPED_DESTRUCTIVE_COMMANDS = [
    (
        re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
        "git reset --hard is blocked. Do not discard user work; inspect the diff and ask for explicit rollback approval.",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--(?:\s|$)", re.IGNORECASE),
        "git checkout -- is blocked because it reverts files. Use targeted review and explicit user approval.",
    ),
]

SHELL_WRAPPER = re.compile(
    r"^\s*(?:bash|sh|pwsh|powershell)(?:\.exe)?\s+(?:-c|-lc|-Command)(?:\s|$)",
    re.IGNORECASE,
)

RAW_BACKGROUND = re.compile(
    r"\bStart-Process\b.*(?:85\s*-?\s*RAW|\.raw\b|RAW-backed|preset)", re.IGNORECASE
)
EXEC_CONFIG_PATHS = [
    "AGENTS.md",
    ".codex/hooks.json",
    ".codex/rules/",
    ".codex/hooks/",
    ".codex/agents/",
    "docs/agent/",
]

WRITE_COMMAND = re.compile(
    r"\b(apply_patch|Set-Content|Add-Content|Out-File|New-Item|Move-Item|Remove-Item|Copy-Item|"
    r"python\s+-c|python\s+-m|uv\s+run\s+python|ruff\s+check\s+--fix)\b",
    re.IGNORECASE,
)


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def deny(reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def context(message: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": message,
            }
        }
    )


def guard_output_text(result: object) -> str:
    stdout = getattr(result, "stdout", "").strip()
    stderr = getattr(result, "stderr", "").strip()
    return "\n".join(part for part in (stdout, stderr) if part)


def get_command(event: dict[str, object]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return ""


def text_from_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(text_from_value(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(text_from_value(item) for item in value)
    return ""


def tool_payload_text(event: dict[str, object]) -> str:
    return text_from_value(event.get("tool_input"))


def is_write_like(event: dict[str, object], command: str, payload: str) -> bool:
    tool_name = str(event.get("tool_name", "")).lower()
    if any(name in tool_name for name in ("apply_patch", "edit", "write")):
        return True
    combined = f"{command}\n{payload}"
    return bool(WRITE_COMMAND.search(combined))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    command = get_command(event)
    payload = tool_payload_text(event)
    if not command and not payload:
        return 0

    if SHELL_WRAPPER.search(command):
        for pattern, reason in WRAPPED_DESTRUCTIVE_COMMANDS:
            if pattern.search(command):
                deny(reason)
                return 0

    if RAW_BACKGROUND.search(command):
        deny(
            "Background RAW launch through Start-Process is blocked. Use the documented foreground heartbeat/timing command shape, or get explicit external-terminal approval."
        )
        return 0

    if is_shell_command_event(str(event.get("tool_name", "")), command) and (
        is_git_commit_command(command)
    ):
        if git_commit_uses_autostage_or_pathspec(command):
            deny(
                "git commit with -a/--all or pathspec is blocked because it can bypass the staged docs placement guard. Run explicit git add first, review the staged diff, then commit."
            )
            return 0
        cwd = str(event.get("cwd", "."))
        guard_result = run_docs_placement_guard(cwd)
        if guard_result.returncode != 0:
            details = guard_output_text(guard_result)
            reason = "Docs placement guard failed before git commit."
            if details:
                reason = f"{reason}\n{details}"
            deny(reason)
            return 0

    normalized = f"{command}\n{payload}".replace("\\", "/")
    if any(path.replace("\\", "/") in normalized for path in EXEC_CONFIG_PATHS):
        context(
            "Execution-affecting XIC agent config may be edited. After the change, run hook/script smoke checks, git diff --check, and a secret/local-path scan before closeout."
        )

    combined_text = f"{command}\n{payload}"
    if is_write_like(event, command, payload) and mentions_any_path(
        combined_text, PRODUCT_SURFACE_PATHS
    ):
        context(
            "Product/public surface may be edited. If this changes a maturity tier, output schema, "
            "review/replay behavior, selected area/counting, or matrix authority, consult the "
            "productization control plane compatibility index at "
            "docs/superpowers/plans/2026-06-15-productization-control-plane.md; update mutable lane "
            f"state in {PRODUCTIZATION_STATUS_INDEX_REL}, writer authority in "
            f"{PRODUCTIZATION_AUTHORITY_MANIFEST_REL}, and readiness/promotion rules in "
            "docs/agent/product-validation-contract.md as applicable. Change the compatibility index only "
            "when a stable lane key or owner route changes. If none changes, say so explicitly."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
