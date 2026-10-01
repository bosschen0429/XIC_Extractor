from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions.models import ReviewActionError
from xic_extractor.review_actions.rows import summarize_review_action_applications
from xic_extractor.review_actions.workflows import (
    load_review_action_target_states,
    load_review_actions,
    plan_review_action_applications,
    plan_review_action_expected_diff_templates,
    write_review_action_application_plan,
    write_review_action_expected_diff_template,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a dry-run review action application plan. This command does "
            "not modify extraction CSV, workbook, selected peak, area, or "
            "matrix values."
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
        "--output-plan-tsv",
        type=Path,
        required=True,
        help="Path to write the review action application plan TSV.",
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print the application plan summary as JSON.",
    )
    parser.add_argument(
        "--expected-diff-template-tsv",
        type=Path,
        help=(
            "Optional path to write review_action_expected_diff template rows "
            "for product-mutating actions blocked by expected-diff review."
        ),
    )
    args = parser.parse_args(argv)

    try:
        actions = load_review_actions(args.review_actions)
        target_states = load_review_action_target_states(args.targeted_long_csv)
        applications = plan_review_action_applications(actions, target_states)
        write_review_action_application_plan(args.output_plan_tsv, applications)
        expected_diff_templates = plan_review_action_expected_diff_templates(
            applications
        )
        if args.expected_diff_template_tsv is not None:
            write_review_action_expected_diff_template(
                args.expected_diff_template_tsv,
                expected_diff_templates,
            )
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = summarize_review_action_applications(applications)
    summary["expected_diff_template_count"] = len(expected_diff_templates)
    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Review action application plan written: "
            f"{summary['application_count']} action(s), "
            f"{summary['blocked_application_count']} blocked, "
            f"{summary['expected_diff_required_count']} require expected-diff "
            "before apply, "
            f"{summary['expected_diff_template_count']} expected-diff template "
            "row(s)."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
