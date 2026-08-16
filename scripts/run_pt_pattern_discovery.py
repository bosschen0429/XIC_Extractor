"""CLI for the Pt pattern Discovery pipeline.

Unlike ``scripts.run_discovery``, this CLI consumes a Detected Features CSV
(not a RAW file / DLL directory), so it has no ``--raw``/``--dll-dir``
requirement. See ``docs/product/discovery.md`` for the current CLI and product
boundary contract.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.delta_mass_pipeline import run_pt_pattern_discovery
from xic_extractor.presets import PresetError, load_preset


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Pt pattern Discovery against a Detected Features CSV, "
            "optionally intersected with existing CID-NL(dR) candidate "
            "evidence and optional trace-summary evidence."
        )
    )
    parser.add_argument(
        "--detected-features-csv",
        type=Path,
        help=(
            "Detected Features CSV v1 input. Mutually exclusive with "
            "--external-feature-table-tsv and --mzmine-feature-table-csv."
        ),
    )
    parser.add_argument(
        "--external-feature-table-tsv",
        type=Path,
        help=(
            "External detected-feature table TSV. The pipeline writes a "
            "canonical detected_features.csv copy into --output-dir before "
            "matching."
        ),
    )
    parser.add_argument(
        "--mzmine-feature-table-csv",
        type=Path,
        help=(
            "MZmine wide feature table CSV with row ID, row m/z, row retention "
            "time, and '<sample>.mzML Peak area' columns. The pipeline writes "
            "a canonical detected_features.csv copy into --output-dir before "
            "matching."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "pt_pattern",
        help=(
            "Output directory for pt_pattern_candidates.csv, "
            "pt_pattern_review.csv, pt_pattern_evidence.csv, and "
            "pt_pattern_sidecar.json."
        ),
    )
    parser.add_argument(
        "--preset",
        default="pt_pattern",
        help="Pt pattern preset name (e.g. pt_pattern, pt_pattern_cid_nl_dr).",
    )
    parser.add_argument(
        "--cid-nl-candidates-csv",
        type=Path,
        help=(
            "Existing discovery_candidates.csv, required when --preset is a "
            "composite Pt pattern + CID-NL(dR) preset such as "
            "pt_pattern_cid_nl_dr."
        ),
    )
    parser.add_argument(
        "--trace-summary-csv",
        type=Path,
        help=(
            "Optional full-context pt_pattern_trace_diagnosis summary CSV. "
            "When supplied, its sibling metrics CSV and trace manifest are "
            "verified before Discovery evidence is evaluated."
        ),
    )
    parser.add_argument(
        "--trace-metrics-csv",
        type=Path,
        help=(
            "Optional metrics CSV override for --trace-summary-csv; defaults "
            "to the canonical full-context sibling path."
        ),
    )
    parser.add_argument(
        "--trace-manifest-json",
        type=Path,
        help=(
            "Optional trace manifest override for --trace-summary-csv; "
            "defaults to the canonical full-context sibling path."
        ),
    )
    return parser


def _preset_requires_cid_nl_csv(preset_name: str) -> bool:
    """Return whether ``preset_name`` is a composite Pt pattern + CID-NL(dR) preset.

    Mirrors ``run_pt_pattern_discovery()``'s own composite check (a
    ``neutral_loss`` tag alongside the ``delta_mass`` tag) so the CLI can
    fail fast via ``parser.error()`` (argparse-level ``SystemExit(2)``)
    before ever calling the pipeline. If the preset itself cannot be loaded
    (unknown name, malformed file), this returns ``False`` and lets
    ``run_pt_pattern_discovery()`` raise the real, more specific error.
    """

    try:
        preset = load_preset(preset_name)
    except PresetError:
        return False
    return any(tag.strategy == "neutral_loss" for tag in preset.tags)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if _preset_requires_cid_nl_csv(args.preset) and args.cid_nl_candidates_csv is None:
        parser.error(
            "--cid-nl-candidates-csv is required when --preset is a "
            "composite Pt pattern + CID-NL(dR) preset"
        )

    feature_source_count = sum(
        source is not None
        for source in (
            args.detected_features_csv,
            args.external_feature_table_tsv,
            args.mzmine_feature_table_csv,
        )
    )
    if feature_source_count != 1:
        parser.error(
            "pass exactly one feature source: --detected-features-csv, "
            "--external-feature-table-tsv, or --mzmine-feature-table-csv"
        )

    detected_features_csv = args.detected_features_csv
    resolved_detected_features_csv = (
        detected_features_csv.resolve()
        if args.detected_features_csv is not None
        else None
    )
    external_feature_table_tsv = args.external_feature_table_tsv
    resolved_external_feature_table_tsv = (
        external_feature_table_tsv.resolve()
        if args.external_feature_table_tsv is not None
        else None
    )
    mzmine_feature_table_csv = args.mzmine_feature_table_csv
    resolved_mzmine_feature_table_csv = (
        mzmine_feature_table_csv.resolve()
        if args.mzmine_feature_table_csv is not None
        else None
    )
    if (
        resolved_detected_features_csv is not None
        and not resolved_detected_features_csv.is_file()
    ):
        print(
            f"{resolved_detected_features_csv}: detected features csv does not exist",
            file=sys.stderr,
        )
        return 2
    if (
        resolved_external_feature_table_tsv is not None
        and not resolved_external_feature_table_tsv.is_file()
    ):
        print(
            f"{resolved_external_feature_table_tsv}: "
            "external feature table does not exist",
            file=sys.stderr,
        )
        return 2
    if (
        resolved_mzmine_feature_table_csv is not None
        and not resolved_mzmine_feature_table_csv.is_file()
    ):
        print(
            f"{resolved_mzmine_feature_table_csv}: MZmine feature table does not exist",
            file=sys.stderr,
        )
        return 2

    output_dir = args.output_dir.resolve()
    cid_nl_candidates_csv = args.cid_nl_candidates_csv
    resolved_cid_nl_candidates_csv = (
        cid_nl_candidates_csv.resolve()
        if args.cid_nl_candidates_csv is not None
        else None
    )
    if (
        resolved_cid_nl_candidates_csv is not None
        and not resolved_cid_nl_candidates_csv.is_file()
    ):
        print(
            f"{resolved_cid_nl_candidates_csv}: CID-NL candidates csv does not exist",
            file=sys.stderr,
        )
        return 2
    trace_summary_csv = args.trace_summary_csv
    resolved_trace_summary_csv = (
        trace_summary_csv.resolve()
        if args.trace_summary_csv is not None
        else None
    )
    if (
        resolved_trace_summary_csv is not None
        and not resolved_trace_summary_csv.is_file()
    ):
        print(
            f"{resolved_trace_summary_csv}: trace summary csv does not exist",
            file=sys.stderr,
        )
        return 2
    if trace_summary_csv is None and (
        args.trace_metrics_csv is not None or args.trace_manifest_json is not None
    ):
        parser.error(
            "--trace-metrics-csv/--trace-manifest-json require "
            "--trace-summary-csv"
        )
    resolved_trace_metrics_csv = (
        args.trace_metrics_csv.resolve()
        if args.trace_metrics_csv is not None
        else None
    )
    resolved_trace_manifest_json = (
        args.trace_manifest_json.resolve()
        if args.trace_manifest_json is not None
        else None
    )
    for trace_path, label in (
        (resolved_trace_metrics_csv, "trace metrics csv"),
        (resolved_trace_manifest_json, "trace manifest json"),
    ):
        if trace_path is not None and not trace_path.is_file():
            print(f"{trace_path}: {label} does not exist", file=sys.stderr)
            return 2

    try:
        trace_overrides = {}
        if resolved_trace_metrics_csv is not None:
            trace_overrides["trace_metrics_csv"] = resolved_trace_metrics_csv
        if resolved_trace_manifest_json is not None:
            trace_overrides["trace_manifest_json"] = resolved_trace_manifest_json
        outputs = run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            external_feature_table_tsv=external_feature_table_tsv,
            mzmine_feature_table_csv=mzmine_feature_table_csv,
            output_dir=output_dir,
            preset_name=args.preset,
            cid_nl_candidates_csv=cid_nl_candidates_csv,
            trace_summary_csv=trace_summary_csv,
            **trace_overrides,
        )
    except (OSError, PresetError, ValueError) as exc:
        print(f"pt_pattern_discovery failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern candidates CSV: {outputs.candidates_csv}")
    print(f"Pt pattern review CSV: {outputs.review_csv}")
    print(f"Pt pattern evidence CSV: {outputs.evidence_csv}")
    print(f"Pt pattern sidecar JSON: {outputs.sidecar_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
