from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.diagnostics.backfill_decision_ledger import (
    build_backfill_decision_ledger,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    outputs = build_backfill_decision_ledger(
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
        expected_code_revision=args.expected_code_revision,
    )
    print(f"ledger={outputs.ledger_tsv}")
    print(f"summary_json={outputs.summary_json}")
    print(f"summary_markdown={outputs.summary_markdown}")
    print("validation_status=diagnostic_only")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic no-RAW Backfill decision ledger from one "
            "retained full-universe artifact."
        )
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-code-revision", required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
