"""Read the diagnostic-only explanation from a completed CID-NL Alignment run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
    read_candidate_examples,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", required=True, help="Path to alignment_explanation.sqlite"
    )
    parser.add_argument(
        "--candidate-id", help="Exact candidate ID; omit for run summary"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    parser.add_argument(
        "--compact-to",
        type=Path,
        help="Write compact review facts from a full report, without RAW or Backfill",
    )
    parser.add_argument(
        "--curve-bundle",
        type=Path,
        help=(
            "Write a candidate's or group's curves into an Inspector bundle: "
            "saved curves where the run kept them, RAW reads for the rest"
        ),
    )
    parser.add_argument(
        "--group-id", help="Exact group ID for selected curve acquisition"
    )
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--dll-dir", type=Path)
    parser.add_argument("--curve-padding-min", type=float, default=0.5)
    args = parser.parse_args(argv)
    if sum(option is not None for option in (args.compact_to, args.curve_bundle)) > 1:
        parser.error("Choose only one of --compact-to or --curve-bundle")
    if args.group_id is not None and args.curve_bundle is None:
        parser.error("--group-id requires --curve-bundle")
    try:
        if args.curve_bundle is not None:
            if (
                args.compact_to is not None
                or args.raw_dir is None
                or args.dll_dir is None
            ):
                parser.error(
                    "--curve-bundle needs --raw-dir and --dll-dir "
                    "and cannot accompany --compact-to"
                )
            from xic_extractor.alignment_explanation.reader import (
                read_review_curves,
            )
            from xic_inspector.sources.curve_bundle import write_curve_bundle
            from xic_workbench.review_curves import review_curve_bundle

            if (args.candidate_id is None) == (args.group_id is None):
                parser.error(
                    "--curve-bundle needs exactly one of --candidate-id or --group-id"
                )
            curves = read_review_curves(
                Path(args.report),
                feature_id=args.group_id,
                candidate_id=args.candidate_id,
            )
            samples = sorted({location.sample_key for location in curves.locations})
            bundle, unusable = review_curve_bundle(
                Path(args.report),
                curves,
                key=args.group_id or args.candidate_id,
                sample_ids=samples,
                raw_dir=args.raw_dir,
                dll_dir=args.dll_dir,
                padding_min=args.curve_padding_min,
            )
            size = write_curve_bundle(bundle, args.curve_bundle)
            print(
                json.dumps(
                    {
                        "output": str(args.curve_bundle.resolve()),
                        "sample_count": len(samples),
                        "curve_count": len(bundle.transitions[0].traces),
                        "unusable_samples": unusable,
                        "bytes": size,
                    },
                    indent=2,
                )
            )
            if unusable:
                # The bundle omits these samples; say so beside the receipt.
                print(
                    "No usable signal in the curve window, omitted: "
                    + ", ".join(unusable),
                    file=sys.stderr,
                )
            return 0
        if args.compact_to is not None:
            from xic_extractor.diagnostics.explanation_conversion import (
                compact_explanation,
            )

            result = compact_explanation(
                Path(args.report),
                args.compact_to,
                progress=lambda message: print(message, file=sys.stderr, flush=True),
            )
            print(json.dumps(result, indent=2))
            return 0
        result = read_alignment_explanation(args.report, candidate_id=args.candidate_id)
        examples = (
            read_candidate_examples(Path(args.report))
            if args.candidate_id is None and not args.json
            else []
        )
    except (ExplanationError, OSError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "diagnostic_only": True,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 2
    if args.json or args.candidate_id is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Diagnostic only | {result['completion']}")
        print(
            f"Candidates: {result['candidate_count']} | "
            f"Matrix rows: {result['matrix_row_count']}"
        )
        print(f"Attempt: {result['processing_attempt_id']}")
        for candidate in examples:
            print(f"  {candidate['sample_stem']}: {candidate['candidate_id']}")
        reasons = sorted(
            result["reason_counts"].items(), key=lambda item: (-item[1], item[0])
        )
        for reason, count in reasons[:12]:
            print(f"  {reason}: {count}")
        if len(reasons) > 12:
            print(f"  ... {len(reasons) - 12} additional reasons (use --json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
