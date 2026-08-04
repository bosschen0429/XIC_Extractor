from __future__ import annotations

import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics import docs_policy as _doc_policy  # noqa: E402

CONTROL_PLANE_PATH = _doc_policy.CONTROL_PLANE_PATH
HANDOFF_CURRENT_DIR = _doc_policy.HANDOFF_CURRENT_DIR
PRODUCTIZATION_AUTHORITY_MANIFEST_REL = (
    _doc_policy.PRODUCTIZATION_AUTHORITY_MANIFEST_REL
)
PRODUCTIZATION_STATUS_INDEX_REL = _doc_policy.PRODUCTIZATION_STATUS_INDEX_REL
normalize_path_text = _doc_policy.normalize_path_text
GIT_GLOBAL_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
GIT_COMMIT_OPTIONS_WITH_VALUE = {
    "-m",
    "-F",
    "-C",
    "-c",
    "--message",
    "--file",
    "--reuse-message",
    "--reedit-message",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--template",
    "--trailer",
}
SHELL_COMMAND_FLAGS = {"-c", "-command", "/c"}
POSIX_SHELL_EXECUTABLES = {"bash", "bash.exe", "sh", "sh.exe"}
POWERSHELL_EXECUTABLES = {
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}
CMD_EXECUTABLES = {"cmd", "cmd.exe"}


def is_git_commit_command(command: str) -> bool:
    return bool(git_subcommand_tails(command, "commit"))


def git_commit_tail(command: str) -> str:
    tails = git_subcommand_tails(command, "commit")
    if not tails:
        return ""
    return " ".join(tails[0])


def git_commit_uses_autostage_or_pathspec(command: str) -> bool:
    return any(
        commit_tail_uses_autostage_or_pathspec(tail)
        for tail in git_subcommand_tails(command, "commit")
    )


def split_shell_words(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        try:
            return shlex.split(command, posix=True)
        except ValueError:
            return command.split()


def clean_shell_token(token: str) -> str:
    return token.strip().strip("'\"").strip()


def command_word(token: str) -> str:
    return clean_shell_token(token).strip("&;|()")


def token_basename(token: str) -> str:
    cleaned = normalize_path_text(command_word(token))
    return cleaned.rsplit("/", 1)[-1].lower()


def is_shell_separator(token: str) -> bool:
    cleaned = clean_shell_token(token)
    return bool(cleaned) and all(char in ";&|()" for char in cleaned)


def is_git_executable(token: str) -> bool:
    return token_basename(token) in {"git", "git.exe"}


def is_shell_executable(token: str) -> bool:
    return token_basename(token) in {
        "bash",
        "bash.exe",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "sh.exe",
    }


def is_shell_command_flag(shell_token: str, option: str) -> bool:
    shell = token_basename(shell_token)
    option = option.lower()
    if shell in POSIX_SHELL_EXECUTABLES:
        return option == "-c" or (
            option.startswith("-")
            and not option.startswith("--")
            and "c" in option[1:]
        )
    if shell in POWERSHELL_EXECUTABLES:
        return option in {"-c", "-command"}
    if shell in CMD_EXECUTABLES:
        return option == "/c"
    return option in SHELL_COMMAND_FLAGS


def skip_git_global_options(tokens: list[str], index: int) -> int:
    while index < len(tokens):
        token = command_word(tokens[index])
        if not token or is_shell_separator(tokens[index]):
            return index
        option = token.split("=", 1)[0]
        if option in GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 1 if "=" in token else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return index


def command_tail_until_separator(tokens: list[str], index: int) -> list[str]:
    tail: list[str] = []
    while index < len(tokens) and not is_shell_separator(tokens[index]):
        tail.append(command_word(tokens[index]))
        index += 1
    return tail


def next_command_start(tokens: list[str], index: int) -> int:
    while index < len(tokens) and not is_shell_separator(tokens[index]):
        index += 1
    return index


def nested_shell_commands(tokens: list[str]) -> list[str]:
    commands: list[str] = []
    for index, token in enumerate(tokens):
        if not is_shell_executable(token):
            continue
        for option_index in range(index + 1, len(tokens)):
            option = command_word(tokens[option_index]).lower()
            if (
                is_shell_command_flag(token, option)
                and option_index + 1 < len(tokens)
            ):
                commands.append(" ".join(tokens[option_index + 1 :]))
                break
    return commands


def git_subcommand_tails(
    command: str,
    subcommand: str,
    *,
    depth: int = 0,
) -> list[list[str]]:
    if depth > 3:
        return []
    tokens = split_shell_words(command)
    tails: list[list[str]] = []
    index = 0
    at_command_start = True
    subcommand_lower = subcommand.lower()
    while index < len(tokens):
        token = command_word(tokens[index])
        if not token:
            index += 1
            continue
        if is_shell_separator(tokens[index]):
            at_command_start = True
            index += 1
            continue
        if at_command_start and is_git_executable(token):
            subcommand_index = skip_git_global_options(tokens, index + 1)
            if (
                subcommand_index < len(tokens)
                and command_word(tokens[subcommand_index]).lower() == subcommand_lower
            ):
                tails.append(command_tail_until_separator(tokens, subcommand_index + 1))
            index = next_command_start(tokens, index + 1)
            continue
        at_command_start = False
        index += 1

    for nested in nested_shell_commands(tokens):
        if nested and nested != command:
            tails.extend(git_subcommand_tails(nested, subcommand, depth=depth + 1))
    return tails


def short_option_uses_autostage(token: str) -> bool:
    return token.startswith("-") and not token.startswith("--") and "a" in token[1:]


def commit_tail_uses_autostage_or_pathspec(tokens: list[str]) -> bool:
    index = 0
    after_separator = False
    while index < len(tokens):
        token = command_word(tokens[index])
        if not token:
            index += 1
            continue
        if after_separator:
            return True
        if token == "--":
            after_separator = True
            index += 1
            continue
        option = token.split("=", 1)[0]
        if token == "--all" or token.startswith("--all="):
            return True
        if option == "--pathspec-from-file":
            return True
        if short_option_uses_autostage(token):
            return True
        if token.startswith("-"):
            if option in GIT_COMMIT_OPTIONS_WITH_VALUE and "=" not in token:
                index += 2
            else:
                index += 1
            continue
        return True
    return False


def is_shell_command_event(tool_name: str, command: str) -> bool:
    if not command.strip():
        return False
    lowered = tool_name.lower()
    if any(name in lowered for name in ("apply_patch", "edit", "write")):
        return False
    return True
