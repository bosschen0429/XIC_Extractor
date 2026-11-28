"""Complete method routes with production stage owners and synthetic RAW I/O."""

import csv
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from tests.untargeted_full_flow_fixtures import (
    FULL_FLOW_EXECUTION,
    install_full_flow_raws,
)
from xic_extractor.applications.untargeted.full_analysis import (
    FullAnalysisRequest,
    UntargetedAnalysis,
)
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.discovery.delta_mass_pipeline import run_delta_mass_discovery
from xic_extractor.discovery.store import DISCOVERY_ARTIFACT_NAME, read_discovery_batch
from xic_extractor.presets import load_preset
from xic_extractor.presets.delta_mass import resolve_delta_mass_discovery
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata
from xic_workbench.untargeted import parse_request


def test_complete_cid_nl_matches_workbench_intake_with_sample_values(
    tmp_path, monkeypatch
):
    _raws, opened = install_full_flow_raws(monkeypatch, tmp_path)
    preset = load_preset("dna_dr")
    samples = (
        SampleMetadata(
            "first", "first", injection_order=1, sample_role="qc", group="control"
        ),
        SampleMetadata(
            "second", "second", injection_order=2, sample_role="sample", group="case"
        ),
    )
    request = FullAnalysisRequest(
        preset="dna_dr",
        preset_snapshot=preset,
        tuning_overrides={},
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "direct",
        execution_profile=ExecutionProfile(
            parallel_workers=1, alignment_workers=1, backend="thread"
        ),
        sample_metadata=samples,
    )
    direct = UntargetedAnalysis(
        request, progress=lambda *_: None, should_cancel=lambda: False
    ).execute()
    assert direct["modules"] == {"alignment": "completed", "backfill": "enabled"}
    assert Path(direct["matrix_tsv"]).is_file()
    assert [
        (s.raw_stem, s.sample_role, s.group)
        for s in load_sample_metadata(request.output_dir / "sample_metadata.tsv")
    ] == [("first", "qc", "control"), ("second", "sample", "case")]
    assert {
        "sample_metadata.tsv",
        "discovery.sqlite",
        "alignment_matrix.tsv",
        "alignment_explanation.sqlite",
    } <= {Path(path).name for path in direct["published_paths"]}
    with Path(direct["matrix_tsv"]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 1
    assert float(rows[0]["first"]) > 0
    assert float(rows[0]["second"]) == pytest.approx(
        2 * float(rows[0]["first"]), rel=1e-5
    )
    intake = replace(
        parse_request(
            {
                "preset": "dna_dr",
                "raw_dir": str(tmp_path),
                "dll_dir": str(tmp_path),
                "output_dir": str(tmp_path / "workbench-intake"),
                "samples": [asdict(s) for s in samples],
            }
        ),
        execution_profile=request.execution_profile,
    )
    workbench = UntargetedAnalysis(
        intake, progress=lambda *_: None, should_cancel=lambda: False
    ).execute()
    assert (
        Path(workbench["matrix_tsv"]).read_bytes()
        == Path(direct["matrix_tsv"]).read_bytes()
    )
    assert workbench["modules"] == direct["modules"]
    assert all(handle.disposed for handles in opened.values() for handle in handles)


@pytest.mark.parametrize("preset", ["pt_pattern", "pt_pattern_cid_nl_dr"])
def test_complete_diagnostic_routes_keep_candidates_sample_bound_without_matrix(
    tmp_path, monkeypatch, preset
):
    install_full_flow_raws(monkeypatch, tmp_path, delta_mass=True)
    run = UntargetedAnalysis(
        FullAnalysisRequest(
            preset=preset,
            tuning_overrides={},
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "result",
            execution_profile=FULL_FLOW_EXECUTION,
        ),
        progress=lambda *_: None,
        should_cancel=lambda: False,
    )
    result = run.execute()
    batch = read_discovery_batch(run.output_dir / DISCOVERY_ARTIFACT_NAME)
    assert [batch.samples[s].raw_file.name for s in batch.sample_order] == [
        "first.raw",
        "second.raw",
    ]
    with Path(result["delta_mass"]["candidates_csv"]).open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        candidates = list(csv.DictReader(handle))
    assert [(r["sample"], float(r["mz"])) for r in candidates] == [
        ("first", 300),
        ("second", 600),
    ]
    assert float(candidates[1]["height"]) == pytest.approx(
        2 * float(candidates[0]["height"]), rel=1e-5
    )
    if preset.endswith("cid_nl_dr"):
        assert all(
            r["cid_nl_peak_id"].startswith(r["sample"] + "@") for r in candidates
        )
        replay = run_delta_mass_discovery(
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "replay",
            strategy=resolve_delta_mass_discovery(preset),
            cid_nl_store=run.output_dir / "cid_nl_dR" / DISCOVERY_ARTIFACT_NAME,
        )
        assert (
            replay.candidates_csv.read_bytes()
            == Path(result["delta_mass"]["candidates_csv"]).read_bytes()
        )
    else:
        assert {r["cid_nl_peak_id"] for r in candidates} == {""}
    assert result["modules"] == {"delta_mass": "completed"}
    assert result["matrix_tsv"] is None
    assert not list(run.output_dir.glob("alignment*"))


def test_composite_rejects_a_dr_input_that_is_not_discovery_before_raw(tmp_path):
    stale = tmp_path / "discovery_peaks.csv"
    stale.write_text("peak_id,sample_stem\n")
    (tmp_path / "sample.raw").touch()
    with pytest.raises(ValueError, match="not a current Discovery output"):
        run_delta_mass_discovery(
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "result",
            strategy=resolve_delta_mass_discovery("pt_pattern_cid_nl_dr"),
            cid_nl_store=stale,
        )
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize("stage", ["before_start", "after_discovery"])
def test_cancel_preserves_previous_matrix(tmp_path, monkeypatch, stage):
    install_full_flow_raws(monkeypatch, tmp_path)
    output = tmp_path / "result"
    output.mkdir()
    previous = output / "alignment_matrix.tsv"
    previous.write_bytes(b"previous completed matrix")
    cancelled = []

    def progress(_done, _total, message):
        if message.startswith("Discovery 完成"):
            cancelled.append(True)

    run = UntargetedAnalysis(
        FullAnalysisRequest(
            preset="dna_dr",
            tuning_overrides={},
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=output,
            execution_profile=FULL_FLOW_EXECUTION,
        ),
        progress=progress,
        should_cancel=lambda: stage == "before_start" or bool(cancelled),
    )
    with pytest.raises(InterruptedError):
        run.execute()
    assert previous.read_bytes() == b"previous completed matrix"
    assert previous not in [path for _label, path in run.available_outputs]
    assert any(
        path.name == DISCOVERY_ARTIFACT_NAME for _label, path in run.available_outputs
    ) == (stage == "after_discovery")


@pytest.mark.parametrize("outcome", ["cancel", "failure"])
def test_diagnostic_interruption_does_not_publish_completion(
    tmp_path, monkeypatch, outcome
):
    from xic_extractor.discovery import delta_mass_pipeline

    install_full_flow_raws(monkeypatch, tmp_path, delta_mass=True)
    open_raw = delta_mass_pipeline.open_raw
    cancelled = []

    def reader(path, *args, **kwargs):
        if outcome == "failure" and path.stem == "second":
            raise OSError("synthetic RAW read failure")
        return open_raw(path, *args, **kwargs)

    def progress(done, _total, message):
        if (
            outcome == "cancel"
            and done == 1
            and message.startswith("Delta Mass Discovery:")
        ):
            cancelled.append(True)

    monkeypatch.setattr(delta_mass_pipeline, "open_raw", reader)
    run = UntargetedAnalysis(
        FullAnalysisRequest(
            preset="pt_pattern_cid_nl_dr",
            tuning_overrides={},
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "result",
            execution_profile=FULL_FLOW_EXECUTION,
        ),
        progress=progress,
        should_cancel=lambda: bool(cancelled),
    )
    with pytest.raises(InterruptedError if outcome == "cancel" else OSError):
        run.execute()
    assert not (run.output_dir / "delta_mass_candidates.csv").exists()
    assert not list(run.output_dir.glob("alignment*"))
    assert any(
        path == run.output_dir / "cid_nl_dR" / DISCOVERY_ARTIFACT_NAME
        for _label, path in run.available_outputs
    )
