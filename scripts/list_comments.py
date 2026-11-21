"""List tracked source comments as JSON lines: path, line and comment."""

import io
import json
import re
import subprocess
import tokenize
from bisect import bisect_right
from collections.abc import Iterator
from functools import cache
from itertools import chain
from pathlib import Path

import yaml
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

LANGUAGES = {
    ".py": "python",
    ".spec": "python",
    ".rules": "python",
    ".js": "javascript",
    ".css": "css",
    ".html": "html",
    ".svg": "xml",
    ".sh": "bash",
    ".ps1": "powershell",
    ".bat": "bat",
    ".cmd": "bat",
    ".vbs": "vbscript",
    ".toml": "toml",
    ".yml": "yaml",
    ".yaml": "yaml",
}
USER_TEMPLATE = "xic_extractor/exclusion_list/example_recipe.toml"
DIRECTIVE = re.compile(
    r"^#\s*(?:noqa(?:\s*:\s*[A-Z]+\d+.*)?|type:\s*.+|pragma:\s*no (?:cover|branch)\b.*"
    r"|fmt:\s*(?:on|off|skip)\b.*|(?:ruff|isort|mypy|pyright|pylint):\s*.+)$",
    re.I,
)
lexer = cache(get_lexer_by_name)


def language(path: str) -> str | None:
    parts = Path(path).parts
    if (
        path == USER_TEMPLATE
        or parts[0] in {"output", "build"}
        or any(part in {"vendor", "third_party", "node_modules"} for part in parts[:-1])
    ):
        return None
    return LANGUAGES.get(Path(path).suffix.lower())


def lex_comments(source: str, lang: str) -> Iterator[tuple[int, str]]:
    if lang == "python":
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type == tokenize.COMMENT:
                yield t.start[0], t.string
        return
    starts = [m.end() for m in re.finditer("\n", source)]
    for offset, kind, value in lexer(lang).get_tokens_unprocessed(source):
        if (
            kind in Comment
            and kind not in Comment.Preproc
            and kind not in Comment.Hashbang
            and value.strip()
        ):
            yield 1 + bisect_right(starts, offset), value.rstrip("\n")


def fields(node: Node | None) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        return {}
    return {k.value: v for k, v in node.value if isinstance(k, ScalarNode)}


def workflow_comments(source: str) -> Iterator[tuple[int, str]]:
    workflow = fields(yaml.compose(source, Loader=yaml.SafeLoader))
    for job in fields(workflow.get("jobs")).values():
        settings = fields(job)
        steps = settings.get("steps")
        if not isinstance(steps, SequenceNode):
            continue
        runner = settings.get("runs-on")
        default = "bash"
        if isinstance(runner, ScalarNode) and runner.value.startswith("windows-"):
            default = "pwsh"
        for owner in (workflow, settings):
            shell = fields(fields(owner.get("defaults")).get("run")).get("shell")
            if isinstance(shell, ScalarNode):
                default = shell.value
        for step in steps.value:
            options = fields(step)
            run = options.get("run")
            if not isinstance(run, ScalarNode):
                continue
            shell = options.get("shell")
            name = (shell.value if isinstance(shell, ScalarNode) else default).split()[
                0
            ]
            lang = {"pwsh": "powershell", "cmd": "bat", "sh": "bash"}.get(name, name)
            start = run.start_mark.line + int(run.style in {"|", ">"})
            for line, value in lex_comments(run.value, lang):
                yield start + line, value


def comments(path: str, source: str) -> Iterator[tuple[int, str]]:
    lang = language(path)
    if lang is None:
        return
    candidates = lex_comments(source, lang)
    if path.startswith(".github/workflows/") and lang == "yaml":
        candidates = chain(candidates, workflow_comments(source))
    for line, value in candidates:
        if lang == "python" and (
            DIRECTIVE.fullmatch(value)
            or (line == 1 and value.startswith("#!"))
            or (line <= 2 and re.match(r"^#[ \t\f]*.*?coding[:=][ \t]*[-\w.]+", value))
        ):
            continue
        if re.match(
            r"(?:#|//|/\*+!?|<!--|')\s*(?:SPDX-License-Identifier:|@license\b"
            r"|Copyright\s+(?:\(c\)\s*)?\d{4})",
            value,
            re.I,
        ):
            continue
        if re.match(
            r"(?://|/\*)\s*(?:@ts-(?:check|nocheck|ignore|expect-error)\b"
            r"|eslint(?:-disable(?:-next-line|-line)?|-enable)?\b"
            r"|stylelint-(?:disable(?:-next-line|-line)?|enable)\b"
            r"|[#@] sourceMappingURL=)",
            value,
        ) or re.match(r"#(?:requires\s+-|\s*(?:shellcheck|yamllint)\s+)", value, re.I):
            continue
        yield line, value


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args]).decode("utf-8-sig")


def main() -> int:
    for path in git("ls-files", "-z").split("\0")[:-1]:
        if language(path) and not Path(path).is_symlink() and Path(path).is_file():
            for line, value in comments(path, Path(path).read_text("utf-8-sig")):
                print(json.dumps({"path": path, "line": line, "comment": value}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
