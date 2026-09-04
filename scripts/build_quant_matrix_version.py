"""Build QuantMatrixVersion v1 from a ProductionAcceptanceManifest.

This QuantMatrixVersion activation renderer writes explicit activation artifacts
only. It does not run RAW, recompute evidence, change ProductWriter defaults,
update workbooks, or touch GUI behavior.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_production_acceptance_manifest import (
    check_production_acceptance_manifest_schema,
)
from xic_extractor.alignment.production_acceptance import (
    ProductionAcceptanceManifestError,
    build_quant_matrix_version_from_checked_manifest,
    load_checked_production_acceptance_manifest,
)
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
    SOURCE_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import (
    read_tsv_with_header_snapshot,
    write_tsv,
)


def run_activation(
    *,
    input_quant_matrix_tsv: Path,
    input_matrix_identity_tsv: Path,
    production_acceptance_manifest_tsv: Path,
    expected_diff_tsv: Path,
    output_dir: Path,
    manifest_root: Path | None = None,
) -> Mapping[str, Path]:
    repo_root = manifest_root or production_acceptance_manifest_tsv.parent
    problems = check_production_acceptance_manifest_schema()
    if problems:
        raise ValueError(
            "ProductionAcceptanceManifest failed validation: "
            + "; ".join(problems),
        )
    try:
        checked_manifest = load_checked_production_acceptance_manifest(
            manifest_path=production_acceptance_manifest_tsv,
            repo_root=repo_root,
        )
    except ProductionAcceptanceManifestError as exc:
        raise ValueError(
            "ProductionAcceptanceManifest failed validation: " + str(exc)
        ) from exc
    matrix_header, matrix_rows, matrix_sha256 = read_tsv_with_header_snapshot(
        input_quant_matrix_tsv,
        encoding="utf-8-sig",
    )
    _identity_header, identity_rows, identity_sha256 = (
        read_tsv_with_header_snapshot(
            input_matrix_identity_tsv,
            required_columns=(
                "matrix_row_index",
                "peak_hypothesis_id",
                "source_feature_family_ids",
            ),
            encoding="utf-8-sig",
        )
    )
    _expected_header, expected_diff_rows, expected_diff_sha256 = (
        read_tsv_with_header_snapshot(
            expected_diff_tsv,
            exact_columns=EXPECTED_DIFF_COLUMNS,
            encoding="utf-8-sig",
        )
    )
    outputs = build_quant_matrix_version_from_checked_manifest(
        matrix_header=matrix_header,
        input_quant_matrix_rows=matrix_rows,
        input_matrix_identity_rows=identity_rows,
        checked_manifest=checked_manifest,
        expected_diff_rows=expected_diff_rows,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    quant_matrix_tsv = output_dir / "quant_matrix.tsv"
    cell_provenance_tsv = output_dir / "cell_provenance.tsv"
    row_summary_tsv = output_dir / "row_summary.tsv"
    expected_diff_summary_tsv = output_dir / "expected_diff_summary.tsv"
    source_summary_tsv = output_dir / "source_summary.tsv"
    write_tsv(
        quant_matrix_tsv,
        outputs.quant_matrix_rows,
        matrix_header,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        cell_provenance_tsv,
        outputs.cell_provenance_rows,
        CELL_PROVENANCE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        row_summary_tsv,
        outputs.row_summary_rows,
        ROW_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        expected_diff_summary_tsv,
        outputs.expected_diff_summary_rows,
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        source_summary_tsv,
        [
            {
                "schema_version": "quant_matrix_version_source_summary_v1",
                "input_quant_matrix_tsv": str(input_quant_matrix_tsv),
                "input_quant_matrix_sha256": matrix_sha256,
                "input_matrix_identity_tsv": str(input_matrix_identity_tsv),
                "input_matrix_identity_sha256": identity_sha256,
                "production_acceptance_manifest_tsv": str(
                    production_acceptance_manifest_tsv,
                ),
                "production_acceptance_manifest_sha256": (
                    checked_manifest.manifest_file_sha256
                ),
                "expected_diff_tsv": str(expected_diff_tsv),
                "expected_diff_sha256": expected_diff_sha256,
            }
        ],
        SOURCE_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    return {
        "quant_matrix": quant_matrix_tsv,
        "cell_provenance": cell_provenance_tsv,
        "row_summary": row_summary_tsv,
        "expected_diff_summary": expected_diff_summary_tsv,
        "source_summary": source_summary_tsv,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_activation(
            input_quant_matrix_tsv=args.input_quant_matrix_tsv,
            input_matrix_identity_tsv=args.input_matrix_identity_tsv,
            production_acceptance_manifest_tsv=args.production_acceptance_manifest_tsv,
            expected_diff_tsv=args.expected_diff_tsv,
            output_dir=args.output_dir,
            manifest_root=args.manifest_root,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-quant-matrix-tsv", type=Path, required=True)
    parser.add_argument("--input-matrix-identity-tsv", type=Path, required=True)
    parser.add_argument(
        "--production-acceptance-manifest-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--expected-diff-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest-root",
        type=Path,
        help=(
            "Root used to resolve source relpaths inside the production "
            "acceptance manifest. Defaults to the manifest parent directory."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
