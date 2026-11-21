"""The one place that decides how a run uses this machine.

An entry point builds one ``ExecutionProfile``; every pool below it is sized
from that value and opened by ``xic_extractor.execution``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALIGNMENT_WORKERS = 4
DEFAULT_WORKER_BACKEND = "process"
WORKER_BACKENDS = ("process", "thread")
RAW_HEADER_READ_THREADS = 8


def default_parallel_workers() -> int:
    cpu_count = os.cpu_count() or 2
    return max(1, cpu_count - 1)


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Worker budgets and where the workers live.

    ``parallel_workers`` bounds work that takes one worker per RAW or per
    independent grouping workset; ``alignment_workers`` bounds Alignment's RAW
    sample phases and source fingerprinting. ``backend="thread"`` keeps every
    worker inside this process, where per-RAW extraction runs one file at a
    time. ``overlap_partitions`` lets Alignment acquire one partition while
    composing another.
    """

    parallel_workers: int = field(default_factory=default_parallel_workers)
    alignment_workers: int = DEFAULT_ALIGNMENT_WORKERS
    backend: str = DEFAULT_WORKER_BACKEND
    overlap_partitions: bool = True

    def __post_init__(self) -> None:
        _validate_workers(self.parallel_workers, field="parallel_workers")
        _validate_workers(self.alignment_workers, field="alignment_workers")


def in_process_execution() -> ExecutionProfile:
    """Run everything in the calling process, for injected readers and tests."""
    return ExecutionProfile(backend="thread", overlap_partitions=False)


def resolve_execution_profile(
    settings: Mapping[str, str] | None = None,
    *,
    parallel_workers: int | None = None,
    alignment_workers: int | None = None,
    backend: str = DEFAULT_WORKER_BACKEND,
) -> ExecutionProfile:
    configured_parallel = default_parallel_workers()
    configured_value = settings.get("parallel_workers", "") if settings else ""
    if parallel_workers is None and configured_value.strip():
        configured_parallel = _parse_workers(configured_value, field="parallel_workers")
    return ExecutionProfile(
        parallel_workers=(
            configured_parallel if parallel_workers is None else parallel_workers
        ),
        alignment_workers=(
            DEFAULT_ALIGNMENT_WORKERS
            if alignment_workers is None
            else alignment_workers
        ),
        backend=backend,
    )


def load_execution_profile(
    *,
    parallel_workers: int | None = None,
    alignment_workers: int | None = None,
    backend: str = DEFAULT_WORKER_BACKEND,
) -> ExecutionProfile:
    """Worker counts come from the machine; only an explicit argument overrides."""
    return resolve_execution_profile(
        parallel_workers=parallel_workers,
        alignment_workers=alignment_workers,
        backend=backend,
    )


def _parse_workers(value: str, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer >= 1") from exc


def _validate_workers(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be an integer >= 1")
    return value


@dataclass(frozen=True, slots=True)
class UntargetedExecutionPolicy:
    """Resources for retained Alignment, independent of scientific settings."""

    profile: ExecutionProfile = field(default_factory=ExecutionProfile)
    retain_acquisition_cache: bool = False
    acquisition_cache_dir: Path | None = None
    max_partition_jobs: int = 100_000
