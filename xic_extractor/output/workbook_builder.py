from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.detection import MissingTargetedProductProjectionError
from xic_extractor.output.review_queue_model import _review_queue_rows
from xic_extractor.output.sheet_diagnostics import _build_diagnostics_sheet
from xic_extractor.output.sheet_metadata import _build_metadata_sheet
from xic_extractor.output.sheet_overview import _build_overview_sheet
from xic_extractor.output.sheet_results import _build_data_sheet
from xic_extractor.output.sheet_review_queue import _build_review_queue_sheet
from xic_extractor.output.sheet_summary import (
    _build_summary_sheet,
    _is_long_detected,
    _long_nl_counts,
    _target_summaries,
)
from xic_extractor.output.sheet_targets import _build_targets_sheet
from xic_extractor.output.workbook_inputs import (
    _read_diagnostics,
    _read_long_results,
)
from xic_extractor.output.workbook_styles import _apply_sheet_role_styles, _excel_text

_COUNTED_PRODUCT_STATES = {"detected_clean", "detected_flagged"}
_NOT_COUNTED_PRODUCT_STATES = {"not_counted", "excluded", "ambiguous"}
_ALL_PRODUCT_STATES = _COUNTED_PRODUCT_STATES | _NOT_COUNTED_PRODUCT_STATES


def write_workbook_from_rows(
    config: ExtractionConfig,
    targets: list[Target],
    rows: list[dict[str, str]],
    *,
    diagnostics: list[dict[str, str]],
    output_path: Path,
    require_projection: bool = True,
    extra_metadata: Sequence[tuple[str, object]] = (),
) -> Path:
    if require_projection:
        _require_product_projection(rows)
    review_rows = _review_queue_rows(rows, diagnostics)
    wb = Workbook()
    ws_overview = wb.active
    _build_overview_sheet(ws_overview, rows, diagnostics, review_rows)

    ws_review = wb.create_sheet("Review Queue")
    _build_review_queue_sheet(ws_review, review_rows)

    ws_data = wb.create_sheet("XIC Results")
    _build_data_sheet(ws_data, rows)

    ws_summary = wb.create_sheet("Summary")
    _build_summary_sheet(
        ws_summary,
        rows,
        count_no_ms2_as_detected=config.count_no_ms2_as_detected,
        review_rows=review_rows,
        require_projection=require_projection,
    )

    ws_targets = wb.create_sheet("Targets")
    _build_targets_sheet(ws_targets, targets)

    ws_diagnostics = wb.create_sheet("Diagnostics")
    _build_diagnostics_sheet(ws_diagnostics, diagnostics)
    ws_diagnostics.sheet_state = "hidden"

    ws_metadata = wb.create_sheet("Run Metadata")
    _build_metadata_sheet(ws_metadata, config, extra_metadata=extra_metadata)

    for title, source in (
        ("Sample Context", config.injection_order_source),
        ("Identity Support", config.targeted_ms1_shape_identity_support_tsv),
        ("Selection Approvals", config.model_selection_expected_diff_approval_registry),
    ):
        if source is not None:
            _embed_input_table(wb, title, source)

    wb.active = wb.index(ws_overview)
    _apply_sheet_role_styles(wb)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    wb.close()

    return output_path


def _embed_input_table(workbook: Workbook, title: str, source: Path) -> None:
    """Keep supplied decision inputs reviewable after private run files expire."""
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        original = load_workbook(source, read_only=True, data_only=True)
        try:
            rows = list(original.active.values)
        finally:
            original.close()
    else:
        with source.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(
                csv.reader(
                    handle, delimiter="\t" if source.suffix.lower() == ".tsv" else ","
                )
            )
    if len(rows) <= 1:
        return
    sheet = workbook.create_sheet(title)
    for values in rows:
        sheet.append(
            [
                _excel_text(value) if isinstance(value, str) else value
                for value in values
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_state = "hidden"


def run_from_config(
    config: ExtractionConfig,
    targets: list[Target],
) -> Path:
    output_dir = config.output_csv.parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    excel_path = output_dir / f"xic_results_{timestamp}.xlsx"

    rows = _read_long_results(config, targets)
    if not rows:
        print("CSV is empty.")
        return excel_path
    require_projection = config.output_csv.with_name("xic_results_long.csv").exists()

    diagnostics = _read_diagnostics(config.diagnostics_csv)
    write_workbook_from_rows(
        config,
        targets,
        rows,
        diagnostics=diagnostics,
        output_path=excel_path,
        require_projection=require_projection,
    )
    _print_summary(
        excel_path,
        rows,
        config.count_no_ms2_as_detected,
        require_projection=require_projection,
    )
    return excel_path


def _print_summary(
    excel_path: Path,
    rows: list[dict[str, str]],
    count_no_ms2_as_detected: bool,
    *,
    require_projection: bool = False,
) -> None:
    print(f"Saved : {excel_path}")
    sample_count = len({row.get("SampleName", "") for row in rows})
    print(f"Rows  : {sample_count}")
    for target in _target_summaries(rows):
        label = target["Target"]
        target_rows = [row for row in rows if row.get("Target") == label]
        detected = sum(
            1
            for row in target_rows
            if _is_long_detected(
                row,
                count_no_ms2_as_detected,
                require_projection=require_projection,
            )
        )
        print(f"  {label} counted detection: {detected}/{len(target_rows)}")
    for target in _target_summaries(rows):
        label = target["Target"]
        target_rows = [row for row in rows if row.get("Target") == label]
        if not any(row.get("NL", "") for row in target_rows):
            continue
        counts = _long_nl_counts(target_rows)
        print(
            f"  {label}_NL  OK:{counts['OK']}  WARN:{counts['WARN']}  "
            f"FAIL:{counts['NL_FAIL']}  NO_MS2:{counts['NO_MS2']}"
        )
    for target in _target_summaries(rows):
        if target.get("Role") != "ISTD":
            continue
        label = target["Target"]
        target_rows = [row for row in rows if row.get("Target") == label]
        detected = sum(
            1
            for row in target_rows
            if _is_long_detected(
                row,
                count_no_ms2_as_detected,
                require_projection=require_projection,
            )
        )
        if detected < len(target_rows):
            print(f"ISTD_ND: {label} {detected}/{len(target_rows)}")


def _require_product_projection(rows: list[dict[str, str]]) -> None:
    for row_index, row in enumerate(rows, start=2):
        counted = row.get("Counted Detection", "").upper()
        product_state = row.get("Product State", "")
        if counted not in {"TRUE", "FALSE"}:
            sample = row.get("SampleName", "")
            target = row.get("Target", "")
            raise MissingTargetedProductProjectionError(
                "Counted Detection TRUE/FALSE is required for targeted product "
                f"workbook rows; row={row_index} sample={sample!r} target={target!r}"
            )
        if product_state not in _ALL_PRODUCT_STATES:
            sample = row.get("SampleName", "")
            target = row.get("Target", "")
            raise MissingTargetedProductProjectionError(
                "Product State is required for targeted product workbook rows; "
                f"row={row_index} sample={sample!r} target={target!r} "
                f"product_state={product_state!r}"
            )
        if counted == "TRUE" and product_state not in _COUNTED_PRODUCT_STATES:
            sample = row.get("SampleName", "")
            target = row.get("Target", "")
            raise MissingTargetedProductProjectionError(
                "Counted Detection TRUE must use a detected product state; "
                f"row={row_index} sample={sample!r} target={target!r} "
                f"product_state={product_state!r}"
            )
        if counted == "FALSE" and product_state not in _NOT_COUNTED_PRODUCT_STATES:
            sample = row.get("SampleName", "")
            target = row.get("Target", "")
            raise MissingTargetedProductProjectionError(
                "Counted Detection FALSE must use a non-detected product state; "
                f"row={row_index} sample={sample!r} target={target!r} "
                f"product_state={product_state!r}"
            )
