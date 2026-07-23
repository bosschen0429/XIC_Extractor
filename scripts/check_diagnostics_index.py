"""CI guard: keep tools/diagnostics/INDEX.md in sync with actual entry points.

The architecture contract requires every PR that adds, removes, or renames a
diagnostic CLI entry point to update ``tools/diagnostics/INDEX.md`` in the same
diff. This script enforces that mechanically so the index cannot drift silently.

Checks:

1. The number of ``### `*.py``` entry headings in the index equals the number of
   ``tools/diagnostics/*.py`` files that declare ``if __name__ == "__main__"``.
2. The prose ``Total entry-points:`` count equals that heading count.
3. The prose ``Total files (incl. helpers):`` count equals the number of
   top-level ``tools/diagnostics/*.py`` files.

Exit code 0 when consistent, 1 when the index has drifted (with a message that
names the divergence and how to fix it).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_DIAGNOSTICS_DIR = Path(__file__).resolve().parents[1] / "tools" / "diagnostics"
_INDEX_PATH = _DIAGNOSTICS_DIR / "INDEX.md"

_HEADING_RE = re.compile(r"^### `[^`]+\.py`", re.MULTILINE)
_MAIN_GUARD_RE = re.compile(r"if __name__ == ['\"]__main__['\"]")
_ENTRY_PROSE_RE = re.compile(r"^\*\*Total entry-points:\*\*\s*(\d+)", re.MULTILINE)
_FILES_PROSE_RE = re.compile(
    r"^\*\*Total files \(incl\. helpers\):\*\*\s*(\d+)", re.MULTILINE
)


def _entry_point_files(diagnostics_dir: Path) -> list[str]:
    names: list[str] = []
    for path in sorted(diagnostics_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _MAIN_GUARD_RE.search(text):
            names.append(path.name)
    return names


def _prose_count(pattern: re.Pattern[str], index_text: str, label: str) -> int:
    match = pattern.search(index_text)
    if match is None:
        raise ValueError(f"INDEX.md is missing the '{label}' line")
    return int(match.group(1))


def main() -> int:
    index_text = _INDEX_PATH.read_text(encoding="utf-8")

    heading_names = sorted(
        match.split("`")[1] for match in _HEADING_RE.findall(index_text)
    )
    entry_files = _entry_point_files(_DIAGNOSTICS_DIR)
    total_py = len(list(_DIAGNOSTICS_DIR.glob("*.py")))

    problems: list[str] = []

    missing_from_index = sorted(set(entry_files) - set(heading_names))
    stale_in_index = sorted(set(heading_names) - set(entry_files))
    if missing_from_index:
        problems.append(
            "Entry points missing a `### ` heading in INDEX.md: "
            + ", ".join(missing_from_index)
        )
    if stale_in_index:
        problems.append(
            "INDEX.md headings with no matching entry-point file: "
            + ", ".join(stale_in_index)
        )

    declared_entries = _prose_count(_ENTRY_PROSE_RE, index_text, "Total entry-points:")
    if declared_entries != len(entry_files):
        problems.append(
            f"Prose 'Total entry-points:' is {declared_entries} but there are "
            f"{len(entry_files)} entry-point files."
        )

    declared_files = _prose_count(
        _FILES_PROSE_RE, index_text, "Total files (incl. helpers):"
    )
    if declared_files != total_py:
        problems.append(
            f"Prose 'Total files (incl. helpers):' is {declared_files} but there "
            f"are {total_py} top-level .py files."
        )

    if problems:
        print("tools/diagnostics/INDEX.md is out of sync with the code:\n")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nFix: update tools/diagnostics/INDEX.md (entry headings and the "
            "Total entry-points / Total files prose) in the same diff."
        )
        return 1

    print(
        f"INDEX.md in sync: {len(entry_files)} entry points, {total_py} total files."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
