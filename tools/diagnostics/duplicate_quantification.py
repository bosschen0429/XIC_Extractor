"""Count one peak quantified in several Matrix rows of an Alignment output."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.diagnostics.duplicate_quantification import (
    read_published_cells,
    summarize_duplicate_quantification,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = summarize_duplicate_quantification(
        read_published_cells(args.alignment_dir),
        max_ppm=args.max_ppm,
        max_apex_sec=args.max_apex_sec,
    )
    text = json.dumps({"alignment_dir": str(args.alignment_dir), **summary}, indent=2)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Screen a validation Alignment output (with cid_nl_explanation.sqlite) "
            "for same-sample cells in different rows that quantify one apex."
        ),
    )
    parser.add_argument("--alignment-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--max-ppm", type=float, default=10.0)
    parser.add_argument("--max-apex-sec", type=float, default=3.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
