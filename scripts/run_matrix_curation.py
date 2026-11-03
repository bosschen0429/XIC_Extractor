"""Curate a published Alignment Matrix with independently switchable steps.

A step runs when its parameters are given. Without --sample-metadata, the
Workbench's confirmed sample data for the run's RAW folder is used.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.matrix_curation.export import STEPS


def main(argv: Sequence[str] | None = None) -> int:
    from xic_workbench.config_io import CONFIG_DIR

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="Alignment output folder with its Matrix and bound Review",
    )
    parser.add_argument(
        "--sample-metadata",
        type=Path,
        help="Canonical sample_metadata TSV/CSV; roles are never inferred",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=CONFIG_DIR,
        help="Workbench configuration holding confirmed sample data",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="New folder for the results (default: matrix_curation_<time> "
        "inside the source folder)",
    )
    for step in STEPS:
        group = parser.add_argument_group(step.label, step.description)
        for parameter in step.parameters:
            group.add_argument(
                f"--{parameter.key.replace('_', '-')}",
                dest=parameter.key,
                type=float,
                help=f"{parameter.label}: {parameter.help}",
            )
    args = parser.parse_args(argv)
    steps = {}
    for step in STEPS:
        given = {
            parameter.key: getattr(args, parameter.key)
            for parameter in step.parameters
            if getattr(args, parameter.key) is not None
        }
        if given:
            steps[step.key] = given
    if not steps:
        parser.error("enable at least one step by giving its parameters")
    from xic_workbench.matrix_curation import run_curation

    try:
        result = run_curation(
            args.config_dir,
            {
                "source_dir": str(args.source_dir),
                "sample_metadata_path": str(args.sample_metadata or ""),
                "output_dir": str(args.output_dir or ""),
                "steps": steps,
            },
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        f"Matrix curation: kept {result['kept_feature_count']} of "
        f"{result['feature_count']} features -> {result['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
