from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from xic_extractor.instrument_qc.lifecycle import (
    DuplicateLifecycleRunError,
    append_lifecycle_dataset,
)
from xic_extractor.instrument_qc.pipeline import run_sdolek_pipeline
from xic_extractor.instrument_qc.sequence_manifest import build_sequence_manifest
from xic_extractor.instrument_qc.sequence_manifest_writers import (
    write_injection_order_csv,
    write_sample_metadata_tsv,
    write_sequence_manifest_json,
    write_sequence_manifest_markdown,
    write_sequence_manifest_tsv,
)
from xic_extractor.raw_reader import RawReaderError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run opt-in instrument-only QC trend extraction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Outputs:\n"
            "  instrument_qc_sdolek_trend.tsv\n"
            "  instrument_qc_sdolek_trend.json\n"
            "  instrument_qc_sdolek_diagnostics.tsv\n"
            "  instrument_qc_trend_sdolek.xlsx\n"
            "  instrument_qc_mixstds_* when --emit-mixstds is supplied\n\n"
            "  instrument_qc_hcd_audit.* when --emit-hcd-audit is supplied\n\n"
            "Input note:\n"
            "  --raw-dir must contain the expected SDOLEK subfolder."
        ),
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        required=True,
        help="Batch RAW root containing the SDOLEK subfolder.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where TSV, JSON, diagnostics TSV, and XLSX are written.",
    )
    parser.add_argument(
        "--mode",
        default="sdolek",
        help="Current instrument QC mode supports only sdolek.",
    )
    parser.add_argument(
        "--injection-order-source",
        type=Path,
        help=(
            "Optional docs-derived Sample_Name,Injection_Order CSV. Use this "
            "when a method doc was already converted."
        ),
    )
    parser.add_argument(
        "--method-doc",
        type=Path,
        help=(
            "Optional source method/sequence .docx. Generates sequence manifest "
            "and injection-order CSV under --output-dir."
        ),
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        help="Optional Thermo DLL directory override.",
    )
    parser.add_argument(
        "--emit-mixstds",
        action="store_true",
        help="Emit audit-only Mix STDs trend outputs from an explicit registry.",
    )
    parser.add_argument(
        "--mixstds-target-registry",
        type=Path,
        help="Reviewed instrument-QC Mix STDs target registry CSV.",
    )
    parser.add_argument(
        "--emit-hcd-audit",
        action="store_true",
        help="Emit audit-only MS2/HCD product-ion review outputs.",
    )
    parser.add_argument(
        "--hcd-product-registry",
        type=Path,
        help="Optional HCD product-ion registry CSV overriding built-ins.",
    )
    parser.add_argument(
        "--append-lifecycle",
        action="store_true",
        help="Append this instrument QC run to an explicit lifecycle dataset.",
    )
    parser.add_argument(
        "--instrument-id",
        help="Instrument identifier required with --append-lifecycle.",
    )
    parser.add_argument(
        "--lifecycle-root",
        type=Path,
        help="Lifecycle dataset directory required with --append-lifecycle.",
    )
    parser.add_argument(
        "--allow-duplicate-lifecycle-run",
        action="store_true",
        help="Allow appending a run fingerprint that already exists.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode != "sdolek":
        print(
            f"unsupported mode: {args.mode}. "
            "Current instrument QC mode supports only sdolek.",
            file=sys.stderr,
        )
        return 2
    if args.method_doc is not None and args.injection_order_source is not None:
        print(
            "Provide either --method-doc or --injection-order-source, not both.",
            file=sys.stderr,
        )
        return 2
    if args.append_lifecycle and not args.instrument_id:
        print("--instrument-id is required with --append-lifecycle.", file=sys.stderr)
        return 2
    if args.append_lifecycle and args.lifecycle_root is None:
        print("--lifecycle-root is required with --append-lifecycle.", file=sys.stderr)
        return 2
    injection_order_source = args.injection_order_source
    manifest_paths: tuple[Path, ...] = ()
    if args.method_doc is not None:
        manifest_result = _build_method_doc_manifest(
            method_doc=args.method_doc,
            raw_dir=args.raw_dir,
            output_dir=args.output_dir,
        )
        if isinstance(manifest_result, str):
            print(manifest_result, file=sys.stderr)
            return 2
        injection_order_source = manifest_result[1]
        manifest_paths = manifest_result
    try:
        output = run_sdolek_pipeline(
            raw_dir=args.raw_dir,
            output_dir=args.output_dir,
            injection_order_source=injection_order_source,
            dll_dir=args.dll_dir,
            emit_mixstds=args.emit_mixstds,
            mixstds_target_registry=args.mixstds_target_registry,
            emit_hcd_audit=args.emit_hcd_audit,
            hcd_product_registry=args.hcd_product_registry,
            sequence_manifest_source=manifest_paths[0] if manifest_paths else None,
        )
    except RawReaderError as exc:
        print(f"RAW reader error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 2

    lifecycle_result = None
    if args.append_lifecycle:
        try:
            lifecycle_result = append_lifecycle_dataset(
                output=output,
                raw_dir=args.raw_dir,
                output_dir=args.output_dir,
                lifecycle_root=args.lifecycle_root,
                instrument_id=args.instrument_id,
                method_doc=args.method_doc,
                allow_duplicate=args.allow_duplicate_lifecycle_run,
            )
        except DuplicateLifecycleRunError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    print(f"Wrote {output.trend_tsv}")
    print(f"Wrote {output.trend_json}")
    print(f"Wrote {output.diagnostics_tsv}")
    print(f"Wrote {output.workbook}")
    for optional_path in (
        output.mixstds_trend_tsv,
        output.mixstds_trend_json,
        output.mixstds_diagnostics_tsv,
        output.hcd_audit_tsv,
        output.hcd_audit_json,
    ):
        if optional_path is not None:
            print(f"Wrote {optional_path}")
    for manifest_path in manifest_paths:
        print(f"Wrote {manifest_path}")
    if lifecycle_result is not None:
        for lifecycle_path in (
            lifecycle_result.runs_tsv,
            lifecycle_result.sdolek_tsv,
            lifecycle_result.mixstds_tsv,
            lifecycle_result.blank_tsv,
            lifecycle_result.summary_json,
        ):
            print(f"Wrote {lifecycle_path}")
    return 0


def _build_method_doc_manifest(
    *,
    method_doc: Path,
    raw_dir: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path, Path, Path] | str:
    if method_doc.name.casefold().startswith("sampleinfo"):
        return "SampleInfo is downstream evidence, not an accepted method-doc input."
    if not method_doc.exists():
        return f"method doc not found: {method_doc}"
    if method_doc.suffix.lower() != ".docx":
        return f"unsupported method doc type: {method_doc.suffix}"
    if not raw_dir.exists():
        return f"raw dir not found: {raw_dir}"

    try:
        rows = build_sequence_manifest(method_doc=method_doc, raw_dir=raw_dir)
    except ValueError as exc:
        return str(exc)
    manifest_tsv = output_dir / "instrument_qc_sequence_manifest.tsv"
    injection_order_csv = output_dir / "instrument_qc_injection_order.csv"
    sample_metadata_tsv = output_dir / "instrument_qc_sample_metadata.tsv"
    manifest_json = output_dir / "instrument_qc_sequence_manifest.json"
    manifest_md = output_dir / "instrument_qc_sequence_manifest.md"
    write_sequence_manifest_tsv(manifest_tsv, rows)
    write_injection_order_csv(injection_order_csv, rows)
    write_sample_metadata_tsv(sample_metadata_tsv, rows)
    write_sequence_manifest_json(manifest_json, rows)
    write_sequence_manifest_markdown(manifest_md, rows)
    return (
        manifest_tsv,
        injection_order_csv,
        sample_metadata_tsv,
        manifest_json,
        manifest_md,
    )


if __name__ == "__main__":
    raise SystemExit(main())
