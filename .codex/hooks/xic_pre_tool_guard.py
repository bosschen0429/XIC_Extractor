# ruff: noqa: E501
from __future__ import annotations

import json
import re
import shlex
import sys

from xic_hook_policy import (
    PRODUCT_SURFACE_PATHS,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    PRODUCTIZATION_STATUS_INDEX_REL,
    git_commit_uses_autostage_or_pathspec,
    is_git_commit_command,
    is_git_executable,
    is_shell_command_event,
    is_shell_command_flag,
    mentions_any_path,
    run_docs_placement_guard,
    skip_git_global_options,
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
WRAPPED_CLEAN_REASON = (
    "Wrapped forced cleanup is denied because PreToolUse cannot request "
    "interactive approval."
)

POSIX_SHELLS = {"bash", "sh"}
POWERSHELLS = {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}
CMD_SHELLS = {"cmd", "cmd.exe"}
SHELL_CONTROL_PREFIXES = {
    "if",
    "while",
    "until",
    "then",
    "do",
    "else",
    "{",
    "(",
    "!",
    "=",
}
SHELL_SEPARATOR_CHARS = ";&|\n"
MAX_WRAPPER_DEPTH = 3

RAW_LAUNCH_SIGNAL = re.compile(
    r"(?:85\s*-?\s*RAW|\.raw\b|RAW-backed|--preset\b|run_alignment\b|"
    r"--raw-dir\b|XIC_RAW_ROOT\b|THERMO_RAWFILE_READER)",
    re.IGNORECASE,
)
POWERSHELL_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_:]*")
POWERSHELL_ASSIGNMENT = re.compile(
    r"^\s*(?P<name>\$[A-Za-z_][A-Za-z0-9_:]*)\s*=", re.IGNORECASE
)
POSIX_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_REDIRECTION = re.compile(r"^(?:\d*)?(?:>>?|<<?|<>|>&|<&)(?P<target>.*)$")
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


def executable_name(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].lower()


def wrapped_shell_payload(command: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if tokens and tokens[0] == "&":
        tokens = tokens[1:]
    if tokens and executable_name(tokens[0]) == "env":
        shell_index = next(
            (
                index
                for index, token in enumerate(tokens[1:], start=1)
                if executable_name(token) in POSIX_SHELLS | POWERSHELLS | CMD_SHELLS
            ),
            None,
        )
        if shell_index is None:
            return None
        tokens = tokens[shell_index:]
    if not tokens:
        return None

    shell = executable_name(tokens[0])
    if shell not in POSIX_SHELLS | POWERSHELLS | CMD_SHELLS:
        return None

    for index, token in enumerate(tokens[1:], start=1):
        if is_shell_command_flag(tokens[0], token.lower()) and index + 1 < len(tokens):
            return " ".join(tokens[index + 1 :])
    return None


def shell_command_segments(payload: str) -> list[str]:
    try:
        lexer = shlex.shlex(
            payload, posix=True, punctuation_chars=SHELL_SEPARATOR_CHARS
        )
        # Keep unquoted newlines as command separators. Quoted newlines remain
        # part of their token and therefore cannot manufacture a command.
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token and all(char in SHELL_SEPARATOR_CHARS for char in token):
            if current:
                segments.append(" ".join(current))
                current = []
            continue
        current.append(token)
    if current:
        segments.append(" ".join(current))
    return segments


def strip_shell_control_prefixes(segment: str) -> str:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return segment
    while tokens and tokens[0].lower() in SHELL_CONTROL_PREFIXES:
        tokens = tokens[1:]
    return " ".join(tokens)


def normalize_wrapped_git_invocation(segment: str) -> str:
    try:
        tokens = shlex.split(strip_shell_control_prefixes(segment), posix=True)
    except ValueError:
        return segment
    while tokens:
        if POSIX_ASSIGNMENT.match(tokens[0]):
            tokens = tokens[1:]
            continue
        redirection = SHELL_REDIRECTION.match(tokens[0])
        if redirection is None:
            break
        tokens = tokens[1:]
        if not redirection.group("target") and tokens:
            tokens = tokens[1:]
    if tokens and tokens[0].lower() == "command":
        if any(token.lower() in {"-v", "-V"} for token in tokens[1:2]):
            return " ".join(tokens)
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            tokens = tokens[1:]
    if tokens and tokens[0].lower() == "exec":
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            option = tokens.pop(0)
            if option == "-a" and tokens:
                tokens.pop(0)
    if not tokens or not is_git_executable(tokens[0]):
        return " ".join(tokens)
    subcommand_index = skip_git_global_options(tokens, 1)
    return " ".join(("git", *tokens[subcommand_index:]))


def wrapped_git_clean_is_forced(segment: str) -> bool:
    try:
        tokens = shlex.split(normalize_wrapped_git_invocation(segment), posix=True)
    except ValueError:
        return False
    if len(tokens) < 3:
        return False
    if executable_name(tokens[0]) not in {"git", "git.exe"}:
        return False
    if tokens[1].lower() != "clean":
        return False

    for token in tokens[2:]:
        if token == "--":
            return False
        if token.lower() == "--force":
            return True
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return True
    return False


def contains_background_process(segment: str) -> bool:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if executable_name(token) != "start-process":
            continue
        if index == 0 or tokens[index - 1].lower() in SHELL_CONTROL_PREFIXES:
            return True
    return False


def has_raw_background_launch(segments: list[str]) -> bool:
    assignments = {
        match.group("name").lower(): segment[match.end() :]
        for segment in segments
        if (match := POWERSHELL_ASSIGNMENT.match(segment)) is not None
    }
    raw_assignments = {
        name for name, value in assignments.items() if RAW_LAUNCH_SIGNAL.search(value)
    }
    changed = True
    while changed:
        changed = False
        for name, value in assignments.items():
            referenced = {item.lower() for item in POWERSHELL_VARIABLE.findall(value)}
            if name not in raw_assignments and referenced & raw_assignments:
                raw_assignments.add(name)
                changed = True
    for segment in segments:
        if not contains_background_process(segment):
            continue
        if RAW_LAUNCH_SIGNAL.search(segment):
            return True
        referenced = {name.lower() for name in POWERSHELL_VARIABLE.findall(segment)}
        if referenced & raw_assignments:
            return True
    return False


def wrapped_command_segments(command: str, *, depth: int = 0) -> list[str]:
    payload = wrapped_shell_payload(command)
    if payload is None:
        return []

    segments = shell_command_segments(payload)
    if depth >= MAX_WRAPPER_DEPTH:
        return segments

    expanded = list(segments)
    for segment in segments:
        expanded.extend(wrapped_command_segments(segment, depth=depth + 1))
    return expanded


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

    wrapped_payload = wrapped_shell_payload(command)
    if wrapped_payload is not None:
        for segment in wrapped_command_segments(command):
            command_segment = normalize_wrapped_git_invocation(segment)
            for pattern, reason in WRAPPED_DESTRUCTIVE_COMMANDS:
                if pattern.match(command_segment):
                    deny(reason)
                    return 0
            if wrapped_git_clean_is_forced(segment):
                deny(WRAPPED_CLEAN_REASON)
                return 0

    effective_segments = (
        wrapped_command_segments(command)
        if wrapped_payload is not None
        else shell_command_segments(command)
    )
    if has_raw_background_launch(effective_segments):
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

    advisory_messages: list[str] = []
    normalized = f"{command}\n{payload}".replace("\\", "/")
    if any(path.replace("\\", "/") in normalized for path in EXEC_CONFIG_PATHS):
        advisory_messages.append(
            "Execution-affecting XIC agent config may be edited. After the change, run hook/script smoke checks, git diff --check, and a secret/local-path scan before closeout."
        )

    combined_text = f"{command}\n{payload}"
    if is_write_like(event, command, payload) and mentions_any_path(
        combined_text, PRODUCT_SURFACE_PATHS
    ):
        advisory_messages.append(
            "Product/public surface may be edited. If this changes a maturity tier, output schema, "
            "review/replay behavior, selected area/counting, or matrix authority, consult the "
            "productization control plane compatibility index at "
            "docs/superpowers/plans/2026-06-15-productization-control-plane.md; update mutable lane "
            f"state in {PRODUCTIZATION_STATUS_INDEX_REL}, writer authority in "
            f"{PRODUCTIZATION_AUTHORITY_MANIFEST_REL}, and readiness/promotion rules in "
            "docs/agent/product-validation-contract.md as applicable. Change the compatibility index only "
            "when a stable lane key or owner route changes. If none changes, say so explicitly."
        )

    if advisory_messages:
        context("\n\n".join(advisory_messages))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
