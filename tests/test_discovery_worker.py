from pathlib import Path
from types import SimpleNamespace

import pytest

from gui.workers import discovery_worker as module
from gui.workers.discovery_worker import DiscoveryRequest, DiscoveryWorker
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs


def test_align_only_summary_uses_existing_batch_index_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "\n".join(
            [
                "sample,high_count,medium_count,low_count,candidate_count",
                "A.raw,1,2,3,6",
                "B.raw,0,1,0,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    preset_path = tmp_path / "preset.toml"
    preset_path.write_text(
        "\n".join(
            [
                'name = "Test"',
                'description = "No alignment publication preset"',
                'combine_mode = "single"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "TEST"',
                "value = 1.0",
                "",
                "[discovery]",
                "",
                "[alignment]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir.mkdir()
    output_dir.mkdir()

    def fake_run_alignment(**_kwargs):
        return AlignmentRunOutputs(matrix_tsv=output_dir / "alignment_matrix.tsv")

    monkeypatch.setattr(module, "run_alignment", fake_run_alignment)
    request = DiscoveryRequest(
        mode="align_only",
        preset=str(preset_path),
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=index_path,
    )
    worker = DiscoveryWorker(request)
    monkeypatch.setattr(
        worker,
        "_build_gallery",
        lambda _request, _outputs, *, raw_dir: {
            "gallery_html": str(output_dir / "gallery.html"),
            "gallery_error": None,
        },
    )

    summary = worker._run_align_only(request)

    assert summary is not None
    assert summary["sample_count"] == 2
    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 3,
        "LOW": 3,
        "total": 7,
    }
    assert summary["discovery_batch_index"] == str(index_path)


def test_single_file_discovery_only_summary_counts_review_priorities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_file = tmp_path / "Sample_A.raw"
    raw_file.write_bytes(b"raw")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    candidates_csv = output_dir / "discovery_candidates.csv"
    review_csv = output_dir / "discovery_review.csv"
    candidates_csv.write_text("candidate_id\nc1\nc2\nc3\nc4\n", encoding="utf-8")
    review_csv.write_text(
        "\n".join(
            [
                "review_priority,candidate_id",
                "HIGH,c1",
                "MEDIUM,c2",
                "LOW,c3",
                "LOW,c4",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run_discovery(*_args, **_kwargs):
        return SimpleNamespace(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
        )

    monkeypatch.setattr(module, "run_discovery", fake_run_discovery)
    request = DiscoveryRequest(
        mode="discovery_only",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=None,
        raw_file=raw_file,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=None,
    )
    worker = DiscoveryWorker(request)

    summary = worker._run_discovery_only(request)

    assert summary is not None
    assert summary["sample_count"] == 1
    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 1,
        "LOW": 2,
        "total": 4,
    }
    assert summary["discovery_candidates_csv"] == str(candidates_csv)
    assert summary["discovery_review_csv"] == str(review_csv)
    assert summary["discovery_batch_index"] is None


@pytest.mark.parametrize("mode", ["align_only", "full"])
def test_gui_alignment_modes_apply_product_ready_preset_alignment_runtime(
    mode: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "\n".join(
            [
                "sample,high_count,medium_count,low_count,candidate_count",
                "A.raw,1,0,0,1",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    raw_dir.mkdir()
    output_dir.mkdir()
    (raw_dir / "A.raw").write_bytes(b"raw")
    captured_alignment: dict[str, object] = {}
    captured_standard_peak: dict[str, object] = {}
    bound_recipes: list[object] = []
    real_bind = module.bind_cid_nl_recipe

    def bind_once(*args, **kwargs):
        recipe = real_bind(*args, **kwargs)
        bound_recipes.append(recipe)
        return recipe

    def fake_run_alignment(**kwargs):
        captured_alignment.update(kwargs)
        return AlignmentRunOutputs(
            review_tsv=output_dir / "alignment_review.tsv",
            matrix_tsv=output_dir / "alignment_matrix.tsv",
            matrix_identity_tsv=output_dir / "alignment_matrix_identity.tsv",
            cells_tsv=output_dir / "alignment_cells.tsv",
            backfill_seed_audit_tsv=(
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        )

    def fake_standard_peak(**kwargs):
        captured_standard_peak.update(kwargs)
        return SimpleNamespace(
            summary_json=output_dir
            / "standard_peak_backfill_preset"
            / "standard_peak_backfill_preset_summary.json",
            published_alignment_manifest_json=(
                output_dir / "standard_peak_publish_manifest.json"
            ),
            gallery_html=None,
        )

    def fake_run_discovery_batch(*_args, **_kwargs):
        return SimpleNamespace(batch_index_csv=index_path)

    monkeypatch.setattr(module, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(module, "run_discovery_batch", fake_run_discovery_batch)
    monkeypatch.setattr(module, "bind_cid_nl_recipe", bind_once)
    monkeypatch.setattr(
        module,
        "run_standard_peak_backfill_preset",
        fake_standard_peak,
    )
    request = DiscoveryRequest(
        mode=mode,
        preset="dna_dr_product_ready",
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=index_path if mode == "align_only" else None,
    )
    worker = DiscoveryWorker(request)
    monkeypatch.setattr(
        worker,
        "_build_gallery",
        lambda _request, _outputs, *, raw_dir: {
            "gallery_html": str(output_dir / "gallery.html"),
            "gallery_error": None,
        },
    )

    if mode == "align_only":
        summary = worker._run_align_only(request)
    else:
        summary = worker._run_full(request)

    assert summary is not None
    assert len(bound_recipes) == 1
    assert captured_alignment["alignment_config"] == AlignmentConfig()
    assert captured_alignment["owner_build_xic_backend"] == "raw_superwindow"
    assert captured_alignment["emit_alignment_cells"] is True
    assert captured_alignment["emit_alignment_backfill_seed_audit"] is True
    assert captured_alignment["audit_evidence_mode"] == "none"
    assert captured_standard_peak["alignment_dir"] == output_dir
    assert captured_standard_peak["raw_dir"] == raw_dir
    assert captured_standard_peak["dll_dir"] == tmp_path / "dll"
    assert captured_standard_peak["chunk_size"] == 240
    assert captured_standard_peak["publication_mode"] == "matrix-only"
    assert captured_standard_peak["write_gallery"] is False
    assert (
        captured_standard_peak["source_run_id"]
        == "alignment-preset:builtin:dna_dr_product_ready:standard-peak-backfill"
    )
    assert (
        summary["alignment_outputs"]["standard_peak_summary_json"]
        == str(
            output_dir
            / "standard_peak_backfill_preset"
            / "standard_peak_backfill_preset_summary.json",
        )
    )
