"""Run preset-defined Delta Mass Discovery from RAW through diagnostic outputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.delta_mass_pipeline import run_delta_mass_discovery
from xic_extractor.presets.apply import resolve_delta_mass_discovery
from xic_extractor.raw_reader import RawReaderError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument(
        "--preset", required=True, help="Built-in name or preset TOML path."
    )
    parser.add_argument("--cid-nl-candidates-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        strategy = resolve_delta_mass_discovery(args.preset)
        outputs = run_delta_mass_discovery(
            raw_dir=args.raw_dir,
            dll_dir=args.dll_dir,
            output_dir=args.output_dir,
            strategy=strategy,
            cid_nl_candidates_csv=args.cid_nl_candidates_csv,
        )
    except (OSError, ValueError, RawReaderError) as exc:
        print(f"delta_mass_discovery failed: {exc}", file=sys.stderr)
        return 2
    print(f"Candidates: {outputs.candidates_csv}")
    print(f"Positions: {outputs.positions_csv}")
    print(f"Sidecar: {outputs.sidecar_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
