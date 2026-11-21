"""List maintained diagnostic entry points with the summary from each docstring.

Each entry point's module docstring owns its purpose, inputs, outputs and
authority; run ``python -m tools.diagnostics.<name> --help`` for its arguments.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MAIN_GUARD = re.compile(r"if __name__ == ['\"]__main__['\"]")


def main() -> None:
    for path in sorted(Path(__file__).parent.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "__main__.py" or not _MAIN_GUARD.search(text):
            continue
        docstring = ast.get_docstring(ast.parse(text)) or ""
        summary = docstring.split("\n\n", 1)[0].replace("\n", " ")
        print(f"{path.stem}: {summary}")


if __name__ == "__main__":
    main()
