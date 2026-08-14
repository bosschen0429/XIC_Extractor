from pathlib import Path

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import owner_edge_evidence as _edge_evidence
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from xic_extractor.alignment import AlignmentConfig


def test_pipeline_rejects_stale_output_pair_before_writer_or_raw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from xic_extractor.alignment import pipeline_outputs

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

    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fail_matrix_writer(
        path,
        matrix,
        *,
        alignment_config=None,
        production_decisions=None,
    ):
        Path(path).write_text("partial matrix", encoding="utf-8")
        raise RuntimeError("matrix failed")

    monkeypatch.setattr(
        pipeline_outputs,
        "write_alignment_matrix_tsv",
        fail_matrix_writer,
    )

    opener = FakeRawOpener()
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=opener,
        )

    assert review.read_text(encoding="utf-8") == "old review"
    assert matrix.read_text(encoding="utf-8") == "old matrix"
    assert not (output_dir / "alignment_review.tsv.tmp").exists()
    assert not (output_dir / "alignment_matrix.tsv.tmp").exists()
    assert opener.calls == []


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

    _patch_owner_pipeline_to_matrix(monkeypatch)
    original_replace = Path.replace

    def fail_second_replace(self: Path, target: Path):
        if self.name == "alignment_matrix.tsv.tmp":
            raise PermissionError("locked matrix")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)

    opener = FakeRawOpener()
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=opener,
        )

    assert review.read_text(encoding="utf-8") == "old review"
    assert matrix.read_text(encoding="utf-8") == "old matrix"
    assert opener.calls == []


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
    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_cluster_owners(
        owners,
        *,
        config,
        drift_lookup=None,
        edge_evidence_sink=None,
    ):
        edge_evidence_sink.append(_edge_evidence())
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fake_cluster_owners,
    )
    original_replace = Path.replace

    def fail_edge_evidence_replace(self: Path, target: Path):
        if self.name == "owner_edge_evidence.tsv.tmp":
            raise PermissionError("locked owner edge evidence")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_edge_evidence_replace)

    opener = FakeRawOpener()
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            output_level="validation",
            raw_opener=opener,
        )

    assert workbook.read_text(encoding="utf-8") == "old workbook"
    assert edge_evidence.read_text(encoding="utf-8") == "old edge evidence"
    assert not (output_dir / "owner_edge_evidence.tsv.tmp").exists()
    assert not (output_dir / "owner_edge_evidence.tsv.bak").exists()
    assert opener.calls == []


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
    _patch_owner_pipeline_to_matrix(monkeypatch)
    original_replace = Path.replace

    def fail_html_replace(self: Path, target: Path):
        if self.name == "review_report.html.tmp":
            raise PermissionError("locked by browser")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_html_replace)

    opener = FakeRawOpener()
    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            output_level="production",
            raw_opener=opener,
        )

    assert workbook.read_text(encoding="utf-8") == "old workbook"
    assert html.read_text(encoding="utf-8") == "old html"
    assert opener.calls == []


def _empty_ownership():
    from xic_extractor.alignment.ownership import OwnershipBuildResult

    return OwnershipBuildResult(assignments=(), ambiguous_records=(), owners=())


def test_pipeline_passes_alignment_config_to_production_writers(monkeypatch, tmp_path):
    from xic_extractor.alignment import pipeline as alignment_pipeline
    from xic_extractor.alignment import pipeline_outputs
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.matrix import AlignmentMatrix

    seen = {"xlsx": None, "matrix_tsv": None, "review_tsv": None}
    matrix = AlignmentMatrix(clusters=(), cells=(), sample_order=())
    config = AlignmentConfig(max_rt_sec=77.0)
    outputs = alignment_pipeline.AlignmentRunOutputs(
        workbook=tmp_path / "alignment_results.xlsx",
        matrix_tsv=tmp_path / "alignment_matrix.tsv",
        review_tsv=tmp_path / "alignment_review.tsv",
        owner_backfill_peak_fact_provenance_tsv=(
            tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
    )

    def fake_xlsx(path, matrix_arg, *, metadata, alignment_config=None):
        seen["xlsx"] = alignment_config
        path.write_text("xlsx", encoding="utf-8")
        return path

    def fake_matrix_tsv(
        path,
        matrix_arg,
        *,
        alignment_config=None,
        production_decisions=None,
    ):
        seen["matrix_tsv"] = alignment_config
        path.write_text("matrix", encoding="utf-8")
        return path

    def fake_review_tsv(
        path,
        matrix_arg,
        *,
        alignment_config=None,
        production_decisions=None,
    ):
        seen["review_tsv"] = alignment_config
        path.write_text("review", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline_outputs, "write_alignment_results_xlsx", fake_xlsx)
    monkeypatch.setattr(
        pipeline_outputs,
        "write_alignment_matrix_tsv",
        fake_matrix_tsv,
    )
    monkeypatch.setattr(
        pipeline_outputs,
        "write_alignment_review_tsv",
        fake_review_tsv,
    )

    pipeline_outputs.write_outputs_atomic(
        outputs,
        matrix,
        metadata={"schema_version": "alignment-results-v3"},
        ownership=_empty_ownership(),
        alignment_config=config,
    )

    assert seen == {"xlsx": config, "matrix_tsv": config, "review_tsv": config}
