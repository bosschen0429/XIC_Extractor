from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.matrix_review_fixtures import current_matrix_review_row
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import read_tsv_with_header, write_tsv


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    samples = ("S1", "S2", "S3", "QC", "Blank", "Excluded")
    values = (("0", "8", ""), ("", "", "3"), ("10", "20", "30"), ("", "", ""))
    matrix, identities = [], []
    for index, study_values in enumerate(values, start=1):
        sample_values = dict(
            zip(samples, (*study_values, "50", "99", "7"), strict=True)
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
    metadata = [
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
        for sample in reversed(samples)
    ]
    paths = (
        tmp_path / "matrix.tsv",
        tmp_path / "review.tsv",
        tmp_path / "samples.tsv",
    )
    write_tsv(paths[0], matrix, ("Mz", "RT", *samples))
    archive = {column: "" for column in MATRIX_REVIEW_COLUMNS}
    archive["feature_id"] = "F000005"
    write_tsv(paths[1], [archive, *reversed(identities)], MATRIX_REVIEW_COLUMNS)
    write_tsv(paths[2], metadata, SAMPLE_METADATA_COLUMNS)
    return paths


def _run(
    paths: tuple[Path, Path, Path], output: Path, threshold: str = "0.6666666666666666"
):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_feature_quality",
            "--matrix",
            str(paths[0]),
            "--review",
            str(paths[1]),
            "--sample-metadata",
            str(paths[2]),
            "--min-finite-fraction",
            threshold,
            "--output-dir",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_selects_original_rows_and_preserves_zero_and_sample_identity(tmp_path):
    paths = _inputs(tmp_path)
    original_bytes = [path.read_bytes() for path in paths]
    output = tmp_path / "quality"
    result = _run(paths, output)
    assert result.returncode == 0, result.stderr
    assert [path.read_bytes() for path in paths] == original_bytes
    source_header, source_rows = read_tsv_with_header(paths[0])
    header, selected = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert header == source_header
    assert selected == [source_rows[0], source_rows[2]]
    assert selected[0]["S1"] == "0"
    assert read_tsv_with_header(output / "full_matrix.tsv") == (
        source_header,
        source_rows,
    )
    assert read_tsv_with_header(output / "full_review.tsv") == read_tsv_with_header(
        paths[1]
    )
    _, quality = read_tsv_with_header(output / "feature_quality.tsv")
    assert [row["decision"] for row in quality] == ["keep", "drop", "keep", "drop"]
    assert [row["finite_count"] for row in quality] == ["2", "1", "3", "0"]
    assert {row["eligible_sample_count"] for row in quality} == {"3"}
    assert [float(row["finite_fraction"]) for row in quality] == [2 / 3, 1 / 3, 1, 0]
    assert [row["reason"] for row in quality] == [
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
    ]
    _, features = read_tsv_with_header(output / "analysis_features.tsv")
    assert [row["source_matrix_row_index"] for row in features] == ["1", "3"]
    assert [row["feature_id"] for row in features] == ["F000001", "F000003"]
    assert [row["analysis_row_index"] for row in features] == ["1", "2"]
    _, samples = read_tsv_with_header(output / "sample_metadata.tsv")
    assert [row["matrix_column"] for row in samples] == list(header[2:])
    assert [row["sample_name"] for row in samples] == [
        f"sample_{name}" for name in header[2:]
    ]
    manifest = json.loads((output / "feature_quality_manifest.json").read_text())
    assert manifest["rule"]["metric"] == "finite_fraction"
    assert manifest["rule"]["eligible_matrix_columns"] == ["S1", "S2", "S3"]
    assert manifest["feature_count"] == 4
    assert manifest["selected_feature_count"] == 2


def test_cli_rejects_stale_identity_without_publishing(tmp_path):
    paths = _inputs(tmp_path)
    paths[0].write_text(paths[0].read_text().replace("\t8\t", "\t9\t"))
    output = tmp_path / "quality"
    result = _run(paths, output)
    assert result.returncode == 2
    assert "binding" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("threshold", ["nan", "-0.1", "1.1"])
def test_cli_rejects_invalid_threshold(tmp_path, threshold):
    paths = _inputs(tmp_path)
    output = tmp_path / "quality"
    result = _run(paths, output, threshold)
    assert result.returncode == 2
    assert "min_finite_fraction" in result.stderr
    assert not output.exists()


def test_cli_rejects_missing_sample_metadata_and_no_eligible_samples(tmp_path):
    paths = _inputs(tmp_path)
    header, metadata = read_tsv_with_header(paths[2])
    write_tsv(paths[2], [row for row in metadata if row["raw_stem"] != "S1"], header)
    output = tmp_path / "quality"
    result = _run(paths, output)
    assert result.returncode == 2
    assert "S1" in result.stderr
    assert not output.exists()
    for row in metadata:
        row["sample_role"] = "unknown"
    write_tsv(paths[2], metadata, header)
    result = _run(paths, output)
    assert result.returncode == 2
    assert "no eligible study samples" in result.stderr
    assert not output.exists()


def _html_quality(paths, output, threshold):
    from tests.test_workbench_analysis import _terminal
    from xic_workbench.jobs import AnalysisJobs

    jobs = AnalysisJobs(output.parent / "config")
    try:
        jobs.start(
            "quality",
            {
                "matrix_path": str(paths[0]),
                "review_path": str(paths[1]),
                "sample_metadata_path": str(paths[2]),
                "output_dir": str(output),
                "min_finite_fraction": threshold,
            },
        )
        return _terminal(jobs)["job"]
    finally:
        jobs.close()


def test_html_exports_the_same_analysis_set_as_cli(tmp_path):
    paths = _inputs(tmp_path)
    originals = [path.read_bytes() for path in paths]
    output = tmp_path / "html_quality"
    job = _html_quality(paths, output, "0.6")
    assert job["status"] == "succeeded", job
    assert [path.read_bytes() for path in paths] == originals
    _, selected = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert [row["Mz"] for row in selected] == ["101.0000", "103.0000"]
    assert selected[0]["S1"] == "0"
    cli_output = tmp_path / "cli_quality"
    result = _run(paths, cli_output, "0.6")
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in output.iterdir()} == {
        p.name: p.read_bytes() for p in cli_output.iterdir()
    }


def test_html_rejects_empty_threshold_and_stale_identity(tmp_path):
    paths = _inputs(tmp_path)
    output = tmp_path / "quality"
    assert _html_quality(paths, output, "")["status"] == "failed"
    assert not output.exists()
    paths[0].write_text(paths[0].read_text().replace("\t8\t", "\t9\t"))
    job = _html_quality(paths, output, "0.6")
    assert job["status"] == "failed"
    assert "binding" in job["error"]
    assert not output.exists()


def test_cli_does_not_overwrite_existing_output(tmp_path):
    paths = _inputs(tmp_path)
    output = tmp_path / "quality"
    output.mkdir()
    marker = output / "analysis_matrix.tsv"
    marker.write_text("existing result")
    result = _run(paths, output)
    assert result.returncode == 2
    assert marker.read_text() == "existing result"
