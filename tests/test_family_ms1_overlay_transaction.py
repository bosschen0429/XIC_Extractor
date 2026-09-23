from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.diagnostics import changed_row_mode_overlay_review as mode_review
from tools.diagnostics import family_ms1_overlay_batch as batch
from tools.diagnostics import family_ms1_overlay_evidence as overlay_evidence
from tools.diagnostics import family_ms1_overlay_plot as report
from tools.diagnostics import family_ms1_overlay_writers as writers
from xic_extractor.alignment.shared_peak_identity_explanation import (
    overlay_trace_data,
)
from xic_extractor.peak_detection import ms1_shape_identity as peak_trace_owner


def test_cli_and_batch_build_one_gaussian15_context_per_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rows = _rows()
    smooth_points_seen: list[int] = []
    original = peak_trace_owner.gaussian_smooth_values

    def _counting_smooth(values, *, points):
        smooth_points_seen.append(points)
        return original(values, points=points)

    monkeypatch.setattr(
        peak_trace_owner,
        "gaussian_smooth_values",
        _counting_smooth,
    )
    monkeypatch.setattr(
        overlay_evidence,
        "gaussian_smooth_values",
        _counting_smooth,
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


def test_serialized_trace_fields_share_one_gaussian15_basis(tmp_path: Path) -> None:
    rt = tuple(index / 10.0 for index in range(31))
    intensity = [0.0] * len(rt)
    intensity[5] = 100.0
    for index in range(15, 26):
        intensity[index] = 40.0
    shifted = report.trace_row_from_arrays(
        _cell("basis", status="detected", height=100.0, apex=2.0),
        "detected_seed",
        rt,
        intensity,
    )
    late_intensity = [0.0] * len(rt)
    late_intensity[29] = 100.0
    for index in range(15, 26):
        late_intensity[index] = 40.0
    shifted_late = report.trace_row_from_arrays(
        _cell("basis-late", status="detected", height=100.0, apex=2.0),
        "detected_seed",
        rt,
        late_intensity,
    )
    inconclusive = report.trace_row_from_arrays(
        _cell("short", status="backfilled", height=10.0, apex=2.0),
        "top_rescued_ms1_area",
        (1.9, 2.0),
        (1.0, 10.0),
    )

    outputs = report.write_family_ms1_overlay_outputs(
        rows=(shifted, shifted_late, inconclusive),
        output_dir=tmp_path,
        output_prefix="basis",
        family_id="FAM001",
        mz=251.165,
        ppm=10.0,
        rt_min=0.0,
        rt_max=3.0,
        family_center_rt=2.0,
        write_pdf=False,
    )

    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        summary_by_sample = {
            row["sample_stem"]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }
    payload = json.loads(outputs.trace_data_json.read_text(encoding="utf-8"))
    trace_by_sample = {trace["sample_stem"]: trace for trace in payload["traces"]}

    serialized = trace_by_sample["basis"]
    assert serialized["rt"] == list(rt)
    assert serialized["intensity"] == intensity
    assert serialized["trace_apex_rt"] == pytest.approx(2.0)
    assert serialized["trace_max_intensity"] == pytest.approx(39.02762293290437)
    assert serialized["global_trace_apex_delta_min"] == pytest.approx(
        serialized["trace_apex_rt"] - serialized["cell_apex_rt"],
    )
    assert serialized["local_window_to_global_max_ratio"] == pytest.approx(
        serialized["local_window_max_intensity"]
        / serialized["trace_max_intensity"],
    )

    summary = summary_by_sample["basis"]
    assert float(summary["global_trace_apex_delta_min"]) == pytest.approx(
        float(summary["trace_apex_rt"]) - float(summary["cell_apex_rt"]),
    )
    assert float(summary["local_window_to_global_max_ratio"]) == pytest.approx(
        float(summary["local_window_max_intensity"])
        / float(summary["trace_max_intensity"]),
        rel=2e-5,
    )

    unavailable = trace_by_sample["short"]
    assert unavailable["trace_apex_rt"] is None
    assert unavailable["trace_max_intensity"] is None
    assert unavailable["global_trace_apex_delta_min"] is None
    assert unavailable["local_window_max_intensity"] is None
    assert unavailable["local_window_to_global_max_ratio"] is None
    assert summary_by_sample["short"]["trace_apex_rt"] == ""
    assert summary_by_sample["short"]["trace_max_intensity"] == ""

    assert shifted.trace_apex_rt == pytest.approx(0.5)
    assert shifted_late.trace_apex_rt == pytest.approx(2.9)
    downstream_bundle = overlay_trace_data.load_overlay_trace_data(
        outputs.trace_data_json,
    )
    downstream_modes = mode_review._global_trace_mode_ids(
        downstream_bundle.trace_mappings,
    )
    assert downstream_modes == {
        "basis": "global_trace_mode_1",
        "basis-late": "global_trace_mode_1",
    }


def test_writer_rejects_incomplete_owner_context_before_output(tmp_path: Path) -> None:
    rows = _rows()
    context = overlay_evidence._build_family_ms1_evidence_context(rows)
    incomplete = replace(
        context,
        normalized_by_row={rows[0]: context.normalized_by_row[rows[0]]},
    )
    swapped_normalized = dict(context.normalized_by_row)
    swapped_normalized[rows[0]], swapped_normalized[rows[1]] = (
        swapped_normalized[rows[1]],
        swapped_normalized[rows[0]],
    )
    swapped_window_facts = dict(context.window_facts_by_row)
    swapped_window_facts[rows[0]], swapped_window_facts[rows[1]] = (
        swapped_window_facts[rows[1]],
        swapped_window_facts[rows[0]],
    )
    crossed = replace(
        context,
        normalized_by_row=swapped_normalized,
        window_facts_by_row=swapped_window_facts,
    )

    for label, invalid_context in (("incomplete", incomplete), ("crossed", crossed)):
        output_dir = tmp_path / label
        with pytest.raises(ValueError, match="exact owner-minted Gaussian15 context"):
            writers.write_family_ms1_overlay_outputs(
                rows=rows,
                context=invalid_context,
                output_dir=output_dir,
                output_prefix=label,
                family_id="FAM001",
                mz=251.165,
                ppm=10.0,
                rt_min=0.8,
                rt_max=1.2,
                family_center_rt=1.0,
                write_pdf=False,
            )
        assert not output_dir.exists()


def test_exported_renderers_keep_existing_call_shape(tmp_path: Path) -> None:
    rows = _rows()
    for renderer, filename in (
        (report.render_family_ms1_overlay, "family.png"),
        (report.render_hypothesis_ms1_overlay, "hypothesis.png"),
    ):
        output_path = tmp_path / filename
        renderer(
            rows=rows,
            png_path=output_path,
            pdf_path=None,
            family_id="FAM001",
            mz=251.165,
            ppm=10.0,
            rt_min=0.8,
            rt_max=1.2,
            family_center_rt=1.0,
        )
        assert output_path.is_file()


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
            _cell("backfilled", status="backfilled", height=80.0),
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
    apex: float = 1.0,
) -> report.FamilyCell:
    return report.FamilyCell(
        sample_stem=sample_stem,
        status=status,
        area=height * 10.0,
        height=height,
        apex_rt=apex,
        peak_start_rt=apex - 0.1,
        peak_end_rt=apex + 0.1,
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
    assert [row["sample_stem"] for row in summary_rows] == ["detected", "backfilled"]
    assert all(row["local_window_max_intensity"] for row in summary_rows)
    assert all(row["local_window_to_global_max_ratio"] for row in summary_rows)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 2
    assert payload["evidence_summary"]["trace_count"] == 2
    assert payload["evidence_summary"]["detected_count"] == 1
    assert payload["evidence_summary"]["backfilled_count"] == 1
    assert [trace["sample_stem"] for trace in payload["traces"]] == [
        "detected",
        "backfilled",
    ]
    assert all(trace["local_window_max_intensity"] for trace in payload["traces"])
