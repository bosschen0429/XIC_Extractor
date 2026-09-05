from pathlib import Path

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.test_targeted_real_data_adapter import _settings
from xic_extractor.alignment import AlignmentConfig


def test_pipeline_rejects_stale_output_pair_before_writer_or_raw(
    tmp_path: Path,
    monkeypatch,
) -> None:

    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    review = output_dir / "alignment_review.tsv"
    matrix = output_dir / "alignment_matrix.tsv"
    review.write_text("old review", encoding="utf-8")
    matrix.write_text("old matrix", encoding="utf-8")

    calls = []

    def fail_completion(**kwargs):
        calls.append(kwargs)
        pytest.fail("collision reached retained acquisition")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_completion
    )
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )

    assert review.read_text(encoding="utf-8") == "old review"
    assert matrix.read_text(encoding="utf-8") == "old matrix"
    assert not (output_dir / "alignment_review.tsv.tmp").exists()
    assert not (output_dir / "alignment_matrix.tsv.tmp").exists()
    assert calls == []


def test_pipeline_rejects_stale_output_pair_before_replace_or_raw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    review = output_dir / "alignment_review.tsv"
    matrix = output_dir / "alignment_matrix.tsv"
    review.write_text("old review", encoding="utf-8")
    matrix.write_text("old matrix", encoding="utf-8")

    calls = []

    def fail_completion(**kwargs):
        calls.append(kwargs)
        pytest.fail("collision reached retained acquisition")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_completion
    )
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )

    assert review.read_text(encoding="utf-8") == "old review"
    assert matrix.read_text(encoding="utf-8") == "old matrix"
    assert calls == []


def test_run_alignment_rejects_stale_owner_edge_outputs_before_raw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    workbook = output_dir / "alignment_results.xlsx"
    edge_evidence = output_dir / "owner_edge_evidence.tsv"
    workbook.write_text("old workbook", encoding="utf-8")
    edge_evidence.write_text("old edge evidence", encoding="utf-8")

    calls = []

    def fail_completion(**kwargs):
        calls.append(kwargs)
        pytest.fail("collision reached retained acquisition")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_completion
    )
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            output_level="validation",
            discovery_settings=_settings(),
        )

    assert workbook.read_text(encoding="utf-8") == "old workbook"
    assert edge_evidence.read_text(encoding="utf-8") == "old edge evidence"
    assert not (output_dir / "owner_edge_evidence.tsv.tmp").exists()
    assert not (output_dir / "owner_edge_evidence.tsv.bak").exists()
    assert calls == []


def test_run_alignment_rejects_stale_product_artifacts_before_raw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    workbook = output_dir / "alignment_results.xlsx"
    html = output_dir / "review_report.html"
    workbook.write_text("old workbook", encoding="utf-8")
    html.write_text("old html", encoding="utf-8")

    calls = []

    def fail_completion(**kwargs):
        calls.append(kwargs)
        pytest.fail("collision reached retained acquisition")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_completion
    )
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            output_level="production",
            discovery_settings=_settings(),
        )

    assert workbook.read_text(encoding="utf-8") == "old workbook"
    assert html.read_text(encoding="utf-8") == "old html"
    assert calls == []
