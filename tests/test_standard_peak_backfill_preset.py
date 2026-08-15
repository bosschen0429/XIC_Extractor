from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tools.diagnostics import standard_peak_backfill_preset
from xic_extractor.diagnostics.retained_backfill_evidence_gate import (
    RetainedBackfillGateOutputs,
)
from xic_extractor.diagnostics.standard_peak_backfill_chunk_consolidation import (
    StandardPeakChunkConsolidationOutputs,
)
from xic_extractor.diagnostics.timing import TimingRecorder


def test_standard_peak_backfill_preset_skips_machine_pipeline_without_queue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    output_dir = tmp_path / "preset"

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text("feature_family_id\n", encoding="utf-8")
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fail_machine(**_kwargs):
        raise AssertionError("machine pipeline should not run without queue rows")

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    outputs = standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=tmp_path / "raws",
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        machine_pipeline_runner=fail_machine,
    )

    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["status"] == "pass"
    assert summary["review_queue_row_count"] == "0"
    assert summary["chunk_count"] == "0"
    assert summary["render_workers"] == "1"
    assert summary["chunk_workers"] == "1"
    assert summary["render_dpi"] == "140"
    assert summary["status_reasons"] == "no_standard_peak_backfill_review_rows"


def test_standard_peak_backfill_preset_chunks_and_publishes_alignment_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    machine_calls: list[dict[str, object]] = []
    consolidation_calls: list[dict[str, object]] = []
    reconciliation_group_calls: list[dict[str, object]] = []
    global_overlay_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text(
            "feature_family_id\nFAM1\nFAM2\nFAM3\n",
            encoding="utf-8",
        )
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fake_machine(**kwargs):
        machine_calls.append(dict(kwargs))
        summary_path = Path(kwargs["output_dir"]) / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text('{"status": "pass"}', encoding="utf-8")
        return summary_path

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text(
            '{"status": "pass", "matrix_cells_written": 3}',
            encoding="utf-8",
        )
        summary_tsv = output / "summary.tsv"
        summary_tsv.write_text("status\npass\n", encoding="utf-8")
        shadow_tsv = output / "consolidated_shadow_projection_cells.tsv"
        shadow_tsv.write_text("x\n", encoding="utf-8")
        gallery = output / "gallery.html"
        gallery.write_text("<html></html>", encoding="utf-8")
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=summary_tsv,
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=shadow_tsv,
            productization=SimpleNamespace(reconciliation_gallery_html=gallery),
            formal_product_output_dir=output / "formal",
            formal_product_manifest_json=output / "formal_manifest.json",
            published_alignment_output_dir=None,
            published_alignment_manifest_json=None,
        )

    fake_reconciliation_groups = _fake_reconciliation_groups_runner(
        output_dir,
        reconciliation_group_calls,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "render_overlay_batch_summary_from_review_queue",
        _fake_global_overlay_renderer(global_overlay_calls),
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    outputs = standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        source_run_id="preset:test",
        chunk_size=2,
        write_gallery=True,
        render_workers=5,
        render_dpi=123,
        machine_pipeline_runner=fake_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=fake_reconciliation_groups,
    )

    assert [call["start_rank"] for call in machine_calls] == [1, 3]
    assert [call["limit"] for call in machine_calls] == [2, 1]
    assert all(call["render_workers"] == 5 for call in machine_calls)
    assert all(call["render_dpi"] == 123 for call in machine_calls)
    assert all("raw_dir" not in call for call in machine_calls)
    assert all("dll_dir" not in call for call in machine_calls)
    assert all("review_queue_tsv" not in call for call in machine_calls)
    assert all("evidence_only" not in call for call in machine_calls)
    assert all("write_overlay_pdf" not in call for call in machine_calls)
    assert all(
        Path(call["overlay_batch_summary_tsv"]).parts[-2:]
        == ("family_ms1_overlay_batch", "family_ms1_overlay_batch_summary.tsv")
        for call in machine_calls
    )
    assert len(reconciliation_group_calls) == 1
    assert len(global_overlay_calls) == 1
    assert global_overlay_calls[0]["raw_dir"] == raw_dir
    assert global_overlay_calls[0]["dll_dir"] == dll_dir
    assert global_overlay_calls[0]["limit"] == 3
    assert global_overlay_calls[0]["workers"] == 5
    assert global_overlay_calls[0]["dpi"] == 123
    assert all(
        call["reconciliation_groups_tsv"]
        == output_dir
        / "global_reconciliation_group_index"
        / "backfill_evidence_reconciliation_groups.tsv"
        for call in machine_calls
    )
    assert all(
        call["source_family_by_family_sample"] == {}
        for call in machine_calls
    )
    assert consolidation_calls[0]["publish_to_source_alignment_output"] is False
    assert consolidation_calls[0]["emit_formal_product_output"] is False
    assert "publish_alignment_matrix_tsv" not in consolidation_calls[0]
    assert "publish_alignment_matrix_identity_tsv" not in consolidation_calls[0]
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["status"] == "pass"
    assert summary["review_queue_row_count"] == "3"
    assert summary["chunk_count"] == "2"
    assert summary["render_workers"] == "5"
    assert summary["chunk_workers"] == "1"
    assert summary["render_dpi"] == "123"
    assert summary["matrix_cells_written"] == "3"
    assert outputs.published_alignment_manifest_json is None
    assert outputs.gallery_html == output_dir / "consolidated/gallery.html"


def test_standard_peak_backfill_preset_matrix_only_uses_evidence_only_chunks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    machine_calls: list[dict[str, object]] = []
    consolidation_calls: list[dict[str, object]] = []
    reconciliation_group_calls: list[dict[str, object]] = []
    global_overlay_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text("feature_family_id\nFAM1\nFAM2\n", encoding="utf-8")
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fake_machine(**kwargs):
        machine_calls.append(dict(kwargs))
        summary_path = Path(kwargs["output_dir"]) / (
            "standard_peak_backfill_machine_pipeline_summary.json"
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text('{"status": "pass"}', encoding="utf-8")
        return summary_path

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text(
            '{"status": "pass", "matrix_cells_written": 2}',
            encoding="utf-8",
        )
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=output / "summary.tsv",
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=output / "shadow.tsv",
            productization=SimpleNamespace(reconciliation_gallery_html=None),
            formal_product_output_dir=output / "formal",
            formal_product_manifest_json=output / "formal_manifest.json",
            published_alignment_output_dir=alignment_dir,
            published_alignment_manifest_json=output / "publish_manifest.json",
        )

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    fake_reconciliation_groups = _fake_reconciliation_groups_runner(
        output_dir,
        reconciliation_group_calls,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "render_overlay_batch_summary_from_review_queue",
        _fake_global_overlay_renderer(global_overlay_calls),
    )
    recorder = TimingRecorder("alignment", run_id="test-standard-peak")

    outputs = standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        chunk_size=2,
        publication_mode="matrix-only",
        write_gallery=True,
        machine_pipeline_runner=fake_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=fake_reconciliation_groups,
        timing_recorder=recorder,
    )

    assert machine_calls[0]["publication_mode"] == "matrix-only"
    assert "evidence_only" not in machine_calls[0]
    assert "review_queue_tsv" not in machine_calls[0]
    assert global_overlay_calls[0]["evidence_only"] is True
    assert machine_calls[0]["defer_projection"] is True
    assert machine_calls[0]["timing_recorder"] is recorder
    assert len(reconciliation_group_calls) == 1
    assert consolidation_calls[0]["write_gallery"] is False
    assert outputs.gallery_html is None
    stages = [record.stage for record in recorder.records]
    assert "standard_peak.retained_gate" in stages
    assert "standard_peak.chunk" in stages
    assert "standard_peak.consolidation" in stages
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["publication_mode"] == "matrix-only"
    assert summary["matrix_cells_written"] == "2"


def test_standard_peak_backfill_preset_parallel_chunks_preserve_order_and_timing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    machine_calls: list[dict[str, object]] = []
    consolidation_calls: list[dict[str, object]] = []
    reconciliation_group_calls: list[dict[str, object]] = []
    global_overlay_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text(
            "feature_family_id\nFAM1\nFAM2\nFAM3\nFAM4\n",
            encoding="utf-8",
        )
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fake_machine(**kwargs):
        machine_calls.append(dict(kwargs))
        kwargs["timing_recorder"].record(
            "standard_peak.fake_inner",
            elapsed_sec=float(kwargs["start_rank"]),
            metrics={"start_rank": kwargs["start_rank"]},
        )
        summary_path = Path(kwargs["output_dir"]) / (
            "standard_peak_backfill_machine_pipeline_summary.json"
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text('{"status": "pass"}', encoding="utf-8")
        return summary_path

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text(
            '{"status": "pass", "matrix_cells_written": 4}',
            encoding="utf-8",
        )
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=output / "summary.tsv",
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=output / "shadow.tsv",
            productization=SimpleNamespace(reconciliation_gallery_html=None),
        )

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "render_overlay_batch_summary_from_review_queue",
        _fake_global_overlay_renderer(global_overlay_calls),
    )
    recorder = TimingRecorder("alignment", run_id="test-standard-peak-parallel")

    standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        source_run_id="preset:test",
        chunk_size=2,
        chunk_workers=2,
        machine_pipeline_runner=fake_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=_fake_reconciliation_groups_runner(
            output_dir,
            reconciliation_group_calls,
        ),
        timing_recorder=recorder,
    )

    assert sorted(call["start_rank"] for call in machine_calls) == [1, 3]
    assert consolidation_calls[0]["machine_pipeline_summary_jsons"] == (
        output_dir
        / "chunks"
        / "r1_2"
        / "standard_peak_backfill_machine_pipeline_summary.json",
        output_dir
        / "chunks"
        / "r3_4"
        / "standard_peak_backfill_machine_pipeline_summary.json",
    )
    dispatch = next(
        record
        for record in recorder.records
        if record.stage == "standard_peak.chunk_dispatch"
    )
    assert dispatch.metrics["chunk_workers"] == 2
    inner_records = [
        record
        for record in recorder.records
        if record.stage == "standard_peak.fake_inner"
    ]
    assert [record.metrics["start_rank"] for record in inner_records] == [1, 3]


def test_standard_peak_backfill_preset_reuses_completed_chunk_summaries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    completed_summary = (
        output_dir
        / "chunks"
        / "r1_2"
        / "standard_peak_backfill_machine_pipeline_summary.json"
    )
    completed_summary.parent.mkdir(parents=True)
    completed_summary.write_text(
        json.dumps(
            {
                "status": "pass",
                "publication_mode": "deep-audit",
                "start_rank": 1,
                "effective_overlay_limit": 2,
                "min_shape_r": 0.95,
                "render_dpi": 140,
                "source_run_id": "standard-peak-backfill-r1-2",
            },
        ),
        encoding="utf-8",
    )
    machine_calls: list[dict[str, object]] = []
    consolidation_calls: list[dict[str, object]] = []
    reconciliation_group_calls: list[dict[str, object]] = []
    global_overlay_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text("feature_family_id\nFAM1\nFAM2\nFAM3\n", encoding="utf-8")
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fake_machine(**kwargs):
        machine_calls.append(dict(kwargs))
        summary_path = Path(kwargs["output_dir"]) / (
            "standard_peak_backfill_machine_pipeline_summary.json"
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text('{"status": "pass"}', encoding="utf-8")
        return summary_path

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text('{"status": "pass"}', encoding="utf-8")
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=output / "summary.tsv",
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=output / "shadow.tsv",
            productization=SimpleNamespace(reconciliation_gallery_html=None),
        )

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    fake_reconciliation_groups = _fake_reconciliation_groups_runner(
        output_dir,
        reconciliation_group_calls,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "render_overlay_batch_summary_from_review_queue",
        _fake_global_overlay_renderer(global_overlay_calls),
    )

    standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        chunk_size=2,
        reuse_existing=True,
        machine_pipeline_runner=fake_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=fake_reconciliation_groups,
    )

    assert [call["start_rank"] for call in machine_calls] == [3]
    assert len(reconciliation_group_calls) == 1
    assert len(global_overlay_calls) == 1
    assert consolidation_calls[0]["machine_pipeline_summary_jsons"] == (
        completed_summary,
        output_dir
        / "chunks"
        / "r3_3"
        / "standard_peak_backfill_machine_pipeline_summary.json",
    )


def test_standard_peak_backfill_preset_skips_global_inputs_when_all_chunks_reused(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    for chunk_name, start_rank, limit in (
        ("r1_2", 1, 2),
        ("r3_3", 3, 1),
    ):
        summary = (
            output_dir
            / "chunks"
            / chunk_name
            / "standard_peak_backfill_machine_pipeline_summary.json"
        )
        summary.parent.mkdir(parents=True)
        end_rank = start_rank + limit - 1
        summary.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "publication_mode": "deep-audit",
                    "start_rank": start_rank,
                    "effective_overlay_limit": limit,
                    "min_shape_r": 0.95,
                    "render_dpi": 140,
                    "source_run_id": (
                        f"standard-peak-backfill-r{start_rank}-{end_rank}"
                    ),
                },
            ),
            encoding="utf-8",
        )
    consolidation_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text("feature_family_id\nFAM1\nFAM2\nFAM3\n", encoding="utf-8")
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fail_machine(**_kwargs):
        raise AssertionError("all chunks should be reused")

    def fail_reconciliation_groups(**_kwargs):
        raise AssertionError("global reconciliation groups should not be built")

    def fail_source_family_loader(*_args, **_kwargs):
        raise AssertionError("source-family map should not be read")

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text('{"status": "pass"}', encoding="utf-8")
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=output / "summary.tsv",
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=output / "shadow.tsv",
            productization=SimpleNamespace(reconciliation_gallery_html=None),
        )

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset.family_ms1_alignment_experiment,
        "load_source_family_by_family_sample",
        fail_source_family_loader,
    )

    standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        chunk_size=2,
        reuse_existing=True,
        machine_pipeline_runner=fail_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=fail_reconciliation_groups,
    )

    assert consolidation_calls[0]["machine_pipeline_summary_jsons"] == (
        output_dir
        / "chunks"
        / "r1_2"
        / "standard_peak_backfill_machine_pipeline_summary.json",
        output_dir
        / "chunks"
        / "r3_3"
        / "standard_peak_backfill_machine_pipeline_summary.json",
    )


def test_standard_peak_backfill_preset_reruns_failed_reuse_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_artifacts(tmp_path)
    raw_dir = tmp_path / "raws"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "preset"
    raw_dir.mkdir()
    dll_dir.mkdir()
    failed_summary = (
        output_dir
        / "chunks"
        / "r1_2"
        / "standard_peak_backfill_machine_pipeline_summary.json"
    )
    failed_summary.parent.mkdir(parents=True)
    failed_summary.write_text(
        json.dumps(
            {
                "status": "fail",
                "publication_mode": "deep-audit",
                "start_rank": 1,
                "effective_overlay_limit": 2,
                "min_shape_r": 0.95,
                "source_run_id": "preset:test-r1-2",
            },
        ),
        encoding="utf-8",
    )
    machine_calls: list[dict[str, object]] = []
    consolidation_calls: list[dict[str, object]] = []
    reconciliation_group_calls: list[dict[str, object]] = []
    global_overlay_calls: list[dict[str, object]] = []

    def fake_gate(**_kwargs):
        gate_dir = output_dir / "retained_backfill_evidence_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        gate_tsv = gate_dir / "retained_backfill_evidence_gate.tsv"
        queue = gate_dir / "review_overlay_queue.tsv"
        gate_tsv.write_text("feature_family_id\n", encoding="utf-8")
        queue.write_text("feature_family_id\nFAM1\nFAM2\nFAM3\n", encoding="utf-8")
        missing = gate_dir / "missing_overlay_queue.tsv"
        missing.write_text("feature_family_id\n", encoding="utf-8")
        return RetainedBackfillGateOutputs(
            tsv=gate_tsv,
            json=gate_dir / "retained_backfill_evidence_gate.json",
            missing_overlay_queue_tsv=missing,
            review_overlay_queue_tsv=queue,
        )

    def fake_machine(**kwargs):
        machine_calls.append(dict(kwargs))
        summary_path = Path(kwargs["output_dir"]) / (
            "standard_peak_backfill_machine_pipeline_summary.json"
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "publication_mode": kwargs["publication_mode"],
                    "start_rank": kwargs["start_rank"],
                    "effective_overlay_limit": kwargs["limit"],
                    "min_shape_r": kwargs["min_shape_r"],
                    "source_run_id": kwargs["source_run_id"],
                },
            ),
            encoding="utf-8",
        )
        return summary_path

    def fake_consolidation(**kwargs):
        consolidation_calls.append(dict(kwargs))
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        summary_json = output / "summary.json"
        summary_json.write_text('{"status": "pass"}', encoding="utf-8")
        return StandardPeakChunkConsolidationOutputs(
            summary_tsv=output / "summary.tsv",
            summary_json=summary_json,
            status="pass",
            merged_shadow_projection_cells_tsv=output / "shadow.tsv",
            productization=SimpleNamespace(reconciliation_gallery_html=None),
        )

    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "run_retained_backfill_evidence_gate",
        fake_gate,
    )
    fake_reconciliation_groups = _fake_reconciliation_groups_runner(
        output_dir,
        reconciliation_group_calls,
    )
    monkeypatch.setattr(
        standard_peak_backfill_preset,
        "render_overlay_batch_summary_from_review_queue",
        _fake_global_overlay_renderer(global_overlay_calls),
    )

    standard_peak_backfill_preset.run_standard_peak_backfill_preset(
        alignment_dir=alignment_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        source_run_id="preset:test",
        chunk_size=2,
        reuse_existing=True,
        machine_pipeline_runner=fake_machine,
        consolidation_runner=fake_consolidation,
        reconciliation_groups_runner=fake_reconciliation_groups,
    )

    assert [call["start_rank"] for call in machine_calls] == [1, 3]
    assert len(reconciliation_group_calls) == 1
    assert len(global_overlay_calls) == 1
    assert consolidation_calls[0]["machine_pipeline_summary_jsons"] == (
        output_dir
        / "chunks"
        / "r1_2"
        / "standard_peak_backfill_machine_pipeline_summary.json",
        output_dir
        / "chunks"
        / "r3_3"
        / "standard_peak_backfill_machine_pipeline_summary.json",
    )


def test_standard_peak_backfill_preset_reuse_summary_requires_matching_provenance(
    tmp_path: Path,
) -> None:
    summary_json = tmp_path / "summary.json"
    reusable_summary = {
        "status": "pass",
        "publication_mode": "deep-audit",
        "start_rank": 1,
        "effective_overlay_limit": 2,
        "min_shape_r": 0.95,
        "render_dpi": 140,
        "source_run_id": "preset:test-r1-2",
    }
    summary_json.write_text(json.dumps(reusable_summary), encoding="utf-8")
    assert standard_peak_backfill_preset._can_reuse_chunk_summary(
        summary_json,
        publication_mode="deep-audit",
        start_rank=1,
        limit=2,
        source_run_id="preset:test-r1-2",
        min_shape_r=0.95,
        render_dpi=140,
    )

    for field, value in (
        ("status", "fail"),
        ("publication_mode", "matrix-only"),
        ("start_rank", 2),
        ("effective_overlay_limit", 1),
        ("source_run_id", "other-run-r1-2"),
        ("min_shape_r", 0.9),
        ("render_dpi", 99),
    ):
        stale_summary = dict(reusable_summary)
        stale_summary[field] = value
        summary_json.write_text(json.dumps(stale_summary), encoding="utf-8")
        assert not standard_peak_backfill_preset._can_reuse_chunk_summary(
            summary_json,
            publication_mode="deep-audit",
            start_rank=1,
            limit=2,
            source_run_id="preset:test-r1-2",
            min_shape_r=0.95,
            render_dpi=140,
        )


def _write_alignment_artifacts(tmp_path: Path) -> Path:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    for name in (
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_owner_backfill_seed_audit.tsv",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
    ):
        (alignment_dir / name).write_text("x\n", encoding="utf-8")
    return alignment_dir


def _fake_reconciliation_groups_runner(
    output_dir: Path,
    calls: list[dict[str, object]],
):
    def fake_reconciliation_groups(**kwargs):
        calls.append(dict(kwargs))
        groups = (
            output_dir
            / "global_reconciliation_group_index"
            / "backfill_evidence_reconciliation_groups.tsv"
        )
        groups.parent.mkdir(parents=True, exist_ok=True)
        groups.write_text(
            "feature_family_id\tproduct_behavior_state\n",
            encoding="utf-8",
        )
        return groups

    return fake_reconciliation_groups


def _fake_global_overlay_renderer(calls: list[dict[str, object]]):
    def fake_global_overlay(**kwargs):
        calls.append(dict(kwargs))
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        queue_lines = Path(kwargs["review_queue_tsv"]).read_text(
            encoding="utf-8",
        ).splitlines()
        rows = max(0, len(queue_lines) - 1)
        summary_tsv = output_dir / "family_ms1_overlay_batch_summary.tsv"
        lines = ["rank\tfeature_family_id\tstatus\n"]
        for rank in range(1, rows + 1):
            lines.append(f"{rank}\tFAM{rank}\tsuccess\n")
        summary_tsv.write_text("".join(lines), encoding="utf-8")
        return SimpleNamespace(
            summary_tsv=summary_tsv,
            metrics={"raw_open_count": 1},
        )

    return fake_global_overlay
