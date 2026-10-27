from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_discovery_ms1_capture import (
    _group,
    _multi_tag_settings,
    _seed,
)
from tests.test_discovery_physical_fact_integration import _OwnerBoundBatchRaw
from tests.test_discovery_pipeline import _settings
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_candidates,
)
from xic_extractor.discovery.output_publication import (
    write_dual_csvs,
)
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_records,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.timing import TimingRecorder


def test_physical_sidecar_and_publication_timing_cover_canonical_bytes_without_rework(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"timed sidecar")
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
    groups = tuple(
        replace(
            _group((_seed(scan_number=scan, rt=rt, product_mz=500.0 - loss),)),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            matched_tag_names=(tag,),
        )
        for scan, rt, tag, loss in (
            (10, 7.78, "dR", 116.047344),
            (20, 7.82, "R", 132.0423),
        )
    )
    result = capture_ms1_candidates(
        _OwnerBoundBatchRaw(source),
        groups,
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    from xic_extractor.peak_detection import physical_peak_codec as owner

    calls = {"prepare": 0, "evaluate": 0}

    def unexpected_prepare(*_args, **_kwargs):
        calls["prepare"] += 1
        pytest.fail("producer re-prepared a persisted physical fact")

    def unexpected_evaluate(*_args, **_kwargs):
        calls["evaluate"] += 1
        pytest.fail("producer rehydrated a persisted physical fact")

    monkeypatch.setattr(owner, "prepare_physical_signal", unexpected_prepare)
    monkeypatch.setattr(owner, "evaluate_physical_peak", unexpected_evaluate)
    recorder = TimingRecorder("discovery", run_id="timed-publication")

    outputs = write_dual_csvs(
        tmp_path / "out",
        result.candidates,
        timing_recorder=recorder,
        sample_stem=raw_path.stem,
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
        physical_work_manifest=result.physical_work_manifest,
        physical_evaluations=result.physical_evaluations,
        physical_bindings=result.physical_bindings,
    )

    assert outputs.physical_facts_json is not None
    persisted = read_discovery_physical_fact_records(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
    )
    # One merged feature candidate with two member work items.
    assert len(persisted) == 2
    by_stage = {record.stage: record for record in recorder.records}
    assert by_stage["discover.write_physical_facts_sidecar"].metrics == {
        "work_count": 2,
        "record_count": 2,
        "byte_size": outputs.physical_facts_json.stat().st_size,
    }
    assert by_stage["discover.publish_outputs"].metrics == {
        "artifact_count": 3,
        "replaced_existing_count": 0,
    }
    assert calls == {"prepare": 0, "evaluate": 0}


@pytest.mark.parametrize("failure", ["sidecar", "publish"])
def test_discovery_output_timing_records_failure_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"timed failure")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
    )
    recorder = TimingRecorder("discovery", run_id=f"timed-{failure}")
    output_dir = tmp_path / "out"
    if failure == "sidecar":
        monkeypatch.setattr(
            "xic_extractor.discovery.output_publication."
            "write_discovery_physical_fact_sidecar",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("timed sidecar failure")
            ),
        )
    else:
        real_replace = Path.replace

        def fail_publish(source: Path, target: Path) -> Path:
            target = Path(target)
            if source.name == "discovery_candidates.csv.tmp" and target.name == (
                "discovery_candidates.csv"
            ):
                raise OSError("timed publish failure")
            return real_replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_publish)

    with pytest.raises(OSError, match=f"timed {failure} failure"):
        write_dual_csvs(
            output_dir,
            (),
            timing_recorder=recorder,
            sample_stem="sample",
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    stages = [record.stage for record in recorder.records]
    assert "discover.write_physical_facts_sidecar" in stages
    assert ("discover.publish_outputs" in stages) is (failure == "publish")
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]
