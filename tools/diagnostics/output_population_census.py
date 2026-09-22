"""Report published tabular outputs that carry no information.

For each ``.tsv`` and ``.csv`` file directly inside an output directory, the
census reports a file with no data rows, a file with exactly one data row, and,
when a file has at least two data rows, every column whose value is the same on
all rows. A finding is evidence for triage: an expected-empty or legitimately
constant field is not a defect. The report never fails.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.diagnostics.diagnostic_io import write_tsv

REPORT_COLUMNS = ("artifact", "finding", "column", "value", "data_row_count")
_DELIMITERS = {".tsv": "\t", ".csv": ","}


@dataclass(frozen=True, slots=True)
class PopulationFinding:
    artifact: str
    finding: str
    column: str
    value: str
    data_row_count: int


def census_output_directory(directory: Path) -> tuple[PopulationFinding, ...]:
    findings: list[PopulationFinding] = []
    for path in sorted(directory.iterdir()):
        delimiter = _DELIMITERS.get(path.suffix.lower())
        if delimiter is None or not path.is_file():
            continue
        findings.extend(_census_table(path, delimiter))
    return tuple(findings)


def _census_table(path: Path, delimiter: str) -> Iterator[PopulationFinding]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, None)
        if header is None:
            yield PopulationFinding(path.name, "no_header", "", "", 0)
            return
        first: list[str] | None = None
        constant = [True] * len(header)
        row_count = 0
        for row in reader:
            row_count += 1
            if first is None:
                first = row
                continue
            for index, value in enumerate(constant):
                if value and _cell(row, index) != _cell(first, index):
                    constant[index] = False
    if row_count == 0:
        yield PopulationFinding(path.name, "no_data_rows", "", "", 0)
        return
    if row_count == 1:
        yield PopulationFinding(path.name, "single_data_row", "", "", 1)
        return
    assert first is not None
    for index, column in enumerate(header):
        if constant[index]:
            yield PopulationFinding(
                path.name, "constant_column", column, _cell(first, index), row_count
            )


def _cell(row: Sequence[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report tabular outputs in a run directory that are empty, single-row, "
            "or hold one value in a column. Report-only: always exits 0 on a "
            "readable directory."
        ),
    )
    parser.add_argument("output_dir", type=Path, help="Run output directory.")
    parser.add_argument(
        "--output-tsv",
        type=Path,
        help="Write the report here instead of standard output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.output_dir.is_dir():
        print(f"not a directory: {args.output_dir}", file=sys.stderr)
        return 2
    findings = census_output_directory(args.output_dir)
    rows = [
        {
            "artifact": finding.artifact,
            "finding": finding.finding,
            "column": finding.column,
            "value": finding.value,
            "data_row_count": finding.data_row_count,
        }
        for finding in findings
    ]
    if args.output_tsv is None:
        print("\t".join(REPORT_COLUMNS))
        for row in rows:
            print("\t".join(str(row[column]) for column in REPORT_COLUMNS))
    else:
        args.output_tsv.parent.mkdir(parents=True, exist_ok=True)
        write_tsv(args.output_tsv, rows, REPORT_COLUMNS)
    print(f"population findings: {len(findings)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
