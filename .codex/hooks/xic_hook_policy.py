from __future__ import annotations

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


def short_option_uses_autostage(token: str) -> bool:
    return token.startswith("-") and not token.startswith("--") and "a" in token[1:]


def commit_tail_uses_autostage_or_pathspec(tokens: tuple[str, ...]) -> bool:
    index = 0
    after_separator = False
    while index < len(tokens):
        token = tokens[index]
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
