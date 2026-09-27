"""Produce read-only RAW injection chronology for diagnostics and offline readers."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

from xic_extractor.injection_timeline import (
    PerturbationPolicy,
    build_injection_timeline,
    compare_sequence,
)
from xic_extractor.raw_reader import (
    preflight_raw_reader,
    read_raw_injection_header,
    read_sld_injections,
)


def discover_raw_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        raise ValueError(f"RAW root is not a directory: {root}")
    return tuple(
        sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".raw")
    )


def _datetime_json(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = PerturbationPolicy()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON sidecar; readable TSV written alongside",
    )
    parser.add_argument(
        "--sld",
        type=Path,
        action="append",
        default=[],
        help="Optional sequence file; repeat for segments",
    )
    parser.add_argument(
        "--max-previous-run-min", type=float, default=defaults.max_previous_run_min
    )
    parser.add_argument(
        "--max-previous-run-fraction",
        type=float,
        default=defaults.max_previous_run_fraction,
    )
    parser.add_argument("--max-idle-min", type=float, default=defaults.max_idle_min)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check paths, DLLs and count RAW without opening RAW",
    )
    args = parser.parse_args(argv)
    started = perf_counter()
    policy = PerturbationPolicy(
        args.max_previous_run_min, args.max_previous_run_fraction, args.max_idle_min
    )
    paths = discover_raw_files(args.raw_root)
    if not paths:
        raise ValueError(f"No RAW files found: {args.raw_root}")
    errors = preflight_raw_reader(args.dll_dir)
    if errors:
        raise ValueError("; ".join(errors))
    for path in args.sld:
        if not path.is_file():
            raise ValueError(f"Sequence file is missing: {path}")
    if args.output.suffix.lower() != ".json":
        raise ValueError("--output must have a .json suffix")
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "raw_count": len(paths),
                    "sld_count": len(args.sld),
                    "policy": asdict(policy),
                    "raw_root": str(args.raw_root.resolve()),
                }
            )
        )
        return 0
    headers = []
    for index, path in enumerate(paths, start=1):
        print(f"Reading RAW {index}/{len(paths)}: {path.name}", flush=True)
        headers.append(read_raw_injection_header(path, args.dll_dir))
        print(f"RAW {index}/{len(paths)}: {path.name}", flush=True)
    timeline = build_injection_timeline(headers, policy)
    scheduled = tuple(
        entry for path in args.sld for entry in read_sld_injections(path, args.dll_dir)
    )
    comparisons = compare_sequence(scheduled, timeline)
    payload = {
        "schema": "injection_timeline",
        "schema_version": 1,
        "evidence_status": "diagnostic_only",
        "raw_root": str(args.raw_root.resolve()),
        "policy": asdict(policy),
        "raw_file_count": len(paths),
        "injection_count": len(timeline),
        "duplicate_copy_count": len(paths) - len(timeline),
        "injections": [asdict(row) for row in timeline],
        "sequence_comparisons": [asdict(row) for row in comparisons],
        "elapsed_seconds": perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            payload,
            default=_datetime_json,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    columns = (
        "injection_order",
        "raw_stem",
        "acquisition_stem",
        "renamed_after_acquisition",
        "acquisition_start",
        "acquisition_end",
        "run_length_min",
        "previous_injection_order",
        "previous_raw_stem",
        "previous_run_length_min",
        "idle_min",
        "possibly_perturbed",
        "timing_warning",
    )
    with args.output.with_suffix(".tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                *columns,
                "instrument_method",
                "acquisition_source",
                "copy_count",
            ),
            delimiter="\t",
        )
        writer.writeheader()
        for row in timeline:
            record = asdict(row)
            writer.writerow(
                {
                    **{key: record[key] for key in columns},
                    "instrument_method": row.header.instrument_method,
                    "acquisition_source": row.header.acquisition_source,
                    "copy_count": len(row.copy_paths),
                }
            )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "injections": len(timeline),
                "duplicates": len(paths) - len(timeline),
                "flagged": sum(r.possibly_perturbed for r in timeline),
                "elapsed_seconds": payload["elapsed_seconds"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
