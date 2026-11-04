"""CI guard: tracked Markdown links resolve and retired docs lanes stay empty.

Relative Markdown links must point at a tracked file or directory, so a moved
or deleted document cannot leave a dangling route. Construction plans, specs,
reports and handoff chronology belong in GitHub Issues, PRs or ignored local
storage; tracked files under those retired lanes are rejected.

Exit code 0 when clean, 1 with one line per problem otherwise.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_DOC_LANES = (
    "docs/deepresearch/",
    "docs/history/",
    "docs/superpowers/closeouts/",
    "docs/superpowers/deepresearch/",
    "docs/superpowers/file-management/",
    "docs/superpowers/goals/",
    "docs/superpowers/handoffs/archive/",
    "docs/superpowers/handoffs/current/",
    "docs/superpowers/notes/",
    "docs/superpowers/plans/",
    "docs/superpowers/productization/",
    "docs/superpowers/pulse-reports/",
    "docs/superpowers/reports/",
    "docs/superpowers/schemas/",
    "docs/superpowers/specs/",
)

_FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def tracked_paths(root: Path) -> tuple[str, ...]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return tuple(path for path in output.decode("utf-8").split("\0") if path)


def markdown_link_targets(text: str) -> tuple[str, ...]:
    text = _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))
    return tuple(match.group(1) for match in _LINK_RE.finditer(text))


def broken_links(
    source: str, text: str, tracked: set[str], directories: set[str]
) -> list[str]:
    problems: list[str] = []
    for target in markdown_link_targets(text):
        if target.startswith("#") or _SCHEME_RE.match(target):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not path:
            continue
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source), path)
        ).rstrip("/")
        if resolved not in tracked and resolved not in directories:
            problems.append(f"{source}: broken link -> {target}")
    return problems


def retired_lane_files(paths: Iterable[str]) -> list[str]:
    return [
        f"{path}: tracked file in retired docs lane"
        for path in paths
        if path.startswith(RETIRED_DOC_LANES)
    ]


def check(root: Path) -> list[str]:
    paths = tracked_paths(root)
    tracked = set(paths)
    directories = {
        path[:index] for path in paths for index, char in enumerate(path) if char == "/"
    }
    problems = retired_lane_files(paths)
    for path in paths:
        if not path.endswith(".md"):
            continue
        source = root / path
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        problems.extend(broken_links(path, text, tracked, directories))
    return problems


def main() -> int:
    problems = check(ROOT)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} repository docs problem(s).")
        return 1
    print("Repository docs links and placement are clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
