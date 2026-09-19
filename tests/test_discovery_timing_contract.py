from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_discovery_physical_fact_persistence import _population
from tests.test_discovery_pipeline import _settings
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.pipeline import _write_dual_csvs
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)


def test_physical_sidecar_and_publication_timing_cover_canonical_bytes_without_rework(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"timed sidecar")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
    )
    manifest, records = _population(spec, spec.acquisition_inputs[0])
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

    outputs = _write_dual_csvs(
        tmp_path / "out",
        (),
        timing_recorder=recorder,
        sample_stem="sample",
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
        physical_work_manifest=manifest,
        physical_evaluations=records,
    )

    assert outputs.physical_facts_json is not None
    by_stage = {record.stage: record for record in recorder.records}
    assert by_stage["discover.write_physical_facts_sidecar"].metrics == {
        "work_count": len(manifest),
        "record_count": len(records),
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
            "xic_extractor.discovery.pipeline."
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
        _write_dual_csvs(
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


def test_legacy_publication_times_only_two_public_outputs(tmp_path: Path) -> None:
    recorder = TimingRecorder("discovery", run_id="legacy-publication")

    outputs = _write_dual_csvs(
        tmp_path / "out",
        (),
        timing_recorder=recorder,
        sample_stem="sample",
    )

    assert outputs.physical_facts_json is None
    by_stage = {record.stage: record for record in recorder.records}
    assert "discover.write_physical_facts_sidecar" not in by_stage
    assert by_stage["discover.publish_outputs"].metrics == {
        "artifact_count": 2,
        "replaced_existing_count": 0,
    }
