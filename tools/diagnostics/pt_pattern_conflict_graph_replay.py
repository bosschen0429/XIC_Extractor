"""Write a provenance-bound Pt hypothesis conflict graph sidecar."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_conflict_graph_replay import (
    GRAPH_RULE_VERSION,
    run_conflict_graph_replay,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a diagnostic-only pairwise conflict graph from frozen Pt "
            "candidate and detected-feature artifacts without opening RAW files."
        )
    )
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--detected-features-csv", type=Path, required=True)
    parser.add_argument("--legacy-conflicts-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--graph-rule-version", default=GRAPH_RULE_VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    for label, path in (
        ("candidates-csv", args.candidates_csv),
        ("detected-features-csv", args.detected_features_csv),
        ("legacy-conflicts-csv", args.legacy_conflicts_csv),
    ):
        if path is not None and not path.is_file():
            parser.error(f"--{label} does not exist: {path}")
    try:
        outputs = run_conflict_graph_replay(
            candidates_csv=args.candidates_csv.resolve(),
            detected_features_csv=args.detected_features_csv.resolve(),
            legacy_conflicts_csv=(
                args.legacy_conflicts_csv.resolve()
                if args.legacy_conflicts_csv
                else None
            ),
            output_dir=args.output_dir.resolve(),
            report_id=args.report_id,
            graph_rule_version=args.graph_rule_version,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_conflict_graph_replay failed: {exc}", file=sys.stderr)
        return 2

    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
