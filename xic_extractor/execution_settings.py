from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALIGNMENT_WORKERS = 4
# Thermo reads scale poorly across threads in one process; separate worker
# processes run them in parallel (#470).
DEFAULT_ALIGNMENT_WORKER_BACKEND = "process"


def default_parallel_workers() -> int:
    cpu_count = os.cpu_count() or 2
    return max(1, cpu_count - 1)


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    parallel_workers: int = field(default_factory=default_parallel_workers)
    alignment_workers: int = DEFAULT_ALIGNMENT_WORKERS

    def __post_init__(self) -> None:
        _validate_workers(self.parallel_workers, field="parallel_workers")
        _validate_workers(self.alignment_workers, field="alignment_workers")


def resolve_execution_profile(
    settings: Mapping[str, str] | None = None,
    *,
    parallel_workers: int | None = None,
    alignment_workers: int | None = None,
) -> ExecutionProfile:
    configured_parallel = default_parallel_workers()
    configured_value = settings.get("parallel_workers", "") if settings else ""
    if parallel_workers is None and configured_value.strip():
        configured_parallel = _parse_workers(
            configured_value, field="parallel_workers"
        )
    return ExecutionProfile(
        parallel_workers=(
            configured_parallel
            if parallel_workers is None
            else parallel_workers
        ),
        alignment_workers=(
            DEFAULT_ALIGNMENT_WORKERS
            if alignment_workers is None
            else alignment_workers
        ),
    )


def load_execution_profile(
    settings_path: Path | None = None,
    *,
    parallel_workers: int | None = None,
    alignment_workers: int | None = None,
) -> ExecutionProfile:
    path = settings_path or (_runtime_root() / "config" / "settings.csv")
    settings: Mapping[str, str] | None = None
    if path.is_file():
        from xic_extractor.configuration.csv_io import _read_settings

        settings = _read_settings(path)
    return resolve_execution_profile(
        settings,
        parallel_workers=parallel_workers,
        alignment_workers=alignment_workers,
    )


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _parse_workers(value: str, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer >= 1") from exc


def _validate_workers(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be an integer >= 1")
    return value
