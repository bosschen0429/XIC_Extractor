"""Review Delta Mass candidates and optional peak-scoped NL intersections."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xic_extractor.diagnostics.delta_mass_review_acquisition import (
    acquire_delta_mass_review,
)
from xic_extractor.diagnostics.delta_mass_review_data import (
    write_delta_mass_nl_summary,
    write_delta_mass_review,
)
from xic_extractor.raw_reader_errors import RawReaderError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-dir", required=True, type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--candidate-id",
        action="append",
        help="Exact saved candidate ID; repeat to review several",
    )
    selection.add_argument(
        "--all-candidates",
        action="store_true",
        help=(
            "Review every saved status=candidate row; "
            "no manual candidate or scan selection"
        ),
    )
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--dll-dir", required=True, type=Path)
    parser.add_argument(
        "--nl-preset",
        help=(
            "Explicit CID-NL preset (e.g. dna_dr): assess NL inside saved feature "
            "bounds; never inferred from sample names"
        ),
    )
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
            nl_preset=args.nl_preset,
        )
        write_delta_mass_review(review, args.output)
        if review.nl_method is not None:
            summary = args.output.with_suffix(".nl.csv")
            write_delta_mass_nl_summary(review, summary)
            print(f"Candidate-level NL intersections: {summary.resolve()}")
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
