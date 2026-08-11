import csv
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.diagnostics import family_ms1_overlay_batch as batch
from tools.diagnostics import family_ms1_overlay_plot as overlay_plot


@pytest.fixture(autouse=True)
def _keep_string_trace_rows_as_test_doubles(monkeypatch) -> None:
    original = batch.overlay_plot._build_family_ms1_evidence_context

    def _build_context(rows):
        if rows and isinstance(rows[0], str):
            evidence_summary = batch.overlay_plot._build_family_ms1_evidence_summary(
                rows,
                window_facts_by_row={},
                shape_similarity={},
                absolute_shape_similarity={},
            )
            return SimpleNamespace(
                rows=tuple(rows),
                evidence_summary=evidence_summary,
                require_exact_rows=lambda _rows: None,
            )
        return original(rows)

    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_context",
        _build_context,
    )


def test_family_ms1_modules_document_legacy_peak_group_terminology() -> None:
    diagnostics_dir = Path(__file__).resolve().parents[1] / "tools" / "diagnostics"

    for path in diagnostics_dir.glob("family_ms1_*.py"):
        source = path.read_text(encoding="utf-8")
        docstring = source.split('"""', 2)[1].lower()
        assert "peak-group" in docstring, path.name
        assert "legacy" in docstring or "family-id" in docstring, path.name


def test_write_tsv_preserves_exact_serialization_contract(tmp_path: Path) -> None:
    path = tmp_path / "summary.tsv"

    batch._write_tsv(
        path,
        (
            {
                "label": "alpha",
                "value": 1.23456789,
                "flag": True,
                "missing": None,
            },
            {
                "label": "beta",
                "value": float("nan"),
                "flag": False,
            },
        ),
        ("label", "value", "flag", "missing"),
    )

    assert path.read_bytes() == (
        b"label\tvalue\tflag\tmissing\n"
        b"alpha\t1.23457\tTrue\t\n"
        b"beta\tnan\tFalse\t\n"
    )


def test_render_family_job_returns_failure_row_instead_of_raising(
    tmp_path: Path,
) -> None:
    # The parallel-render worker must never propagate exceptions; a failed
    # peak group becomes a failure row so the batch (and pool) keep going.
    request = batch.OverlayBatchRequest(
        rank=1,
        family_id="FAM001",
        seed_group_id="",
        mz=251.0,
        ppm=10.0,
        rt_min=1.0,
        rt_max=1.2,
        family_center_rt=1.1,
        output_prefix="fam001",
    )
    payload = (
        request,
        {
            "alignment_cells": tmp_path / "missing.tsv",  # forces a load failure
            "raw_dir": tmp_path,
            "dll_dir": tmp_path,
            "output_dir": tmp_path / "out",
            "max_highlight_rescued": 8,
            "source_provenance": {},
            "write_pdf": False,
            "evidence_only": False,
            "trace_rows": None,
            "dpi": 140,
        },
    )

    row = batch._render_family_job(payload)

    assert row["status"] == "failed"
    assert row["failure_reason"]
    assert row["feature_family_id"] == "FAM001"


def test_batch_uses_structured_queue_columns_and_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(
        queue_tsv,
        [
            _queue_row(
                "FAM001",
                mz="251.165",
                rt_min="1.0",
                rt_max="1.2",
                seed_group_id="seed::FAM001::a",
            ),
            _queue_row("FAM002", mz="252.165", rt_min="2.0", rt_max="2.2"),
        ],
    )
    calls: list[dict[str, object]] = []
    write_calls: list[dict[str, object]] = []

    def fake_load_family_cells(_alignment_cells: Path, family_id: str) -> list[str]:
        return [family_id]

    def fake_extract_family_trace_rows(**kwargs):
        calls.append(dict(kwargs))
        return ["trace-row"]

    def fake_write_family_ms1_overlay_outputs(**kwargs):
        write_calls.append(dict(kwargs))
        prefix = kwargs["output_prefix"]
        for suffix in (".png", ".pdf", "_trace_summary.tsv", "_trace_data.json"):
            (output_dir / f"{prefix}{suffix}").write_text("ok", encoding="utf-8")
        return overlay_plot.FamilyMs1OverlayOutputs(
            png_path=output_dir / f"{prefix}.png",
            pdf_path=output_dir / f"{prefix}.pdf",
            summary_tsv=output_dir / f"{prefix}_trace_summary.tsv",
            trace_data_json=output_dir / f"{prefix}_trace_data.json",
        )

    monkeypatch.setattr(batch.overlay_plot, "load_family_cells", fake_load_family_cells)
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        fake_extract_family_trace_rows,
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        fake_write_family_ms1_overlay_outputs,
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": "ms1_shape_supports_family_backfill",
            "dda_trigger_limited_ms2_support": True,
            "detected_count": 2,
            "rescued_count": 80,
            "detected_rescued_count": 82,
            "evaluable_trace_count": 82,
            "global_apex_assessable_trace_count": 80,
            "global_apex_assessable_fraction": 0.975,
            "selected_apex_in_trace_window_count": 79,
            "selected_apex_in_trace_window_fraction": 0.963,
            "local_apex_assessable_trace_count": 78,
            "global_apex_interference_count": 1,
            "shape_supported_fraction": 0.8,
            "absolute_own_max_evaluable_trace_count": 82,
            "absolute_own_max_shape_supported_count": 81,
            "absolute_own_max_shape_supported_fraction": 0.988,
            "absolute_trace_apex_assessable_count": 82,
            "absolute_trace_apex_cluster_count": 80,
            "absolute_trace_apex_cluster_fraction": 0.976,
            "absolute_trace_apex_delta_abs_median_min": 0.012,
            "global_apex_interference_fraction": 0.0125,
            "local_apex_supported_count": 77,
            "local_apex_supported_fraction": 0.987,
        },
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=tmp_path / "alignment_cells.tsv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        limit=1,
    )
    batch._write_outputs(output_dir, rows)

    assert len(rows) == 1
    assert rows[0]["feature_family_id"] == "FAM001"
    assert rows[0]["seed_group_id"] == "seed::FAM001::a"
    assert rows[0]["status"] == "success"
    assert rows[0]["family_verdict"] == "ms1_shape_supports_family_backfill"
    assert calls[0]["mz"] == 251.165
    assert calls[0]["ppm"] == 10.0
    assert calls[0]["rt_min"] == 1.0
    assert calls[0]["rt_max"] == 1.2
    assert write_calls[0]["dpi"] == 140
    summary_tsv = output_dir / "family_ms1_overlay_batch_summary.tsv"
    _assert_summary_tsv_contract(summary_tsv)
    summary = _read_tsv(summary_tsv)
    assert summary[0]["seed_group_id"] == "seed::FAM001::a"
    assert summary[0]["png_path"].endswith("fam001_overlay.png")
    assert summary[0]["ppm"] == "10"
    assert summary[0]["global_apex_assessable_trace_count"] == "80"
    assert summary[0]["selected_apex_in_trace_window_fraction"] == "0.963"
    assert summary[0]["global_apex_interference_count"] == "1"
    assert summary[0]["absolute_own_max_shape_supported_fraction"] == "0.988"
    assert summary[0]["absolute_trace_apex_cluster_fraction"] == "0.976"
    markdown = (output_dir / "family_ms1_overlay_batch.md").read_text(
        encoding="utf-8",
    )
    assert "`FAM001`" in markdown


def test_batch_evidence_only_writes_trace_artifacts_without_png(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )

    def fail_render(**_kwargs):
        raise AssertionError("evidence-only mode must not render PNG overlays")

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\nS1\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "provenance": {},
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                    },
                },
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        fail_render,
    )
    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {"family_verdict": batch.SUPPORT_FAMILY_VERDICT},
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        limit=1,
        evidence_only=True,
    )
    batch._write_outputs(output_dir, rows)

    assert rows[0]["status"] == "success"
    assert rows[0]["png_path"] == ""
    assert rows[0]["pdf_path"] == ""
    assert rows[0]["trace_summary_tsv"].endswith("fam001_overlay_trace_summary.tsv")
    assert rows[0]["trace_data_json"].endswith("fam001_overlay_trace_data.json")
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert summary[0]["png_path"] == ""
    assert summary[0]["trace_data_json"].endswith("fam001_overlay_trace_data.json")


def test_batch_evidence_only_reuses_content_keyed_cache_across_output_dirs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
            "detected_count": 1,
        },
    )

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\tstatus\nS1\trescued\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "mz": 251.165,
                    "ppm": 10.0,
                    "rt_min": 1.0,
                    "rt_max": 1.2,
                    "provenance": dict(_kwargs["provenance"]),
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                        "detected_count": 1,
                    },
                    "traces": [],
                },
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)

    first_metrics: dict[str, object] = {}
    first_rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=first_metrics,
    )

    assert first_rows[0]["status"] == "success"
    assert first_metrics["evidence_cache_hit_count"] == 0
    assert first_metrics["evidence_cache_miss_count"] == 1
    assert first_metrics["evidence_cache_store_count"] == 1

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit should not load cells or extract RAW traces"),
        ),
    )
    second_metrics: dict[str, object] = {}
    second_rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=second_metrics,
    )

    assert second_rows[0]["status"] == "success"
    assert second_metrics["evidence_cache_hit_count"] == 1
    assert second_metrics["evidence_cache_miss_count"] == 0
    trace_path = Path(str(second_rows[0]["trace_data_json"]))
    assert trace_path.is_file()
    assert cache_dir in trace_path.parents
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["provenance"]["review_queue_tsv"] == str(queue_tsv)
    assert payload["provenance"]["output_prefix"] == "fam001_overlay"


def test_batch_evidence_cache_v6_rejects_v5_scalar_semantics(
    tmp_path: Path,
) -> None:
    assert batch.OVERLAY_BATCH_SOURCE == "family_ms1_overlay_batch_v2"
    assert batch.OVERLAY_EVIDENCE_CACHE_SCHEMA == "family_ms1_overlay_evidence_cache_v6"
    cache_dir = tmp_path / "overlay_cache"
    cache_dir.mkdir()
    batch._cache_index_path(cache_dir).write_text(
        json.dumps(
            {
                "schema_version": "family_ms1_overlay_evidence_cache_v5",
                "entries": {"OLD": {"schema_version": "v5"}},
            },
        ),
        encoding="utf-8",
    )
    assert batch._read_cache_index(cache_dir) == {}

    request = batch.OverlayBatchRequest(
        rank=1,
        family_id="FAM001",
        seed_group_id="SEED001",
        mz=251.165,
        ppm=10.0,
        rt_min=1.0,
        rt_max=1.2,
        family_center_rt=1.1,
        output_prefix="fam001_overlay",
    )
    current_payload = batch._cache_key_payload(
        request,
        source_provenance={
            "overlay_batch_source": batch.OVERLAY_BATCH_SOURCE,
            "alignment_cells_sha256": "ABC",
            "raw_dir": "raw",
            "dll_dir": "dll",
        },
        max_highlight_rescued=8,
    )
    stale_payload = {
        **current_payload,
        "schema_version": "family_ms1_overlay_evidence_cache_v5",
    }
    assert batch._cache_key(stale_payload) != batch._cache_key(current_payload)
    assert not batch._cache_index_entry_matches(
        {
            "schema_version": "family_ms1_overlay_evidence_cache_v5",
            "cache_key": batch._cache_key(stale_payload),
            "key_payload": stale_payload,
        },
        cache_key=batch._cache_key(current_payload),
        key_payload=current_payload,
    )
    assert not batch._existing_provenance_matches_request(
        {
            "overlay_batch_source": "family_ms1_overlay_batch_v1",
            "alignment_cells_sha256": "ABC",
            "raw_dir": "raw",
            "dll_dir": "dll",
            "seed_group_id": "SEED001",
            "output_prefix": "fam001_overlay",
        },
        request,
        source_provenance={
            "overlay_batch_source": batch.OVERLAY_BATCH_SOURCE,
            "alignment_cells_sha256": "ABC",
            "raw_dir": "raw",
            "dll_dir": "dll",
        },
    )


def test_batch_evidence_cache_stale_index_entry_falls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
            "detected_count": 1,
        },
    )

    render_count = 0

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\tstatus\nS1\trescued\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        nonlocal render_count
        render_count += 1
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "mz": 251.165,
                    "ppm": 10.0,
                    "rt_min": 1.0,
                    "rt_max": 1.2,
                    "provenance": dict(_kwargs["provenance"]),
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                        "detected_count": 1,
                    },
                    "traces": [],
                },
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)

    batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    assert render_count == 1

    cache_trace_json = next(cache_dir.glob("*/*_trace_data.json"))
    cache_trace_json.unlink()
    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 0
    assert metrics["evidence_cache_miss_count"] == 1
    assert render_count == 2
    trace_path = Path(str(rows[0]["trace_data_json"]))
    assert trace_path.is_file()
    assert second_output in trace_path.parents


def test_batch_evidence_cache_rejects_same_size_trace_payload_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
            "detected_count": 1,
        },
    )

    render_count = 0

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\tstatus\nS1\trescued\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        nonlocal render_count
        render_count += 1
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "mz": 251.165,
                    "ppm": 10.0,
                    "rt_min": 1.0,
                    "rt_max": 1.2,
                    "provenance": dict(_kwargs["provenance"]),
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                        "detected_count": 1,
                    },
                    "traces": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)

    batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    cache_trace_json = next(cache_dir.glob("*/*_trace_data.json"))
    corrupt_payload = json.loads(cache_trace_json.read_text(encoding="utf-8"))
    corrupt_payload["family_id"] = "FAM999"
    cache_trace_json.write_text(
        json.dumps(corrupt_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    assert render_count == 1

    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 0
    assert metrics["evidence_cache_miss_count"] == 1
    assert render_count == 2


def test_batch_evidence_cache_rejects_same_size_summary_payload_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    render_count = _install_fake_cache_renderer(monkeypatch)

    batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    assert render_count() == 1

    cache_summary = next(cache_dir.glob("*/*_trace_summary.tsv"))
    original_size = cache_summary.stat().st_size
    cache_summary.write_text(
        "sample_stem\tstatus\nS1\tblocked\n",
        encoding="utf-8",
    )
    assert cache_summary.stat().st_size == original_size

    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 0
    assert metrics["evidence_cache_miss_count"] == 1
    assert render_count() == 2
    summary_path = Path(str(rows[0]["trace_summary_tsv"]))
    assert second_output in summary_path.parents


def test_batch_evidence_cache_reuses_same_request_across_copied_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_a = tmp_path / "queue_a.tsv"
    queue_b = tmp_path / "queue_b.tsv"
    cells_a = tmp_path / "alignment_cells_a.tsv"
    cells_b = tmp_path / "alignment_cells_b.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    cells_a.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    cells_b.write_text(cells_a.read_text(encoding="utf-8"), encoding="utf-8")
    _write_queue(queue_a, [_queue_row("FAM001")])
    queue_b.write_text(queue_a.read_text(encoding="utf-8"), encoding="utf-8")
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
            "detected_count": 1,
        },
    )

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\tstatus\nS1\trescued\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "mz": 251.165,
                    "ppm": 10.0,
                    "rt_min": 1.0,
                    "rt_max": 1.2,
                    "provenance": dict(_kwargs["provenance"]),
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                        "detected_count": 1,
                    },
                    "traces": [],
                },
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)

    batch.run_overlay_batch(
        review_queue_tsv=queue_a,
        alignment_cells=cells_a,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("copied-input cache hit should not reload RAW evidence"),
        ),
    )
    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_b,
        alignment_cells=cells_b,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 1
    assert metrics["evidence_cache_miss_count"] == 0


def test_batch_evidence_cache_rejects_replaced_raw_file_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    render_count = _install_fake_cache_renderer(monkeypatch)

    batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    assert render_count() == 1

    _write_raw_identity_file(raw_dir, "S1", "raw-v2-replaced")
    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 0
    assert metrics["evidence_cache_miss_count"] == 1
    assert render_count() == 2
    trace_path = Path(str(rows[0]["trace_data_json"]))
    assert second_output in trace_path.parents


def test_batch_evidence_cache_manifest_fallback_rejects_replaced_raw_file_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    first_output = tmp_path / "out_first"
    second_output = tmp_path / "out_second"
    cache_dir = tmp_path / "overlay_cache"
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")

    render_count = _install_fake_cache_renderer(monkeypatch)

    batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=first_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
    )
    assert render_count() == 1

    batch._cache_index_path(cache_dir).unlink()
    _write_raw_identity_file(raw_dir, "S1", "raw-v2-replaced")
    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=second_output,
        limit=1,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 0
    assert metrics["evidence_cache_miss_count"] == 1
    assert render_count() == 2
    trace_path = Path(str(rows[0]["trace_data_json"]))
    assert second_output in trace_path.parents


def test_seed_evidence_cache_from_existing_overlay_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    overlay_dir = tmp_path / "existing_overlay"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    overlay_dir.mkdir()
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    _write_raw_identity_file(raw_dir, "S1", "raw-v1")
    (overlay_dir / "fam001_overlay_trace_summary.tsv").write_text(
        "sample_stem\tstatus\nS1\trescued\n",
        encoding="utf-8",
    )
    (overlay_dir / "fam001_overlay_trace_data.json").write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 251.165,
                "ppm": 20.0,
                "rt_min": 1.0,
                "rt_max": 1.2,
                "provenance": {},
                "evidence_summary": {
                    "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                    "detected_count": 1,
                },
                "traces": [],
            },
        ),
        encoding="utf-8",
    )
    (overlay_dir / "family_ms1_overlay_batch_summary.tsv").write_text(
        "rank\tfeature_family_id\tseed_group_id\tmz\tppm\trt_min\trt_max\t"
        "family_center_rt\toutput_prefix\tstatus\tfamily_verdict\t"
        "trace_summary_tsv\ttrace_data_json\n"
        "1\tFAM001\t\t251.165\t20\t1.0\t1.2\t1.1\tfam001_overlay\t"
        f"success\t{batch.SUPPORT_FAMILY_VERDICT}\t"
        "fam001_overlay_trace_summary.tsv\tfam001_overlay_trace_data.json\n",
        encoding="utf-8",
    )

    summary = batch.seed_evidence_cache_from_overlay_summary(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        overlay_batch_summary_tsv=overlay_dir / "family_ms1_overlay_batch_summary.tsv",
        evidence_cache_dir=cache_dir,
    )

    assert summary["cache_store_count"] == 1
    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("seeded cache should avoid RAW extraction"),
        ),
    )
    metrics: dict[str, object] = {}
    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        ppm=20.0,
        evidence_only=True,
        evidence_cache_dir=cache_dir,
        metrics=metrics,
    )

    assert rows[0]["status"] == "success"
    assert metrics["evidence_cache_hit_count"] == 1


def test_seed_evidence_cache_skips_mismatched_overlay_summary_row(
    tmp_path: Path,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    overlay_dir = tmp_path / "existing_overlay"
    cache_dir = tmp_path / "cache"
    overlay_dir.mkdir()
    alignment_cells.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM001")])
    (overlay_dir / "fam001_overlay_trace_summary.tsv").write_text(
        "sample_stem\tstatus\nS1\trescued\n",
        encoding="utf-8",
    )
    (overlay_dir / "fam001_overlay_trace_data.json").write_text(
        json.dumps(
            {
                "family_id": "FAM999",
                "mz": 251.165,
                "ppm": 20.0,
                "rt_min": 1.0,
                "rt_max": 1.2,
                "provenance": {},
                "evidence_summary": {
                    "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                    "detected_count": 1,
                },
                "traces": [],
            },
        ),
        encoding="utf-8",
    )
    (overlay_dir / "family_ms1_overlay_batch_summary.tsv").write_text(
        "rank\tfeature_family_id\tseed_group_id\tmz\tppm\trt_min\trt_max\t"
        "family_center_rt\toutput_prefix\tstatus\tfamily_verdict\t"
        "trace_summary_tsv\ttrace_data_json\n"
        "1\tFAM001\t\t251.165\t20\t1.0\t1.2\t1.1\tfam001_overlay\t"
        f"success\t{batch.SUPPORT_FAMILY_VERDICT}\t"
        "fam001_overlay_trace_summary.tsv\tfam001_overlay_trace_data.json\n",
        encoding="utf-8",
    )

    summary = batch.seed_evidence_cache_from_overlay_summary(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        overlay_batch_summary_tsv=overlay_dir / "family_ms1_overlay_batch_summary.tsv",
        evidence_cache_dir=cache_dir,
    )

    assert summary["cache_store_count"] == 0


def test_batch_uses_backfill_seed_mz_when_seed_queue_provides_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM_SEED\n", encoding="utf-8")
    row = _queue_row("FAM_SEED", mz="300.0", rt_min="10.0", rt_max="11.0")
    row["backfill_seed_mz"] = "301.123"
    row["ppm"] = "20"
    _write_queue(queue_tsv, [row])
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )

    def fake_extract_family_trace_rows(**kwargs):
        calls.append(dict(kwargs))
        return ["trace-row"]

    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        fake_extract_family_trace_rows,
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        lambda **kwargs: overlay_plot.FamilyMs1OverlayOutputs(
            png_path=output_dir / "seed.png",
            pdf_path=output_dir / "seed.pdf",
            summary_tsv=output_dir / "seed_trace_summary.tsv",
            trace_data_json=output_dir / "seed_trace_data.json",
        ),
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": "ms1_shape_supports_family_backfill"
        },
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=tmp_path / "alignment_cells.tsv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        limit=1,
    )

    assert rows[0]["status"] == "success"
    assert calls[0]["mz"] == 301.123
    assert calls[0]["ppm"] == 20.0


def test_batch_no_pdf_skips_pdf_output_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM_NO_PDF\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM_NO_PDF")])
    writer_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )

    def fake_write_family_ms1_overlay_outputs(**kwargs):
        writer_calls.append(dict(kwargs))
        prefix = kwargs["output_prefix"]
        for suffix in (
            ".png",
            "_hypothesis.png",
            "_trace_summary.tsv",
            "_trace_data.json",
        ):
            (output_dir / f"{prefix}{suffix}").write_text("ok", encoding="utf-8")
        return overlay_plot.FamilyMs1OverlayOutputs(
            png_path=output_dir / f"{prefix}.png",
            pdf_path=None,
            summary_tsv=output_dir / f"{prefix}_trace_summary.tsv",
            trace_data_json=output_dir / f"{prefix}_trace_data.json",
        )

    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        fake_write_family_ms1_overlay_outputs,
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": "ms1_shape_supports_family_backfill"
        },
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        limit=1,
        write_pdf=False,
    )
    batch._write_outputs(output_dir, rows)

    assert writer_calls[0]["write_pdf"] is False
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert summary[0]["png_path"].endswith("fam_no_pdf_overlay.png")
    assert summary[0]["pdf_path"] == ""
    assert not (output_dir / "fam_no_pdf_overlay.pdf").exists()
    assert not (output_dir / "fam_no_pdf_overlay_hypothesis.pdf").exists()


def test_batch_fast_path_opens_each_raw_sample_once(tmp_path: Path) -> None:
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    for sample in ("Sample_A", "Sample_B"):
        (raw_dir / f"{sample}.raw").write_text("raw", encoding="utf-8")
    _write_alignment_cells(
        alignment_cells,
        [
            _cell_row("FAM_A", "Sample_A", "detected", "1000"),
            _cell_row("FAM_A", "Sample_B", "rescued", "500"),
            _cell_row("FAM_B", "Sample_A", "detected", "900"),
            _cell_row("FAM_B", "Sample_B", "rescued", "400"),
        ],
    )
    requests = (
        batch.OverlayBatchRequest(
            rank=1,
            family_id="FAM_A",
            seed_group_id="seed-a",
            mz=251.0,
            ppm=10.0,
            rt_min=1.0,
            rt_max=1.2,
            family_center_rt=1.1,
            output_prefix="fam_a",
        ),
        batch.OverlayBatchRequest(
            rank=2,
            family_id="FAM_B",
            seed_group_id="seed-b",
            mz=252.0,
            ppm=10.0,
            rt_min=2.0,
            rt_max=2.2,
            family_center_rt=2.1,
            output_prefix="fam_b",
        ),
    )
    cells_by_family = overlay_plot.load_family_cells_for_families(
        alignment_cells,
        ("FAM_A", "FAM_B"),
    )
    opened: list[str] = []
    extract_counts: dict[str, int] = {}

    class FakeRaw:
        def __init__(self, sample_stem: str) -> None:
            self.sample_stem = sample_stem
            self.raw_chromatogram_call_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_xic_many(self, xic_requests):
            self.raw_chromatogram_call_count += 1
            extract_counts[self.sample_stem] = len(xic_requests)
            return tuple(
                SimpleNamespace(
                    rt=(request.rt_min, request.rt_max),
                    intensity=(request.mz, request.mz + 1),
                )
                for request in xic_requests
            )

    def fake_open_raw(raw_path: Path, _dll_dir: Path) -> FakeRaw:
        opened.append(raw_path.name)
        return FakeRaw(raw_path.stem)

    stats = batch.OverlayExtractionStats.empty()
    rows_by_rank = batch._extract_batch_family_trace_rows(
        requests,
        cells_by_family=cells_by_family,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        max_highlight_rescued=8,
        open_raw_func=fake_open_raw,
        extraction_stats=stats,
    )

    assert opened == ["Sample_A.raw", "Sample_B.raw"]
    assert extract_counts == {"Sample_A": 2, "Sample_B": 2}
    assert stats.to_metrics() == {
        "sample_count": 2,
        "sample_stems": "Sample_A,Sample_B",
        "raw_open_count": 2,
        "extract_xic_batch_count": 2,
        "extract_xic_count": 4,
        "raw_chromatogram_call_count": 2,
        "mean_xic_per_raw_chromatogram_call": 2.0,
        "trace_point_count": 8,
        "exact_scan_window_count": 0,
        "superwindow_group_count": 0,
        "superwindow_fallback_sample_count": 2,
        "superwindow_span_factor": 2,
    }
    assert [row.sample_stem for row in rows_by_rank[1]] == [
        "Sample_A",
        "Sample_B",
    ]
    assert [row.sample_stem for row in rows_by_rank[2]] == [
        "Sample_A",
        "Sample_B",
    ]


def test_batch_superwindow_crops_back_to_original_scan_windows(
    tmp_path: Path,
) -> None:
    from xic_extractor.xic_models import XICTrace

    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _write_alignment_cells(
        alignment_cells,
        [
            _cell_row("FAM_A", "Sample_A", "detected", "1000"),
            _cell_row("FAM_B", "Sample_A", "detected", "900"),
        ],
    )
    requests = (
        batch.OverlayBatchRequest(
            rank=1,
            family_id="FAM_A",
            seed_group_id="seed-a",
            mz=251.0,
            ppm=10.0,
            rt_min=1.0,
            rt_max=2.0,
            family_center_rt=1.5,
            output_prefix="fam_a",
        ),
        batch.OverlayBatchRequest(
            rank=2,
            family_id="FAM_B",
            seed_group_id="seed-b",
            mz=252.0,
            ppm=10.0,
            rt_min=1.2,
            rt_max=2.2,
            family_center_rt=1.7,
            output_prefix="fam_b",
        ),
    )
    cells_by_family = overlay_plot.load_family_cells_for_families(
        alignment_cells,
        ("FAM_A", "FAM_B"),
    )
    extract_batch_sizes: list[int] = []
    rt_lookup_counts: dict[int, int] = {}

    class FakeRaw:
        raw_chromatogram_call_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scan_window_for_request(self, request):
            return int(round(request.rt_min * 10)), int(round(request.rt_max * 10))

        def retention_time_for_scan(self, scan_number: int):
            rt_lookup_counts[scan_number] = rt_lookup_counts.get(scan_number, 0) + 1
            return scan_number / 10.0

        def extract_xic_many(self, xic_requests):
            self.raw_chromatogram_call_count += 1
            extract_batch_sizes.append(len(xic_requests))
            rt = [1.0, 1.1, 1.2, 2.0, 2.1, 2.2]
            return tuple(
                XICTrace.from_arrays(
                    rt,
                    [request.mz + offset for offset, _rt in enumerate(rt)],
                )
                for request in xic_requests
            )

    def fake_open_raw(_raw_path: Path, _dll_dir: Path) -> FakeRaw:
        return FakeRaw()

    stats = batch.OverlayExtractionStats.empty()
    rows_by_rank = batch._extract_batch_family_trace_rows(
        requests,
        cells_by_family=cells_by_family,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        max_highlight_rescued=8,
        open_raw_func=fake_open_raw,
        extraction_stats=stats,
    )

    assert extract_batch_sizes == [2]
    assert rows_by_rank[1][0].rt == (1.0, 1.1, 1.2, 2.0)
    assert rows_by_rank[2][0].rt == (1.2, 2.0, 2.1, 2.2)
    assert stats.to_metrics()["extract_xic_batch_count"] == 1
    assert stats.to_metrics()["raw_chromatogram_call_count"] == 1
    assert stats.to_metrics()["exact_scan_window_count"] == 2
    assert stats.to_metrics()["superwindow_group_count"] == 1
    assert stats.to_metrics()["trace_point_count"] == 8
    assert rt_lookup_counts == {10: 1, 12: 1, 20: 1, 22: 1}


def test_batch_reuses_duplicate_sample_xic_requests_without_dropping_rows(
    tmp_path: Path,
) -> None:
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _write_alignment_cells(
        alignment_cells,
        [
            _cell_row("FAM_A", "Sample_A", "detected", "1000"),
            _cell_row("FAM_B", "Sample_A", "rescued", "900"),
        ],
    )
    requests = (
        batch.OverlayBatchRequest(
            rank=1,
            family_id="FAM_A",
            seed_group_id="seed-a",
            mz=251.0,
            ppm=10.0,
            rt_min=1.0,
            rt_max=1.2,
            family_center_rt=1.1,
            output_prefix="fam_a",
        ),
        batch.OverlayBatchRequest(
            rank=2,
            family_id="FAM_B",
            seed_group_id="seed-b",
            mz=251.0,
            ppm=10.0,
            rt_min=1.0,
            rt_max=1.2,
            family_center_rt=1.1,
            output_prefix="fam_b",
        ),
    )
    cells_by_family = overlay_plot.load_family_cells_for_families(
        alignment_cells,
        ("FAM_A", "FAM_B"),
    )
    extract_batch_sizes: list[int] = []

    class FakeRaw:
        raw_chromatogram_call_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_xic_many(self, xic_requests):
            self.raw_chromatogram_call_count += 1
            extract_batch_sizes.append(len(xic_requests))
            return tuple(
                SimpleNamespace(
                    rt=(request.rt_min, request.rt_max),
                    intensity=(request.mz, request.mz + 1),
                )
                for request in xic_requests
            )

    def fake_open_raw(_raw_path: Path, _dll_dir: Path) -> FakeRaw:
        return FakeRaw()

    stats = batch.OverlayExtractionStats.empty()
    rows_by_rank = batch._extract_batch_family_trace_rows(
        requests,
        cells_by_family=cells_by_family,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        max_highlight_rescued=8,
        open_raw_func=fake_open_raw,
        extraction_stats=stats,
    )

    assert extract_batch_sizes == [1]
    assert rows_by_rank[1][0].sample_stem == "Sample_A"
    assert rows_by_rank[2][0].sample_stem == "Sample_A"
    assert rows_by_rank[1][0].rt == (1.0, 1.2)
    assert rows_by_rank[2][0].rt == (1.0, 1.2)
    assert stats.to_metrics()["extract_xic_count"] == 2
    assert stats.to_metrics()["extract_xic_batch_count"] == 1
    assert stats.to_metrics()["raw_chromatogram_call_count"] == 1
    assert stats.to_metrics()["trace_point_count"] == 4


def test_batch_reuses_existing_outputs_without_raw_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    alignment_cells.write_text("feature_family_id\nFAM_REUSE\n", encoding="utf-8")
    seed_group_id = (
        "seed::FAM_REUSE::mz=251.165::rt=1.1000::"
        "window=1.0000-1.2000::ppm=10"
    )
    _write_queue(queue_tsv, [_queue_row("FAM_REUSE", seed_group_id=seed_group_id)])
    prefix = "fam_reuse_overlay"
    for suffix in (".png", ".pdf", "_trace_summary.tsv"):
        (output_dir / f"{prefix}{suffix}").write_text("ok", encoding="utf-8")
    (output_dir / f"{prefix}_trace_data.json").write_text(
        json.dumps(
            {
                "family_id": "FAM_REUSE",
                "mz": 251.165,
                "ppm": 10.0,
                "rt_min": 1.0,
                "rt_max": 1.2,
                "provenance": {
                    "overlay_batch_source": batch.OVERLAY_BATCH_SOURCE,
                    "review_queue_tsv": str(queue_tsv),
                    "review_queue_sha256": _sha256_file(queue_tsv),
                    "alignment_cells_tsv": str(alignment_cells),
                    "alignment_cells_sha256": _sha256_file(alignment_cells),
                    "raw_dir": str(raw_dir),
                    "dll_dir": str(dll_dir),
                    "seed_group_id": seed_group_id,
                    "output_prefix": prefix,
                },
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill",
                    "detected_count": 2,
                    "rescued_count": 3,
                    "detected_rescued_count": 5,
                    "evaluable_trace_count": 5,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "local_apex_supported_fraction": 1.0,
                }
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("RAW path should not be used"),
        ),
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        reuse_existing=True,
        write_incremental=True,
    )

    assert rows[0]["status"] == "success"
    assert rows[0]["family_verdict"] == "ms1_shape_supports_family_backfill"
    assert rows[0]["detected_count"] == 2
    assert rows[0]["png_path"].endswith("fam_reuse_overlay.png")
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert summary[0]["feature_family_id"] == "FAM_REUSE"
    assert summary[0]["absolute_own_max_shape_supported_fraction"] == "0.8"


def test_parallel_batch_writes_incremental_prefixes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM001\nFAM002\n", encoding="utf-8")
    _write_queue(
        queue_tsv,
        [
            _queue_row("FAM001", seed_group_id="seed::FAM001::a"),
            _queue_row("FAM002", seed_group_id="seed::FAM002::a"),
        ],
    )
    write_calls: list[list[str]] = []

    class FakeExecutor:
        def __init__(self, max_workers: int) -> None:
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def map(self, func, payloads):
            for payload in payloads:
                yield func(payload)

    def fake_render_job(payload):
        request, _kwargs = payload
        return {
            "rank": str(request.rank),
            "feature_family_id": request.family_id,
            "seed_group_id": request.seed_group_id,
            "status": "success",
        }

    def fake_batch_trace_rows(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(batch, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(batch, "_batch_trace_rows_for_requests", fake_batch_trace_rows)
    monkeypatch.setattr(batch, "_render_family_job", fake_render_job)
    monkeypatch.setattr(
        batch,
        "_write_outputs",
        lambda _output_dir, rows, **_kwargs: write_calls.append(
            [row["feature_family_id"] for row in rows]
        ),
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        workers=2,
        write_incremental=True,
    )

    assert [row["feature_family_id"] for row in rows] == ["FAM001", "FAM002"]
    assert write_calls == [["FAM001"], ["FAM001", "FAM002"]]


def test_batch_does_not_reuse_existing_outputs_with_mismatched_trace_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    alignment_cells.write_text("feature_family_id\nFAM_REUSE\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM_REUSE")])
    prefix = "fam_reuse_overlay"
    for suffix in (".png", ".pdf", "_trace_summary.tsv"):
        (output_dir / f"{prefix}{suffix}").write_text("stale", encoding="utf-8")
    (output_dir / f"{prefix}_trace_data.json").write_text(
        json.dumps(
            {
                "family_id": "FAM_STALE",
                "mz": 999.0,
                "ppm": 20.0,
                "rt_min": 99.0,
                "rt_max": 100.0,
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill",
                },
            },
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_load_family_cells(_alignment_cells: Path, family_id: str) -> list[str]:
        calls.append(family_id)
        return [family_id]

    monkeypatch.setattr(batch.overlay_plot, "load_family_cells", fake_load_family_cells)
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        lambda **kwargs: overlay_plot.FamilyMs1OverlayOutputs(
            png_path=output_dir / f"{kwargs['output_prefix']}.png",
            pdf_path=output_dir / f"{kwargs['output_prefix']}.pdf",
            summary_tsv=output_dir / f"{kwargs['output_prefix']}_trace_summary.tsv",
            trace_data_json=output_dir / f"{kwargs['output_prefix']}_trace_data.json",
        ),
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": "review_required_uncertain_ms1_shape"
        },
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        reuse_existing=True,
    )

    assert calls == ["FAM_REUSE"]
    assert rows[0]["status"] == "success"
    assert rows[0]["family_verdict"] == "review_required_uncertain_ms1_shape"


def test_batch_does_not_reuse_legacy_trace_json_without_batch_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    alignment_cells.write_text("feature_family_id\nFAM_REUSE\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM_REUSE")])
    prefix = "fam_reuse_overlay"
    for suffix in (".png", ".pdf", "_trace_summary.tsv"):
        (output_dir / f"{prefix}{suffix}").write_text("legacy", encoding="utf-8")
    (output_dir / f"{prefix}_trace_data.json").write_text(
        json.dumps(
            {
                "family_id": "FAM_REUSE",
                "mz": 251.165,
                "ppm": 10.0,
                "rt_min": 1.0,
                "rt_max": 1.2,
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill",
                },
            },
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_load_family_cells(_alignment_cells: Path, family_id: str) -> list[str]:
        calls.append(family_id)
        return [family_id]

    monkeypatch.setattr(batch.overlay_plot, "load_family_cells", fake_load_family_cells)
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_render_family_ms1_overlay_outputs",
        lambda **kwargs: overlay_plot.FamilyMs1OverlayOutputs(
            png_path=output_dir / f"{kwargs['output_prefix']}.png",
            pdf_path=output_dir / f"{kwargs['output_prefix']}.pdf",
            summary_tsv=output_dir / f"{kwargs['output_prefix']}_trace_summary.tsv",
            trace_data_json=output_dir / f"{kwargs['output_prefix']}_trace_data.json",
        ),
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": "review_required_uncertain_ms1_shape"
        },
    )

    rows = batch.run_overlay_batch(
        review_queue_tsv=queue_tsv,
        alignment_cells=alignment_cells,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        reuse_existing=True,
    )

    assert calls == ["FAM_REUSE"]
    assert rows[0]["family_verdict"] == "review_required_uncertain_ms1_shape"


def test_markdown_blocks_top30_expansion_for_review_required_family(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    rows = [
        _batch_row(1, "FAM_SUPPORT", batch.SUPPORT_FAMILY_VERDICT),
        _batch_row(2, "FAM000906", "review_required_uncertain_ms1_shape"),
    ]

    batch._write_outputs(output_dir, rows)

    markdown = (output_dir / "family_ms1_overlay_batch.md").read_text(
        encoding="utf-8",
    )
    assert "- Top 30 expansion: `blocked`" in markdown
    assert "eligible only when every row succeeds" in markdown
    assert "rank 2 `FAM000906` (`review_required_uncertain_ms1_shape`)" in markdown
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert summary[0]["top30_expansion_gate"] == "blocked"
    assert summary[0]["top30_expansion_blocker"] == ""
    assert summary[1]["top30_expansion_gate"] == "blocked"
    assert summary[1]["top30_expansion_blocker"] == (
        "review_required_family_verdict"
    )
    assert "FAM000906 review_required_uncertain_ms1_shape" in summary[1][
        "top30_expansion_blockers"
    ]


def test_markdown_marks_top30_expansion_eligible_when_all_rows_support(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    rows = [
        _batch_row(1, "FAM001", batch.SUPPORT_FAMILY_VERDICT),
        _batch_row(2, "FAM002", batch.SUPPORT_FAMILY_VERDICT),
    ]

    batch._write_outputs(output_dir, rows)

    markdown = (output_dir / "family_ms1_overlay_batch.md").read_text(
        encoding="utf-8",
    )
    assert "- Top 30 expansion: `eligible`" in markdown
    assert "- Blocking peak groups: none" in markdown
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert [row["top30_expansion_gate"] for row in summary] == [
        "eligible",
        "eligible",
    ]
    assert [row["top30_expansion_blocker"] for row in summary] == ["", ""]
    assert [row["top30_expansion_blockers"] for row in summary] == ["", ""]


def test_summary_blocks_top30_expansion_for_failed_or_insufficient_seed_rows(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    rows = [
        _batch_row(1, "FAM_FAIL", "", status="failed"),
        _batch_row(2, "FAM_LOW_SEED", "insufficient_nl_seed_support"),
    ]

    batch._write_outputs(output_dir, rows)

    markdown = (output_dir / "family_ms1_overlay_batch.md").read_text(
        encoding="utf-8",
    )
    assert "- Top 30 expansion: `blocked`" in markdown
    assert "rank 1 `FAM_FAIL` (`failed`)" in markdown
    assert "rank 2 `FAM_LOW_SEED` (`insufficient_nl_seed_support`)" in markdown
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert [row["top30_expansion_blocker"] for row in summary] == [
        "failed_row",
        "insufficient_nl_seed_support",
    ]


def test_missing_required_queue_columns_fail_clearly(tmp_path: Path) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    queue_tsv.write_text("feature_family_id\nFAM001\n", encoding="utf-8")

    try:
        batch.run_overlay_batch(
            review_queue_tsv=queue_tsv,
            alignment_cells=tmp_path / "alignment_cells.tsv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
        )
    except ValueError as exc:
        assert "missing required columns" in str(exc)
        assert "family_center_mz" in str(exc)
    else:
        raise AssertionError("Expected missing-column failure")


def test_family_failure_is_recorded_and_main_exits_2(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queue_tsv = tmp_path / "queue.tsv"
    alignment_cells = tmp_path / "alignment_cells.tsv"
    output_dir = tmp_path / "out"
    alignment_cells.write_text("feature_family_id\nFAM_FAIL\n", encoding="utf-8")
    _write_queue(queue_tsv, [_queue_row("FAM_FAIL")])

    def fake_load_family_cells(_alignment_cells: Path, _family_id: str) -> list[str]:
        raise FileNotFoundError("missing raw")

    monkeypatch.setattr(batch.overlay_plot, "load_family_cells", fake_load_family_cells)

    code = batch.main(
        [
            "--review-queue-tsv",
            str(queue_tsv),
            "--alignment-cells",
            str(alignment_cells),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--dll-dir",
            str(tmp_path / "dll"),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 2
    summary = _read_tsv(output_dir / "family_ms1_overlay_batch_summary.tsv")
    assert summary[0]["status"] == "failed"
    assert "missing raw" in summary[0]["failure_reason"]


def _queue_row(
    family_id: str,
    *,
    mz: str = "251.165",
    rt_min: str = "1.0",
    rt_max: str = "1.2",
    seed_group_id: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "seed_group_id": seed_group_id,
        "family_center_mz": mz,
        "family_center_rt": "1.1",
        "suggested_rt_min": rt_min,
        "suggested_rt_max": rt_max,
        "suggested_output_prefix": f"{family_id.lower()}_overlay",
        "suggested_overlay_command_args": "--family-id WRONG --mz 999",
    }


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    fields = (
        "feature_family_id",
        "seed_group_id",
        "family_center_mz",
        "backfill_seed_mz",
        "ppm",
        "family_center_rt",
        "suggested_rt_min",
        "suggested_rt_max",
        "suggested_output_prefix",
        "suggested_overlay_command_args",
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _install_fake_cache_renderer(monkeypatch) -> Callable[[], int]:
    render_count = 0

    monkeypatch.setattr(
        batch.overlay_plot,
        "load_family_cells",
        lambda _alignment_cells, family_id: [family_id],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "extract_family_trace_rows",
        lambda **_kwargs: ["trace-row"],
    )
    monkeypatch.setattr(
        batch.overlay_plot,
        "_build_family_ms1_evidence_summary",
        lambda _rows, **_kwargs: {
            "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
            "detected_count": 1,
        },
    )

    def fake_write_summary(path: Path, _rows, **_kwargs) -> None:
        path.write_text("sample_stem\tstatus\nS1\trescued\n", encoding="utf-8")

    def fake_write_trace_data(path: Path, **_kwargs) -> None:
        nonlocal render_count
        render_count += 1
        path.write_text(
            json.dumps(
                {
                    "family_id": "FAM001",
                    "mz": 251.165,
                    "ppm": 10.0,
                    "rt_min": 1.0,
                    "rt_max": 1.2,
                    "provenance": dict(_kwargs["provenance"]),
                    "evidence_summary": {
                        "family_verdict": batch.SUPPORT_FAMILY_VERDICT,
                        "detected_count": 1,
                    },
                    "traces": [],
                },
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(batch.overlay_plot, "_write_summary", fake_write_summary)
    monkeypatch.setattr(batch.overlay_plot, "_write_trace_data", fake_write_trace_data)
    return lambda: render_count


def _write_raw_identity_file(raw_dir: Path, sample_stem: str, content: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{sample_stem}.raw").write_text(content, encoding="utf-8")


def _cell_row(
    family_id: str,
    sample_stem: str,
    status: str,
    area: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "status": status,
        "area": area,
        "apex_rt": "1.1",
        "height": "100",
        "peak_start_rt": "1.0",
        "peak_end_rt": "1.2",
        "region_shadow_verdict": "current_supported",
        "source_candidate_id": "",
    }


def _write_alignment_cells(path: Path, rows: list[dict[str, str]]) -> None:
    fields = (
        "feature_family_id",
        "sample_stem",
        "status",
        "area",
        "apex_rt",
        "height",
        "peak_start_rt",
        "peak_end_rt",
        "region_shadow_verdict",
        "source_candidate_id",
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _batch_row(
    rank: int,
    family_id: str,
    family_verdict: str,
    *,
    status: str = "success",
) -> dict[str, object]:
    return {
        "rank": rank,
        "feature_family_id": family_id,
        "seed_group_id": "",
        "mz": 251.165 + rank,
        "ppm": 10.0,
        "rt_min": 1.0,
        "rt_max": 1.2,
        "family_center_rt": 1.1,
        "output_prefix": f"{family_id.lower()}_overlay",
        "status": status,
        "family_verdict": family_verdict,
        "dda_trigger_limited_ms2_support": True,
        "detected_count": 2,
        "rescued_count": 80,
        "detected_rescued_count": 82,
        "evaluable_trace_count": 82,
        "global_apex_assessable_trace_count": 80,
        "global_apex_assessable_fraction": 0.975,
        "selected_apex_in_trace_window_count": 79,
        "selected_apex_in_trace_window_fraction": 0.963,
        "local_apex_assessable_trace_count": 78,
        "global_apex_interference_count": 1,
        "shape_supported_fraction": 0.8,
        "absolute_own_max_evaluable_trace_count": 82,
        "absolute_own_max_shape_supported_count": 81,
        "absolute_own_max_shape_supported_fraction": 0.988,
        "absolute_trace_apex_assessable_count": 82,
        "absolute_trace_apex_cluster_count": 80,
        "absolute_trace_apex_cluster_fraction": 0.976,
        "absolute_trace_apex_delta_abs_median_min": 0.012,
        "global_apex_interference_fraction": 0.1,
        "local_apex_supported_count": 77,
        "local_apex_supported_fraction": 0.8,
        "png_path": "",
        "pdf_path": "",
        "trace_summary_tsv": "",
        "trace_data_json": "",
        "failure_reason": "render failed" if status == "failed" else "",
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _assert_summary_tsv_contract(path: Path) -> None:
    assert b"\r\n" not in path.read_bytes()
    assert path.read_text(encoding="utf-8").splitlines()[0].split("\t") == list(
        batch._summary_fields()
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
