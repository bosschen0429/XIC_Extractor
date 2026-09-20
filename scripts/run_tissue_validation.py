from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from xic_extractor.alignment.drift_evidence import read_known_istd_reference
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targets import read_istd_targets
from xic_extractor.discovery.blank_qualification import BlankBackgroundLibrary
from xic_extractor.execution_settings import load_execution_profile
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.presets import bind_cid_nl_recipe, load_preset
from xic_extractor.raw_reader import preflight_raw_reader

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TARGETS = _REPO_ROOT / "config" / "targets.example.csv"
_LOCAL_ENV = _REPO_ROOT / ".env.xic-local"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        plan = _build_plan(args)
    except (ConfigError, OSError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.preflight_only:
        return 0

    output_dir = Path(plan["execution"]["output_dir"])
    output_dir.mkdir(parents=True)
    plan_path = output_dir / "validation_plan.json"
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "logs").mkdir()
    (output_dir / "timing").mkdir()

    stage_timings: dict[str, dict[str, float | int]] = {}
    for stage in ("discovery", "alignment_preflight", "alignment"):
        command = plan["commands"][stage]
        log_path = output_dir / "logs" / f"{stage}.log"
        started = time.perf_counter()
        returncode = _run_command(command, log_path)
        stage_timings[stage] = {
            "elapsed_seconds": time.perf_counter() - started,
            "returncode": returncode,
        }
        (output_dir / "stage_timings.json").write_text(
            json.dumps(stage_timings, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if returncode != 0:
            print(f"{stage} failed; see {log_path}", file=sys.stderr)
            return returncode
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the shared 8RAW/85RAW tissue Discovery→Alignment validation."
    )
    parser.add_argument("--dataset", choices=("8", "85"), required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=_positive_int)
    parser.add_argument("--alignment-workers", type=_positive_int)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--dll-dir", type=Path)
    parser.add_argument(
        "--blank-library", type=Path,
        help="Explicitly opt in to blank filtering with this library (default: off).",
    )
    istd = parser.add_mutually_exclusive_group()
    istd.add_argument("--targets-csv", type=Path)
    istd.add_argument("--known-istd-reference-workbook", type=Path)
    return parser.parse_args(argv)


def _build_plan(args: argparse.Namespace) -> dict[str, Any]:
    dataset = int(args.dataset)
    execution_profile = load_execution_profile(
        parallel_workers=args.workers,
        alignment_workers=args.alignment_workers,
    )
    environment = _effective_environment()
    raw_dir = _resolve_input_dir(
        args.raw_dir,
        environment,
        "XIC_RAW_VALIDATION_DIR" if dataset == 8 else "XIC_RAW_ROOT",
        label="RAW",
    )
    dll_dir = _resolve_input_dir(
        args.dll_dir,
        environment,
        "THERMO_RAWFILE_READER_DLL_DIR",
        label="DLL",
    )
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (
            _REPO_ROOT
            / "output"
            / "tissue-validation"
            / f"{dataset}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        ).resolve()
    )
    if output_dir.exists():
        raise ValueError(f"{output_dir}: output directory already exists")

    raw_paths = tuple(
        sorted(
            (
                path.resolve()
                for path in raw_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".raw"
            ),
            key=lambda path: path.name.casefold(),
        )
    )
    if len(raw_paths) != dataset:
        raise ValueError(
            f"{raw_dir}: expected {dataset} nonrecursive .raw files, "
            f"found {len(raw_paths)}"
        )
    dll_errors = preflight_raw_reader(dll_dir)
    if dll_errors:
        raise ValueError("Thermo DLL preflight failed:\n- " + "\n- ".join(dll_errors))

    blank_library = None
    if args.blank_library is not None:
        blank_library = args.blank_library.resolve()
        BlankBackgroundLibrary.load_json(blank_library)
    targets_csv, workbook = _validate_istd_input(args)

    discovery_dir = output_dir / "discovery"
    alignment_dir = output_dir / "alignment"
    timing_dir = output_dir / "timing"
    discovery_command = [
        sys.executable,
        "-m",
        "scripts.run_discovery",
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(discovery_dir),
        "--preset",
        "dna_dr",
        "--max-workers",
        str(execution_profile.parallel_workers),
        "--timing-output",
        str(timing_dir / "discovery.json"),
    ]
    alignment_command = [
        sys.executable,
        "-m",
        "scripts.run_alignment",
        "--preset",
        "dna_dr",
        "--discovery-batch-index",
        str(discovery_dir / "discovery_batch_index.csv"),
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(alignment_dir),
        "--raw-workers",
        str(execution_profile.alignment_workers),
        "--expected-sample-count",
        str(dataset),
        "--output-level",
        "validation-minimal",
        "--timing-output",
        str(timing_dir / "alignment.json"),
        "--timing-live-output",
        str(timing_dir / "alignment.live.json"),
    ]
    if blank_library is not None:
        discovery_command.extend(["--blank-library", str(blank_library)])
        alignment_command.extend(["--blank-library", str(blank_library)])
    if workbook is not None:
        alignment_command.extend(
            ["--known-istd-reference-workbook", str(workbook)]
        )
    else:
        alignment_command.extend(["--targets-csv", str(targets_csv)])

    recipe = bind_cid_nl_recipe(load_preset("dna_dr"), "full")
    effective_discovery = replace(
        recipe.discovery_settings,
        enable_blank_qualification=blank_library is not None,
        blank_library_path=None,
    )
    return {
        "dataset": dataset,
        "science": {
            "preset": "dna_dr",
            "discovery": _json_value(effective_discovery),
            "alignment": _json_value(recipe.alignment_config),
            "owner_build_xic_backend": recipe.owner_build_xic_backend,
            "signal_processing": _json_value(DEFAULT_SIGNAL_PROCESSING_PROFILE),
        },
        "inputs": {
            "raw_dir": str(raw_dir),
            "raw_count": len(raw_paths),
            "dll_dir": str(dll_dir),
            "blank_library": str(blank_library) if blank_library is not None else None,
            "targets_csv": str(targets_csv) if targets_csv is not None else None,
            "known_istd_reference_workbook": (
                str(workbook) if workbook is not None else None
            ),
        },
        "execution": {
            "parallel_workers": execution_profile.parallel_workers,
            "alignment_workers": execution_profile.alignment_workers,
            "output_dir": str(output_dir),
            "timing_dir": str(timing_dir),
            "logs_dir": str(output_dir / "logs"),
        },
        "commands": {
            "discovery": discovery_command,
            "alignment_preflight": alignment_command + ["--preflight-only"],
            "alignment": alignment_command,
        },
    }


def _effective_environment() -> dict[str, str]:
    values = _read_local_environment(_LOCAL_ENV)
    values.update(os.environ)
    return values


def _read_local_environment(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key in {
            "XIC_RAW_VALIDATION_DIR",
            "XIC_RAW_ROOT",
            "THERMO_RAWFILE_READER_DLL_DIR",
        }:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _resolve_input_dir(
    explicit: Path | None,
    environment: Mapping[str, str],
    variable: str,
    *,
    label: str,
) -> Path:
    configured = environment.get(variable)
    if explicit is None and not configured:
        raise ValueError(
            f"{label} directory is required "
            f"(--{label.lower()}-dir or {variable})"
        )
    value = explicit if explicit is not None else Path(str(configured))
    resolved = value.resolve()
    if not resolved.is_dir():
        raise ValueError(f"{resolved}: {label} directory does not exist")
    return resolved


def _validate_istd_input(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    if args.known_istd_reference_workbook is not None:
        workbook = args.known_istd_reference_workbook.resolve()
        if not workbook.is_file():
            raise ValueError(f"{workbook}: known ISTD workbook does not exist")
        read_known_istd_reference(workbook)
        return None, workbook
    targets = (args.targets_csv or _DEFAULT_TARGETS).resolve()
    if not targets.is_file():
        raise ValueError(f"{targets}: targets CSV does not exist")
    selection = read_istd_targets(targets)
    if not selection.targets:
        raise ValueError(f"{targets}: targets CSV contains no ISTD targets")
    return targets, None


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    return value


def _run_command(command: list[str], log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8", newline="") as log:
        completed = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return completed.returncode


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("workers must be >= 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
