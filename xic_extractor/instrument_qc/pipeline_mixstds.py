from __future__ import annotations

from pathlib import Path

from xic_extractor.instrument_qc.hcd_registry import resolve_hcd_product_group
from xic_extractor.instrument_qc.mixstds import (
    MixSTDSTargetRegistry,
    discover_mixstds_raws,
)
from xic_extractor.instrument_qc.models import (
    ActivationMethod,
    HCDAuditRow,
    HCDProductIon,
    InstrumentQCDiagnostic,
    SDOLEKTrendRow,
)
from xic_extractor.instrument_qc.pipeline_contracts import RawOpener
from xic_extractor.instrument_qc.pipeline_extraction import (
    error_row,
    extract_target_row,
)
from xic_extractor.instrument_qc.pipeline_hcd import append_hcd_audit_row


def run_mixstds_extraction(
    *,
    raw_dir: Path,
    injection_order: dict[str, int],
    opener: RawOpener,
    dll_dir: Path,
    registry: MixSTDSTargetRegistry,
    diagnostics: list[InstrumentQCDiagnostic],
    emit_hcd_audit: bool,
    hcd_rows: list[HCDAuditRow],
    hcd_products: tuple[HCDProductIon, ...],
    manifest_context: dict[str, tuple[str, ActivationMethod]],
) -> tuple[SDOLEKTrendRow, ...]:
    if registry.status != "loaded":
        diagnostics.append(
            InstrumentQCDiagnostic(
                sample_name="",
                raw_path=registry.source or raw_dir,
                issue=f"MIXSTDS_TARGET_REGISTRY_{registry.status.upper()}",
                detail=registry.reason,
            )
        )
        return ()

    rows: list[SDOLEKTrendRow] = []
    raw_paths = discover_mixstds_raws(raw_dir, diagnostics)
    if not raw_paths:
        diagnostics.append(
            InstrumentQCDiagnostic(
                sample_name="",
                raw_path=raw_dir,
                issue="MIXSTDS_RAW_MISSING",
                detail=f"No Mix STDs RAW files found below {raw_dir}.",
            )
        )
        return ()

    for raw_path in raw_paths:
        sample_name = raw_path.stem
        try:
            with opener(raw_path) as raw:
                for target in registry.targets:
                    try:
                        row = extract_target_row(
                            raw=raw,
                            raw_path=raw_path,
                            sample_name=sample_name,
                            injection_order=injection_order.get(sample_name),
                            target=target,
                            dll_dir=dll_dir,
                        )
                        rows.append(row)
                        if emit_hcd_audit:
                            group, source = resolve_hcd_product_group(
                                target.compound,
                                explicit_base_group=registry.hcd_base_groups.get(
                                    target.compound
                                ),
                                explicit_product_group=registry.hcd_product_groups.get(
                                    target.compound
                                ),
                            )
                            append_hcd_audit_row(
                                hcd_rows,
                                trend_row=row,
                                raw=raw,
                                products=hcd_products,
                                manifest_context=manifest_context,
                                hcd_product_group=group,
                                hcd_mapping_source=source,
                                cid_neutral_loss_da=target.neutral_loss_da,
                            )
                    except Exception as exc:
                        diagnostics.append(
                            InstrumentQCDiagnostic(
                                sample_name=sample_name,
                                raw_path=raw_path,
                                issue="MIXSTDS_TARGET_EXTRACTION_ERROR",
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
                    issue="MIXSTDS_RAW_EXTRACTION_ERROR",
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
                for target in registry.targets
            )
    return tuple(rows)
