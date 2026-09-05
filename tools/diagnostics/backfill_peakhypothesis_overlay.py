"""Render RAW XIC overlays for anchored FULL COHORT hypothesis review candidates."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_overlay
from xic_extractor.raw_reader import open_raw


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        requests = backfill_peakhypothesis_overlay.build_overlay_requests(
            review_queue_rows=_read_tsv(args.review_queue_tsv),
            full_cohort_review_rows=_read_tsv(args.full_cohort_alignment_review_tsv),
            discovery_batch_rows=_read_csv(args.discovery_batch_index_csv),
            rt_padding_min=args.rt_padding_min,
            ppm_tolerance=args.ppm_tolerance,
        )
        outputs = backfill_peakhypothesis_overlay.write_full_cohort_overlay_outputs(
            args.output_dir,
            requests,
            trace_provider=_raw_trace_provider(args.dll_dir),
            source_run_id=args.source_run_id,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"FULL COHORT overlay index TSV: {outputs.index_tsv}")
    print(f"FULL COHORT overlay gallery HTML: {outputs.gallery_html}")
    print(f"FULL COHORT overlay plot directory: {outputs.plot_dir}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-queue-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-alignment-review-tsv", type=Path, required=True)
    parser.add_argument("--discovery-batch-index-csv", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    parser.add_argument("--ppm-tolerance", type=float, default=20.0)
    parser.add_argument("--rt-padding-min", type=float, default=0.75)
    return parser.parse_args(argv)


def _raw_trace_provider(dll_dir: Path):
    def provider(
        raw_file: Path,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tolerance: float,
    ):
        if not raw_file.is_file():
            raise ValueError(f"{raw_file}: RAW file does not exist")
        with open_raw(raw_file, dll_dir) as raw:
            return raw.extract_xic(mz, rt_min, rt_max, ppm_tolerance)

    return provider


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter="\t"))


def _read_csv(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(main())
