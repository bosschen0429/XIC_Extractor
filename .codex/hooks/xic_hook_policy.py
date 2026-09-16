from __future__ import annotations

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
    if not token.startswith("-") or token.startswith("--"):
        return False
    for option in token[1:]:
        if option == "a":
            return True
        # The rest of a short value-taking option is data, not more flags.
        if option in {"m", "F", "C", "c", "t", "S", "u"}:
            break
    return False


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
