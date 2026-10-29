"""Read the diagnostic-only explanation from a completed CID-NL Alignment run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    read_cid_nl_explanation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", required=True, help="Path to cid_nl_explanation.sqlite"
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
        help="Acquire selected candidate/group XIC windows into an Inspector bundle",
    )
    parser.add_argument(
        "--group-id", help="Exact group ID for selected curve acquisition"
    )
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--dll-dir", type=Path)
    parser.add_argument("--curve-padding-min", type=float, default=0.5)
    parser.add_argument("--curve-ppm", type=float, default=5.0)
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
            from xic_workbench.acquisition import (
                export_explanation_curves,
            )

            result = export_explanation_curves(
                Path(args.report),
                args.curve_bundle,
                raw_dir=args.raw_dir,
                dll_dir=args.dll_dir,
                candidate_id=args.candidate_id,
                group_id=args.group_id,
                padding_min=args.curve_padding_min,
                ppm=args.curve_ppm,
            )
            print(json.dumps(result, indent=2))
            if result["unusable_samples"]:
                # The bundle omits these samples; say so beside the receipt.
                print(
                    "No usable signal near the retained apex, omitted: "
                    + ", ".join(result["unusable_samples"]),
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
        result = read_cid_nl_explanation(args.report, candidate_id=args.candidate_id)
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
        for candidate in result.get("candidate_examples", ()):
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
