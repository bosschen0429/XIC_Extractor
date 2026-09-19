from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions import (
    ReviewActionError,
    load_review_action_expected_diff_approvals,
    summarize_review_action_expected_diff_approvals,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate approved review_action_expected_diff TSV rows. This "
            "command does not apply actions or modify extraction outputs."
        )
    )
    parser.add_argument(
        "expected_diff_tsv",
        type=Path,
        help="Approved review_action_expected_diff TSV.",
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print validation summary as JSON.",
    )
    args = parser.parse_args(argv)

    try:
        approvals = load_review_action_expected_diff_approvals(
            args.expected_diff_tsv
        )
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = summarize_review_action_expected_diff_approvals(approvals)
    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Review action expected-diff approvals validated: "
            f"{summary['approval_count']} approved row(s)."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
