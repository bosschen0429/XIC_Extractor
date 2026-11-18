"""Build DDA Targeted Mass Exclusion lists from a solvent blank.

Start a recipe with --new, edit it to match your methods, then run it:

  xic-exclusion-list --new recipe.toml
  xic-exclusion-list recipe.toml

Each [[lists]] table becomes <name>.csv for the Method Editor import, written
next to the recipe with exclusion_report.txt. No row can stop a target in the
recipe's target files from triggering MS2.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from xic_extractor.exclusion_list.recipe import ExclusionListError, read_recipe
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.thermo_reader_install import default_thermo_reader_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xic-exclusion-list",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "recipe", type=Path, help="recipe TOML to run, or to create with --new"
    )
    parser.add_argument(
        "--new", action="store_true", help="write a commented starter recipe and stop"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="where to write the lists (default: the recipe's folder)",
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        default=Path(
            os.getenv("THERMO_RAWFILE_READER_DLL_DIR") or default_thermo_reader_dir()
        ),
        help="Thermo RawFileReader DLL directory",
    )
    args = parser.parse_args(argv)
    if args.new:
        return _write_starter(args.recipe)

    from xic_extractor.exclusion_list.build import build_exclusion_lists
    from xic_extractor.exclusion_list.export import list_summary, write_exclusion_lists

    try:
        recipe = read_recipe(args.recipe)
        result = build_exclusion_lists(recipe, args.dll_dir)
        report = write_exclusion_lists(
            result, recipe, args.output_dir or args.recipe.parent
        )
    except (ExclusionListError, RawReaderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for exclusion in result.lists:
        print(list_summary(exclusion))
    print(f"report: {report}")
    return 0


def _write_starter(path: Path) -> int:
    if path.exists():
        print(f"error: {path} already exists", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    starter = files("xic_extractor.exclusion_list").joinpath("example_recipe.toml")
    path.write_text(starter.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {path}; edit it, then run: xic-exclusion-list {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
