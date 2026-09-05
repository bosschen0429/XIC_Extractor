from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from xic_extractor.instrument_qc.calibration import (
    calibrate_sdolek_rows,
    load_metadata_source_status,
    load_sdolek_trend_rows,
)
from xic_extractor.instrument_qc.calibration_writers import (
    write_calibrated_trend_tsv,
    write_calibration_review_markdown,
    write_calibration_summary_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate SDO/LEK instrument trends with method-doc order.",
    )
    parser.add_argument("--trend-tsv", type=Path, required=True)
    parser.add_argument("--trend-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--injection-order-source", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    error = _validate_inputs(
        trend_tsv=args.trend_tsv,
        trend_json=args.trend_json,
        injection_order_source=args.injection_order_source,
    )
    if error is not None:
        print(error, file=sys.stderr)
        return 2

    try:
        trend_rows = load_sdolek_trend_rows(args.trend_tsv)
        metadata_source_status = load_metadata_source_status(args.trend_json)
        result = calibrate_sdolek_rows(
            trend_rows,
            metadata_source_status=metadata_source_status,
            injection_order_source=args.injection_order_source,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_dir = args.output_dir
    calibrated_tsv = output_dir / "instrument_qc_sdolek_calibrated_trend.tsv"
    summary_json = output_dir / "instrument_qc_sdolek_calibration_summary.json"
    review_md = output_dir / "instrument_qc_sdolek_review.md"

    try:
        write_calibrated_trend_tsv(calibrated_tsv, result.rows)
        write_calibration_summary_json(summary_json, result)
        write_calibration_review_markdown(review_md, result)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Wrote {calibrated_tsv}")
    print(f"Wrote {summary_json}")
    print(f"Wrote {review_md}")
    return 0


def _validate_inputs(
    *,
    trend_tsv: Path,
    trend_json: Path,
    injection_order_source: Path | None,
) -> str | None:
    if not trend_tsv.exists():
        return f"trend TSV not found: {trend_tsv}"
    if not trend_json.exists():
        return f"trend JSON not found: {trend_json}"
    if injection_order_source is None:
        return None
    if injection_order_source.name.casefold().startswith("sampleinfo"):
        return (
            "SampleInfo is downstream evidence, not an accepted "
            "method-doc injection-order source."
        )
    if not injection_order_source.exists():
        return f"injection-order source not found: {injection_order_source}"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
