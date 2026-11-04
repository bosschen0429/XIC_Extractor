from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.detection import (
    MissingTargetedProductProjectionError,
    is_accepted_row_detection,
)
from xic_extractor.output.review_queue_model import review_queue_rows
from xic_extractor.output.sheet_area_matrix import build_area_matrix_sheet
from xic_extractor.output.sheet_diagnostic_events import build_diagnostic_events_sheet
from xic_extractor.output.sheet_field_guide import build_field_guide_sheet
from xic_extractor.output.sheet_identification import build_identification_sheets
from xic_extractor.output.sheet_metadata import _build_metadata_sheet
from xic_extractor.output.sheet_overview import build_overview_sheet
from xic_extractor.output.sheet_results import _build_data_sheet
from xic_extractor.output.sheet_review import build_review_sheet
from xic_extractor.output.sheet_targets import _build_targets_sheet
from xic_extractor.output.targeted_overview import (
    focus_worklist,
    summarize_targeted_results,
)
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
    identification_rows: list[dict] | None = None,
) -> Path:
    if require_projection:
        _require_product_projection(rows)
    review_rows = review_queue_rows(rows, diagnostics)
    overview = summarize_targeted_results(rows, review_rows)
    worklist = focus_worklist(overview, rows, review_rows, diagnostics)
    result_rows = {
        (row.get("SampleName", ""), row.get("Target", "")): index
        for index, row in enumerate(rows, 2)
    }
    wb = Workbook()
    summary = wb.active
    summary.title = "摘要"
    review_rows_by_target = (
        build_review_sheet(wb.create_sheet("需檢查"), worklist, result_rows=result_rows)
        if worklist
        else {}
    )
    build_overview_sheet(
        summary,
        overview,
        pairs={target.label: target.istd_pair for target in targets},
        worklist_size=len(worklist),
        review_rows_by_target=review_rows_by_target,
    )
    build_area_matrix_sheet(wb.create_sheet("面積"), rows)
    _build_data_sheet(wb.create_sheet("XIC Results"), rows, review_rows=review_rows)
    _build_targets_sheet(wb.create_sheet("Targets"), targets)
    build_identification_sheets(wb, targets, identification_rows or [])
    _build_metadata_sheet(
        wb.create_sheet("Run Metadata"),
        config,
    )
    build_field_guide_sheet(
        wb.create_sheet("欄位說明"),
        identification_methods=any(target.identification_method for target in targets),
    )
    if diagnostics:
        build_diagnostic_events_sheet(wb.create_sheet("診斷事件"), diagnostics)

    if config.injection_order_source is not None:
        _embed_input_table(wb, "Sample Context", config.injection_order_source)

    wb.active = wb.index(summary)
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
    by_target: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_target[row.get("Target", "")].append(row)
    for label, target_rows in by_target.items():
        detected = sum(
            is_accepted_row_detection(
                row, count_no_ms2_as_detected, require_projection=require_projection
            )
            for row in target_rows
        )
        print(f"  {label} counted detection: {detected}/{len(target_rows)}")
        counts = Counter(row.get("NL", "") for row in target_rows)
        if any(counts):
            warning_count = sum(
                count for value, count in counts.items() if value.startswith("WARN_")
            )
            print(
                f"  {label}_NL  OK:{counts['OK']}  WARN:{warning_count}  "
                f"FAIL:{counts['NL_FAIL']}  NO_MS2:{counts['NO_MS2']}"
            )
        if target_rows[0].get("Role") == "ISTD" and detected < len(target_rows):
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
