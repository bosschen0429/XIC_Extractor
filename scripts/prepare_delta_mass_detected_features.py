"""Prepare Detected Features CSV input for Delta Mass / Pt pattern Discovery."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.detected_features import (
    load_external_feature_table_tsv,
    load_mzmine_wide_feature_table_csv,
    write_detected_features_csv,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an external detected-feature table into the standard "
            "Detected Features CSV v1 input consumed by Delta Mass / Pt "
            "pattern Discovery."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--external-feature-table-tsv",
        type=Path,
        help=(
            "Normalized external feature table TSV with explicit source "
            "metadata, m/z, RT, and positive area/intensity."
        ),
    )
    source.add_argument(
        "--mzmine-feature-table-csv",
        type=Path,
        help=(
            "MZmine wide feature table CSV with row ID, row m/z, row retention "
            "time, and '<sample>.mzML Peak area' columns."
        ),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("output") / "delta_mass" / "detected_features.csv",
        help="Destination Detected Features CSV v1 path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    source_path = (
        args.external_feature_table_tsv or args.mzmine_feature_table_csv
    ).resolve()
    if not source_path.is_file():
        print(
            f"{source_path}: feature table does not exist",
            file=sys.stderr,
        )
        return 2

    try:
        features = (
            load_external_feature_table_tsv(source_path)
            if args.external_feature_table_tsv is not None
            else load_mzmine_wide_feature_table_csv(source_path)
        )
    except (FileNotFoundError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    output_csv = args.output_csv.resolve()
    write_detected_features_csv(output_csv, features)
    print(f"Detected features CSV: {output_csv}")
    print(f"Detected feature rows: {len(features)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
