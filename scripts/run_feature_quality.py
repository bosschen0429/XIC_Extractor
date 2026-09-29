"""Publish feature availability, selection reasons, and a traceable analysis set."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.feature_quality_io import export_feature_quality


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix", type=Path, required=True, help="Finalized alignment_matrix.tsv"
    )
    parser.add_argument(
        "--identity",
        type=Path,
        required=True,
        help="Bound alignment_matrix_identity.tsv",
    )
    parser.add_argument(
        "--sample-metadata",
        type=Path,
        required=True,
        help="Canonical sample_metadata TSV; roles are never inferred",
    )
    parser.add_argument(
        "--min-finite-fraction",
        type=float,
        required=True,
        help=(
            "Keep features with finite values in at least this fraction of "
            "non-excluded study samples (0..1); zero counts as available, "
            "not as detection"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "New directory for full and selected matrices, identity mapping, "
            "metrics, and manifest"
        ),
    )
    args = parser.parse_args(argv)
    try:
        manifest = export_feature_quality(
            matrix_path=args.matrix,
            identity_path=args.identity,
            sample_metadata_path=args.sample_metadata,
            output_dir=args.output_dir,
            min_finite_fraction=args.min_finite_fraction,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Feature quality bundle: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
