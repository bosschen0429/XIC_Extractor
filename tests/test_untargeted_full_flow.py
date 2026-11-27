"""Complete method routes with production stage owners and synthetic RAW I/O."""

import csv
import json
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
        "discovery_batch_index.csv",
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
    sidecar = json.loads((run.output_dir / "delta_mass_sidecar.json").read_text())
    export = (
        run.output_dir / "cid_nl_dR" / "discovery_peaks.csv"
        if preset.endswith("cid_nl_dr")
        else None
    )
    assert sidecar["inputs"] == {
        "raw_dir": str(tmp_path),
        "raw_files": ["first.raw", "second.raw"],
        "cid_nl_peaks_csv": str(export) if export else None,
    }
    with Path(result["delta_mass"]["candidates_csv"]).open(
        encoding="utf-8", newline=""
    ) as handle:
        candidates = list(csv.DictReader(handle))
    supported = [r for r in candidates if r["status"] == "candidate"]
    assert [(r["sample_stem"], float(r["base_mz"])) for r in supported] == [
        ("first", 300),
        ("second", 600),
    ]
    assert float(supported[1]["base_height"]) == pytest.approx(
        2 * float(supported[0]["base_height"]), rel=1e-5
    )
    if preset.endswith("cid_nl_dr"):
        assert [r["cid_nl_base_status"] for r in supported] == ["matched", "matched"]
        assert all(
            r["cid_nl_base_peak_id"].startswith(r["sample_stem"] + "@")
            for r in supported
        )
        assert Path(result["delta_mass"]["cid_nl_peaks_csv"]).is_file()
        merged = Path(result["delta_mass"]["cid_nl_peaks_csv"])
        merged.unlink()
        replay = run_delta_mass_discovery(
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "table-input",
            strategy=resolve_delta_mass_discovery(preset),
            cid_nl_export_csv=merged,
            cid_nl_peak_tables=tuple(
                run.output_dir / "cid_nl_dR" / stem / "discovery_peaks.csv"
                for stem in ("first", "second")
            ),
        )
        assert not merged.exists()
        assert (
            replay.candidates_csv.read_bytes()
            == Path(result["delta_mass"]["candidates_csv"]).read_bytes()
        )
    assert result["modules"] == {"delta_mass": "completed"}
    assert result["matrix_tsv"] is None
    assert not list(run.output_dir.glob("alignment*"))


@pytest.mark.parametrize("invalid", ["duplicate", "missing_column", "nonfinite"])
def test_composite_validates_across_sample_tables_before_raw(tmp_path, invalid):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    header = "peak_id,sample_stem,matched_tag_names,status,peak_mz,ms1_apex_rt\n"
    first.write_text(header + "id-first,first,DNA_dR,admitted,300,1\n")
    payload = {
        "duplicate": header + "id-first,second,DNA_dR,admitted,600,1\n",
        "missing_column": "peak_id,sample_stem\nid-second,second\n",
        "nonfinite": header + "id-second,second,DNA_dR,admitted,nan,1\n",
    }[invalid]
    second.write_text(payload)
    (tmp_path / "sample.raw").touch()
    with pytest.raises(ValueError, match=str(second).replace("\\", "\\\\")):
        run_delta_mass_discovery(
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "result",
            strategy=resolve_delta_mass_discovery("pt_pattern_cid_nl_dr"),
            cid_nl_peak_tables=(first, second),
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
        path.name == "discovery_batch_index.csv"
        for _label, path in run.available_outputs
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
        path.name == "discovery_peaks.csv" for _label, path in run.available_outputs
    )
