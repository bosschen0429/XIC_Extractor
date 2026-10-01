from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions.models import ReviewActionError
from xic_extractor.review_actions.rows import (
    summarize_review_action_expected_diff_approvals,
    summarize_review_actions,
)
from xic_extractor.review_actions.workflows import (
    load_review_action_expected_diff_approvals,
    load_review_actions,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a targeted review action TSV/CSV import file."
    )
    parser.add_argument("path", type=Path, help="Review action TSV or CSV path.")
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print the validation summary as JSON.",
    )
    parser.add_argument(
        "--expected-diff",
        action="store_true",
        help="Validate approved expected-diff rows instead of action rows.",
    )
    args = parser.parse_args(argv)

    try:
        if args.expected_diff:
            summary = summarize_review_action_expected_diff_approvals(
                load_review_action_expected_diff_approvals(args.path)
            )
        else:
            summary = summarize_review_actions(load_review_actions(args.path))
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif args.expected_diff:
        print(
            f"Expected-diff approvals valid: {summary['approval_count']} "
            "approved row(s)."
        )
    else:
        print(
            "Review actions valid: "
            f"{summary['action_count']} action(s), "
            f"{summary['product_mutating_action_count']} product-mutating."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
