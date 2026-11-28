from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from tests.discovery_ms1_capture_fixtures import (
    _multi_tag_settings,
    _seed,
    with_scan,
)
from tests.discovery_physical_measurement_fixtures import _OwnerBoundBatchRaw
from tests.discovery_pipeline_fixtures import _settings
from tests.discovery_store_fixtures import stored_peaks
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_peaks,
)
from xic_extractor.discovery.output_publication import (
    publish_discovery_batch,
    publish_discovery_batch_sample,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder


def test_publication_timing_covers_the_published_tables(tmp_path: Path) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"timed publication")
    settings = DiscoverySettings(
        selected_tag_names=("dR", "R"),
        tag_combine_mode="union",
        neutral_loss_profiles=_multi_tag_settings().neutral_loss_profiles[:2],
    )
    profile = SignalProcessingProfile(window_points=3)
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings, signal_processing_profile=profile
    )
    source = spec.acquisition_inputs[0].acquisition_source
    seeds = tuple(
        replace(
            with_scan(
                _seed(scan_number=scan, rt=rt, product_mz=500.0 - loss),
                configured_neutral_loss_da=loss,
                observed_neutral_loss_da=loss,
            ),
            neutral_loss_tag=tag,
        )
        for scan, rt, tag, loss in (
            (10, 7.78, "dR", 116.047344),
            (20, 7.82, "R", 132.0423),
        )
    )
    result = capture_ms1_peaks(
        _OwnerBoundBatchRaw(source),
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    recorder = TimingRecorder("discovery", run_id="timed-publication")

    sample = publish_discovery_batch_sample(
        raw_path,
        result.peaks,
        result.attachments,
        output_dir=tmp_path / "out",
        sample_index=1,
        neutral_loss_by_tag=settings.neutral_loss_by_tag,
        acquisition_source=source,
        timing_recorder=recorder,
    )
    outputs = publish_discovery_batch(
        tmp_path / "out", [sample], timing_recorder=recorder
    )

    assert len(stored_peaks(outputs.store)) == 1
    by_stage = {record.stage: record for record in recorder.records}
    assert by_stage["discover.write_sample"].metrics == {
        "peak_count": 1,
        "event_count": len(result.attachments),
    }
    assert by_stage["discover.publish_batch"].metrics == {"sample_count": 1}


def test_discovery_output_timing_records_failure_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"timed failure")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    recorder = TimingRecorder("discovery", run_id="timed-publish")
    output_dir = tmp_path / "out"
    sample = publish_discovery_batch_sample(
        raw_path,
        (),
        (),
        output_dir=output_dir,
        sample_index=1,
        neutral_loss_by_tag=settings.neutral_loss_by_tag,
        acquisition_source=spec.acquisition_inputs[0].acquisition_source,
        timing_recorder=recorder,
    )

    def fail_publish(source, target):
        raise OSError("timed publish failure")

    monkeypatch.setattr(os, "replace", fail_publish)
    with pytest.raises(OSError, match="timed publish failure"):
        publish_discovery_batch(output_dir, [sample], timing_recorder=recorder)

    stages = [record.stage for record in recorder.records]
    assert stages == ["discover.write_sample", "discover.publish_batch"]
    assert not [path for path in output_dir.rglob("*") if path.name.endswith(".tmp")]
