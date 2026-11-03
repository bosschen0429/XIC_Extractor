"""Curate a published Alignment Matrix with independently switchable steps.

A step runs when its parameters are given. Without --sample-metadata, sample
roles are read from the run's RAW headers, as Alignment reads them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.matrix_curation.export import (
    STEPS,
    default_output_dir,
    export_matrix_curation,
    locate_curation_source,
    read_run_samples,
    read_sample_metadata_file,
)
from xic_extractor.sample_metadata import SampleMetadata


def main(argv: Sequence[str] | None = None) -> int:
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
        help="Canonical sample_metadata TSV/CSV (default: roles from the RAW headers)",
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
    try:
        source = locate_curation_source(args.source_dir)
        metadata: tuple[SampleMetadata, ...] | None
        if args.sample_metadata is not None:
            metadata = read_sample_metadata_file(args.sample_metadata)
            sample_source = str(args.sample_metadata)
        else:
            metadata = read_run_samples(source)
            sample_source = f"RAW headers: {source.raw_dir}" if metadata else ""
        output_dir = args.output_dir or default_output_dir(source)
        summary = json.loads(
            export_matrix_curation(
                source=source,
                sample_metadata=metadata,
                sample_source=sample_source,
                steps=steps,
                output_dir=output_dir,
            ).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        f"Matrix curation: kept {summary['kept_feature_count']} of "
        f"{summary['feature_count']} features -> {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
