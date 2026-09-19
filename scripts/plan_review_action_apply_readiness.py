from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions import (
    ReviewActionError,
    load_review_action_expected_diff_approvals,
    load_review_action_target_states,
    load_review_actions,
    plan_review_action_applications,
    plan_review_action_apply_readiness,
    summarize_review_action_apply_readiness_plan,
    write_review_action_apply_readiness_plan,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a dry-run review action apply-readiness plan. This command "
            "does not apply actions or modify extraction CSV, workbook, selected "
            "peak, area, counted detection, or matrix values."
        )
    )
    parser.add_argument(
        "--review-actions",
        type=Path,
        required=True,
        help="Review action TSV/CSV using review_action.",
    )
    parser.add_argument(
        "--targeted-long-csv",
        type=Path,
        required=True,
        help="Current targeted long CSV/TSV containing SampleName and Target.",
    )
    parser.add_argument(
        "--expected-diff-approvals",
        type=Path,
        help="Approved review_action_expected_diff TSV.",
    )
    parser.add_argument(
        "--output-apply-readiness-tsv",
        type=Path,
        required=True,
        help="Path to write review_action_apply_readiness TSV.",
    )
    parser.add_argument(
        "--allow-unused-approvals",
        action="store_true",
        help=(
            "Allow valid expected-diff approval rows that do not match this "
            "review action set. By default, unused approvals are rejected."
        ),
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print the apply-readiness summary as JSON.",
    )
    args = parser.parse_args(argv)

    try:
        actions = load_review_actions(args.review_actions)
        target_states = load_review_action_target_states(args.targeted_long_csv)
        applications = plan_review_action_applications(actions, target_states)
        approvals = (
            load_review_action_expected_diff_approvals(
                args.expected_diff_approvals
            )
            if args.expected_diff_approvals is not None
            else {}
        )
        plan = plan_review_action_apply_readiness(applications, approvals)
        if plan.unused_expected_diff_approvals and not args.allow_unused_approvals:
            unused = ", ".join(
                approval.stable_row_id
                for approval in plan.unused_expected_diff_approvals
            )
            raise ReviewActionError(
                "unused expected-diff approval row(s): " + unused
            )
        write_review_action_apply_readiness_plan(
            args.output_apply_readiness_tsv,
            plan,
        )
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = summarize_review_action_apply_readiness_plan(plan)
    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Review action apply-readiness plan written: "
            f"{summary['row_count']} row(s), "
            f"{summary['ready_count']} ready, "
            f"{summary['blocked_count']} blocked."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
