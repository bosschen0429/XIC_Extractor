"""Build an opt-in Parquet shadow from owner-readable Backfill TSV evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.diagnostics.backfill_evidence.bundle import (
    open_backfill_evidence_parquet_shadow_bundle,
    write_backfill_evidence_parquet_shadow_bundle,
)


def run_backfill_evidence_parquet_shadow(
    *,
    backfill_tsv: Path,
    skipped_tsv: Path,
    output_dir: Path,
    run_metadata_json: Path | None = None,
) -> Path:
    return write_backfill_evidence_parquet_shadow_bundle(
        output_dir,
        backfill_tsv=backfill_tsv,
        skipped_tsv=skipped_tsv,
        run_metadata_json=run_metadata_json,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a diagnostic-only Parquet shadow bundle without changing TSV "
            "defaults or artifact authority."
        )
    )
    parser.add_argument("--backfill-tsv", type=Path, required=True)
    parser.add_argument("--skipped-tsv", type=Path, required=True)
    parser.add_argument("--run-metadata-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = run_backfill_evidence_parquet_shadow(
        backfill_tsv=args.backfill_tsv,
        skipped_tsv=args.skipped_tsv,
        run_metadata_json=args.run_metadata_json,
        output_dir=args.output_dir,
    )
    bundle = open_backfill_evidence_parquet_shadow_bundle(output_dir)
    sys.stdout.write(
        json.dumps(
            {
                "authority": bundle.authority,
                "backfill_row_count": bundle.backfill_row_count,
                "output_dir": str(output_dir),
                "schema_version": bundle.schema_version,
                "skipped_row_count": bundle.skipped_row_count,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ("build_parser", "main", "run_backfill_evidence_parquet_shadow")
