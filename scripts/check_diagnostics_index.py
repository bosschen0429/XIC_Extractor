"""CI guard: keep tools/diagnostics/INDEX.md in sync with actual entry points.

The architecture contract requires every PR that adds, removes, or renames a
diagnostic CLI entry point to update ``tools/diagnostics/INDEX.md`` in the same
diff. This script enforces that mechanically so the index cannot drift silently.

Check that indexed filenames match the diagnostic entry points. Missing entries
hide available tools; stale entries direct readers to unavailable commands.

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


def _entry_point_files(diagnostics_dir: Path) -> list[str]:
    names: list[str] = []
    for path in sorted(diagnostics_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _MAIN_GUARD_RE.search(text):
            names.append(path.name)
    return names


def main() -> int:
    index_text = _INDEX_PATH.read_text(encoding="utf-8")

    heading_names = sorted(
        match.split("`")[1] for match in _HEADING_RE.findall(index_text)
    )
    entry_files = _entry_point_files(_DIAGNOSTICS_DIR)

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

    if problems:
        print("tools/diagnostics/INDEX.md is out of sync with the code:\n")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nFix: update the entry headings in tools/diagnostics/INDEX.md "
            "to match the diagnostic entry-point filenames."
        )
        return 1

    print(f"INDEX.md in sync: {len(entry_files)} diagnostic entry points.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
