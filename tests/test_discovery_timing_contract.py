from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.discovery_ms1_capture_fixtures import (
    _multi_tag_settings,
    _seed,
)
from tests.discovery_physical_measurement_fixtures import _OwnerBoundBatchRaw
from tests.discovery_pipeline_fixtures import _settings
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_peaks,
)
from xic_extractor.discovery.output_publication import (
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
            _seed(scan_number=scan, rt=rt, product_mz=500.0 - loss),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            observed_neutral_loss_da=loss,
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

    outputs = publish_discovery_batch_sample(
        raw_path,
        result.peaks,
        result.attachments,
        output_dir=tmp_path,
        sample_output_dir=tmp_path / "out",
        acquisition_source=source,
        timing_recorder=recorder,
    ).outputs

    # Both NL events reference one measured peak; publication retains it once.
    assert len(read_discovery_peaks_csv(outputs.peaks_csv)) == 1
    by_stage = {record.stage: record for record in recorder.records}
    assert by_stage["discover.write_peaks_csv"].metrics == {"row_count": 1}
    assert by_stage["discover.publish_outputs"].metrics == {
        "artifact_count": 2,
        "replaced_existing_count": 0,
    }


def test_discovery_output_timing_records_failure_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"timed failure")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
    )
    recorder = TimingRecorder("discovery", run_id="timed-publish")
    output_dir = tmp_path / "out"
    real_replace = Path.replace

    def fail_publish(source: Path, target: Path) -> Path:
        target = Path(target)
        if source.name == "discovery_peaks.csv.tmp" and target.name == (
            "discovery_peaks.csv"
        ):
            raise OSError("timed publish failure")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_publish)

    with pytest.raises(OSError, match="timed publish failure"):
        publish_discovery_batch_sample(
            raw_path,
            (),
            (),
            output_dir=tmp_path,
            sample_output_dir=output_dir,
            acquisition_source=spec.acquisition_inputs[0].acquisition_source,
            timing_recorder=recorder,
        )

    stages = [record.stage for record in recorder.records]
    assert "discover.write_events_csv" in stages
    assert "discover.publish_outputs" in stages
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]
