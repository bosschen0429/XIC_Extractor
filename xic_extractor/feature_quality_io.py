"""Read finalized XIC tables and deliver an explicit feature selection bundle."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_ROW_BINDING_COLUMN,
    validate_matrix_identity_rows,
)
from xic_extractor.alignment.output_rows import escape_excel_formula
from xic_extractor.feature_quality import evaluate_feature_quality
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadata,
    parse_sample_metadata,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_with_header_snapshot,
    write_tsv,
)

FEATURE_QUALITY_SCHEMA = "xic_feature_quality.v1"
QUALITY_COLUMNS = (
    "source_matrix_row_index",
    "peak_hypothesis_id",
    "finite_count",
    "eligible_sample_count",
    "finite_fraction",
    "decision",
    "reason",
)
ANALYSIS_FEATURE_COLUMNS = (
    "analysis_row_index",
    "source_matrix_row_index",
    "peak_hypothesis_id",
    "source_matrix_row_binding_sha256",
)


def export_feature_quality(
    *,
    matrix_path: Path,
    identity_path: Path,
    sample_metadata_path: Path,
    output_dir: Path,
    min_finite_fraction: float,
) -> Path:
    """Export a new bundle; never replace inputs or an existing destination.

    The full tables retain source tokens. Selected rows use a separate mapping
    to their original identity positions, rather than pretending to be a newly
    finalized Alignment Matrix. The manifest is written last on success.
    """
    header, rows, matrix_digest = read_tsv_with_header_snapshot(
        matrix_path,
        required_columns=("Mz", "RT"),
        encoding="utf-8-sig",
    )
    identity_header, identities, identity_digest = read_tsv_with_header_snapshot(
        identity_path,
        encoding="utf-8-sig",
    )
    validate_matrix_identity_rows(
        matrix_header=header,
        matrix_rows=rows,
        identity_columns=identity_header,
        identity_rows=identities,
    )
    _, metadata_rows, metadata_digest = read_tsv_with_header_snapshot(
        sample_metadata_path,
        required_columns=SAMPLE_METADATA_COLUMNS,
        encoding="utf-8-sig",
    )
    metadata = parse_sample_metadata(metadata_rows, source=str(sample_metadata_path))
    # The metadata owner already rejects duplicate and ambiguous aliases.
    aliases: dict[str, SampleMetadata] = {}
    for metadata_row in metadata:
        for alias in {metadata_row.sample_name, metadata_row.raw_stem}:
            column = escape_excel_formula(alias)
            if column in aliases and aliases[column] != metadata_row:
                raise ValueError(f"ambiguous serialized sample identity: {column!r}")
            aliases[column] = metadata_row
    samples = header[2:]
    missing = [column for column in samples if column not in aliases]
    if missing:
        raise ValueError(f"matrix samples missing exact sample metadata: {missing!r}")
    matched = tuple(aliases[column] for column in samples)
    if len({row.sample_name for row in matched}) != len(samples):
        raise ValueError("matrix columns must resolve to distinct sample identities")
    eligible = tuple(
        row.sample_role == "study_sample" and not row.excluded for row in matched
    )
    values = np.empty((len(rows), len(samples)), dtype=float)
    for index, row in enumerate(rows):
        for column_index, column in enumerate(samples):
            token = row[column]
            try:
                values[index, column_index] = float(token) if token.strip() else np.nan
            except ValueError as exc:
                raise ValueError(
                    f"matrix row {index + 1}, sample {column!r}: "
                    f"invalid numeric value {token!r}"
                ) from exc
    quality = evaluate_feature_quality(
        values,
        eligible_samples=eligible,
        min_finite_fraction=min_finite_fraction,
    )
    quality_rows = []
    selected_rows = []
    selected_features = []
    for index, (row, identity, result) in enumerate(
        zip(rows, identities, quality, strict=True), start=1
    ):
        quality_rows.append(
            {
                "source_matrix_row_index": index,
                "peak_hypothesis_id": identity["peak_hypothesis_id"],
                "finite_count": result.finite_count,
                "eligible_sample_count": result.eligible_sample_count,
                "finite_fraction": repr(result.finite_fraction),
                "decision": "keep" if result.keep else "drop",
                "reason": result.reason,
            }
        )
        if result.keep:
            selected_rows.append(row)
            selected_features.append(
                {
                    "analysis_row_index": len(selected_rows),
                    "source_matrix_row_index": index,
                    "peak_hypothesis_id": identity["peak_hypothesis_id"],
                    "source_matrix_row_binding_sha256": identity[
                        MATRIX_ROW_BINDING_COLUMN
                    ],
                }
            )
    sample_rows = [
        {
            "matrix_column": column,
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            **asdict(row),
            "excluded": "TRUE" if row.excluded else "FALSE",
        }
        for column, row in zip(samples, matched, strict=True)
    ]
    # A fresh destination also prevents a stale manifest from certifying a
    # partially overwritten result. Inputs have all been checked before mkdir.
    output_dir.mkdir(parents=True, exist_ok=False)
    tables = (
        ("full_matrix.tsv", rows, header),
        ("full_matrix_identity.tsv", identities, identity_header),
        ("analysis_matrix.tsv", selected_rows, header),
        ("analysis_features.tsv", selected_features, ANALYSIS_FEATURE_COLUMNS),
        ("feature_quality.tsv", quality_rows, QUALITY_COLUMNS),
        (
            "sample_metadata.tsv",
            sample_rows,
            ("matrix_column", *SAMPLE_METADATA_COLUMNS),
        ),
    )
    artifacts = {}
    for filename, table_rows, columns in tables:
        path = output_dir / filename
        write_tsv(path, table_rows, columns)
        artifacts[filename] = {
            "sha256": file_sha256(path),
            "row_count": len(table_rows),
        }
    manifest = {
        "schema_version": FEATURE_QUALITY_SCHEMA,
        "input_stage": "finalized_matrix",
        "output_stage": "feature_selected",
        "feature_count": len(rows),
        "selected_feature_count": len(selected_rows),
        "sample_count": len(samples),
        "sources": {
            "matrix": {"path": str(matrix_path.resolve()), "sha256": matrix_digest},
            "identity": {
                "path": str(identity_path.resolve()),
                "sha256": identity_digest,
            },
            "sample_metadata": {
                "path": str(sample_metadata_path.resolve()),
                "sha256": metadata_digest,
            },
        },
        "rule": {
            "metric": "finite_fraction",
            "min_finite_fraction": min_finite_fraction,
            "eligible_matrix_columns": [
                column for column, use in zip(samples, eligible, strict=True) if use
            ],
            "eligible_sample_role": "study_sample",
            "excluded_samples_eligible": False,
            "zero_is_available": True,
            "counted_detection": "not_evaluated",
        },
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "feature_quality_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path
