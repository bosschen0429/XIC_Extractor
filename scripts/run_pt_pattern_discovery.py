"""CLI for the Pt pattern Discovery pipeline.

Unlike ``scripts.run_discovery``, this CLI consumes a Detected Features CSV
(not a RAW file / DLL directory), so it has no ``--raw``/``--dll-dir``
requirement. See
``docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md``
for the CLI contract.
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
        required=True,
        help="Detected Features CSV v1 input.",
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
            "Optional pt_pattern_trace_diagnosis summary CSV. When supplied, "
            "the Discovery evidence CSV evaluates trace-level accept/reject "
            "with the shared Pt evidence rule."
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

    detected_features_csv = args.detected_features_csv.resolve()
    if not detected_features_csv.is_file():
        print(
            f"{detected_features_csv}: detected features csv does not exist",
            file=sys.stderr,
        )
        return 2

    output_dir = args.output_dir.resolve()
    cid_nl_candidates_csv = (
        args.cid_nl_candidates_csv.resolve()
        if args.cid_nl_candidates_csv is not None
        else None
    )
    trace_summary_csv = (
        args.trace_summary_csv.resolve()
        if args.trace_summary_csv is not None
        else None
    )
    if trace_summary_csv is not None and not trace_summary_csv.is_file():
        print(
            f"{trace_summary_csv}: trace summary csv does not exist",
            file=sys.stderr,
        )
        return 2

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name=args.preset,
        cid_nl_candidates_csv=cid_nl_candidates_csv,
        trace_summary_csv=trace_summary_csv,
    )

    print(f"Pt pattern candidates CSV: {outputs.candidates_csv}")
    print(f"Pt pattern review CSV: {outputs.review_csv}")
    print(f"Pt pattern evidence CSV: {outputs.evidence_csv}")
    print(f"Pt pattern sidecar JSON: {outputs.sidecar_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
