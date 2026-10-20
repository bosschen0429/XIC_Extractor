from pathlib import Path

import numpy as np

from xic_extractor.instrument_qc.hcd_evidence import build_hcd_audit_row
from xic_extractor.instrument_qc.hcd_registry import (
    base_group_from_label_base_letter,
    built_in_hcd_products,
    infer_base_group_from_label,
    load_hcd_product_registry,
    resolve_hcd_product_group,
)
from xic_extractor.instrument_qc.models import SDOLEKTrendRow
from xic_extractor.instrument_qc.sequence_manifest import (
    activation_method_from_instrument_method,
)
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent


class FakeMS2Raw:
    def __init__(self, events: tuple[Ms2ScanEvent, ...]) -> None:
        self.events = events

    def iter_ms2_scans(self, rt_min: float, rt_max: float):
        for event in self.events:
            if event.scan is None:
                yield event
                continue
            if rt_min <= event.scan.rt <= rt_max:
                yield event


def _trend(
    compound: str = "SDO",
    mz: float = 311.0814,
    *,
    status: str = "detected",
    apex_rt: float | None = 6.0,
) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name="sample",
        raw_path=Path("sample.raw"),
        injection_order=1,
        compound=compound,
        precursor_mz=mz,
        identity_evidence="MS1_ONLY",
        reference_rt_min=6.0,
        rt_delta_to_reference_min=0.0,
        apex_rt_min=apex_rt,
        area=1000.0,
        base_width_min=0.1,
        reference_base_width_min=0.1,
        base_width_ratio_to_reference=1.0,
        peak_start_rt_min=5.9,
        peak_end_rt_min=6.1,
        trend_confidence="clean",
        trend_flags=(),
        status=status,  # type: ignore[arg-type]
        reason="OK",
    )


def _scan(
    *,
    precursor_mz: float = 311.0814,
    rt: float = 6.01,
    masses: tuple[float, ...] = (156.0768, 200.0),
    intensities: tuple[float, ...] = (1000.0, 100.0),
) -> Ms2ScanEvent:
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=1,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.array(masses),
            intensities=np.array(intensities),
            base_peak=max(intensities),
        ),
        parse_error=None,
        scan_number=1,
    )


def test_builtin_registry_contains_sdolek_and_base_products() -> None:
    products = built_in_hcd_products()

    assert any(
        item.compound_or_group == "SDO" and item.activation == "CID"
        for item in products
    )
    assert any(
        item.compound_or_group == "LEK" and item.activation == "wHCD"
        for item in products
    )
    assert any(
        item.compound_or_group == "G" and item.product_mz == 151.0494
        for item in products
    )


def test_product_registry_csv_overrides_builtins(tmp_path: Path) -> None:
    registry = tmp_path / "hcd.csv"
    registry.write_text(
        "compound_or_group,precursor_mz,activation,product_label,product_mz,product_role\n"
        "SDO,311.0814,CID,Product-1,156.0000,override\n",
        encoding="utf-8",
    )

    products = load_hcd_product_registry(registry)

    assert any(
        item.compound_or_group == "SDO"
        and item.activation == "CID"
        and item.product_label == "Product-1"
        and item.product_mz == 156.0000
        for item in products
    )


def test_activation_method_parser_uses_method_doc_text() -> None:
    assert (
        activation_method_from_instrument_method("20260105 STDs_ddMS2_CIDwHCD_EASY-IC")
        == "CIDwHCD"
    )
    assert (
        activation_method_from_instrument_method(
            "20260105 Breast Cancer_ddMS2-CID_Tissue"
        )
        == "CID"
    )
    assert activation_method_from_instrument_method("20260105 SDOLEK") == "unknown"


def test_mixstds_group_mapping_prefers_explicit_then_heuristic() -> None:
    assert resolve_hcd_product_group("8-oxodG", explicit_base_group="G") == (
        "G",
        "explicit_base_group",
    )
    assert resolve_hcd_product_group("5-medC") == ("C", "label_base_letter")
    assert infer_base_group_from_label("unknown-target") is None


def test_known_base_mapping_uses_target_specific_base() -> None:
    assert resolve_hcd_product_group("5-hmdC") == ("C", "label_base_letter")
    assert resolve_hcd_product_group("5-cadC") == ("C", "label_base_letter")
    assert resolve_hcd_product_group("8-oxo-dA") == ("A", "label_base_letter")
    assert resolve_hcd_product_group("8-oxo-Guo") == ("G", "label_base_letter")
    assert resolve_hcd_product_group("Guo") == ("G", "label_base_letter")
    assert resolve_hcd_product_group("t6A") == ("A", "label_base_letter")
    assert resolve_hcd_product_group("Y") == (None, "unmapped")


def test_label_base_letter_ignores_isotope_brackets() -> None:
    assert base_group_from_label_base_letter("[13C,15N2]-8-oxo-Guo") == "G"
    assert base_group_from_label_base_letter("15N5-8-oxodG") == "G"


def test_u_base_products_are_available_for_uridine_labels() -> None:
    products = built_in_hcd_products()

    assert resolve_hcd_product_group("mcm5U") == ("U", "label_base_letter")
    assert any(
        item.compound_or_group == "U" and item.product_label == "U+H"
        for item in products
    )


def test_hcd_supported_when_expected_product_matches() -> None:
    row = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw((_scan(),)),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="sdolek_builtin",
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert row.matched_product_count == 1
    assert row.best_ms2_scan_rt_min == 6.01


def test_hcd_counts_multiple_products_from_one_scan() -> None:
    row = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw(
            (
                _scan(
                    masses=(156.0768, 245.1035, 218.0232, 400.0),
                    intensities=(1000.0, 800.0, 500.0, 10.0),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="sdolek_builtin",
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert row.matched_product_count == 3
    assert row.matched_products == (
        "CID:Product-1",
        "CID:Product-2",
        "CID:Product-3",
    )


def test_sdolek_whcd_uses_sdolek_whcd_products_only() -> None:
    row = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw(
            (
                _scan(
                    masses=(156.0770, 108.0446, 92.0497, 245.1035),
                    intensities=(1000.0, 800.0, 500.0, 900.0),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="20260105 SDOLEK",
        activation_method="wHCD",
        hcd_product_group="SDO",
        hcd_mapping_source="sdolek_builtin",
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert row.expected_product_count == 4
    assert row.matched_products == (
        "wHCD:Product-1",
        "wHCD:Product-2",
        "wHCD:Product-3",
    )
    assert "CID:Product-2" not in row.matched_products


def test_cidwhcd_uses_hcd_base_and_cid_neutral_loss_products() -> None:
    precursor_mz = 258.1085
    neutral_loss_da = 116.0474
    row = build_hcd_audit_row(
        trend_row=_trend("5-hmdC", precursor_mz),
        raw=FakeMS2Raw(
            (
                _scan(
                    precursor_mz=precursor_mz,
                    masses=(112.0505, precursor_mz - neutral_loss_da),
                    intensities=(800.0, 1000.0),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="CIDwHCD",
        activation_method="CIDwHCD",
        hcd_product_group="C",
        hcd_mapping_source="label_base_letter",
        cid_neutral_loss_da=neutral_loss_da,
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert "CIDwHCD:NL-116.0474" in row.matched_products
    assert "HCD:C+H" in row.matched_products


def test_hcd_method_can_use_neutral_loss_review_evidence() -> None:
    precursor_mz = 258.1085
    neutral_loss_da = 116.0474
    row = build_hcd_audit_row(
        trend_row=_trend("5-hmdC", precursor_mz),
        raw=FakeMS2Raw(
            (
                _scan(
                    precursor_mz=precursor_mz,
                    masses=(precursor_mz - neutral_loss_da,),
                    intensities=(1000.0,),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="HCD",
        activation_method="HCD",
        hcd_product_group="C",
        hcd_mapping_source="label_base_letter",
        cid_neutral_loss_da=neutral_loss_da,
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert "HCD:NL-116.0474" in row.matched_products


def test_product_evidence_outside_selected_window_marks_rt_review() -> None:
    precursor_mz = 351.2139
    neutral_loss_da = 116.0474
    row = build_hcd_audit_row(
        trend_row=_trend("N6-6Ah-dA", precursor_mz, apex_rt=24.42),
        raw=FakeMS2Raw(
            (
                _scan(
                    precursor_mz=precursor_mz,
                    rt=20.09,
                    masses=(precursor_mz - neutral_loss_da,),
                    intensities=(1000.0,),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="CIDwHCD",
        activation_method="CIDwHCD",
        hcd_product_group="A",
        hcd_mapping_source="label_base_letter",
        cid_neutral_loss_da=neutral_loss_da,
    )

    assert row is not None
    assert row.hcd_status == "hcd_supported"
    assert row.best_ms2_scan_rt_min == 20.09
    assert "target_rt_window_review" in row.review_flags
    assert "CIDwHCD:NL-116.0474" in row.matched_products


def test_not_detected_target_can_have_outside_window_ms2_support() -> None:
    precursor_mz = 413.1416
    neutral_loss_da = 132.0423
    row = build_hcd_audit_row(
        trend_row=_trend("t6A", precursor_mz, status="not_detected", apex_rt=None),
        raw=FakeMS2Raw(
            (
                _scan(
                    precursor_mz=precursor_mz,
                    rt=33.06,
                    masses=(precursor_mz - neutral_loss_da,),
                    intensities=(1000.0,),
                ),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="CIDwHCD",
        activation_method="CIDwHCD",
        hcd_product_group="A",
        hcd_mapping_source="label_base_letter",
        cid_neutral_loss_da=neutral_loss_da,
    )

    assert row is not None
    assert row.ms1_status == "not_detected"
    assert row.hcd_status == "hcd_supported"
    assert row.best_ms2_scan_rt_min == 33.06
    assert "target_rt_window_review" in row.review_flags


def test_hcd_partial_when_product_is_near_but_too_weak() -> None:
    row = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw((_scan(intensities=(1.0, 1000.0)),)),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="sdolek_builtin",
    )

    assert row is not None
    assert row.hcd_status == "hcd_partial"


def test_no_product_match_records_closest_trigger_rt() -> None:
    row = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw(
            (
                _scan(rt=5.85, masses=(300.0,), intensities=(1000.0,)),
                _scan(rt=6.08, masses=(300.0,), intensities=(1000.0,)),
            )
        ),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="sdolek_builtin",
    )

    assert row is not None
    assert row.hcd_status == "no_product_match"
    assert row.best_ms2_scan_rt_min == 6.08
    assert round(row.apex_ms2_delta_min or 0.0, 2) == 0.08


def test_hcd_no_trigger_and_group_unmapped_statuses() -> None:
    no_trigger = build_hcd_audit_row(
        trend_row=_trend(),
        raw=FakeMS2Raw(()),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="sdolek_builtin",
    )
    unmapped = build_hcd_audit_row(
        trend_row=_trend("unknown", 500.0),
        raw=FakeMS2Raw((_scan(precursor_mz=500.0),)),
        products=built_in_hcd_products(),
        instrument_method="method",
        activation_method="CID",
        hcd_product_group=None,
        hcd_mapping_source="unmapped",
    )

    assert no_trigger is not None
    assert no_trigger.hcd_status == "no_ms2_trigger"
    assert unmapped is not None
    assert unmapped.hcd_status == "hcd_group_unmapped"
