from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import family_ms1_alignment_experiment_batch as batch


def test_batch_renders_shift_aware_outputs_from_overlay_summary(
    tmp_path: Path,
) -> None:
    trace_json = tmp_path / "fam001_trace_data.json"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": "001_fam001_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "shape_supported_fraction": 0.3,
                },
                "traces": [
                    _trace_json(
                        "detected-a",
                        status="detected",
                        group="detected_seed",
                    ),
                    _trace_json("rescued-a", status="backfilled"),
                ],
            },
        ),
        encoding="utf-8",
    )
    cell_evidence = tmp_path / "cells.tsv"
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
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "\t".join(
            (
                "rank",
                "feature_family_id",
                "seed_group_id",
                "output_prefix",
                "status",
                "trace_data_json",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "1",
                "FAM001",
                "seed::FAM001",
                "001_fam001_retained_backfill_missing_overlay",
                "success",
                str(trace_json),
            ),
        )
        + "\n"
        + "\t".join(
            (
                "2",
                "FAM002",
                "seed::FAM002",
                "002_fam002_retained_backfill_missing_overlay",
                "failure",
                "",
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "shift"

    code = batch.main(
        [
            "--overlay-batch-summary-tsv",
            str(overlay_summary),
            "--cell-evidence-tsv",
            str(cell_evidence),
            "--output-dir",
            str(out_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(out_dir / "family_ms1_alignment_experiment_batch_summary.tsv")
    assert rows[0]["alignment_status"] == "rendered"
    assert rows[0]["output_prefix"] == "001_fam001_shift_aware"
    assert Path(rows[0]["source_best_shift_summary_tsv"]).is_file()
    assert rows[1]["alignment_status"] == "skipped"
    assert rows[1]["failure_reason"] == "overlay_status_not_success:failure"


def test_batch_no_images_writes_best_shift_summary_without_png(
    tmp_path: Path,
) -> None:
    trace_json = tmp_path / "fam001_trace_data.json"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": "001_fam001_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "shape_supported_fraction": 0.3,
                },
                "traces": [
                    _trace_json(
                        "detected-a",
                        status="detected",
                        group="detected_seed",
                    ),
                    _trace_json("rescued-a", status="backfilled"),
                ],
            },
        ),
        encoding="utf-8",
    )
    cell_evidence = tmp_path / "cells.tsv"
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
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "\t".join(
            (
                "rank",
                "feature_family_id",
                "seed_group_id",
                "output_prefix",
                "status",
                "trace_data_json",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "1",
                "FAM001",
                "seed::FAM001",
                "001_fam001_retained_backfill_missing_overlay",
                "success",
                str(trace_json),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "shift"

    code = batch.main(
        [
            "--overlay-batch-summary-tsv",
            str(overlay_summary),
            "--cell-evidence-tsv",
            str(cell_evidence),
            "--output-dir",
            str(out_dir),
            "--no-images",
        ],
    )

    assert code == 0
    rows = _read_tsv(out_dir / "family_ms1_alignment_experiment_batch_summary.tsv")
    assert rows[0]["alignment_status"] == "rendered"
    assert rows[0]["source_best_shift_png"] == ""
    assert Path(rows[0]["source_best_shift_summary_tsv"]).is_file()
    assert (out_dir / "001_fam001_shift_aware_summary.tsv").is_file()
    assert (out_dir / "001_fam001_shift_aware_source_family_summary.tsv").is_file()
    assert (
        out_dir / "001_fam001_shift_aware_source_family_shift_summary.tsv"
    ).is_file()
    assert not list(out_dir.glob("*.png"))


def test_batch_best_shift_only_skips_auxiliary_summaries(
    tmp_path: Path,
) -> None:
    trace_json = _write_trace_json(tmp_path, family_id="FAM001")
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n"
        "FAM001\tFAM001-detected\tsource_family=FAM000001\n"
        "FAM001\tFAM001-rescued\tsource_family=FAM000002\n",
        encoding="utf-8",
    )
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "rank\tfeature_family_id\tseed_group_id\toutput_prefix\tstatus\t"
        "trace_data_json\n"
        f"1\tFAM001\tseed::FAM001\t001_fam001\tsuccess\t{trace_json}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "shift"

    code = batch.main(
        [
            "--overlay-batch-summary-tsv",
            str(overlay_summary),
            "--cell-evidence-tsv",
            str(cell_evidence),
            "--output-dir",
            str(out_dir),
            "--no-images",
            "--best-shift-only",
        ],
    )

    assert code == 0
    rows = _read_tsv(out_dir / "family_ms1_alignment_experiment_batch_summary.tsv")
    assert rows[0]["alignment_status"] == "rendered"
    assert Path(rows[0]["source_best_shift_summary_tsv"]).is_file()
    assert not (out_dir / "001_fam001_shift_aware_summary.tsv").exists()
    assert not (out_dir / "001_fam001_shift_aware_source_family_summary.tsv").exists()
    assert not (
        out_dir / "001_fam001_shift_aware_source_family_shift_summary.tsv"
    ).exists()


def test_batch_preloads_cell_evidence_once_for_selected_families(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_json_1 = _write_trace_json(tmp_path, family_id="FAM001")
    trace_json_2 = _write_trace_json(tmp_path, family_id="FAM002")
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "\t".join(("feature_family_id", "sample_stem", "reason"))
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "FAM001-detected",
                "primary family consolidation; source_family=FAM000001",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "FAM001",
                "FAM001-rescued",
                "primary family consolidation; source_family=FAM000002",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "FAM002",
                "FAM002-detected",
                "primary family consolidation; source_family=FAM000003",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "FAM002",
                "FAM002-rescued",
                "primary family consolidation; source_family=FAM000004",
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "\t".join(
            (
                "rank",
                "feature_family_id",
                "seed_group_id",
                "output_prefix",
                "status",
                "trace_data_json",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "1",
                "FAM001",
                "seed::FAM001",
                "001_fam001_retained_backfill_missing_overlay",
                "success",
                str(trace_json_1),
            ),
        )
        + "\n"
        + "\t".join(
            (
                "2",
                "FAM002",
                "seed::FAM002",
                "002_fam002_retained_backfill_missing_overlay",
                "success",
                str(trace_json_2),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    original_loader = (
        batch.family_ms1_alignment_experiment.load_source_family_by_family_sample
    )
    load_calls: list[tuple[str, ...]] = []

    def tracking_loader(
        path: Path,
        *,
        family_ids=None,
    ) -> dict[str, dict[str, str]]:
        load_calls.append(tuple(family_ids or ()))
        return original_loader(path, family_ids=family_ids)

    def fail_single_family_loader(*_args, **_kwargs) -> dict[str, str]:
        raise AssertionError("batch should not load source families per row")

    monkeypatch.setattr(
        batch.family_ms1_alignment_experiment,
        "load_source_family_by_family_sample",
        tracking_loader,
    )
    monkeypatch.setattr(
        batch.family_ms1_alignment_experiment,
        "load_source_family_by_sample",
        fail_single_family_loader,
    )
    out_dir = tmp_path / "shift"

    rows, summary = batch.run_alignment_experiment_batch(
        overlay_batch_summary_tsv=overlay_summary,
        cell_evidence_tsv=cell_evidence,
        output_dir=out_dir,
        render_images=False,
    )

    assert load_calls == [("FAM001", "FAM002")]
    assert [row["alignment_status"] for row in rows] == ["rendered", "rendered"]
    assert summary["successful_shift_aware_row_count"] == 2
    assert summary["source_family_map_source"] == "cell_evidence_tsv"


def test_batch_uses_preloaded_source_family_map(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_json = _write_trace_json(tmp_path, family_id="FAM001")
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
    )
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "\t".join(
            (
                "rank",
                "feature_family_id",
                "seed_group_id",
                "output_prefix",
                "status",
                "trace_data_json",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "1",
                "FAM001",
                "seed::FAM001",
                "001_fam001_retained_backfill_missing_overlay",
                "success",
                str(trace_json),
            ),
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_loader(*_args, **_kwargs) -> dict[str, dict[str, str]]:
        raise AssertionError("preloaded source-family map should avoid TSV scan")

    monkeypatch.setattr(
        batch.family_ms1_alignment_experiment,
        "load_source_family_by_family_sample",
        fail_loader,
    )
    out_dir = tmp_path / "shift"

    rows, summary = batch.run_alignment_experiment_batch(
        overlay_batch_summary_tsv=overlay_summary,
        cell_evidence_tsv=cell_evidence,
        output_dir=out_dir,
        render_images=False,
        source_family_by_family_sample={
            "FAM001": {
                "FAM001-detected": "FAM000001",
                "FAM001-rescued": "FAM000002",
            },
        },
    )

    assert rows[0]["alignment_status"] == "rendered"
    assert summary["source_family_map_source"] == "preloaded"


def test_batch_reuses_existing_best_shift_summary(tmp_path: Path) -> None:
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    trace_json = tmp_path / "fam001_trace_data.json"
    trace_json.write_text("{}", encoding="utf-8")
    overlay_summary.write_text(
        "\t".join(
            (
                "rank",
                "feature_family_id",
                "seed_group_id",
                "output_prefix",
                "status",
                "trace_data_json",
            ),
        )
        + "\n"
        + "\t".join(
            (
                "1",
                "FAM001",
                "seed::FAM001",
                "001_fam001_retained_backfill_missing_overlay",
                "success",
                str(trace_json),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "shift"
    out_dir.mkdir()
    existing = out_dir / "001_fam001_shift_aware_source_family_best_shift_summary.tsv"
    existing.write_text(
        "feature_family_id\tsource_family\nFAM001\tFAM000001\n",
        encoding="utf-8",
    )
    existing_png = (
        out_dir / "001_fam001_shift_aware_source_family_best_shift_alignment.png"
    )
    existing_png.write_bytes(b"png")

    code = batch.main(
        [
            "--overlay-batch-summary-tsv",
            str(overlay_summary),
            "--cell-evidence-tsv",
            str(cell_evidence),
            "--output-dir",
            str(out_dir),
            "--reuse-existing",
        ],
    )

    assert code == 0
    rows = _read_tsv(out_dir / "family_ms1_alignment_experiment_batch_summary.tsv")
    assert rows[0]["alignment_status"] == "reused"
    assert rows[0]["source_best_shift_summary_tsv"] == str(existing)


def test_render_or_reuse_row_job_runs_in_process(tmp_path: Path) -> None:
    # The process-pool worker must unpack its payload and return a status row.
    trace_json = _write_trace_json(tmp_path, family_id="FAM001")
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
    )
    payload = (
        {
            "rank": "1",
            "feature_family_id": "FAM001",
            "seed_group_id": "seed::FAM001",
            "output_prefix": "001_fam001_retained_backfill_missing_overlay",
            "status": "success",
            "trace_data_json": str(trace_json),
        },
        {
            "cell_evidence_tsv": cell_evidence,
            "output_dir": tmp_path / "shift",
            "reuse_existing": False,
            "render_images": False,
            "write_auxiliary_summaries": True,
            "source_family_by_sample": {
                "FAM001-detected": "FAM000001",
                "FAM001-rescued": "FAM000002",
            },
            "dpi": 140,
        },
    )

    result = batch._render_or_reuse_row_job(payload)

    assert result["feature_family_id"] == "FAM001"
    assert result["alignment_status"] == "rendered"


def test_batch_threads_dpi_to_run_alignment_experiment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> dict[str, Path]:
        captured["dpi"] = kwargs.get("dpi")
        output_dir = kwargs["output_dir"]
        prefix = kwargs["output_prefix"]
        (output_dir / f"{prefix}_source_family_best_shift_summary.tsv").write_text(
            "x\n",
            encoding="utf-8",
        )
        return {}

    monkeypatch.setattr(
        batch.family_ms1_alignment_experiment,
        "run_alignment_experiment",
        fake_run,
    )
    trace_json = _write_trace_json(tmp_path, family_id="FAM001")
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
    )
    overlay_summary = tmp_path / "family_ms1_overlay_batch_summary.tsv"
    overlay_summary.write_text(
        "rank\tfeature_family_id\tseed_group_id\toutput_prefix\tstatus\t"
        "trace_data_json\n"
        f"1\tFAM001\tseed::FAM001\t001_fam001\tsuccess\t{trace_json}\n",
        encoding="utf-8",
    )

    rows, summary = batch.run_alignment_experiment_batch(
        overlay_batch_summary_tsv=overlay_summary,
        cell_evidence_tsv=cell_evidence,
        output_dir=tmp_path / "shift",
        dpi=99,
    )

    assert captured["dpi"] == 99
    assert rows[0]["alignment_status"] == "rendered"
    assert summary["render_dpi"] == 99


def test_batch_workers_must_be_positive(tmp_path: Path) -> None:
    overlay_summary = tmp_path / "overlay.tsv"
    overlay_summary.write_text(
        "rank\tfeature_family_id\toutput_prefix\tstatus\n",
        encoding="utf-8",
    )
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="workers"):
        batch.run_alignment_experiment_batch(
            overlay_batch_summary_tsv=overlay_summary,
            cell_evidence_tsv=cell_evidence,
            output_dir=tmp_path / "out",
            workers=0,
        )


def test_parallel_batch_writes_incremental_summary_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    overlay_summary = tmp_path / "overlay.tsv"
    overlay_summary.write_text(
        "\n".join(
            [
                "rank\tfeature_family_id\toutput_prefix\tstatus",
                "1\tFAM001\t001_fam001\tsuccess",
                "2\tFAM002\t002_fam002\tsuccess",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cell_evidence = tmp_path / "cells.tsv"
    cell_evidence.write_text(
        "feature_family_id\tsample_stem\treason\n",
        encoding="utf-8",
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
        row, _kwargs = payload
        return {
            "rank": row["rank"],
            "feature_family_id": row["feature_family_id"],
            "seed_group_id": "",
            "overlay_status": row["status"],
            "source_best_shift_summary_tsv": "",
            "alignment_status": "rendered",
            "failure_reason": "",
        }

    monkeypatch.setattr(batch, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(batch, "_render_or_reuse_row_job", fake_render_job)
    monkeypatch.setattr(
        batch,
        "write_tsv",
        lambda _path, rows, _columns: write_calls.append(
            [row["feature_family_id"] for row in rows]
        ),
    )

    rows, summary = batch.run_alignment_experiment_batch(
        overlay_batch_summary_tsv=overlay_summary,
        cell_evidence_tsv=cell_evidence,
        output_dir=tmp_path / "out",
        write_incremental=True,
        source_family_by_family_sample={},
        workers=2,
    )

    assert [row["feature_family_id"] for row in rows] == ["FAM001", "FAM002"]
    assert write_calls == [["FAM001"], ["FAM001", "FAM002"]]
    assert summary["successful_shift_aware_row_count"] == 2


def _trace_json(
    sample: str,
    *,
    status: str,
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


def _write_trace_json(tmp_path: Path, *, family_id: str) -> Path:
    path = tmp_path / f"{family_id.lower()}_trace_data.json"
    path.write_text(
        json.dumps(
            {
                "family_id": family_id,
                "mz": 123.45,
                "ppm": 20.0,
                "rt_min": 7.0,
                "rt_max": 9.0,
                "family_center_rt": 8.0,
                "provenance": {"output_prefix": f"{family_id.lower()}_overlay"},
                "evidence_summary": {
                    "absolute_trace_apex_cluster_fraction": 0.75,
                    "absolute_own_max_shape_supported_fraction": 0.8,
                    "shape_supported_fraction": 0.3,
                },
                "traces": [
                    _trace_json(
                        f"{family_id}-detected",
                        status="detected",
                        group="detected_seed",
                    ),
                    _trace_json(f"{family_id}-rescued", status="backfilled"),
                ],
            },
        ),
        encoding="utf-8",
    )
    return path


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"), strict=False)) for line in lines[1:]]
