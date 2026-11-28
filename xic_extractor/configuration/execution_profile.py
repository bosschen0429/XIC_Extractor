"""The one place that decides how a run uses this machine.

An entry point builds one ``ExecutionProfile``; every pool below it is sized
from that value and opened by ``xic_extractor.execution``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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


def load_execution_profile(
    *,
    parallel_workers: int | None = None,
    alignment_workers: int | None = None,
    backend: str = DEFAULT_WORKER_BACKEND,
) -> ExecutionProfile:
    """Worker counts come from the machine; only an explicit argument overrides."""
    if backend not in WORKER_BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(WORKER_BACKENDS)}")
    return ExecutionProfile(
        parallel_workers=(
            default_parallel_workers()
            if parallel_workers is None
            else _validate_workers(parallel_workers, field="parallel_workers")
        ),
        alignment_workers=(
            DEFAULT_ALIGNMENT_WORKERS
            if alignment_workers is None
            else _validate_workers(alignment_workers, field="alignment_workers")
        ),
        backend=backend,
    )


def _validate_workers(value: int, *, field: str) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be an integer >= 1")
    return value


@dataclass(frozen=True, slots=True)
class UntargetedExecutionPolicy:
    """Resources for retained Alignment, independent of scientific settings."""

    profile: ExecutionProfile
    max_partition_jobs: int = 100_000
