from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.matrix_review_fixtures import current_matrix_review_row
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import read_tsv_with_header, write_tsv

SAMPLES = ("S1", "S2", "S3", "QC", "Blank", "Excluded")
OUTPUT_FILES = {
    "analysis_matrix.tsv",
    "feature_decisions.tsv",
    "sample_metadata.tsv",
    "matrix_curation_summary.json",
}


def alignment_output(folder: Path, *, raw_dir: Path | None = None) -> Path:
    """A published Alignment folder; S1-S3 are the eligible study samples."""
    folder.mkdir(parents=True)
    values = (("0", "8", ""), ("", "", "3"), ("10", "20", "30"), ("", "", ""))
    matrix, identities = [], []
    for index, study_values in enumerate(values, start=1):
        sample_values = dict(
            zip(SAMPLES, (*study_values, "50", "99", "7"), strict=True)
        )
        matrix.append({"Mz": f"{100 + index}.0000", "RT": "2.5", **sample_values})
        identities.append(
            current_matrix_review_row(
                index=index,
                mz=str(100 + index),
                rt="2.5",
                feature_id=f"F{index:06d}",
                sample_values=sample_values,
            )
        )
    write_tsv(folder / "alignment_matrix.tsv", matrix, ("Mz", "RT", *SAMPLES))
    archive = {column: "" for column in MATRIX_REVIEW_COLUMNS}
    archive["feature_id"] = "F000005"
    write_tsv(
        folder / "alignment_review.tsv",
        [archive, *reversed(identities)],
        MATRIX_REVIEW_COLUMNS,
    )
    if raw_dir is not None:
        (folder / "alignment_run_metadata.json").write_text(
            json.dumps({"raw_dir": str(raw_dir), "dll_dir": str(folder / "dll")}),
            encoding="utf-8",
        )
    return folder


def sample_rows() -> list[dict[str, str]]:
    return [
        {
            "schema_version": "sample_metadata",
            "sample_name": f"sample_{sample}",
            "raw_stem": sample,
            "sample_role": "pooled_qc"
            if sample == "QC"
            else "blank"
            if sample == "Blank"
            else "study_sample",
            "excluded": "TRUE" if sample == "Excluded" else "FALSE",
            "exclusion_reason": "operator exclusion" if sample == "Excluded" else "",
        }
        for sample in reversed(SAMPLES)
    ]


def sample_payload() -> list[dict[str, object]]:
    """The same samples as Workbench rows, as an Untargeted run carries them."""
    return [
        {
            key: value
            for key, value in row.items()
            if key != "schema_version" and value != ""
        }
        | {"excluded": row["excluded"] == "TRUE"}
        for row in sample_rows()
    ]


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    source = alignment_output(tmp_path / "alignment")
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    return source, samples


def _run(
    source: Path,
    samples: Path | None,
    output: Path,
    *extra: str,
    threshold: str | None = "0.6666666666666666",
):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_matrix_curation",
            "--source-dir",
            str(source),
            *(["--sample-metadata", str(samples)] if samples else []),
            *(["--min-finite-fraction", threshold] if threshold is not None else []),
            "--output-dir",
            str(output),
            *extra,
        ],
        text=True,
        encoding="utf-8",
        # Messages are Chinese; without this the child writes the console code page.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        check=False,
    )


def _files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir())


def test_cli_keeps_original_rows_and_records_each_decision(tmp_path):
    source, samples = _inputs(tmp_path)
    inputs = [*_files(source), samples]
    original_bytes = [path.read_bytes() for path in inputs]
    output = tmp_path / "curated"
    result = _run(source, samples, output)
    assert result.returncode == 0, result.stderr
    assert [path.read_bytes() for path in inputs] == original_bytes
    assert {path.name for path in output.iterdir()} == OUTPUT_FILES
    source_header, source_rows = read_tsv_with_header(source / "alignment_matrix.tsv")
    header, selected = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert header == source_header
    assert selected == [source_rows[0], source_rows[2]]
    assert selected[0]["S1"] == "0"
    decision_header, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    assert decision_header == (
        "source_matrix_row_index",
        "feature_id",
        "Mz",
        "RT",
        "istd_label",
        "finite_count",
        "eligible_sample_count",
        "finite_fraction",
        "finite_fraction_decision",
        "finite_fraction_reason",
        "decision",
        "decision_reason",
    )
    assert [row["source_matrix_row_index"] for row in decisions] == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert [row["feature_id"] for row in decisions] == [
        "F000001",
        "F000002",
        "F000003",
        "F000004",
    ]
    assert [row["Mz"] for row in decisions] == [row["Mz"] for row in source_rows]
    assert [row["finite_count"] for row in decisions] == ["2", "1", "3", "0"]
    assert {row["eligible_sample_count"] for row in decisions} == {"3"}
    assert [float(row["finite_fraction"]) for row in decisions] == [
        2 / 3,
        1 / 3,
        1,
        0,
    ]
    assert [row["finite_fraction_reason"] for row in decisions] == [
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
    ]
    assert [row["decision"] for row in decisions] == ["keep", "drop", "keep", "drop"]
    assert [row["decision_reason"] for row in decisions] == [
        "kept_by_all_steps",
        "dropped_by:finite_fraction",
        "kept_by_all_steps",
        "dropped_by:finite_fraction",
    ]
    # The fixture run declared no internal standards.
    assert {row["istd_label"] for row in decisions} == {""}
    _, matched = read_tsv_with_header(output / "sample_metadata.tsv")
    assert [row["matrix_column"] for row in matched] == list(SAMPLES)
    assert [row["sample_name"] for row in matched] == [
        f"sample_{name}" for name in SAMPLES
    ]
    summary = json.loads(
        (output / "matrix_curation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["steps"] == [
        {
            "key": "finite_fraction",
            "parameters": {"min_finite_fraction": 2 / 3},
            "kept_feature_count": 2,
            "eligible_matrix_columns": ["S1", "S2", "S3"],
        }
    ]
    assert summary["istd"] == {
        "source": "",
        "standard_count": 0,
        "protected_feature_count": 0,
    }
    assert summary["inputs"]["sample_metadata"] == str(samples)
    assert (summary["feature_count"], summary["kept_feature_count"]) == (4, 2)
    assert summary["sample_count"] == len(SAMPLES)


def test_cli_rejects_stale_identity_without_publishing(tmp_path):
    source, samples = _inputs(tmp_path)
    matrix = source / "alignment_matrix.tsv"
    matrix.write_text(matrix.read_text().replace("\t8\t", "\t9\t"))
    output = tmp_path / "curated"
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert "binding" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("threshold", ["nan", "-0.1", "1.1", None])
def test_cli_rejects_invalid_or_missing_threshold(tmp_path, threshold):
    source, samples = _inputs(tmp_path)
    output = tmp_path / "curated"
    result = _run(source, samples, output, threshold=threshold)
    assert result.returncode == 2
    assert not output.exists()


def test_cli_rejects_unbound_samples_and_no_eligible_samples(tmp_path):
    source, samples = _inputs(tmp_path)
    header, metadata = read_tsv_with_header(samples)
    write_tsv(samples, [row for row in metadata if row["raw_stem"] != "S1"], header)
    output = tmp_path / "curated"
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert "S1" in result.stderr
    assert not output.exists()
    for row in metadata:
        row["sample_role"] = "unknown"
    write_tsv(samples, metadata, header)
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert "study_sample" in result.stderr
    assert not output.exists()


def test_cli_does_not_overwrite_existing_output(tmp_path):
    source, samples = _inputs(tmp_path)
    output = tmp_path / "curated"
    output.mkdir()
    marker = output / "analysis_matrix.tsv"
    marker.write_text("existing result")
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert marker.read_text() == "existing result"


def test_sample_data_prefers_file_then_run_then_confirmed_then_raw_headers(
    tmp_path, monkeypatch
):
    from xic_workbench.matrix_curation import describe_source
    from xic_workbench.samples import from_payload, save_batch_samples

    config = tmp_path / "config"
    raw_dir = tmp_path / "raw"
    source = alignment_output(tmp_path / "alignment", raw_dir=raw_dir)
    header_reads = []

    def read_analysis_set(root, dll_dir):
        header_reads.append((root, dll_dir))
        return SimpleNamespace(sample_metadata=from_payload(sample_payload()))

    monkeypatch.setattr("xic_extractor.raw_reader.read_analysis_set", read_analysis_set)

    def detect(**values):
        return describe_source(config, {"source_dir": str(source), **values})

    # The run's RAW folder is not on this computer: nothing to bind.
    missing = detect()
    assert missing["sample_source"] is None
    assert "找不到樣本資料" in missing["steps"][0]["unavailable_reason"]
    assert header_reads == []

    raw_dir.mkdir()
    from_headers = detect()
    assert from_headers["sample_source"] == {"kind": "raw", "label": str(raw_dir)}
    assert from_headers["steps"][0]["unavailable_reason"] == ""
    assert header_reads == [(raw_dir, source / "dll")]

    confirmed = save_batch_samples(config, raw_dir, sample_payload())["path"]
    assert detect()["sample_source"] == {"kind": "confirmed", "label": confirmed}
    run = detect(samples=sample_payload())
    assert run["sample_source"]["kind"] == "run"
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    chosen = detect(samples=sample_payload(), sample_metadata_path=str(samples))
    assert chosen["sample_source"] == {"kind": "file", "label": str(samples)}
    assert len(header_reads) == 1
    assert Path(chosen["output_dir"]).parent == source


def test_workbench_job_matches_cli_and_writes_inside_the_source(tmp_path):
    from tests.test_workbench_analysis import _terminal
    from xic_workbench.jobs import AnalysisJobs
    from xic_workbench.samples import save_batch_samples

    config = tmp_path / "config"
    raw_dir = tmp_path / "raw"
    source = alignment_output(tmp_path / "alignment", raw_dir=raw_dir)
    save_batch_samples(config, raw_dir, sample_payload())
    jobs = AnalysisJobs(config)
    try:
        jobs.start(
            "curation",
            {
                "source_dir": str(source),
                "steps": {"finite_fraction": {"min_finite_fraction": 0.6}},
            },
        )
        job = _terminal(jobs)["job"]
    finally:
        jobs.close()
    assert job["status"] == "succeeded", job
    output = Path(job["result"]["output_dir"])
    assert output.parent == source
    assert (job["result"]["feature_count"], job["result"]["kept_feature_count"]) == (
        4,
        2,
    )
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    cli_output = tmp_path / "cli"
    result = _run(source, samples, cli_output, threshold="0.6")
    assert result.returncode == 0, result.stderr
    for name in OUTPUT_FILES - {"matrix_curation_summary.json"}:
        assert (output / name).read_bytes() == (cli_output / name).read_bytes()


@pytest.mark.parametrize("protected", [False, True])
def test_preview_matches_export_without_writing_or_changing_inputs(tmp_path, protected):
    from xic_extractor.matrix_curation.export import (
        export_matrix_curation,
        locate_curation_source,
        prepare_matrix_curation,
        read_sample_metadata_file,
    )
    from xic_extractor.matrix_curation.istd import IstdWindow

    folder, samples = _inputs(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    arguments = dict(
        source=locate_curation_source(folder),
        sample_metadata=read_sample_metadata_file(samples),
        sample_source=str(samples),
        steps={"finite_fraction": {"min_finite_fraction": 2 / 3}},
        istd_windows=(IstdWindow("blank-only", 104, 2, 3),) if protected else (),
    )
    preview = prepare_matrix_curation(**arguments).preview
    assert {
        str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
    assert preview["kept"] == 2 + protected
    assert preview["removed"] == 2 - protected
    step = preview["steps"][0]
    assert (step["minimum_finite_count"], step["eligible_sample_count"]) == (2, 3)
    assert step["protected"] == int(protected)
    assert step["undetermined"] == 0
    output = tmp_path / "export"
    export_matrix_curation(**arguments, output_dir=output)
    _, rows = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert len(rows) == preview["kept"]
    assert [row["Mz"] for row in rows] == (
        ["101.0000", "103.0000", "104.0000"] if protected else ["101.0000", "103.0000"]
    )
