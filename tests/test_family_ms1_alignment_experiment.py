from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.diagnostics.family_ms1_alignment_experiment import (
    _best_source_family_shape_shift,
    _drift_corrected_normalized_trace,
    _pearson_similarity,
    _relative_aligned_normalized_trace,
    _source_family_normalized_traces,
    _source_family_shifted_median_curve_from_normalized_traces,
    _source_family_shifted_trace,
    build_source_family_best_shift_plan,
    build_source_family_shift_plan,
    load_source_family_by_family_sample,
    load_source_family_by_sample,
    load_trace_data_bundle,
    main,
)
from tools.diagnostics.family_ms1_overlay_models import TraceOverlayRow
from xic_extractor.alignment.edge_scoring import DriftPriorSource


def test_relative_alignment_can_compare_cell_apex_and_trace_apex() -> None:
    row = _trace_row(
        cell_apex=10.0,
        trace_apex=8.0,
        rt=(7.9, 8.0, 8.1, 9.9, 10.0, 10.1),
        intensity=(0.0, 100.0, 0.0, 0.0, 20.0, 0.0),
    )

    cell_rt, cell_normalized = _relative_aligned_normalized_trace(
        row,
        apex_source="cell",
    )
    trace_rt, trace_normalized = _relative_aligned_normalized_trace(
        row,
        apex_source="trace",
    )

    assert cell_rt.tolist() == pytest.approx([-0.1, 0.0, 0.1])
    assert trace_rt.tolist() == pytest.approx([-0.1, 0.0, 0.1])
    assert max(cell_normalized) == pytest.approx(1.0)
    assert max(trace_normalized) == pytest.approx(1.0)


def test_drift_corrected_trace_subtracts_sample_delta() -> None:
    row = _trace_row(
        sample="sample-a",
        rt=(7.9, 8.0, 8.1),
        intensity=(0.0, 100.0, 0.0),
    )
    lookup = _FakeDriftLookup({"sample-a": 0.2})

    rt, normalized = _drift_corrected_normalized_trace(
        row,
        drift_lookup=lookup,
    )

    assert rt.tolist() == pytest.approx([7.7, 7.8, 7.9])
    assert normalized.tolist() == pytest.approx([0.0, 1.0, 0.0])


def test_load_trace_data_bundle_reads_existing_overlay_json_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fam_trace_data.json"
    path.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": "fam001_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                },
                "traces": [
                    {
                        "sample_stem": "sample-a",
                        "status": "backfilled",
                        "group": "top_rescued_ms1_area",
                        "cell_area": 10.0,
                        "cell_height": 20.0,
                        "cell_apex_rt": 8.0,
                        "cell_start_rt": 7.9,
                        "cell_end_rt": 8.1,
                        "trace_max_intensity": 100.0,
                        "trace_apex_rt": 8.0,
                        "rt": [7.9, 8.0, 8.1],
                        "intensity": [0.0, 100.0, 0.0],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    bundle = load_trace_data_bundle(path)

    assert bundle.family_id == "FAM001"
    assert bundle.output_prefix == "fam001_overlay"
    assert len(bundle.rows) == 1
    assert bundle.rows[0].rt == (7.9, 8.0, 8.1)


def test_load_source_family_by_sample_reads_reason_provenance(tmp_path: Path) -> None:
    path = tmp_path / "cells.tsv"
    path.write_text(
        "\t".join(("feature_family_id", "sample_stem", "reason"))
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "sample-a",
                "primary family consolidation; source_family=FAM000123",
            ),
        )
        + "\n"
        + "\t".join(("FAM002", "sample-b", "source_family=FAM000999"))
        + "\n",
        encoding="utf-8",
    )

    mapping = load_source_family_by_sample(path, family_id="FAM001")

    assert mapping == {"sample-a": "FAM000123"}


def test_load_source_family_by_family_sample_reads_requested_families(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cells.tsv"
    path.write_text(
        "\t".join(("feature_family_id", "sample_stem", "reason"))
        + "\n"
        + "\t".join(("FAM001", "sample-a", "source_family=FAM000123"))
        + "\n"
        + "\t".join(("FAM002", "sample-b", "source_family=FAM000999"))
        + "\n"
        + "\t".join(("FAM003", "sample-c", "missing source"))
        + "\n",
        encoding="utf-8",
    )

    mapping = load_source_family_by_family_sample(
        path,
        family_ids=("FAM001", "FAM003"),
    )

    assert mapping == {
        "FAM001": {"sample-a": "FAM000123"},
        "FAM003": {"sample-c": "(none)"},
    }


def test_source_family_shift_plan_uses_one_group_shift() -> None:
    reference = _trace_row(
        "reference",
        status="detected",
        group="detected_seed",
        cell_apex=8.0,
        trace_apex=8.0,
    )
    shifted = _trace_row(
        "shifted",
        cell_apex=9.0,
        trace_apex=9.0,
        rt=(8.9, 9.0, 9.1),
        intensity=(0.0, 100.0, 0.0),
    )

    shifts = build_source_family_shift_plan(
        (reference, shifted),
        source_family_by_sample={
            "reference": "FAM000001",
            "shifted": "FAM000002",
        },
    )

    by_source = {shift.source_family: shift for shift in shifts}
    assert by_source["FAM000001"].is_reference is True
    assert by_source["FAM000001"].shift_to_reference_min == pytest.approx(0.0)
    assert by_source["FAM000002"].shift_to_reference_min == pytest.approx(-1.0)
    rt, normalized = _source_family_shifted_trace(
        shifted,
        shift_min=by_source["FAM000002"].shift_to_reference_min or 0.0,
    )
    assert rt.tolist() == pytest.approx([7.9, 8.0, 8.1])
    assert normalized.tolist() == pytest.approx([0.0, 1.0, 0.0])


def test_source_family_best_shift_plan_uses_group_shape_correlation() -> None:
    reference = _trace_row(
        "reference",
        status="detected",
        group="detected_seed",
        cell_apex=8.0,
        trace_apex=8.0,
        rt=(7.8, 7.9, 8.0, 8.1, 8.2),
        intensity=(0.0, 40.0, 100.0, 40.0, 0.0),
    )
    shifted = _trace_row(
        "shifted",
        cell_apex=9.0,
        trace_apex=9.0,
        rt=(8.8, 8.9, 9.0, 9.1, 9.2),
        intensity=(0.0, 40.0, 100.0, 40.0, 0.0),
    )

    shifts = build_source_family_best_shift_plan(
        (reference, shifted),
        source_family_by_sample={
            "reference": "FAM000001",
            "shifted": "FAM000002",
        },
        rt_min=7.5,
        rt_max=9.5,
        shift_min=-1.2,
        shift_max=0.0,
        shift_step=0.1,
    )

    by_source = {shift.source_family: shift for shift in shifts}
    assert by_source["FAM000001"].is_reference is True
    assert by_source["FAM000002"].shift_basis == "median_shape_correlation"
    assert by_source["FAM000002"].shift_to_reference_min == pytest.approx(-1.0)
    assert by_source["FAM000002"].shape_similarity_to_reference is not None
    assert by_source["FAM000002"].shape_similarity_to_reference > 0.99


def test_best_shift_vectorized_search_matches_scalar_loop() -> None:
    reference_rows = (
        _trace_row(
            "reference-a",
            rt=(7.8, 7.9, 8.0, 8.1, 8.2),
            intensity=(0.0, 40.0, 100.0, 40.0, 0.0),
        ),
        _trace_row(
            "reference-b",
            rt=(7.8, 7.9, 8.0, 8.1, 8.2),
            intensity=(0.0, 35.0, 95.0, 35.0, 0.0),
        ),
    )
    shifted_rows = (
        _trace_row(
            "shifted-a",
            rt=(8.8, 8.9, 9.0, 9.1, 9.2),
            intensity=(0.0, 40.0, 100.0, 40.0, 0.0),
        ),
        _trace_row(
            "shifted-b",
            rt=(8.8, 8.9, 9.0, 9.1, 9.2),
            intensity=(0.0, 35.0, 95.0, 35.0, 0.0),
        ),
    )
    grid = np.linspace(7.5, 9.5, 600)
    reference_curve = _source_family_shifted_median_curve_from_normalized_traces(
        _source_family_normalized_traces(reference_rows),
        shift_min=0.0,
        grid=grid,
    )
    traces = _source_family_normalized_traces(shifted_rows)

    shift, similarity = _best_source_family_shape_shift(
        traces,
        reference_curve=reference_curve,
        grid=grid,
        shift_min=-1.2,
        shift_max=0.0,
        shift_step=0.1,
    )
    scalar_shift, scalar_similarity = _scalar_best_source_family_shape_shift(
        traces,
        reference_curve=reference_curve,
        grid=grid,
        shift_min=-1.2,
        shift_max=0.0,
        shift_step=0.1,
    )

    assert shift == pytest.approx(scalar_shift)
    assert similarity == pytest.approx(scalar_similarity)


def test_cli_renders_alignment_experiment_without_raw_or_drift(tmp_path: Path) -> None:
    trace_json = tmp_path / "fam_trace_data.json"
    cell_evidence = tmp_path / "cells.tsv"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": "fam001_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "shape_supported_fraction": 0.3,
                },
                "traces": [
                    _trace_json("detected-a", status="detected", group="detected_seed"),
                    _trace_json("rescued-a", status="backfilled"),
                ],
            },
        ),
        encoding="utf-8",
    )
    cell_evidence.write_text(
        "\t".join(("feature_family_id", "sample_stem", "reason"))
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "detected-a",
                "primary family consolidation; source_family=FAM000001",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "rescued-a",
                "primary family consolidation; source_family=FAM000002",
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    exit_code = main(
        [
            "--trace-data-json",
            str(trace_json),
            "--output-dir",
            str(out_dir),
            "--output-prefix",
            "fam001_experiment",
            "--cell-evidence-tsv",
            str(cell_evidence),
        ],
    )

    assert exit_code == 0
    assert (out_dir / "fam001_experiment.png").is_file()
    summary_tsv = out_dir / "fam001_experiment_summary.tsv"
    _assert_tsv_header_and_lf(
        summary_tsv,
        [
            "method",
            "plotted_traces",
            "total_focus_traces",
            "basis",
            "interpretation",
            "absolute_trace_apex_cluster_fraction",
            "absolute_own_max_shape_supported_fraction",
            "cell_apex_shape_supported_fraction",
        ],
    )
    summary = summary_tsv.read_text(encoding="utf-8")
    assert "trace_apex_alignment" in summary
    assert "drift_corrected_rt_context\t0\t2" in summary
    assert "0.750" in summary
    assert (out_dir / "fam001_experiment_source_family_split.png").is_file()
    assert (
        out_dir / "fam001_experiment_source_family_shift_alignment.png"
    ).is_file()
    assert (
        out_dir / "fam001_experiment_source_family_best_shift_alignment.png"
    ).is_file()
    source_summary_tsv = out_dir / "fam001_experiment_source_family_summary.tsv"
    _assert_tsv_header_and_lf(
        source_summary_tsv,
        [
            "source_family",
            "trace_count",
            "detected_count",
            "backfilled_count",
            "median_cell_apex_rt",
            "min_cell_apex_rt",
            "max_cell_apex_rt",
        ],
    )
    source_summary = source_summary_tsv.read_text(encoding="utf-8")
    assert "FAM000001" in source_summary
    assert "FAM000002" in source_summary
    shift_summary_tsv = out_dir / "fam001_experiment_source_family_shift_summary.tsv"
    _assert_tsv_header_and_lf(
        shift_summary_tsv,
        [
            "feature_family_id",
            "source_family",
            "is_reference",
            "trace_count",
            "detected_count",
            "shift_basis",
            "median_cell_apex_rt",
            "shift_to_reference_min",
            "shift_to_reference_sec",
            "shape_similarity_to_reference_after_group_shift",
        ],
    )
    shift_summary = shift_summary_tsv.read_text(encoding="utf-8")
    assert "shift_to_reference_min" in shift_summary
    best_shift_summary_tsv = (
        out_dir / "fam001_experiment_source_family_best_shift_summary.tsv"
    )
    _assert_tsv_header_and_lf(
        best_shift_summary_tsv,
        [
            "feature_family_id",
            "source_family",
            "is_reference",
            "trace_count",
            "detected_count",
            "shift_basis",
            "median_cell_apex_rt",
            "shift_to_reference_min",
            "shift_to_reference_sec",
            "shape_similarity_to_reference_after_group_shift",
        ],
    )
    best_shift_summary = best_shift_summary_tsv.read_text(encoding="utf-8")
    assert "feature_family_id" in best_shift_summary
    assert "FAM001" in best_shift_summary
    assert "median_shape_correlation" in best_shift_summary


def test_cli_no_images_writes_shift_summaries_without_pngs(tmp_path: Path) -> None:
    trace_json = tmp_path / "fam_trace_data.json"
    cell_evidence = tmp_path / "cells.tsv"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": "fam001_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "shape_supported_fraction": 0.3,
                },
                "traces": [
                    _trace_json("detected-a", status="detected", group="detected_seed"),
                    _trace_json("rescued-a", status="backfilled"),
                ],
            },
        ),
        encoding="utf-8",
    )
    cell_evidence.write_text(
        "\t".join(("feature_family_id", "sample_stem", "reason"))
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "detected-a",
                "primary family consolidation; source_family=FAM000001",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "rescued-a",
                "primary family consolidation; source_family=FAM000002",
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    exit_code = main(
        [
            "--trace-data-json",
            str(trace_json),
            "--output-dir",
            str(out_dir),
            "--output-prefix",
            "fam001_experiment",
            "--cell-evidence-tsv",
            str(cell_evidence),
            "--no-images",
        ],
    )

    assert exit_code == 0
    assert (out_dir / "fam001_experiment_summary.tsv").is_file()
    assert (out_dir / "fam001_experiment_source_family_summary.tsv").is_file()
    assert (out_dir / "fam001_experiment_source_family_shift_summary.tsv").is_file()
    assert (
        out_dir / "fam001_experiment_source_family_best_shift_summary.tsv"
    ).is_file()
    assert not list(out_dir.glob("*.png"))


class _FakeDriftLookup:
    def __init__(self, deltas: dict[str, float]) -> None:
        self._deltas = deltas
        self._source: DriftPriorSource = "targeted_istd_trend"

    @property
    def source(self) -> DriftPriorSource:
        return self._source

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return None


def _trace_row(
    sample: str = "sample-a",
    *,
    status: str = "backfilled",
    group: str = "top_rescued_ms1_area",
    cell_apex: float = 8.0,
    trace_apex: float = 8.0,
    rt: tuple[float, ...] = (7.9, 8.0, 8.1),
    intensity: tuple[float, ...] = (0.0, 100.0, 0.0),
) -> TraceOverlayRow:
    return TraceOverlayRow(
        sample_stem=sample,
        status=status,
        group=group,
        cell_area=1000.0,
        cell_height=100.0,
        cell_apex_rt=cell_apex,
        cell_start_rt=cell_apex - 0.1,
        cell_end_rt=cell_apex + 0.1,
        trace_max_intensity=100.0,
        trace_apex_rt=trace_apex,
        region_shadow_verdict="",
        source_candidate_id="",
        rt=rt,
        intensity=intensity,
    )


def _scalar_best_source_family_shape_shift(
    traces: tuple[tuple[np.ndarray, np.ndarray], ...],
    *,
    reference_curve: np.ndarray,
    grid: np.ndarray,
    shift_min: float,
    shift_max: float,
    shift_step: float,
) -> tuple[float | None, float | None]:
    best_shift: float | None = None
    best_similarity: float | None = None
    for shift in np.arange(shift_min, shift_max + shift_step / 2.0, shift_step):
        curve = _source_family_shifted_median_curve_from_normalized_traces(
            traces,
            shift_min=float(shift),
            grid=grid,
        )
        similarity = _pearson_similarity(curve, reference_curve)
        if similarity is None:
            continue
        if best_similarity is None or similarity > best_similarity:
            best_shift = float(shift)
            best_similarity = similarity
    return best_shift, best_similarity


def _trace_json(
    sample: str,
    *,
    status: str = "backfilled",
    group: str = "top_rescued_ms1_area",
) -> dict[str, object]:
    return {
        "sample_stem": sample,
        "status": status,
        "group": group,
        "cell_area": 1000.0,
        "cell_height": 100.0,
        "cell_apex_rt": 8.0,
        "cell_start_rt": 7.9,
        "cell_end_rt": 8.1,
        "trace_max_intensity": 100.0,
        "trace_apex_rt": 8.0,
        "region_shadow_verdict": "",
        "source_candidate_id": "",
        "rt": [7.9, 8.0, 8.1],
        "intensity": [0.0, 100.0, 0.0],
    }


def _assert_tsv_header_and_lf(path: Path, expected_header: list[str]) -> None:
    assert b"\r\n" not in path.read_bytes()
    assert path.read_text(encoding="utf-8").splitlines()[0].split(
        "\t"
    ) == expected_header
