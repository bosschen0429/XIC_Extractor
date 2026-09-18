"""CLI tool to extract a BlankBackgroundLibrary from pristine solvent blanks."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from xic_extractor.discovery.blank_qualification import (
    extract_blank_background_library,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract background ion library from Thermo RAW solvent blanks."
    )
    parser.add_argument(
        "--blank",
        nargs="+",
        required=True,
        help="One or more paths to Blank RAW files (or a directory containing them).",
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        default=Path(
            os.getenv("THERMO_RAWFILE_READER_DLL_DIR", r"C:\Xcalibur\system\programs")
        ),
        help="Directory containing Thermo CommonCore DLLs.",
    )
    parser.add_argument(
        "--min-intensity",
        type=float,
        default=25000.0,
        help="Minimum MS1 centroid intensity to consider (default: 25000.0).",
    )
    parser.add_argument(
        "--min-scans",
        type=int,
        default=3,
        help="Minimum scans a centroid must recur to be background (default: 3).",
    )
    parser.add_argument(
        "--sample-step",
        type=int,
        default=3,
        help="Sample every Nth MS1 scan for extraction speed (default: 3).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Target output JSON path for the background library.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    blank_paths: list[Path] = []
    for item in args.blank:
        p = Path(item)
        if p.is_dir():
            blank_paths.extend(sorted(p.glob("*.raw")))
        elif p.is_file():
            blank_paths.append(p)
        else:
            print(f"Error: Path does not exist: {p}", file=sys.stderr)
            return 1

    if not blank_paths:
        print("Error: No valid RAW blank files found.", file=sys.stderr)
        return 1

    print(f"Found {len(blank_paths)} blank RAW files for extraction:")
    for bp in blank_paths:
        print(f"  - {bp.name} ({bp})")

    print(
        f"Extracting background library (min_intensity={args.min_intensity}, "
        f"min_scans={args.min_scans})..."
    )
    library = extract_blank_background_library(
        blank_paths,
        args.dll_dir,
        min_intensity=args.min_intensity,
        min_scans=args.min_scans,
        sample_step=args.sample_step,
    )

    print(f"Successfully extracted {len(library.ions)} stable background ions!")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    library.save_json(args.output)
    print(f"Saved library to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
