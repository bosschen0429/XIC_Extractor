"""Run CID-NL Discovery over a batch and publish its ``discovery.sqlite``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.discovery.cid_nl_search import (
    DiscoverySampleJob,
    RawOpener,
    discover_sample,
)
from xic_extractor.discovery.models import DiscoveryBatchOutputs
from xic_extractor.discovery.output_publication import publish_discovery_batch
from xic_extractor.discovery.sample_process_execution import (
    discover_samples_in_processes,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_run_spec import ProcessingRunSpec


def run_discovery_batch(
    run_spec: ProcessingRunSpec,
    *,
    output_dir: Path,
    dll_dir: Path,
    execution: ExecutionProfile,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryBatchOutputs:
    """Discover every sample, in processes when the profile allows, then merge."""
    raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    jobs = tuple(
        DiscoverySampleJob(
            sample_index=sample_index,
            output_dir=output_dir,
            settings=run_spec.settings,
            signal_processing_profile=run_spec.signal_processing_profile,
            dll_dir=dll_dir,
            acquisition_input=acquisition_input,
        )
        for sample_index, acquisition_input in enumerate(run_spec.acquisition_inputs)
    )
    max_workers = min(len(jobs), execution.parallel_workers)
    if execution.backend == "process" and max_workers > 1:
        if raw_opener is not None:
            raise ValueError(
                "parallel Discovery owns RAW handles inside spawned workers; "
                "raw_opener injection requires in-process execution"
            )
        published = discover_samples_in_processes(
            jobs,
            timing_recorder=recorder,
            max_workers=max_workers,
            executor_factory=executor_factory,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
    else:
        samples = []
        for job in jobs:
            raise_if_discovery_cancelled(should_cancel)
            samples.append(
                discover_sample(job, timing_recorder=recorder, raw_opener=raw_opener)
            )
            if progress_callback is not None:
                progress_callback(len(samples), len(jobs), samples[-1].sample_stem)
        published = tuple(samples)

    raise_if_discovery_cancelled(should_cancel)
    return publish_discovery_batch(output_dir, published, timing_recorder=recorder)


def raise_if_discovery_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Discovery cancelled before output publication")
