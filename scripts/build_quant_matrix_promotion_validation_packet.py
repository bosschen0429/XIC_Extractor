"""Build/check a QuantMatrix promotion validation evidence packet.

This adapter is no-RAW and read-only. It binds existing evidence artifacts by
copying them into a self-contained packet and recording source/copy hashes. It
does not run a scorer, read RAW, mutate ProductWriter, change default quant
matrix authority, update workbooks, or touch GUI behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.alignment.quant_matrix_promotion import (
    evaluate_quant_matrix_promotion_readiness,
)
from xic_extractor.alignment.quant_matrix_report import QUANT_MATRIX_REVIEW_SCHEMA
from xic_extractor.alignment.quant_matrix_validation_packet import (
    ValidationEvidenceArtifact,
    build_quant_matrix_validation_evidence_packet,
    validate_quant_matrix_validation_evidence_packet,
)
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import write_tsv

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT_DIR = Path(
    "output/validation/quant_matrix_evidence_packet",
)
DEFAULT_LARGE_COHORT_ARTIFACT = Path(
    "docs/superpowers/validation/quant_matrix_real_bundle/"
    "source_artifacts/cohort_activation_summary.json",
)
DEFAULT_HELDOUT_ORACLE_ARTIFACT = Path(
    "docs/superpowers/validation/quant_matrix_real_bundle/"
    "source_artifacts/heldout_oracle_summary.json",
)
DEFAULT_PACKET_ID = "quant-matrix-evidence-packet"
DEFAULT_COHORT_ID = "seed-guard-realdata-full_cohort-consolidated-20260617"
DEFAULT_ORACLE_PACKET_ID = (
    "seed-guard-realdata-full_cohort-low-height-low-scan-clean-smoke-20260617"
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        evidence_json = args.validation_evidence_json or (
            args.output_dir / "quant_matrix_validation_evidence.json"
        )
        problems = validate_quant_matrix_validation_evidence_packet(
            evidence_json,
            source_root=args.source_root,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"validation_evidence_json: {evidence_json}")
        print("packet_status: pass")
        return 0

    evidence_artifacts = _evidence_artifacts_from_args(args)
    try:
        outputs = build_quant_matrix_validation_evidence_packet(
            output_dir=args.output_dir,
            evidence_artifacts=evidence_artifacts,
            packet_id=args.packet_id,
            requested_readiness_label=args.requested_readiness_label,
            source_root=args.source_root,
        )
        if args.write_readiness_fixture:
            fixture_outputs = _write_readiness_integration_fixture(
                args.output_dir,
                validation_evidence_json=outputs["validation_evidence_json"],
            )
            outputs = {**outputs, **fixture_outputs}
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--packet-id", default=DEFAULT_PACKET_ID)
    parser.add_argument("--requested-readiness-label", default="production_ready")
    parser.add_argument(
        "--large-cohort-artifact",
        type=Path,
        default=DEFAULT_LARGE_COHORT_ARTIFACT,
        help="Existing large-cohort artifact to copy/hash into the packet.",
    )
    parser.add_argument("--cohort-id", default=DEFAULT_COHORT_ID)
    parser.add_argument("--raw-run-count", type=int, default=85)
    parser.add_argument(
        "--heldout-oracle-artifact",
        type=Path,
        default=DEFAULT_HELDOUT_ORACLE_ARTIFACT,
        help="Existing heldout-oracle artifact to copy/hash into the packet.",
    )
    parser.add_argument("--oracle-packet-id", default=DEFAULT_ORACLE_PACKET_ID)
    parser.add_argument(
        "--downstream-impact-artifact",
        type=Path,
        help=(
            "Optional downstream-impact smoke artifact. Omit when this evidence "
            "is not yet available; the packet will remain science-inconclusive."
        ),
    )
    parser.add_argument("--downstream-scope", default="")
    parser.add_argument(
        "--write-readiness-fixture",
        action="store_true",
        help=(
            "Also write a synthetic contract-ready fixture and run the promotion "
            "readiness evaluation against this packet."
        ),
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--validation-evidence-json", type=Path)
    return parser.parse_args(argv)


def _evidence_artifacts_from_args(
    args: argparse.Namespace,
) -> list[ValidationEvidenceArtifact]:
    artifacts = [
        ValidationEvidenceArtifact(
            tier="full_cohort",
            status="pass",
            source_artifact=args.large_cohort_artifact,
            cohort_id=args.cohort_id,
            raw_run_count=args.raw_run_count,
            evidence_note=(
                "Existing no-RAW FULL_COHORT consolidated standard-peak activation "
                "input summary; large-cohort evidence only, not matrix authority."
            ),
        ),
        ValidationEvidenceArtifact(
            tier="heldout_oracle",
            status="pass",
            source_artifact=args.heldout_oracle_artifact,
            oracle_packet_id=args.oracle_packet_id,
            evidence_note=(
                "Existing 20-case heldout trace reintegration oracle smoke pass; "
                "oracle evidence only, not truth authority."
            ),
        ),
    ]
    if args.downstream_impact_artifact is not None:
        artifacts.append(
            ValidationEvidenceArtifact(
                tier="downstream_impact_smoke",
                status="pass",
                source_artifact=args.downstream_impact_artifact,
                downstream_scope=args.downstream_scope,
                evidence_note="Existing downstream-impact smoke artifact.",
            )
        )
    return artifacts


def _write_readiness_integration_fixture(
    output_dir: Path,
    *,
    validation_evidence_json: Path,
) -> dict[str, Path]:
    fixture_dir = output_dir / "readiness_integration_fixture"
    inputs_dir = fixture_dir / "inputs"
    readiness_dir = fixture_dir / "readiness"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    expected_diff_summary = inputs_dir / "expected_diff_summary.tsv"
    cell_provenance = inputs_dir / "cell_provenance.tsv"
    row_summary = inputs_dir / "row_summary.tsv"
    review_summary = inputs_dir / "quant_matrix_review_summary.json"

    _write_contract_ready_inputs(
        expected_diff_summary=expected_diff_summary,
        cell_provenance=cell_provenance,
        row_summary=row_summary,
        review_summary=review_summary,
    )
    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=expected_diff_summary,
        cell_provenance_tsv=cell_provenance,
        row_summary_tsv=row_summary,
        review_summary_json=review_summary,
        validation_evidence_json=validation_evidence_json,
        output_dir=readiness_dir,
    )
    return {
        "readiness_fixture_expected_diff_summary": expected_diff_summary,
        "readiness_fixture_cell_provenance": cell_provenance,
        "readiness_fixture_row_summary": row_summary,
        "readiness_fixture_review_summary": review_summary,
        "readiness_fixture_summary_json": outputs["summary_json"],
        "readiness_fixture_checks_tsv": outputs["checks_tsv"],
    }


def _write_contract_ready_inputs(
    *,
    expected_diff_summary: Path,
    cell_provenance: Path,
    row_summary: Path,
    review_summary: Path,
) -> None:
    write_tsv(
        expected_diff_summary,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff_summary",
                "acceptance_status": "pass",
                "expected_diff_count": "1",
                "written_backfill_count": "1",
                "unused_expected_diff_count": "0",
                "blocking_reasons": "none",
            }
        ],
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        cell_provenance,
        [
            {
                "schema_version": "quant_matrix_cell_provenance",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleA",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "100",
                "cell_status": "detected",
                "value_source": "input_quant_matrix",
                "write_authority": "FALSE",
                "acceptance_decision": "not_applicable",
                "acceptance_basis": "not_applicable",
                "truth_status": "not_applicable",
                "quant_value_source": "input_quant_matrix",
                "matrix_area_source": "input_quant_matrix",
                "source_artifact_relpath": "not_applicable",
                "source_artifact_sha256": "not_applicable",
                "source_row_sha256": "not_applicable",
                "manifest_sha256": "not_applicable",
            },
            {
                "schema_version": "quant_matrix_cell_provenance",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleB",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "222.2",
                "cell_status": "accepted_backfill",
                "value_source": "ProductionAcceptanceManifest",
                "write_authority": "TRUE",
                "acceptance_decision": "accept_basic_backfill",
                "acceptance_basis": "machine_basic",
                "truth_status": "not_truth_claimed",
                "quant_value_source": "gaussian_smoothed_integration",
                "matrix_area_source": "gaussian_smoothed_boundary_integration",
                "source_artifact_relpath": "sources/cell_evidence.tsv",
                "source_artifact_sha256": "A" * 64,
                "source_row_sha256": "B" * 64,
                "manifest_sha256": "C" * 64,
            },
        ],
        CELL_PROVENANCE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        row_summary,
        [
            {
                "schema_version": "quant_matrix_row_summary",
                "peak_hypothesis_id": "PH001",
                "source_feature_family_ids": "FAM001",
                "detected_count": "1",
                "accepted_backfilled_count": "1",
                "quant_available_count": "2",
                "missing_count": "0",
                "backfill_fraction": "0.500000",
                "prevalence_flags": "low_seed_support",
            }
        ],
        ROW_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    review_summary.write_text(
        json.dumps(
            {
                "schema_version": QUANT_MATRIX_REVIEW_SCHEMA,
                "validation_label": "shadow_review",
                "accepted_backfill_count": 1,
                "detected_count": 1,
                "report_only_risk_count": 1,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
