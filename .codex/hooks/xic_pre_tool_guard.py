# ruff: noqa: E501
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xic_hook_policy import (  # noqa: E402
    commit_tail_uses_autostage_or_pathspec,
    is_shell_command_event,
)

from tools.command_policy import (  # noqa: E402
    GitPolicyPrefix,
    analyze_command,
)

SHELL_SEPARATOR_CHARS = ";&|\n"
RAW_LAUNCH_SIGNAL = re.compile(
    r"(?:85\s*-?\s*RAW|\.raw\b|RAW-backed|--preset\b|run_alignment\b|"
    r"--raw-dir\b|XIC_RAW_ROOT\b|THERMO_RAWFILE_READER)",
    re.IGNORECASE,
)
POWERSHELL_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_:]*")
POWERSHELL_ASSIGNMENT = re.compile(
    r"^\s*(?P<name>\$[A-Za-z_][A-Za-z0-9_:]*)\s*=", re.IGNORECASE
)
INERT_TEXT_COMMANDS = {
    "echo",
    "echo.exe",
    "findstr",
    "findstr.exe",
    "grep",
    "grep.exe",
    "printf",
    "select-string",
    "write-host",
    "write-output",
}


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


def get_command(event: dict[str, object]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return ""


def is_complete_apply_patch(event: dict[str, object], command: str) -> bool:
    if str(event.get("tool_name", "")).casefold() != "apply_patch":
        return False
    lines = command.strip().splitlines()
    return (
        len(lines) >= 2
        and lines[0] == "*** Begin Patch"
        and lines[-1] == "*** End Patch"
    )


def executable_name(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].lower()


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


def git_clean_tail_is_forced(tokens: list[str] | tuple[str, ...]) -> bool:
    force = False
    dry_run = False
    for token in tokens:
        if token == "--":
            break
        if token.lower() == "--force":
            force = True
        elif token.lower() == "--dry-run":
            dry_run = True
        elif token.startswith("-") and not token.startswith("--"):
            flags = token[1:]
            force = force or "f" in flags
            dry_run = dry_run or "n" in flags
    return force and not dry_run


def contains_background_process(segment: str) -> bool:
    return (
        re.search(
            r"(?<![A-Za-z0-9_-])Start-Process(?![A-Za-z0-9_-])",
            segment,
            re.IGNORECASE,
        )
        is not None
    )


def is_inert_text_only_command(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens or executable_name(tokens[0]) not in INERT_TEXT_COMMANDS:
        return False

    quote: str | None = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "`":
            # POSIX shells execute backtick substitutions; PowerShell uses the
            # same character for escaping. Neither shape is provably inert.
            return False
        if quote == "'":
            if character == "'":
                quote = None
            continue
        if quote == '"':
            if character == '"':
                quote = None
            elif character == "\\":
                escaped = True
            elif character == "$" and command[index : index + 2] == "$(":
                return False
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in ";&|(){}\r\n":
            return False
        elif character == "$" and command[index : index + 2] == "$(":
            return False
    return quote is None


def has_raw_background_launch(segments: list[str]) -> bool:
    raw_variables: set[str] = set()
    for segment in segments:
        assignment = POWERSHELL_ASSIGNMENT.match(segment)
        if assignment is not None:
            name = assignment.group("name").lower()
            value = segment[assignment.end() :]
            referenced = {item.lower() for item in POWERSHELL_VARIABLE.findall(value)}
            if RAW_LAUNCH_SIGNAL.search(value) or referenced & raw_variables:
                raw_variables.add(name)
            else:
                raw_variables.discard(name)
        if not contains_background_process(segment):
            continue
        if RAW_LAUNCH_SIGNAL.search(segment):
            return True
        referenced = {name.lower() for name in POWERSHELL_VARIABLE.findall(segment)}
        if referenced & raw_variables:
            return True
    return False


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    command = get_command(event)
    if not command:
        return 0

    policy_command = "" if is_complete_apply_patch(event, command) else command
    command_analysis = analyze_command(policy_command)
    if command_analysis.requires_rewrite:
        deny(command_analysis.rewrite_reason)
        return 0

    effective_segments = shell_command_segments(policy_command)
    if not is_inert_text_only_command(policy_command) and has_raw_background_launch(
        effective_segments
    ):
        deny(
            "Background RAW launch through Start-Process is blocked. Use the documented foreground heartbeat/timing command shape, or get explicit external-terminal approval."
        )
        return 0

    invocation = command_analysis.git_invocation
    if invocation is not None:
        if invocation.network_prompt_prefix_mismatch:
            deny(
                "Optioned Git network commands are denied because they cannot reach a bounded execpolicy prompt prefix. Run direct `git <network-subcommand> ...`; use direct `git -c safe.directory=<repo> <network-subcommand> ...` only for invocation-scoped ownership recovery."
            )
            return 0
        if invocation.subcommand == "reset" and "--hard" in invocation.tail:
            if (
                invocation.policy_prefix is not GitPolicyPrefix.SUBCOMMAND
                or invocation.tail[:1] != ("--hard",)
            ):
                deny(
                    "git reset --hard is blocked. Do not discard user work; inspect the diff and ask for explicit rollback approval."
                )
                return 0
        elif invocation.subcommand == "checkout" and "--" in invocation.tail:
            if (
                invocation.policy_prefix is not GitPolicyPrefix.SUBCOMMAND
                or invocation.tail[:1] != ("--",)
            ):
                deny(
                    "git checkout -- is blocked because it reverts files. Use targeted review and explicit user approval."
                )
                return 0
        elif invocation.subcommand == "clean" and git_clean_tail_is_forced(
            invocation.tail
        ):
            if invocation.policy_prefix is GitPolicyPrefix.UNSUPPORTED:
                deny(
                    "Optioned Git cleanup is denied because it cannot reach a bounded execpolicy prompt prefix. Run one direct `git clean ...` command."
                )
                return 0
    if (
        is_shell_command_event(str(event.get("tool_name", "")), policy_command)
        and invocation is not None
        and invocation.subcommand == "commit"
    ):
        if commit_tail_uses_autostage_or_pathspec(invocation.tail):
            deny(
                "git commit with -a/--all or pathspec is blocked because it can sweep unrelated changes into the commit. Stage explicit paths, review the staged diff, then commit."
            )
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
