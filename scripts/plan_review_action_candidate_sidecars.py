from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions.models import ReviewActionError
from xic_extractor.review_actions.rows import summarize_review_action_candidate_sidecars
from xic_extractor.review_actions.workflows import (
    load_review_action_peak_candidate_rows,
    load_review_actions,
    plan_review_action_candidate_sidecars,
    write_review_action_candidate_sidecar_plan,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a ReviewAction candidate-sidecar verification plan. This "
            "command validates select_candidate candidate_id values against "
            "peak_candidates.tsv and does not change selected peaks, areas, "
            "counted detection, workbooks, or matrices."
        )
    )
    parser.add_argument(
        "--review-actions",
        type=Path,
        required=True,
        help="Review action TSV/CSV using review_action.",
    )
    parser.add_argument(
        "--peak-candidates-tsv",
        type=Path,
        required=True,
        help="Targeted peak_candidates.tsv emitted with emit_peak_candidates=true.",
    )
    parser.add_argument(
        "--output-candidate-sidecar-tsv",
        type=Path,
        required=True,
        help="Path to write review_action_candidate_sidecar TSV.",
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print the candidate-sidecar summary as JSON.",
    )
    args = parser.parse_args(argv)

    try:
        actions = load_review_actions(args.review_actions)
        peak_candidates = load_review_action_peak_candidate_rows(
            args.peak_candidates_tsv
        )
        checks = plan_review_action_candidate_sidecars(actions, peak_candidates)
        write_review_action_candidate_sidecar_plan(
            args.output_candidate_sidecar_tsv,
            checks,
        )
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = summarize_review_action_candidate_sidecars(checks)
    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Review action candidate-sidecar plan written: "
            f"{summary['row_count']} row(s), "
            f"{summary['verified_count']} verified, "
            f"{summary['blocked_count']} blocked."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
