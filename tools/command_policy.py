from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
)
GIT_GLOBAL_FLAGS = frozenset(
    {
        "-h",
        "-p",
        "-P",
        "--bare",
        "--glob-pathspecs",
        "--help",
        "--html-path",
        "--icase-pathspecs",
        "--info-path",
        "--literal-pathspecs",
        "--man-path",
        "--no-lazy-fetch",
        "--no-optional-locks",
        "--no-pager",
        "--no-replace-objects",
        "--noglob-pathspecs",
        "--paginate",
        "--version",
    }
)

AMBIGUOUS_GIT_CODE = "ambiguous_git_command"
AMBIGUOUS_GIT_REASON = (
    "Git-bearing shell syntax is outside the supported command-policy grammar. "
    "Use one single direct Git command without a compound block, assignment, "
    "path-qualified executable, command expansion, redirection, or unsupported "
    "global option. If Git text is data, use a supported top-level search/output "
    "command."
)
UNSUPPORTED_WRAPPER_CODE = "unsupported_shell_wrapper"
UNSUPPORTED_WRAPPER_REASON = (
    "Explicit nested shell and command wrappers are outside the repository "
    "command-policy grammar. Run the payload directly; Git operations must be "
    "one single direct Git command."
)

_GIT_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])git(?:\.(?:exe|cmd|bat|ps1|com|sh))?"
    r"(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)
_SHELL_CONTROL_CHARS = frozenset(";&|(){}<>\r\n")
_COMMAND_EXPANSION = re.compile(r"\$\(|`")
_POSIX_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_EXECUTABLE_ALIASES = frozenset(
    {"git", "git.exe", "git.cmd", "git.bat", "git.ps1", "git.com", "git.sh"}
)
_EXPLICIT_COMMAND_WRAPPERS = frozenset(
    {
        "bash",
        "bash.exe",
        "cmd",
        "cmd.exe",
        "command",
        "env",
        "exec",
        "iex",
        "invoke-expression",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "sh.exe",
        "wsl",
        "wsl.exe",
    }
)
_WRAPPER_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.:-])(?:bash(?:\.exe)?|cmd(?:\.exe)?|command|env|exec|iex|"
    r"invoke-expression|powershell(?:\.exe)?|pwsh(?:\.exe)?|sh(?:\.exe)?|"
    r"wsl(?:\.exe)?)(?![A-Za-z0-9_.:-])",
    re.IGNORECASE,
)
_INERT_TEXT_CONSUMERS = frozenset(
    {
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
)
_GIT_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "ls-remote", "pull", "push"})


class GitPolicyPrefix(str, Enum):
    SUBCOMMAND = "subcommand"
    INVOCATION_CONFIG = "invocation_config"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class GitInvocation:
    subcommand: str
    tail: tuple[str, ...]
    global_options: tuple[str, ...]

    @property
    def policy_prefix(self) -> GitPolicyPrefix:
        if not self.global_options:
            return GitPolicyPrefix.SUBCOMMAND
        if self.global_options[0] == "-c":
            return GitPolicyPrefix.INVOCATION_CONFIG
        return GitPolicyPrefix.UNSUPPORTED

    @property
    def needs_network(self) -> bool:
        return self.subcommand in _GIT_NETWORK_SUBCOMMANDS

    @property
    def network_prompt_prefix_mismatch(self) -> bool:
        return self.needs_network and self.policy_prefix is GitPolicyPrefix.UNSUPPORTED


@dataclass(frozen=True)
class CommandAnalysis:
    git_invocation: GitInvocation | None = None
    rewrite_code: str = ""
    rewrite_reason: str = ""

    @property
    def requires_rewrite(self) -> bool:
        return bool(self.rewrite_code)


@dataclass(frozen=True)
class _LexicalFacts:
    unquoted_text: str
    has_unquoted_control: bool
    unterminated_quote: bool


def analyze_command(command: str) -> CommandAnalysis:
    """Classify the bounded command shapes owned by repository policy.

    Explicit nested command wrappers always require a rewrite. Outside a
    wrapper, only one canonical lowercase bare ``git`` invocation is parsed.
    Git-bearing compound expressions, assignments, malformed quoting, path-qualified
    executables, executable aliases, and unsupported global options also
    require a rewrite. Quoted Git phrases passed to a simple non-wrapper
    command remain ordinary text only for bounded inert search/output consumers
    and only without shell command-expansion syntax. A wrapper token in the
    bounded outer executable position fails closed; its payload is never
    interpreted.
    """

    stripped = command.strip()
    if not stripped:
        return CommandAnalysis()

    words = _split_outer_words(stripped)
    first_token = words[0] if words else ""
    first_word = _token_basename(first_token)
    if first_word in _EXPLICIT_COMMAND_WRAPPERS:
        return _rewrite(UNSUPPORTED_WRAPPER_CODE, UNSUPPORTED_WRAPPER_REASON)

    facts = _lexical_facts(stripped)
    has_git_reference = _GIT_REFERENCE.search(stripped) is not None
    has_command_expansion = _COMMAND_EXPANSION.search(stripped) is not None
    has_raw_wrapper_reference = _WRAPPER_REFERENCE.search(stripped) is not None
    outer_executables = (
        _outer_executable_token(_split_prefix_words(stripped)),
        _outer_executable_token(
            _split_prefix_words(stripped, preserve_backslashes=True)
        ),
    )
    has_outer_wrapper = any(
        _token_basename(executable) in _EXPLICIT_COMMAND_WRAPPERS
        for executable in outer_executables
    )
    if has_outer_wrapper or (facts.unterminated_quote and has_raw_wrapper_reference):
        return _rewrite(UNSUPPORTED_WRAPPER_CODE, UNSUPPORTED_WRAPPER_REASON)

    git_executable_alias = first_word in _GIT_EXECUTABLE_ALIASES
    direct_git = git_executable_alias and _is_bare_executable(first_token)

    if direct_git:
        if (
            facts.unterminated_quote
            or facts.has_unquoted_control
            or has_command_expansion
            or words is None
        ):
            return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
        assert words
        return _parse_direct_git(words)

    if not has_git_reference:
        return CommandAnalysis()

    unquoted_git = _GIT_REFERENCE.search(facts.unquoted_text) is not None
    if (
        git_executable_alias
        or facts.unterminated_quote
        or facts.has_unquoted_control
        or has_command_expansion
        or unquoted_git
    ):
        return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)

    # Only known inert consumers may treat a quoted Git phrase as data. This is
    # a positive boundary: arbitrary programs and prefixed wrappers do not gain
    # a silent path merely because their Git-bearing payload is quoted.
    if first_word in _INERT_TEXT_CONSUMERS:
        return CommandAnalysis()
    return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)


def _rewrite(code: str, reason: str) -> CommandAnalysis:
    return CommandAnalysis(rewrite_code=code, rewrite_reason=reason)


def _parse_direct_git(words: list[str]) -> CommandAnalysis:
    global_options: list[str] = []
    index = 1
    while index < len(words):
        token = _clean_token(words[index])
        if not token:
            return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
        if not token.startswith("-") or token == "-":
            break

        option, separator, inline_value = token.partition("=")
        if option in GIT_GLOBAL_OPTIONS_WITH_VALUE:
            global_options.append(option)
            if separator:
                if not inline_value:
                    return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
                global_options.append(inline_value)
                index += 1
                continue
            if index + 1 >= len(words):
                return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
            value = _clean_token(words[index + 1])
            if not value:
                return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
            global_options.append(value)
            index += 2
            continue

        if token in GIT_GLOBAL_FLAGS:
            global_options.append(token)
            index += 1
            continue

        return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)

    if index >= len(words):
        return CommandAnalysis(
            git_invocation=GitInvocation(
                subcommand="",
                tail=(),
                global_options=tuple(global_options),
            )
        )

    subcommand = _clean_token(words[index])
    if not subcommand or subcommand.startswith("-") or subcommand != subcommand.lower():
        return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
    tail = tuple(_clean_token(token) for token in words[index + 1 :])
    if any(not token for token in tail):
        return _rewrite(AMBIGUOUS_GIT_CODE, AMBIGUOUS_GIT_REASON)
    return CommandAnalysis(
        git_invocation=GitInvocation(
            subcommand=subcommand,
            tail=tail,
            global_options=tuple(global_options),
        )
    )


def _split_outer_words(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return None


def _split_prefix_words(
    command: str, *, preserve_backslashes: bool = False
) -> list[str] | None:
    try:
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        # A second view preserves Windows paths. The default POSIX view retains
        # escaped-space grouping; either view may prove that the bounded outer
        # executable is an explicit wrapper.
        if preserve_backslashes:
            lexer.escape = ""
        return list(lexer)
    except ValueError:
        return None


def _outer_executable_token(words: list[str] | None) -> str:
    if not words:
        return ""

    index = 0
    if _clean_token(words[index]) == "&":
        index += 1

    while index < len(words):
        token = _clean_token(words[index])
        if _POSIX_ASSIGNMENT_PREFIX.match(token):
            index += 1
            continue
        return words[index]

    return ""


def _clean_token(token: str) -> str:
    stripped = token.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        return stripped[1:-1]
    return stripped


def _token_basename(token: str) -> str:
    cleaned = _clean_token(token).replace("\\", "/")
    return cleaned.rsplit("/", 1)[-1].lower()


def _is_bare_executable(token: str) -> bool:
    return token == "git"


def _lexical_facts(command: str) -> _LexicalFacts:
    quote: str | None = None
    unquoted: list[str] = []
    has_control = False
    escaped = False

    for character in command:
        if escaped:
            unquoted.append(" ")
            escaped = False
            continue
        if character == "`":
            escaped = True
            unquoted.append(" ")
            continue
        if quote is not None:
            if character == quote:
                quote = None
            unquoted.append(" ")
            continue
        if character in {"'", '"'}:
            quote = character
            unquoted.append(" ")
            continue
        if character in _SHELL_CONTROL_CHARS:
            has_control = True
        unquoted.append(character)

    return _LexicalFacts(
        unquoted_text="".join(unquoted),
        has_unquoted_control=has_control,
        unterminated_quote=quote is not None or escaped,
    )
