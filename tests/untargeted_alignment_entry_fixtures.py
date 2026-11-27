"""Run the formal Untargeted Alignment entry on synthetic acquisitions.

Tests persist Discovery results, replace only the Thermo RAW boundary and read
what ``run_untargeted_application`` publishes. ``publish_alignment`` also
checks the replaced boundary and the timing record on every run.

Synthetic acquisitions carry no lock masses, so Discovery recorded none.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _settings,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws, persist_discovery_batch
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.applications.untargeted.application import (
    run_untargeted_application,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.discovery.csv_reader import DiscoveryBatchIndex
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.sample_metadata import SampleMetadata

SETTINGS = replace(_settings(), mass_recalibration=MassRecalibration.NONE)


def raw_paths(tmp_path: Path, stems: tuple[str, ...]) -> dict[str, Path]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    paths = {}
    for stem in stems:
        path = raw_dir / f"{stem}.raw"
        path.write_bytes(f"acquisition:{stem}".encode())
        paths[stem] = path
    return paths


def discover(raw: _FakeRaw, path: Path, settings=SETTINGS):
    return tuple(_current_candidates(raw, raw_file=path, settings=settings))


def sample_metadata_for(
    stems, **overrides: SampleMetadata
) -> tuple[SampleMetadata, ...]:
    return tuple(
        overrides.get(
            stem,
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="sample",
            ),
        )
        for index, stem in enumerate(stems, start=1)
    )


def publish_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    paths: Mapping[str, Path],
    raws: Mapping[str, _FakeRaw],
    peaks: Mapping[str, tuple],
    settings=SETTINGS,
    alignment_config: AlignmentConfig | None = None,
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    output_level: str = "machine",
    known_istd_reference_workbook: Path | None = None,
    name: str = "alignment",
    opened_samples: set[str] | None = None,
    execution_changes: Mapping[str, object] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    timing_recorder: TimingRecorder | None = None,
) -> Path:
    """Run the formal Untargeted Alignment entry and return its output folder.

    Every run also checks the replaced RAW boundary and the timing record:
    an acquisition is opened at most once and serves all of its traces in one
    batched request, and no stage runs twice for the same sample. ``opened_samples``
    receives the stems whose RAW was opened, also when the run raises.
    """
    stems = tuple(paths)
    discovery = persist_discovery_batch(
        raw_paths=tuple(paths.values()),
        settings=settings,
        peaks_by_sample=peaks,
        root=tmp_path / f"{name}-discovery",
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path={paths[stem]: raws[stem] for stem in stems},
        tmp_path=tmp_path,
    )
    output_dir = tmp_path / name
    raw_dir = tmp_path / "raw"
    recorder = timing_recorder or TimingRecorder("alignment")
    try:
        run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=DiscoveryBatchIndex(
                    path=tmp_path / f"{name}-discovery" / "discovery_batch_index.csv",
                    sample_order=discovery.sample_order,
                    peak_csvs=discovery.peak_csvs,
                    raw_files=discovery.raw_files,
                    event_csvs=discovery.event_csvs,
                    peak_receipts=discovery.peak_receipts,
                ),
                raw_dir=raw_dir,
                dll_dir=tmp_path,
                output_dir=output_dir,
                alignment_config=alignment_config or AlignmentConfig(),
                discovery_settings=settings,
                sample_metadata=sample_metadata or sample_metadata_for(stems),
                known_istd_reference_workbook=known_istd_reference_workbook,
                execution=replace(
                    UntargetedExecutionPolicy(
                        profile=ExecutionProfile(
                            alignment_workers=1,
                            backend="thread",
                            overlap_partitions=False,
                        ),
                    ),
                    **(execution_changes or {}),
                ),
                publication=UntargetedPublicationPolicy(
                    output_level=output_level,
                    emit_alignment_cells=True,
                    emit_alignment_explanation=True,
                ),
            ),
            UntargetedExecutionContext(
                timing_recorder=recorder, should_cancel=should_cancel
            ),
        )
    finally:
        handles = {
            stem: opened.get(paths[stem], [])
            for stem in stems
            if opened.get(paths[stem])
        }
        if opened_samples is not None:
            opened_samples.update(handles)
    repeated = {
        stem: windows
        for stem, opens in handles.items()
        for handle in opens
        if len(windows := handle.chromatogram_windows) != len(set(windows))
    }
    assert repeated == {}, repeated
    stages = Counter((r.stage, r.sample_stem) for r in recorder.records)
    batch_count = len({row.batch_id for row in sample_metadata or ()}) or 1
    repeated_stages = {
        (stage, stem): count
        for (stage, stem), count in stages.items()
        if count > (1 if stem else batch_count)
    }
    assert not repeated_stages, repeated_stages
    return output_dir


PUBLISHED_TABLES = (
    "alignment_matrix.tsv",
    "alignment_cells.tsv",
    "alignment_rt_anchors.tsv",
)


def published_tables(output: Path) -> dict[str, bytes]:
    return {name: (output / name).read_bytes() for name in PUBLISHED_TABLES}


def published_matrix(output: Path) -> dict[str, dict[str, str]]:
    metadata = json.loads((output / "alignment_run_metadata.json").read_text("utf-8"))
    ids = [feature for feature, _ in metadata["matrix_identity"]["rows"]]
    return dict(zip(ids, read_tsv(output / "alignment_matrix.tsv"), strict=True))


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def tsv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle, delimiter="\t"))


def published_cells(output_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["feature_id"], row["sample_stem"]): row
        for row in read_tsv(output_dir / "alignment_cells.tsv")
    }


def cell_sources(output_dir: Path) -> set[str]:
    return {
        row["source_candidate_id"]
        for row in published_cells(output_dir).values()
        if row["source_candidate_id"]
    }
