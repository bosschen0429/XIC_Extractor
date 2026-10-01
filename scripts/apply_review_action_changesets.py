from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.review_actions.models import ReviewActionError
from xic_extractor.review_actions.workflows import (
    apply_review_action_changeset_rows,
    load_review_action_apply_changeset_rows,
    load_targeted_long_rows,
    write_review_action_applied_targeted_long,
    write_review_action_apply_audit,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply review_action_apply_changeset rows to an audited copy of "
            "targeted long output. This command never overwrites the input CSV."
        )
    )
    parser.add_argument(
        "--targeted-long-csv",
        type=Path,
        required=True,
        help="Current targeted long CSV/TSV containing SampleName and Target.",
    )
    parser.add_argument(
        "--changeset-tsv",
        type=Path,
        required=True,
        help=(
            "review_action_apply_changeset TSV from "
            "plan_review_action_apply_changesets.py."
        ),
    )
    parser.add_argument(
        "--output-targeted-long-csv",
        type=Path,
        required=True,
        help="Path to write the audited targeted long output copy.",
    )
    parser.add_argument(
        "--output-audit-tsv",
        type=Path,
        required=True,
        help="Path to write review_action_apply_audit TSV.",
    )
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help=(
            "Write audit rows for blocked changesets instead of failing. "
            "Default is to reject blocked rows."
        ),
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print the apply summary as JSON.",
    )
    args = parser.parse_args(argv)

    output_targeted_long_csv = args.output_targeted_long_csv.resolve()
    if output_targeted_long_csv == args.targeted_long_csv.resolve():
        print(
            "output-targeted-long-csv must not overwrite targeted-long-csv",
            file=sys.stderr,
        )
        return 2

    try:
        targeted_rows = load_targeted_long_rows(args.targeted_long_csv)
        changeset_rows = load_review_action_apply_changeset_rows(args.changeset_tsv)
        output = apply_review_action_changeset_rows(
            targeted_rows,
            changeset_rows,
            allow_blocked=args.allow_blocked,
        )
        write_review_action_applied_targeted_long(
            args.output_targeted_long_csv,
            output,
        )
        write_review_action_apply_audit(args.output_audit_tsv, output)
    except ReviewActionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = output.summary
    if args.summary_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "Review action changesets applied to audited copy: "
            f"{summary['applied_count']} applied, "
            f"{summary['audit_only_count']} audit-only, "
            f"{summary['deferred_count']} deferred."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
