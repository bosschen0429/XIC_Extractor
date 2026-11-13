"""Helpers shared by test_matrix_curation and other test modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.matrix_review_fixtures import current_matrix_review_row
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
)
from xic_extractor.tabular_io import write_tsv

SAMPLES = ("S1", "S2", "S3", "QC", "Blank", "Excluded")


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
            "sample_role": "qc"
            if sample == "QC"
            else "blank"
            if sample == "Blank"
            else "sample",
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


def published_curation(
    folder: Path,
    source_dir: Path,
    decisions: list[dict[str, str]],
    *,
    min_finite_fraction: float | None = None,
) -> Path:
    """A completed curation folder in its published layout, written by hand."""
    folder.mkdir()
    columns = (
        "source_matrix_row_index",
        "feature_id",
        "same_compound_reason",
        "same_compound_representative_feature_id",
        "same_compound_relation",
        "same_compound_via_feature_ids",
        "finite_count",
        "eligible_sample_count",
        "decision",
        "decision_reason",
    )
    write_tsv(
        folder / "feature_decisions.tsv",
        [
            {column: str(index) if column == columns[0] else "" for column in columns}
            | row
            for index, row in enumerate(decisions, start=1)
        ],
        columns,
    )
    kept = sum(row["decision"] == "keep" for row in decisions)
    write_tsv(folder / "analysis_matrix.tsv", [{"Mz": "1"}] * kept, ("Mz",))
    steps = [{"key": "same_compound", "parameters": {}}]
    if min_finite_fraction is not None:
        steps.append(
            {
                "key": "finite_fraction",
                "parameters": {"min_finite_fraction": min_finite_fraction},
            }
        )
    (folder / "matrix_curation_summary.json").write_text(
        json.dumps(
            {
                "steps": steps,
                "inputs": {"source_dir": str(source_dir)},
                "feature_count": len(decisions),
                "kept_feature_count": kept,
            }
        ),
        encoding="utf-8",
    )
    return folder
