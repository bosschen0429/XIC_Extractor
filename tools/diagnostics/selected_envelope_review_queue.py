"""Build selected-envelope changed-row and boundary-oracle review artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.peak_detection.selected_envelope_diagnostics import (
    SELECTED_ENVELOPE_DIAGNOSTIC_HEADERS,
    SELECTED_ENVELOPE_GATE_MANIFEST_FIELDS,
    build_selected_envelope_gate_manifest_from_rows,
)
from xic_extractor.peak_detection.selected_envelope_oracle_artifacts import (
    SELECTED_ENVELOPE_ORACLE_REVIEW_QUEUE_HEADERS,
    build_selected_envelope_oracle_review_queue,
)
from xic_extractor.tabular_io import write_tsv


@dataclass(frozen=True)
class SelectedEnvelopeReviewQueueOutputs:
    changed_rows_tsv: Path
    oracle_review_queue_tsv: Path
    diagnostic_manifest_tsv: Path
    json_path: Path


@dataclass(frozen=True)
class SelectedEnvelopeReviewQueueResult:
    input_row_count: int
    changed_row_count: int
    oracle_review_queue_row_count: int
    diagnostic_manifest: dict[str, str]


def run_selected_envelope_review_queue(
    *,
    selected_envelope_diagnostics_tsv: Path,
    output_dir: Path,
) -> tuple[SelectedEnvelopeReviewQueueOutputs, SelectedEnvelopeReviewQueueResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = SelectedEnvelopeReviewQueueOutputs(
        changed_rows_tsv=output_dir / "selected_envelope_changed_rows.tsv",
        oracle_review_queue_tsv=(
            output_dir / "selected_envelope_oracle_review_queue.tsv"
        ),
        diagnostic_manifest_tsv=(
            output_dir / "selected_envelope_diagnostic_manifest.tsv"
        ),
        json_path=output_dir / "selected_envelope_review_queue.json",
    )
    rows = tuple(
        _read_tsv(
            selected_envelope_diagnostics_tsv,
            required_columns=set(SELECTED_ENVELOPE_DIAGNOSTIC_HEADERS),
        )
    )
    changed_rows = tuple(row for row in rows if _requires_changed_row_review(row))
    oracle_review_queue = build_selected_envelope_oracle_review_queue(rows)
    manifest = build_selected_envelope_gate_manifest_from_rows(rows)
    result = SelectedEnvelopeReviewQueueResult(
        input_row_count=len(rows),
        changed_row_count=len(changed_rows),
        oracle_review_queue_row_count=len(oracle_review_queue),
        diagnostic_manifest=manifest,
    )

    _write_tsv(
        outputs.changed_rows_tsv,
        SELECTED_ENVELOPE_DIAGNOSTIC_HEADERS,
        changed_rows,
    )
    _write_tsv(
        outputs.oracle_review_queue_tsv,
        SELECTED_ENVELOPE_ORACLE_REVIEW_QUEUE_HEADERS,
        oracle_review_queue,
    )
    _write_tsv(
        outputs.diagnostic_manifest_tsv,
        SELECTED_ENVELOPE_GATE_MANIFEST_FIELDS,
        (manifest,),
    )
    _write_json(
        outputs.json_path,
        outputs=outputs,
        result=result,
        selected_envelope_diagnostics_tsv=selected_envelope_diagnostics_tsv,
    )
    return outputs, result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selected-envelope-diagnostics-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        outputs, result = run_selected_envelope_review_queue(
            selected_envelope_diagnostics_tsv=args.selected_envelope_diagnostics_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Changed rows TSV: {outputs.changed_rows_tsv}")
    print(f"Oracle review queue TSV: {outputs.oracle_review_queue_tsv}")
    print(f"Diagnostic manifest TSV: {outputs.diagnostic_manifest_tsv}")
    print(f"Review queue JSON: {outputs.json_path}")
    print(
        "Rows: "
        f"{result.input_row_count}; changed: {result.changed_row_count}; "
        f"oracle queue: {result.oracle_review_queue_row_count}; "
        f"gate: {result.diagnostic_manifest['gate_decision']}"
    )
    return 0


def _requires_changed_row_review(row: Mapping[str, str]) -> bool:
    return (
        row.get("boundary_change_class", "") != "no_change"
        or row.get("row_boundary_decision", "") != "accept_candidate"
    )


def _read_tsv(path: Path, *, required_columns: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = set(reader.fieldnames or ())
        missing = sorted(required_columns - columns)
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
        return [dict(row) for row in reader]


def _write_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        tuple(rows),
        fieldnames,
        formatter=_format_tsv_value,
        lineterminator="\n",
    )


def _format_tsv_value(value: object) -> str:
    return _sanitize_field(str(value))


def _write_json(
    path: Path,
    *,
    outputs: SelectedEnvelopeReviewQueueOutputs,
    result: SelectedEnvelopeReviewQueueResult,
    selected_envelope_diagnostics_tsv: Path,
) -> None:
    payload = {
        "readiness_label": "diagnostic_only",
        "selected_envelope_diagnostics_tsv": str(selected_envelope_diagnostics_tsv),
        "outputs": {name: str(value) for name, value in asdict(outputs).items()},
        "input_row_count": result.input_row_count,
        "changed_row_count": result.changed_row_count,
        "oracle_review_queue_row_count": result.oracle_review_queue_row_count,
        "diagnostic_manifest": result.diagnostic_manifest,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
