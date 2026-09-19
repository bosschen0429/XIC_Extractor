from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_discovery_pipeline import _settings
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
)
from xic_extractor.discovery.pipeline import _write_dual_csvs
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)


@pytest.mark.parametrize(
    ("spec_bound", "replacement_position"),
    [(False, 1), (False, 2), (True, 1), (True, 2), (True, 3)],
)
@pytest.mark.parametrize("prior_outputs", [False, True])
def test_discovery_output_publication_rolls_back_every_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec_bound: bool,
    replacement_position: int,
    prior_outputs: bool,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    final_paths = [
        output_dir / "discovery_candidates.csv",
        output_dir / "discovery_review.csv",
        output_dir / "discovery_physical_facts.json",
    ]
    run_spec = None
    acquisition_input = None
    if spec_bound:
        raw_path = tmp_path / "sample.raw"
        raw_path.write_bytes(b"atomic-publication")
        run_spec = compile_discovery_processing_run_spec(
            (raw_path,), discovery_settings=_settings()
        )
        acquisition_input = run_spec.acquisition_inputs[0]
    prior = {
        path: f"prior-{index}".encode("utf-8")
        for index, path in enumerate(final_paths)
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
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
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


def test_legacy_publication_removes_stale_spec_bound_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spec-to-legacy-success")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    spec_outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
    )
    assert spec_outputs.physical_facts_json is not None
    assert spec_outputs.physical_facts_json.exists()
    expected_candidates = (",".join(DISCOVERY_CANDIDATE_COLUMNS) + "\n").encode()
    expected_review = (",".join(DISCOVERY_BRIEF_COLUMNS) + "\n").encode()

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_candidates_csv",
        lambda path, _candidates: path.write_bytes(expected_candidates),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_review_csv",
        lambda path, _candidates: path.write_bytes(expected_review),
    )

    outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
    )

    assert outputs.candidates_csv.read_bytes() == expected_candidates
    assert outputs.review_csv.read_bytes() == expected_review
    assert outputs.physical_facts_json is None
    assert not (output_dir / "discovery_physical_facts.json").exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


@pytest.mark.parametrize("failure_point", ["backup", "publish-1", "publish-2"])
def test_spec_to_legacy_failure_restores_complete_prior_member_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    output_dir = tmp_path / "out"
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spec-to-legacy-rollback")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    spec_outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
    )
    assert spec_outputs.physical_facts_json is not None
    final_paths = (
        spec_outputs.candidates_csv,
        spec_outputs.review_csv,
        spec_outputs.physical_facts_json,
    )
    prior = {path: path.read_bytes() for path in final_paths}
    physical_path = spec_outputs.physical_facts_json
    real_replace = Path.replace
    published = 0

    def fail_transition(source: Path, target: Path) -> Path:
        nonlocal published
        target = Path(target)
        if (
            failure_point == "backup"
            and source == physical_path
            and target.name == f"{physical_path.name}.bak"
        ):
            raise OSError("legacy transition backup failure")
        if source.name.endswith(".tmp") and target in final_paths[:2]:
            published += 1
            if failure_point == f"publish-{published}":
                raise OSError(f"legacy transition {failure_point} failure")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_transition)

    with pytest.raises(OSError, match="legacy transition"):
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
        )

    assert {path: path.read_bytes() for path in final_paths} == prior
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
        output_dir / "discovery_candidates.csv",
        output_dir / "discovery_review.csv",
        output_dir / "discovery_physical_facts.json",
    )
    prior = {path: f"prior-{index}".encode() for index, path in enumerate(final_paths)}
    if prior_outputs:
        for path, payload in prior.items():
            path.write_bytes(payload)
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_physical_fact_sidecar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sidecar staging failed")
        ),
    )

    with pytest.raises(OSError, match="sidecar staging failed"):
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
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
