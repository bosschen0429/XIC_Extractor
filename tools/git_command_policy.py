from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

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
SHELL_COMMAND_FLAGS = {"-c", "-command", "/c"}
POSIX_SHELL_EXECUTABLES = {"bash", "bash.exe", "sh", "sh.exe"}
POWERSHELL_EXECUTABLES = {
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}
CMD_SHELL_EXECUTABLES = {"cmd", "cmd.exe"}
ENV_OPTIONS_WITH_VALUE = {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}
ENV_SPLIT_OPTIONS = {"-S", "--split-string"}
POSIX_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_PUNCTUATION_CHARS = "();<>|&\n"
SHELL_WHITESPACE = " \t\r"


@dataclass(frozen=True)
class GitInvocation:
    executable: str
    subcommand: str
    tail: tuple[str, ...]
    uses_global_options: bool
    global_options: tuple[str, ...]
    uses_wrapper: bool


def split_shell_words(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars=SHELL_PUNCTUATION_CHARS,
        )
        # An unquoted newline is a command boundary, not interchangeable
        # whitespace. Quoted newlines remain embedded in their argument token.
        lexer.whitespace = SHELL_WHITESPACE
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
    cleaned = command_word(token).replace("\\", "/")
    return cleaned.rsplit("/", 1)[-1].lower()


def is_shell_separator(token: str) -> bool:
    if token and all(char in ";&|()\n" for char in token):
        return True
    cleaned = clean_shell_token(token)
    return bool(cleaned) and all(char in ";&|()" for char in cleaned)


def is_git_executable(token: str) -> bool:
    return token_basename(token) in {"git", "git.exe"}


def is_shell_executable(token: str) -> bool:
    return token_basename(token) in (
        POSIX_SHELL_EXECUTABLES | POWERSHELL_EXECUTABLES | CMD_SHELL_EXECUTABLES
    )


def is_shell_command_flag(shell_token: str, option: str) -> bool:
    shell = token_basename(shell_token)
    option = option.lower()
    if shell in POSIX_SHELL_EXECUTABLES:
        return option == "-c" or (
            option.startswith("-") and not option.startswith("--") and "c" in option[1:]
        )
    if shell in POWERSHELL_EXECUTABLES:
        return option in {"-c", "-command"}
    if shell in CMD_SHELL_EXECUTABLES:
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
    for command_start in command_start_indices(tokens):
        shell_index = command_executable_index(
            tokens,
            command_start,
            stop_at_shell=True,
        )
        if shell_index >= len(tokens) or not is_shell_executable(tokens[shell_index]):
            continue
        for option_index in range(shell_index + 1, len(tokens)):
            option = command_word(tokens[option_index]).lower()
            if is_shell_command_flag(
                tokens[shell_index], option
            ) and option_index + 1 < len(tokens):
                commands.append(" ".join(tokens[option_index + 1 :]))
                break
    return commands


def command_start_indices(tokens: list[str]) -> list[int]:
    starts: list[int] = []
    at_command_start = True
    for index, token in enumerate(tokens):
        if is_shell_separator(token):
            at_command_start = True
            continue
        if at_command_start:
            starts.append(index)
            at_command_start = False
    return starts


def nested_env_split_commands(tokens: list[str]) -> list[str]:
    commands: list[str] = []
    for command_start in command_start_indices(tokens):
        env_index = command_executable_index(
            tokens,
            command_start,
            stop_at_env=True,
        )
        if env_index >= len(tokens) or token_basename(tokens[env_index]) != "env":
            continue
        option_index = env_index + 1
        while option_index < len(tokens):
            env_token = command_word(tokens[option_index])
            if not env_token or is_shell_separator(tokens[option_index]):
                break
            if POSIX_ENV_ASSIGNMENT.match(env_token):
                option_index += 1
                continue
            option, separator, inline_value = env_token.partition("=")
            if option in ENV_SPLIT_OPTIONS:
                if separator:
                    payload = inline_value
                    trailing_index = option_index + 1
                elif option_index + 1 < len(tokens):
                    payload = command_word(tokens[option_index + 1])
                    trailing_index = option_index + 2
                else:
                    break
                trailing = command_tail_until_separator(tokens, trailing_index)
                commands.append(" ".join(("env", payload, *trailing)))
                break
            if option in ENV_OPTIONS_WITH_VALUE:
                option_index += 1 if separator else 2
                continue
            if env_token.startswith("-"):
                option_index += 1
                continue
            break
    return commands


def command_executable_index(
    tokens: list[str],
    index: int,
    *,
    stop_at_env: bool = False,
    stop_at_shell: bool = False,
) -> int:
    while index < len(tokens):
        token = command_word(tokens[index])
        if POSIX_ENV_ASSIGNMENT.match(token):
            index += 1
            continue

        executable = token_basename(token)
        if stop_at_shell and is_shell_executable(token):
            return index
        if executable == "env":
            if stop_at_env:
                return index
            index += 1
            while index < len(tokens):
                env_token = command_word(tokens[index])
                if POSIX_ENV_ASSIGNMENT.match(env_token):
                    index += 1
                    continue
                option = env_token.split("=", 1)[0]
                if option in ENV_OPTIONS_WITH_VALUE:
                    index += 1 if "=" in env_token else 2
                    continue
                if env_token.startswith("-"):
                    index += 1
                    continue
                break
            continue
        if executable == "command":
            index += 1
            while index < len(tokens) and command_word(tokens[index]).startswith("-"):
                index += 1
            continue
        if executable == "exec":
            index += 1
            while index < len(tokens) and command_word(tokens[index]).startswith("-"):
                option = command_word(tokens[index])
                index += 1
                if option == "-a" and index < len(tokens):
                    index += 1
            continue
        return index
    return index


def git_invocations(command: str, *, depth: int = 0) -> list[GitInvocation]:
    if depth > 3:
        return []
    tokens = split_shell_words(command)
    invocations: list[GitInvocation] = []
    index = 0
    at_command_start = True
    while index < len(tokens):
        if is_shell_separator(tokens[index]):
            at_command_start = True
            index += 1
            continue
        token = command_word(tokens[index])
        if not token:
            index += 1
            continue
        executable_index = command_executable_index(tokens, index)
        if (
            at_command_start
            and executable_index < len(tokens)
            and is_git_executable(tokens[executable_index])
        ):
            first_argument_index = executable_index + 1
            subcommand_index = skip_git_global_options(tokens, first_argument_index)
            if subcommand_index < len(tokens):
                subcommand = command_word(tokens[subcommand_index]).lower()
                if subcommand and not subcommand.startswith("-"):
                    global_options = tuple(
                        command_word(token)
                        for token in tokens[first_argument_index:subcommand_index]
                    )
                    invocations.append(
                        GitInvocation(
                            executable=command_word(tokens[executable_index]).lower(),
                            subcommand=subcommand,
                            tail=tuple(
                                command_tail_until_separator(
                                    tokens, subcommand_index + 1
                                )
                            ),
                            uses_global_options=bool(global_options),
                            global_options=global_options,
                            uses_wrapper=(depth > 0 or executable_index != index),
                        )
                    )
            index = next_command_start(tokens, first_argument_index)
            continue
        at_command_start = False
        index += 1

    nested_commands = nested_shell_commands(tokens) + nested_env_split_commands(tokens)
    for nested in nested_commands:
        if nested and nested != command:
            invocations.extend(git_invocations(nested, depth=depth + 1))
    return invocations


def git_subcommand_tails(command: str, subcommand: str) -> list[list[str]]:
    subcommand_lower = subcommand.lower()
    return [
        list(invocation.tail)
        for invocation in git_invocations(command)
        if invocation.subcommand == subcommand_lower
    ]
