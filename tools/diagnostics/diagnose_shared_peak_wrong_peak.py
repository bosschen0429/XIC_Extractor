"""Diagnose wrong-peak machine evidence without mutating product outputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.alignment.shared_peak_identity_explanation import (
    wrong_peak_root_cause,
)
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    WRONG_PEAK_ROOT_CAUSE_COLUMNS,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv

_MACHINE_SUPPORT_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_id",
    "machine_current_label",
    "observed_machine_metrics",
    "missing_machine_evidence",
)
_ALIGNMENT_CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_diagnostic(
            machine_evidence_support_tsv=args.machine_evidence_support_tsv,
            alignment_cells_tsv=args.alignment_cells_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def run_diagnostic(
    *,
    machine_evidence_support_tsv: Path,
    alignment_cells_tsv: Path,
    output_dir: Path,
) -> Mapping[str, Path]:
    support_rows = read_tsv_required(
        machine_evidence_support_tsv,
        _MACHINE_SUPPORT_REQUIRED_COLUMNS,
    )
    cell_rows = read_tsv_required(
        alignment_cells_tsv,
        _ALIGNMENT_CELL_REQUIRED_COLUMNS,
    )
    rows = wrong_peak_root_cause.build_wrong_peak_root_cause_rows(
        machine_evidence_support_rows=support_rows,
        alignment_cell_rows=cell_rows,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "shared_peak_identity_wrong_peak_root_cause.tsv"
    write_tsv(path, rows, WRONG_PEAK_ROOT_CAUSE_COLUMNS, lineterminator="\n")
    return {"wrong_peak_root_cause": path}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--machine-evidence-support-tsv",
        type=Path,
        required=True,
        help="shared_peak_identity_machine_evidence_support.tsv",
    )
    parser.add_argument(
        "--alignment-cells-tsv",
        type=Path,
        required=True,
        help="alignment_cells.tsv used by the explanation run",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for wrong-peak root-cause sidecar outputs",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
