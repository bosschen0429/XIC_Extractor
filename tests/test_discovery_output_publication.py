from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_discovery_pipeline import _settings
from xic_extractor.discovery.output_publication import (
    write_dual_csvs,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.timing import TimingRecorder


@pytest.mark.parametrize(
    "basis", ("scan_precursor", "mixed", "product_plus_neutral_loss"),
)
@pytest.mark.parametrize("state", (
    "ms1_feature_nl_supported", "ms1_feature_nl_rescued",
    "review_only_orphan_nl", "review_only_ambiguous_coisolation",
    "rejected_noise_or_outside_rt",
))
def test_discovery_publication_retains_only_used_candidate_states(
    tmp_path: Path, basis: str, state: str,
) -> None:
    from tests.test_discovery_csv import _candidate
    from tests.test_discovery_pipeline import _read_csv
    from xic_extractor.discovery.models import MS1_FEATURE_BACKED_STATES

    feature_backed = state in MS1_FEATURE_BACKED_STATES
    candidate = replace(
        _candidate(), precursor_mz_basis=basis,
        discovery_candidate_state=state, ms1_peak_found=feature_backed,
    )
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"retained-states")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    outputs = write_dual_csvs(
        tmp_path / "out", (candidate,), sample_stem=candidate.sample_stem,
        timing_recorder=TimingRecorder.disabled("discovery"),
        run_spec=spec, acquisition_input=spec.acquisition_inputs[0],
    )
    expected_ids = [candidate.candidate_id] if feature_backed or basis in {
        "scan_precursor", "mixed",
    } else []
    for path in (outputs.candidates_csv, outputs.review_csv):
        assert [row["candidate_id"] for row in _read_csv(path)] == expected_ids


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
        output_dir / "discovery_candidates.csv",
        output_dir / "discovery_review.csv",
        output_dir / "discovery_physical_facts.json",
    ]
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
        write_dual_csvs(
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
        "xic_extractor.discovery.output_publication.write_discovery_physical_fact_sidecar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sidecar staging failed")
        ),
    )

    with pytest.raises(OSError, match="sidecar staging failed"):
        write_dual_csvs(
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
