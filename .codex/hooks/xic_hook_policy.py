from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics import docs_policy as _doc_policy  # noqa: E402

CLOSEOUT_DIR = _doc_policy.CLOSEOUT_DIR
CONTROL_PLANE_PATH = _doc_policy.CONTROL_PLANE_PATH
DEFAULT_LOCAL_ACTIVE_HANDOFF_PATH = _doc_policy.DEFAULT_LOCAL_ACTIVE_HANDOFF_PATH
DOC_CANONICAL_OWNER_DIRS = _doc_policy.DOC_CANONICAL_OWNER_DIRS
DOC_CANONICAL_OWNER_FILES = _doc_policy.DOC_CANONICAL_OWNER_FILES
DOC_EXIT_RULE_MARKER = _doc_policy.DOC_EXIT_RULE_MARKER
DOC_KIND_MARKER = _doc_policy.DOC_KIND_MARKER
DOC_KIND_VALUES = _doc_policy.DOC_KIND_VALUES
DOC_LIFECYCLES_REQUIRING_EXIT_RULE = _doc_policy.DOC_LIFECYCLES_REQUIRING_EXIT_RULE
DOC_LIFECYCLE_MARKER = _doc_policy.DOC_LIFECYCLE_MARKER
DOC_LIFECYCLE_VALUES = _doc_policy.DOC_LIFECYCLE_VALUES
DOC_PLACEMENTS_REQUIRING_REPO_OWNER = (
    _doc_policy.DOC_PLACEMENTS_REQUIRING_REPO_OWNER
)
DOC_PLACEMENT_MARKER = _doc_policy.DOC_PLACEMENT_MARKER
DOC_PLACEMENT_VALUES = _doc_policy.DOC_PLACEMENT_VALUES
DOC_REPO_OWNER_MARKER = _doc_policy.DOC_REPO_OWNER_MARKER
DOC_ROUTING_GOVERNANCE_PREFIXES = _doc_policy.DOC_ROUTING_GOVERNANCE_PREFIXES
DOC_ROUTING_HANDOFF_PREFIX = _doc_policy.DOC_ROUTING_HANDOFF_PREFIX
DOC_ROUTING_LEGACY_HISTORY_PREFIXES = _doc_policy.DOC_ROUTING_LEGACY_HISTORY_PREFIXES
DOC_ROUTING_MECHANICAL_REFERRER_PREFIXES = (
    _doc_policy.DOC_ROUTING_MECHANICAL_REFERRER_PREFIXES
)
DOC_ROUTING_SCAN_PREFIXES = _doc_policy.DOC_ROUTING_SCAN_PREFIXES
DOC_ROUTING_AUTHORITY_REFERRER_PATHS = (
    _doc_policy.DOC_ROUTING_AUTHORITY_REFERRER_PATHS
)
DOC_ROUTING_SPECS_INDEX_PATH = _doc_policy.DOC_ROUTING_SPECS_INDEX_PATH
DOC_ROUTING_TOPIC_PREFIX = _doc_policy.DOC_ROUTING_TOPIC_PREFIX
DOC_ROUTING_VALIDATION_PREFIXES = _doc_policy.DOC_ROUTING_VALIDATION_PREFIXES
HANDOFF_ARCHIVE_DIR = _doc_policy.HANDOFF_ARCHIVE_DIR
HANDOFF_CURRENT_DIR = _doc_policy.HANDOFF_CURRENT_DIR
HIGH_RISK_DOC_DIRS = _doc_policy.HIGH_RISK_DOC_DIRS
MECHANICAL_ADJUDICATION_INDEX_REL = _doc_policy.MECHANICAL_ADJUDICATION_INDEX_REL
MISPLACED_HANDOFF_PUBLIC_RECORD_PATTERNS = (
    _doc_policy.MISPLACED_HANDOFF_PUBLIC_RECORD_PATTERNS
)
NON_REPO_DOC_PLACEMENTS = _doc_policy.NON_REPO_DOC_PLACEMENTS
PRIVATE_HISTORY_SIGNALS = _doc_policy.PRIVATE_HISTORY_SIGNALS
PRODUCTIZATION_AUTHORITY_MANIFEST_REL = (
    _doc_policy.PRODUCTIZATION_AUTHORITY_MANIFEST_REL
)
PRODUCTIZATION_SCHEMA_REL = _doc_policy.PRODUCTIZATION_SCHEMA_REL
PRODUCTIZATION_STATUS_ANCHOR_PATH = _doc_policy.PRODUCTIZATION_STATUS_ANCHOR_PATH
PRODUCTIZATION_STATUS_HANDOFF_PATH = _doc_policy.PRODUCTIZATION_STATUS_HANDOFF_PATH
PRODUCTIZATION_STATUS_INDEX_REL = _doc_policy.PRODUCTIZATION_STATUS_INDEX_REL
classify_doc = _doc_policy.classify_doc
classify_doc_path = _doc_policy.classify_doc_path
doc_exit_rule_value = _doc_policy.doc_exit_rule_value
doc_kind_value = _doc_policy.doc_kind_value
doc_lifecycle_requires_exit_rule = _doc_policy.doc_lifecycle_requires_exit_rule
doc_lifecycle_value = _doc_policy.doc_lifecycle_value
doc_placement_is_non_repo = _doc_policy.doc_placement_is_non_repo
doc_placement_requires_repo_owner = _doc_policy.doc_placement_requires_repo_owner
doc_placement_value = _doc_policy.doc_placement_value
has_private_history_signal = _doc_policy.has_private_history_signal
infer_doc_kind_from_path = _doc_policy.infer_doc_kind_from_path
is_branch_closeout_summary_path = _doc_policy.is_branch_closeout_summary_path
is_canonical_doc_owner_path = _doc_policy.is_canonical_doc_owner_path
is_high_risk_repo_doc_path = _doc_policy.is_high_risk_repo_doc_path
is_lifecycle_managed_doc_path = _doc_policy.is_lifecycle_managed_doc_path
is_markdown_path = _doc_policy.is_markdown_path
is_misplaced_handoff_public_record_path = (
    _doc_policy.is_misplaced_handoff_public_record_path
)
is_public_handoff_archive_evidence_path = (
    _doc_policy.is_public_handoff_archive_evidence_path
)
is_repo_doc_path = _doc_policy.is_repo_doc_path
marker_value = _doc_policy.marker_value
normalize_path_text = _doc_policy.normalize_path_text
path_is_under = _doc_policy.path_is_under
repo_owner_value = _doc_policy.repo_owner_value

HANDOFF_MAX_LINES = 200

PRODUCT_SURFACE_PATHS = [
    "README.md",
    "AGENTS.md",
    "docs/architecture-contract.md",
    "docs/product/",
    "docs/user/",
    "docs/agent/product-validation-contract.md",
    "docs/agent/architecture-public-contracts.md",
    "docs/superpowers/schemas/",
    "docs/superpowers/plans/",
    "docs/superpowers/validation/productization_status_index_v1.tsv",
    "xic_extractor/extractor.py",
    "xic_extractor/signal_processing.py",
    "xic_extractor/output/",
    "xic_extractor/alignment/",
    "xic_extractor/extraction/",
    "xic_extractor/peak_detection/",
    "xic_extractor/configuration/",
    "xic_extractor/settings_schema.py",
    "scripts/",
    "gui/",
]
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


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def path_match_text(text: str) -> str:
    normalized = normalize_path_text(text)
    return normalized.replace("../", "").replace("./", "")


def mentions_path(text: str, path: str) -> bool:
    normalized_path = normalize_path_text(path).lstrip("./")
    return normalized_path in path_match_text(text)


def mentions_any_path(text: str, paths: list[str]) -> bool:
    return any(mentions_path(text, path) for path in paths)


def touches_product_surface(paths: list[str]) -> bool:
    return any(
        path_is_under(path, root)
        for path in paths
        for root in PRODUCT_SURFACE_PATHS
    )


def touches_control_plane(paths: list[str]) -> bool:
    return any(path_is_under(path, CONTROL_PLANE_PATH) for path in paths)


def touches_handoff(paths: list[str]) -> bool:
    return any(path_is_under(path, HANDOFF_CURRENT_DIR) for path in paths)


def is_productization_status_handoff(path: str) -> bool:
    return path_is_under(path, PRODUCTIZATION_STATUS_HANDOFF_PATH)


def branch_slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", branch.strip().lower()).strip("-")


def touched_handoff_paths(paths: list[str]) -> list[str]:
    return [
        normalize_path_text(path).lstrip("./")
        for path in paths
        if path_is_under(path, HANDOFF_CURRENT_DIR)
    ]


def is_git_add_command(command: str) -> bool:
    return bool(git_subcommand_tails(command, "add"))


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


def repo_root_from_cwd(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or ".",
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return cwd or "."
    if result.returncode != 0:
        return cwd or "."
    return result.stdout.strip() or cwd or "."


def run_docs_placement_guard(cwd: str) -> CommandResult:
    override = os.environ.get("XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE")
    if override is not None:
        try:
            returncode = int(override)
        except ValueError:
            returncode = 1
        return CommandResult(
            returncode=returncode,
            stdout=os.environ.get("XIC_DOCS_PLACEMENT_GUARD_STDOUT", ""),
            stderr=os.environ.get("XIC_DOCS_PLACEMENT_GUARD_STDERR", ""),
        )

    root = repo_root_from_cwd(cwd)
    script = os.path.join(root, "tools", "diagnostics", "docs_placement_guard.py")
    try:
        result = subprocess.run(
            [sys.executable, script, "--staged"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(
            returncode=1,
            stdout="",
            stderr=f"docs placement guard could not run: {exc}",
        )
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
