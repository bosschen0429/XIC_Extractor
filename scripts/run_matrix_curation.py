"""Curate a published Alignment Matrix with independently switchable steps.

A step runs when its parameters are given, or its flag for a step without
parameters. Without --sample-metadata, sample roles are read from the run's RAW
headers, as Alignment reads them. Internal standards come from --istd-list, or
else from the list the run declared; they are always kept.
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
    read_run_istd,
    read_run_samples,
    read_sample_metadata_file,
)
from xic_extractor.matrix_curation.istd import IstdWindow, read_istd_file
from xic_extractor.matrix_curation.source import (
    RUN_METADATA_NAME,
    locate_curation_source,
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
        "--istd-list",
        type=Path,
        help="Target list whose ISTD rows are kept (default: the run's own list)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="New folder for the results (default: matrix_curation_<time> "
        "inside the source folder)",
    )
    for step in STEPS:
        group = parser.add_argument_group(step.label, step.description)
        if not step.parameters:
            group.add_argument(
                f"--{step.key.replace('_', '-')}",
                dest=step.key,
                action="store_true",
                help=f"Enable {step.label}",
            )
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
        if given or (not step.parameters and getattr(args, step.key)):
            steps[step.key] = given
    if not steps:
        parser.error("enable at least one step by its parameters or flag")
    try:
        source = locate_curation_source(args.source_dir)
        metadata: tuple[SampleMetadata, ...] | None
        if args.sample_metadata is not None:
            metadata = read_sample_metadata_file(args.sample_metadata)
            sample_source = str(args.sample_metadata)
        else:
            metadata = read_run_samples(source)
            sample_source = f"RAW headers: {source.raw_dir}" if metadata else ""
        istd: tuple[IstdWindow, ...] | None
        if args.istd_list is not None:
            istd, istd_source = read_istd_file(args.istd_list), str(args.istd_list)
        else:
            istd = read_run_istd(source)
            istd_source = (
                f"run metadata: {source.source_dir / RUN_METADATA_NAME}" if istd else ""
            )
        output_dir = args.output_dir or default_output_dir(source)
        summary = json.loads(
            export_matrix_curation(
                source=source,
                sample_metadata=metadata,
                sample_source=sample_source,
                steps=steps,
                output_dir=output_dir,
                istd_windows=istd,
                istd_source=istd_source,
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
