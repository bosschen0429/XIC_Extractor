from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.diagnostics import family_ms1_overlay_batch as batch
from tools.diagnostics import family_ms1_overlay_plot as report
from tools.diagnostics import family_ms1_overlay_writers as writers
from xic_extractor.peak_detection import evidence_facts as peak_evidence_owner


def test_cli_and_batch_build_one_gaussian15_context_per_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rows = _rows()
    smooth_points_seen: list[int] = []
    original = peak_evidence_owner.own_max_normalized_trace

    def _counting_owner(rt, intensity, *, smooth_points):
        smooth_points_seen.append(smooth_points)
        return original(rt, intensity, smooth_points=smooth_points)

    monkeypatch.setattr(
        peak_evidence_owner,
        "own_max_normalized_trace",
        _counting_owner,
    )
    monkeypatch.setattr(writers, "render_family_ms1_overlay", lambda **_kwargs: None)
    monkeypatch.setattr(
        writers,
        "render_hypothesis_ms1_overlay",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(report, "load_family_cells", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        report,
        "extract_family_trace_rows",
        lambda **_kwargs: rows,
    )
    monkeypatch.setattr(
        batch,
        "_raw_identity_by_sample",
        lambda *_args, **_kwargs: {},
    )

    scalar_dir = tmp_path / "scalar"
    assert (
        report.main(
            [
                "--alignment-cells",
                str(tmp_path / "cells.tsv"),
                "--family-id",
                "FAM001",
                "--raw-dir",
                str(tmp_path / "raw"),
                "--dll-dir",
                str(tmp_path / "dll"),
                "--mz",
                "251.165",
                "--rt-min",
                "0.8",
                "--rt-max",
                "1.2",
                "--family-center-rt",
                "1.0",
                "--output-dir",
                str(scalar_dir),
                "--output-prefix",
                "scalar",
            ]
        )
        == 0
    )
    assert smooth_points_seen == [15, 15]
    _assert_evidence_outputs(scalar_dir, "scalar")

    smooth_points_seen.clear()
    full_dir = tmp_path / "batch-full"
    full_request = _request("batch-full")
    full_row = batch._render_family(
        full_request,
        alignment_cells=tmp_path / "cells.tsv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=full_dir,
        max_highlight_rescued=8,
        source_provenance={},
        write_pdf=False,
        evidence_only=False,
        trace_rows=rows,
    )
    assert smooth_points_seen == [15, 15]
    assert full_row["status"] == "success"
    assert full_row["family_verdict"]
    _assert_evidence_outputs(full_dir, "batch-full")

    smooth_points_seen.clear()
    evidence_dir = tmp_path / "batch-evidence"
    evidence_request = _request("batch-evidence")
    evidence_row = batch._render_family(
        evidence_request,
        alignment_cells=tmp_path / "cells.tsv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=evidence_dir,
        max_highlight_rescued=8,
        source_provenance={},
        write_pdf=False,
        evidence_only=True,
        trace_rows=rows,
    )
    assert smooth_points_seen == [15, 15]
    assert evidence_row["status"] == "success"
    assert evidence_row["family_verdict"] == full_row["family_verdict"]
    _assert_evidence_outputs(evidence_dir, "batch-evidence")


def _rows() -> tuple[report.TraceOverlayRow, ...]:
    rt = [0.8, 0.9, 1.0, 1.1, 1.2]
    return (
        report.trace_row_from_arrays(
            _cell("detected", status="detected", height=100.0),
            "detected_seed",
            rt,
            [0.0, 20.0, 100.0, 20.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _cell("rescued", status="rescued", height=80.0),
            "top_rescued_ms1_area",
            rt,
            [0.0, 10.0, 80.0, 10.0, 0.0],
        ),
    )


def _cell(
    sample_stem: str,
    *,
    status: str,
    height: float,
) -> report.FamilyCell:
    return report.FamilyCell(
        sample_stem=sample_stem,
        status=status,
        area=height * 10.0,
        height=height,
        apex_rt=1.0,
        peak_start_rt=0.9,
        peak_end_rt=1.1,
        region_shadow_verdict="current_supported",
        source_candidate_id=f"{sample_stem}#1",
    )


def _request(output_prefix: str) -> batch.OverlayBatchRequest:
    return batch.OverlayBatchRequest(
        rank=1,
        family_id="FAM001",
        seed_group_id="SEED001",
        mz=251.165,
        ppm=10.0,
        rt_min=0.8,
        rt_max=1.2,
        family_center_rt=1.0,
        output_prefix=output_prefix,
    )


def _assert_evidence_outputs(output_dir: Path, output_prefix: str) -> None:
    summary_path = output_dir / f"{output_prefix}_trace_summary.tsv"
    json_path = output_dir / f"{output_prefix}_trace_data.json"
    with summary_path.open(encoding="utf-8", newline="") as handle:
        summary_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert [row["sample_stem"] for row in summary_rows] == ["detected", "rescued"]
    assert all(row["local_window_max_intensity"] for row in summary_rows)
    assert all(row["local_window_to_global_max_ratio"] for row in summary_rows)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 2
    assert payload["evidence_summary"]["trace_count"] == 2
    assert payload["evidence_summary"]["detected_count"] == 1
    assert payload["evidence_summary"]["rescued_count"] == 1
    assert [trace["sample_stem"] for trace in payload["traces"]] == [
        "detected",
        "rescued",
    ]
    assert all(trace["local_window_max_intensity"] for trace in payload["traces"])
