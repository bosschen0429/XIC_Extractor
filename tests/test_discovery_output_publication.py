from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.discovery_pipeline_fixtures import _settings
from tests.discovery_store_fixtures import single_peak_sample, write_discovery
from xic_extractor.discovery import store
from xic_extractor.discovery.output_publication import (
    publish_discovery_batch,
    publish_discovery_batch_sample,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.timing import TimingRecorder

_DISABLED = TimingRecorder.disabled("discovery")


def _published_sample(tmp_path: Path, output_dir: Path, stem: str = "sample"):
    raw_path = tmp_path / f"{stem}.raw"
    raw_path.write_bytes(stem.encode())
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    return publish_discovery_batch_sample(
        raw_path,
        (),
        (),
        output_dir=output_dir,
        sample_index=1,
        neutral_loss_by_tag=settings.neutral_loss_by_tag,
        acquisition_source=spec.acquisition_inputs[0].acquisition_source,
        timing_recorder=_DISABLED,
    )


def _leftovers(directory: Path) -> list[Path]:
    return [path for path in directory.rglob("*") if path.name.endswith(".tmp")]


def test_failed_sample_write_leaves_no_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        store, "sample_row", lambda *_args: (_ for _ in ()).throw(OSError("no row"))
    )

    with pytest.raises(OSError, match="no row"):
        _published_sample(tmp_path, output_dir)

    assert not store.part_path(output_dir, "sample").exists()
    assert not _leftovers(output_dir)


def test_failed_batch_publication_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "out"
    previous = write_discovery(output_dir, [single_peak_sample(tmp_path, "old")])
    before = previous.read_bytes()
    sample = _published_sample(tmp_path, output_dir)

    def fail_replace(source, target):
        raise OSError("publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        publish_discovery_batch(output_dir, [sample], timing_recorder=_DISABLED)

    assert previous.read_bytes() == before
    assert sample.part.is_file()
    assert not _leftovers(output_dir)


def test_batch_publication_replaces_parts_with_one_file(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    sample = _published_sample(tmp_path, output_dir)

    outputs = publish_discovery_batch(output_dir, [sample], timing_recorder=_DISABLED)

    assert outputs.store == output_dir / store.DISCOVERY_ARTIFACT_NAME
    assert outputs.sample_stems == ("sample",)
    assert sorted(path.name for path in output_dir.iterdir()) == [
        store.DISCOVERY_ARTIFACT_NAME
    ]
