"""Review Targeted input references without changing any extraction decisions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.configuration.models import ConfigError
from xic_extractor.diagnostics.reference_consistency import ReferencePolicy
from xic_extractor.diagnostics.reference_consistency_io import (
    read_reference_manifest,
    reference_preflight,
    run_reference_diagnostic,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help=(
            'JSON: {"groups": [{"name": "method", '
            '"targets_csv": "targets.csv", "raw_files": []}]}'
        ),
    )
    parser.add_argument("--dll-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--rt-min", type=float, default=1.0, help="Full-trace start in minutes"
    )
    parser.add_argument(
        "--rt-max", type=float, default=80.0, help="Full-trace end in minutes"
    )
    parser.add_argument(
        "--mapping-residual-min",
        type=float,
        default=0.5,
        help="Absolute leave-one-out residual for an informational alert",
    )
    parser.add_argument(
        "--peak-match-min",
        type=float,
        default=0.2,
        help="Reference-to-apex matching tolerance in minutes",
    )
    parser.add_argument(
        "--peak-prominence-ratio",
        type=float,
        default=0.20,
        help="Minimum peak prominence and height relative to trace maximum",
    )
    parser.add_argument(
        "--peak-separation-min",
        type=float,
        default=0.35,
        help="Minimum distinct peak-apex separation in minutes",
    )
    parser.add_argument(
        "--peak-min-width-points",
        type=float,
        default=6.0,
        help="Minimum half-prominence width in scans; rejects smoothed impulses",
    )
    args = parser.parse_args(argv)
    try:
        policy = ReferencePolicy(
            args.mapping_residual_min,
            args.peak_match_min,
            args.peak_prominence_ratio,
            args.peak_separation_min,
            args.peak_min_width_points,
        )
        groups = read_reference_manifest(args.manifest)
        if args.preflight_only:
            print(
                json.dumps(
                    reference_preflight(groups, args.dll_dir, args.rt_min, args.rt_max),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            report = run_reference_diagnostic(
                groups,
                dll_dir=args.dll_dir,
                output_dir=args.output_dir,
                policy=policy,
                rt_min=args.rt_min,
                rt_max=args.rt_max,
                progress=lambda message: print(message, flush=True),
            )
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in report["summary"].items()
                        if key != "provenance"
                    },
                    indent=2,
                )
            )
    except (OSError, ValueError, KeyError, TypeError, ConfigError) as exc:
        parser.exit(2, f"Reference diagnostic: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
