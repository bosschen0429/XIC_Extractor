"""Read the diagnostic-only explanation from a completed CID-NL Alignment run."""

from __future__ import annotations

import argparse
import json
import sys

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
    args = parser.parse_args(argv)
    try:
        result = read_cid_nl_explanation(args.report, candidate_id=args.candidate_id)
    except ExplanationError as exc:
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
        print(f"Request: {result['processing_request_id']}")
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
