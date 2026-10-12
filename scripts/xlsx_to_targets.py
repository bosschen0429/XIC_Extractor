"""Convert RT-check Excel tables to XIC Extractor targets.csv format.

Usage examples
--------------
# DNA sheet only (default)
uv run python scripts/xlsx_to_targets.py path/to/RT_check.xlsx

# RNA sheet
uv run python scripts/xlsx_to_targets.py path/to/RT_check.xlsx --sheet RNA

# Both sheets together
uv run python scripts/xlsx_to_targets.py path/to/RT_check.xlsx --sheet both

# Custom output path and RT window
uv run python scripts/xlsx_to_targets.py path/to/RT_check.xlsx `
    --output config/my_targets.csv --rt-window 1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl

from xic_extractor.configuration.target_workbook import (
    DEFAULT_PPM_TOL,
    DEFAULT_RT_WINDOW,
    TargetRow,
    assign_istd_pairs,
    parse_dna_sheet,
    parse_rna_sheet,
    write_targets_csv,
)


def _pick_xlsx_via_dialog() -> Path:
    """Open a file chooser, falling back to a terminal prompt."""

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="選擇 RT 標準品 Excel 檔案",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
        )
        root.destroy()
        if not path:
            print("未選擇檔案，結束。", file=sys.stderr)
            sys.exit(0)
        return Path(path)
    except Exception:
        path_str = input("請輸入 Excel 檔案路徑：").strip().strip('"')
        return Path(path_str)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert RT-check Excel table to XIC Extractor targets.csv",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "xlsx",
        nargs="?",
        type=Path,
        help="Input Excel file (.xlsx)；省略時跳出選檔視窗",
    )
    parser.add_argument(
        "--sheet",
        choices=["DNA", "RNA", "both"],
        default="DNA",
        help="Which sheet to parse",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output CSV path. Default: same folder as the input xlsx, "
            "named <stem>_targets_<SHEET>.csv"
        ),
    )
    parser.add_argument(
        "--rt-window",
        type=float,
        default=DEFAULT_RT_WINDOW,
        metavar="MIN",
        help="RT window (± minutes) around observed RT",
    )
    parser.add_argument(
        "--ppm-tol",
        type=int,
        default=DEFAULT_PPM_TOL,
        help="XIC precursor m/z tolerance (ppm)",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    xlsx_path: Path = args.xlsx if args.xlsx is not None else _pick_xlsx_via_dialog()
    if not xlsx_path.exists():
        print(f"Error: file not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {xlsx_path} …")
    workbook = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False)
    targets: list[TargetRow] = []

    if args.sheet in ("DNA", "both"):
        if "DNA" not in workbook.sheetnames:
            print("Warning: 'DNA' sheet not found in workbook", file=sys.stderr)
        else:
            dna_targets = parse_dna_sheet(workbook["DNA"], args.rt_window)
            print(f"  DNA: {len(dna_targets)} compounds found")
            targets.extend(dna_targets)

    if args.sheet in ("RNA", "both"):
        if "RNA" not in workbook.sheetnames:
            print("Warning: 'RNA' sheet not found in workbook", file=sys.stderr)
        else:
            rna_targets = parse_rna_sheet(workbook["RNA"], args.rt_window)
            print(f"  RNA: {len(rna_targets)} compounds found")
            targets.extend(rna_targets)

    workbook.close()
    if not targets:
        print("No targets extracted. Check warnings above.", file=sys.stderr)
        sys.exit(1)

    assign_istd_pairs(targets)
    paired = sum(1 for target in targets if target["istd_pair"])
    print(f"  ISTD pairs matched: {paired}")

    if args.output is not None:
        output_path = args.output
    else:
        config_dir = Path(__file__).resolve().parent.parent / "config"
        output_path = config_dir / f"{xlsx_path.stem}_targets_{args.sheet}.csv"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_targets_csv(targets, output_path)


if __name__ == "__main__":
    main()
