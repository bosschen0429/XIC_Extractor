from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from xic_extractor.instrument_qc.sequence_manifest import (
    ManifestMatchStatus,
    SequenceManifestRow,
)
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
)
from xic_extractor.tabular_io import (
    format_spreadsheet_value,
    write_delimited_rows,
    write_tsv,
)

MANIFEST_TSV_COLUMNS = [
    "source_doc",
    "source_section",
    "doc_display_name",
    "raw_stem",
    "injection_order",
    "instrument_qc_class",
    "match_status",
    "match_confidence",
    "match_reason",
    "instrument_method",
    "activation_method",
]

INJECTION_ORDER_COLUMNS = ["Sample_Name", "Injection_Order"]
SAMPLE_METADATA_OUTPUT_COLUMNS = SAMPLE_METADATA_COLUMNS


def write_sequence_manifest_tsv(
    path: Path,
    rows: Iterable[SequenceManifestRow],
) -> None:
    """Write the spreadsheet-facing audit, not the machine metadata handoff."""
    row_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        [_manifest_row_to_dict(row) for row in row_list],
        MANIFEST_TSV_COLUMNS,
        formatter=format_spreadsheet_value,
    )


def write_injection_order_csv(
    path: Path,
    rows: Iterable[SequenceManifestRow],
) -> None:
    output_rows = []
    for row in rows:
        if row.match_status != ManifestMatchStatus.MATCHED:
            continue
        if row.injection_order is None:
            continue
        output_rows.append(
            {
                "Sample_Name": row.raw_stem,
                "Injection_Order": row.injection_order,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        path,
        output_rows,
        INJECTION_ORDER_COLUMNS,
        formatter=_format_tabular_value,
    )


def write_sample_metadata_tsv(
    path: Path,
    rows: Iterable[SequenceManifestRow],
) -> None:
    output_rows = []
    for row in rows:
        if row.match_status != ManifestMatchStatus.MATCHED and (
            row.source_section != "raw_dir"
        ):
            continue
        output_rows.append(_sample_metadata_row_to_dict(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        output_rows,
        SAMPLE_METADATA_OUTPUT_COLUMNS,
        formatter=_format_tabular_value,
        lineterminator="\n",
    )


def write_sequence_manifest_json(
    path: Path,
    rows: Iterable[SequenceManifestRow],
) -> None:
    row_list = list(rows)
    payload = {
        "summary": _summary(row_list),
        "source_contract": "method_docs_only",
        "rows": [_manifest_row_to_dict(row) for row in row_list],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_sequence_manifest_markdown(
    path: Path,
    rows: Iterable[SequenceManifestRow],
) -> None:
    row_list = list(rows)
    status_counts = _counts(row.match_status.value for row in row_list)
    lines = [
        "# Instrument QC Sequence Manifest",
        "",
        "- source contract: `method_docs_only`",
        f"- total rows: {len(row_list)}",
        f"- matched rows: {status_counts.get('matched', 0)}",
        f"- unmatched rows: {status_counts.get('unmatched', 0)}",
        f"- ambiguous rows: {status_counts.get('ambiguous', 0)}",
        f"- manual review rows: {status_counts.get('manual_review', 0)}",
        "",
        "## Review Rows",
        "",
    ]
    review_rows = [
        row for row in row_list if row.match_status != ManifestMatchStatus.MATCHED
    ][:20]
    if not review_rows:
        lines.append("No unmatched or ambiguous rows.")
    else:
        lines.append("| display name | raw stem | class | status | reason |")
        lines.append("|---|---|---|---|---|")
        for row in review_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_md(row.doc_display_name),
                        _escape_md(row.raw_stem),
                        row.instrument_qc_class.value,
                        row.match_status.value,
                        _escape_md(row.match_reason),
                    ]
                )
                + " |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest_row_to_dict(row: SequenceManifestRow) -> dict[str, object]:
    return {
        "source_doc": row.source_doc,
        "source_section": row.source_section,
        "doc_display_name": row.doc_display_name,
        "raw_stem": row.raw_stem,
        "injection_order": "" if row.injection_order is None else row.injection_order,
        "instrument_qc_class": row.instrument_qc_class.value,
        "match_status": row.match_status.value,
        "match_confidence": row.match_confidence.value,
        "match_reason": row.match_reason,
        "instrument_method": row.instrument_method,
        "activation_method": row.activation_method,
    }


def _sample_metadata_row_to_dict(row: SequenceManifestRow) -> dict[str, object]:
    return {
        "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
        "sample_name": row.raw_stem,
        "raw_stem": row.raw_stem,
        "injection_order": "" if row.injection_order is None else row.injection_order,
        "sample_role": _sample_role(row),
        "batch_id": "",
        "prep_batch_id": "",
        "matrix_type": "instrument_qc",
        "group": row.instrument_qc_class.value,
        "excluded": "FALSE",
        "exclusion_reason": "",
    }


def _sample_role(row: SequenceManifestRow) -> str:
    if row.instrument_qc_class.value == "SDOLEK":
        return "system_suitability"
    if row.instrument_qc_class.value == "MIX_STDS":
        return "calibrator"
    if row.instrument_qc_class.value == "BLANK":
        return "blank"
    if row.instrument_qc_class.value == "POOLED_QC":
        return "pooled_qc"
    return "unknown"


def _format_tabular_value(value: object) -> str:
    # Injection order and sample metadata must round-trip exact RAW identities.
    if value is None:
        return ""
    return str(value)


def _summary(rows: list[SequenceManifestRow]) -> dict[str, object]:
    return {
        "total_rows": len(rows),
        "match_status_counts": _counts(row.match_status.value for row in rows),
        "instrument_qc_class_counts": _counts(
            row.instrument_qc_class.value for row in rows
        ),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|")
