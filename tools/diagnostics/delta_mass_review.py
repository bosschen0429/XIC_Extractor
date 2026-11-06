"""Export selected Delta Mass XICs and window MS2 for offline Inspector review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xic_extractor.diagnostics.delta_mass_review_acquisition import (
    acquire_delta_mass_review,
)
from xic_extractor.diagnostics.delta_mass_review_data import write_delta_mass_review
from xic_extractor.raw_reader_errors import RawReaderError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-dir", required=True, type=Path)
    parser.add_argument(
        "--candidate-id",
        required=True,
        action="append",
        help="Exact saved candidate ID; repeat to review several",
    )
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--dll-dir", required=True, type=Path)
    parser.add_argument(
        "--padding-min",
        type=float,
        default=2.0,
        help="RAW context on both sides of the saved apices (default: 2)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Review JSON to open with xic-inspector; no Discovery outputs change",
    )
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".json":
        parser.error("--output must be a .json review file")
    if (
        args.output.resolve()
        == (args.discovery_dir / "delta_mass_sidecar.json").resolve()
    ):
        parser.error("--output cannot replace the Discovery sidecar")
    try:
        review = acquire_delta_mass_review(
            args.discovery_dir,
            candidate_ids=args.candidate_id,
            raw_directory=args.raw_dir,
            dll_directory=args.dll_dir,
            padding_min=args.padding_min,
        )
        write_delta_mass_review(review, args.output)
    except (OSError, ValueError, RawReaderError) as exc:
        print(f"Delta Mass review failed: {exc}", file=sys.stderr)
        return 2
    print(f"Review: {args.output.resolve()}")
    print(
        f"Candidates: {len(review.candidates)}; "
        "new diagnostic RAW reads, no reclassification"
    )
    errors = sum(len(c.ms2_read_errors) for c in review.candidates)
    if errors:
        print(f"MS2 read errors retained in review: {errors}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
