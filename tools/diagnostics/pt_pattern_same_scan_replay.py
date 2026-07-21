"""Run bounded Pt same-scan evidence replay."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_same_scan_evidence import SameScanSettings
from xic_extractor.discovery.pt_pattern_same_scan_replay import run_same_scan_replay
from xic_extractor.ms1_scan_index import (
    MS1ScanReadError,
    build_ms1_scan_index,
    read_ms1_scan_index_npz,
    read_ms1_scan_index_provenance,
)
from xic_extractor.raw_reader import RawReaderError, open_raw


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare current Pt feature-first hypotheses with strict four-position "
            "and 0/+1/+2 core same-scan evidence."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ms1-index-npz", type=Path)
    source.add_argument("--raw", type=Path)
    parser.add_argument("--dll-dir", type=Path)
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--evidence-csv", type=Path, required=True)
    parser.add_argument("--known-diagnosis-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--mass-tolerance-ppm", type=float, default=20.0)
    parser.add_argument("--noise-level", type=float, default=25_000.0)
    parser.add_argument("--minimum-joint-scans", type=int, default=2)
    parser.add_argument(
        "--candidate-rt-half-window-sec",
        type=float,
        default=45.0,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    for label, path in (
        ("candidates-csv", args.candidates_csv),
        ("evidence-csv", args.evidence_csv),
        ("known-diagnosis-csv", args.known_diagnosis_csv),
    ):
        if not path.is_file():
            parser.error(f"--{label} does not exist: {path}")
    if not args.report_id.strip():
        parser.error("--report-id must be non-empty")
    if args.ms1_index_npz is not None:
        if not args.ms1_index_npz.is_file():
            parser.error(
                f"--ms1-index-npz does not exist: {args.ms1_index_npz}"
            )
        index_source = "npz"
        index_input = args.ms1_index_npz.resolve()
        try:
            index = read_ms1_scan_index_npz(index_input)
            index_provenance = read_ms1_scan_index_provenance(index_input)
            if index_provenance is None:
                raise ValueError(
                    "MS1 index source provenance is required for Pt evidence"
                )
        except (OSError, ValueError) as exc:
            print(f"failed to read MS1 index: {exc}", file=sys.stderr)
            return 2
        source_sample_stem = index_provenance.sample_stem
    else:
        if args.dll_dir is None:
            parser.error("--dll-dir is required with --raw")
        if not args.raw.is_file():
            parser.error(f"--raw does not exist: {args.raw}")
        if not args.dll_dir.is_dir():
            parser.error(f"--dll-dir does not exist: {args.dll_dir}")
        index_source = "raw"
        index_input = args.raw.resolve()
        index_provenance = None
        source_sample_stem = index_input.stem
        try:
            with open_raw(index_input, args.dll_dir.resolve()) as handle:
                index = build_ms1_scan_index(handle)
        except (MS1ScanReadError, RawReaderError) as exc:
            print(f"failed to build MS1 index: {exc}", file=sys.stderr)
            return 2
    try:
        outputs = run_same_scan_replay(
            index=index,
            candidates_csv=args.candidates_csv.resolve(),
            evidence_csv=args.evidence_csv.resolve(),
            known_diagnosis_csv=args.known_diagnosis_csv.resolve(),
            output_dir=args.output_dir.resolve(),
            report_id=args.report_id,
            source_sample_stem=source_sample_stem,
            index_input=index_input,
            settings=SameScanSettings(
                mass_tolerance_ppm=args.mass_tolerance_ppm,
                noise_level=args.noise_level,
                minimum_joint_scans=args.minimum_joint_scans,
            ),
            candidate_rt_half_window_sec=args.candidate_rt_half_window_sec,
            index_source=index_source,
            index_provenance=index_provenance,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_same_scan_replay failed: {exc}", file=sys.stderr)
        return 2

    print(f"Same-scan evidence CSV: {outputs['evidence_csv']}")
    print(f"Same-scan observations CSV: {outputs['observations_csv']}")
    print(f"Conflict components CSV: {outputs['conflicts_csv']}")
    print(f"Summary JSON: {outputs['summary_json']}")
    print(f"Human report HTML: {outputs['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
