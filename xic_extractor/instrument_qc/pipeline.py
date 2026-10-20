from pathlib import Path

from xic_extractor.instrument_qc.hcd_registry import load_hcd_product_registry
from xic_extractor.instrument_qc.mixstds import load_mixstds_target_registry
from xic_extractor.instrument_qc.models import (
    HCDAuditRow,
    InstrumentQCDiagnostic,
    InstrumentQCRunOutput,
    SDOLEKTrendRow,
)
from xic_extractor.instrument_qc.pipeline_contracts import RawOpener, XICSource
from xic_extractor.instrument_qc.pipeline_extraction import (
    error_row,
    extract_target_row,
)
from xic_extractor.instrument_qc.pipeline_hcd import append_hcd_audit_row
from xic_extractor.instrument_qc.pipeline_inputs import (
    discover_sdolek_raws,
    metadata_source_status,
    read_optional_injection_order,
    read_sequence_manifest_context,
)
from xic_extractor.instrument_qc.pipeline_mixstds import run_mixstds_extraction
from xic_extractor.instrument_qc.targets import SDOLEK_TARGETS
from xic_extractor.instrument_qc.workbook import write_sdolek_workbook
from xic_extractor.instrument_qc.writers import (
    write_diagnostics_tsv,
    write_hcd_audit_json,
    write_hcd_audit_tsv,
    write_sdolek_json,
    write_trend_tsv,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

DEFAULT_DLL_DIR = Path(CANONICAL_SETTINGS_DEFAULTS["dll_dir"])

__all__ = ["DEFAULT_DLL_DIR", "RawOpener", "XICSource", "run_sdolek_pipeline"]


def run_sdolek_pipeline(
    *,
    raw_dir: Path,
    output_dir: Path,
    injection_order_source: Path | None = None,
    dll_dir: Path | None = None,
    raw_opener: RawOpener | None = None,
    emit_mixstds: bool = False,
    mixstds_target_registry: Path | None = None,
    emit_hcd_audit: bool = False,
    hcd_product_registry: Path | None = None,
    sequence_manifest_source: Path | None = None,
) -> InstrumentQCRunOutput:
    diagnostics: list[InstrumentQCDiagnostic] = []
    raw_paths = discover_sdolek_raws(raw_dir, diagnostics)
    injection_order = read_optional_injection_order(
        injection_order_source,
        raw_paths,
        diagnostics,
    )
    effective_dll_dir = dll_dir or DEFAULT_DLL_DIR
    opener = raw_opener or (lambda path: open_raw(path, effective_dll_dir))
    rows: list[SDOLEKTrendRow] = []
    hcd_rows: list[HCDAuditRow] = []
    hcd_products = (
        load_hcd_product_registry(hcd_product_registry) if emit_hcd_audit else ()
    )
    manifest_context = (
        read_sequence_manifest_context(sequence_manifest_source)
        if emit_hcd_audit
        else {}
    )

    for raw_path in raw_paths:
        sample_name = raw_path.stem
        try:
            with opener(raw_path) as raw:
                for target in SDOLEK_TARGETS:
                    try:
                        row = extract_target_row(
                            raw=raw,
                            raw_path=raw_path,
                            sample_name=sample_name,
                            injection_order=injection_order.get(sample_name),
                            target=target,
                            dll_dir=effective_dll_dir,
                        )
                        rows.append(row)
                        if emit_hcd_audit:
                            append_hcd_audit_row(
                                hcd_rows,
                                trend_row=row,
                                raw=raw,
                                products=hcd_products,
                                manifest_context=manifest_context,
                                hcd_product_group=target.compound,
                                hcd_mapping_source="sdolek_builtin",
                                cid_neutral_loss_da=target.neutral_loss_da,
                            )
                    except Exception as exc:
                        diagnostics.append(
                            InstrumentQCDiagnostic(
                                sample_name=sample_name,
                                raw_path=raw_path,
                                issue="TARGET_EXTRACTION_ERROR",
                                detail=f"{target.compound}: {exc}",
                            )
                        )
                        rows.append(
                            error_row(
                                raw_path=raw_path,
                                sample_name=sample_name,
                                injection_order=injection_order.get(sample_name),
                                target=target,
                                reason=str(exc),
                            )
                        )
        except Exception as exc:
            diagnostics.append(
                InstrumentQCDiagnostic(
                    sample_name=sample_name,
                    raw_path=raw_path,
                    issue="RAW_EXTRACTION_ERROR",
                    detail=str(exc),
                )
            )
            rows.extend(
                error_row(
                    raw_path=raw_path,
                    sample_name=sample_name,
                    injection_order=injection_order.get(sample_name),
                    target=target,
                    reason=str(exc),
                )
                for target in SDOLEK_TARGETS
            )

    mixstds_rows: tuple[SDOLEKTrendRow, ...] = ()
    mixstds_diagnostics: list[InstrumentQCDiagnostic] = []
    if emit_mixstds:
        mixstds_registry = load_mixstds_target_registry(mixstds_target_registry)
        mixstds_rows = run_mixstds_extraction(
            raw_dir=raw_dir,
            injection_order=injection_order,
            opener=opener,
            dll_dir=effective_dll_dir,
            registry=mixstds_registry,
            diagnostics=mixstds_diagnostics,
            emit_hcd_audit=emit_hcd_audit,
            hcd_rows=hcd_rows,
            hcd_products=hcd_products,
            manifest_context=manifest_context,
        )

    trend_tsv = output_dir / "instrument_qc_sdolek_trend.tsv"
    trend_json = output_dir / "instrument_qc_sdolek_trend.json"
    diagnostics_tsv = output_dir / "instrument_qc_sdolek_diagnostics.tsv"
    workbook = output_dir / "instrument_qc_trend_sdolek.xlsx"
    mixstds_trend_tsv = (
        output_dir / "instrument_qc_mixstds_trend.tsv" if emit_mixstds else None
    )
    mixstds_trend_json = (
        output_dir / "instrument_qc_mixstds_trend.json" if emit_mixstds else None
    )
    mixstds_diagnostics_tsv = (
        output_dir / "instrument_qc_mixstds_diagnostics.tsv" if emit_mixstds else None
    )
    hcd_audit_tsv = (
        output_dir / "instrument_qc_hcd_audit.tsv" if emit_hcd_audit else None
    )
    hcd_audit_json = (
        output_dir / "instrument_qc_hcd_audit.json" if emit_hcd_audit else None
    )
    write_trend_tsv(trend_tsv, rows)
    source_status = metadata_source_status(injection_order_source)
    write_sdolek_json(
        trend_json,
        rows,
        diagnostics,
        metadata_source_status=source_status,
    )
    write_diagnostics_tsv(diagnostics_tsv, diagnostics)
    if mixstds_trend_tsv is not None:
        write_trend_tsv(mixstds_trend_tsv, mixstds_rows)
    if mixstds_trend_json is not None:
        write_sdolek_json(
            mixstds_trend_json,
            mixstds_rows,
            mixstds_diagnostics,
            metadata_source_status={
                "target_registry_source": str(mixstds_target_registry or ""),
                "target_registry_status": (
                    "provided" if mixstds_target_registry else "missing"
                ),
            },
        )
    if mixstds_diagnostics_tsv is not None:
        write_diagnostics_tsv(mixstds_diagnostics_tsv, mixstds_diagnostics)
    if hcd_audit_tsv is not None:
        write_hcd_audit_tsv(hcd_audit_tsv, hcd_rows)
    if hcd_audit_json is not None:
        write_hcd_audit_json(hcd_audit_json, hcd_rows)
    write_sdolek_workbook(
        workbook,
        rows,
        diagnostics,
        metadata_source_status=source_status,
        mixstds_rows=mixstds_rows if emit_mixstds else None,
        hcd_rows=hcd_rows if emit_hcd_audit else None,
    )
    return InstrumentQCRunOutput(
        trend_rows=tuple(rows),
        diagnostics=tuple(diagnostics + mixstds_diagnostics),
        trend_tsv=trend_tsv,
        trend_json=trend_json,
        diagnostics_tsv=diagnostics_tsv,
        workbook=workbook,
        mixstds_rows=mixstds_rows,
        mixstds_trend_tsv=mixstds_trend_tsv,
        mixstds_trend_json=mixstds_trend_json,
        mixstds_diagnostics_tsv=mixstds_diagnostics_tsv,
        hcd_audit_rows=tuple(hcd_rows),
        hcd_audit_tsv=hcd_audit_tsv,
        hcd_audit_json=hcd_audit_json,
    )
