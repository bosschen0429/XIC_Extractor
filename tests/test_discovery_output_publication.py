from __future__ import annotations

from pathlib import Path

import pytest

from tests.discovery_pipeline_fixtures import _settings
from xic_extractor.discovery.output_publication import (
    publish_discovery_batch_sample,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.timing import TimingRecorder


@pytest.mark.parametrize("replacement_position", [1, 2, 3])
@pytest.mark.parametrize("prior_outputs", [False, True])
def test_discovery_output_publication_rolls_back_every_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_position: int,
    prior_outputs: bool,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    final_paths = [
        output_dir / "discovery_peaks.csv",
        output_dir / "discovery_events.csv",
        output_dir / "discovery_physical_facts.json",
    ]
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"atomic-publication")
    run_spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    acquisition_input = run_spec.acquisition_inputs[0]
    prior = {
        path: f"prior-{index}".encode("utf-8") for index, path in enumerate(final_paths)
    }
    if prior_outputs:
        for path, payload in prior.items():
            path.write_bytes(payload)

    real_replace = Path.replace
    published = 0
    final_names = {path.name for path in final_paths}

    def fail_selected_publication(source: Path, target: Path) -> Path:
        nonlocal published
        target = Path(target)
        if (
            source.name.endswith(".tmp")
            and target.name in final_names
            and not target.name.endswith(".tmp")
        ):
            published += 1
            if published == replacement_position:
                raise OSError(f"publication failure {replacement_position}")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_selected_publication)

    with pytest.raises(OSError, match=f"publication failure {replacement_position}"):
        publish_discovery_batch_sample(
            raw_path,
            (),
            (),
            output_dir=output_dir,
            sample_output_dir=output_dir,
            acquisition_source=acquisition_input.acquisition_source,
            timing_recorder=TimingRecorder.disabled("discovery"),
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )

    for path in final_paths:
        if prior_outputs:
            assert path.read_bytes() == prior[path]
        else:
            assert not path.exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


@pytest.mark.parametrize("prior_outputs", [False, True])
def test_discovery_output_publication_rolls_back_sidecar_staging_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_outputs: bool,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sidecar-stage-failure")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    final_paths = (
        output_dir / "discovery_peaks.csv",
        output_dir / "discovery_events.csv",
        output_dir / "discovery_physical_facts.json",
    )
    prior = {path: f"prior-{index}".encode() for index, path in enumerate(final_paths)}
    if prior_outputs:
        for path, payload in prior.items():
            path.write_bytes(payload)
    monkeypatch.setattr(
        "xic_extractor.discovery.output_publication.write_discovery_physical_fact_sidecar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sidecar staging failed")
        ),
    )

    with pytest.raises(OSError, match="sidecar staging failed"):
        publish_discovery_batch_sample(
            raw_path,
            (),
            (),
            output_dir=output_dir,
            sample_output_dir=output_dir,
            acquisition_source=spec.acquisition_inputs[0].acquisition_source,
            timing_recorder=TimingRecorder.disabled("discovery"),
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    for path in final_paths:
        if prior_outputs:
            assert path.read_bytes() == prior[path]
        else:
            assert not path.exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]
