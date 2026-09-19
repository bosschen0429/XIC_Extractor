from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from tools.diagnostics import build_targeted_ms1_shape_identity_supports as tool
from xic_extractor.config import Target
from xic_extractor.diagnostics import (
    targeted_ms1_shape_identity_support_producer as support_producer,
)
from xic_extractor.xic_models import XICTrace


def test_run_build_targeted_ms1_shape_identity_supports_writes_generic_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    long_csv = tmp_path / "xic_results_long.csv"
    output_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    targets = (_target("5-hmdC", istd_pair="d3-5-hmdC"), _istd("d3-5-hmdC"))
    _write_csv(
        long_csv,
        [
            *_reference_rows("RefA", target_rt="9.10", istd_rt="9.00"),
            *_reference_rows("RefB", target_rt="9.12", istd_rt="9.02"),
            *_reference_rows("RefC", target_rt="9.14", istd_rt="9.04"),
            *_candidate_rows("CandidateA", istd_rt="9.01"),
            *_candidate_rows("CandidateB", istd_rt="9.03"),
        ],
    )

    monkeypatch.setattr(
        support_producer,
        "load_config",
        lambda *_args, **_kwargs: (object(), targets),
    )
    rt = np.linspace(8.7, 9.6, 181)

    def trace_loader(
        request: support_producer.TargetedMs1ShapeTraceRequest,
    ) -> XICTrace:
        centers = {
            ("RefB", "5-hmdC"): 9.12,
            ("CandidateA", "5-hmdC"): 9.11,
            ("CandidateB", "5-hmdC"): 9.13,
        }
        return XICTrace.from_arrays(
            rt,
            _gaussian(rt, center=centers[request.key], width=0.045, scale=100.0),
        )

    outputs = tool.run_build_targeted_ms1_shape_identity_supports(
        long_csv=long_csv,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        config_dir=tmp_path,
        output_tsv=output_tsv,
        trace_loader=trace_loader,
    )

    rows = _read_tsv(outputs.evidence_tsv)
    assert outputs.candidate_count == 2
    assert outputs.evidence_row_count == 2
    assert outputs.trace_request_count == 3
    assert [row["sample_name"] for row in rows] == ["CandidateA", "CandidateB"]
    assert {row["own_max_same_peak_status"] for row in rows} == {
        "own_max_same_peak_supported"
    }


def _target(label: str, *, istd_pair: str) -> Target:
    return Target(
        label=label,
        mz=258.0,
        rt_min=8.8,
        rt_max=9.8,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=False,
        istd_pair=istd_pair,
    )


def _istd(label: str) -> Target:
    return Target(
        label=label,
        mz=261.0,
        rt_min=8.8,
        rt_max=9.8,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=True,
        istd_pair="",
    )


def _reference_rows(
    sample_name: str,
    *,
    target_rt: str,
    istd_rt: str,
) -> list[dict[str, str]]:
    return [
        _long_row(
            sample_name,
            "5-hmdC",
            role="Analyte",
            istd_pair="d3-5-hmdC",
            rt=target_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
        _long_row(
            sample_name,
            "d3-5-hmdC",
            role="ISTD",
            istd_pair="",
            rt=istd_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
    ]


def _candidate_rows(sample_name: str, *, istd_rt: str) -> list[dict[str, str]]:
    return [
        _long_row(
            sample_name,
            "5-hmdC",
            role="Analyte",
            istd_pair="d3-5-hmdC",
            rt="ND",
            nl="NL_FAIL",
            product_state="not_counted",
            counted="FALSE",
            reason=(
                "decision: not_counted; "
                "support: ms1_coherent, paired_istd_rt_within_1min_support, "
                "paired_area_ratio_support; "
                "not_counted: analyte_nl_fail_requires_policy"
            ),
        ),
        _long_row(
            sample_name,
            "d3-5-hmdC",
            role="ISTD",
            istd_pair="",
            rt=istd_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
    ]


def _long_row(
    sample_name: str,
    target_name: str,
    *,
    role: str,
    istd_pair: str,
    rt: str,
    nl: str,
    product_state: str,
    counted: str,
    reason: str,
) -> dict[str, str]:
    return {
        "SampleName": sample_name,
        "Target": target_name,
        "Role": role,
        "ISTD Pair": istd_pair,
        "RT": rt,
        "NL": nl,
        "Product State": product_state,
        "Counted Detection": counted,
        "Reason": reason,
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _gaussian(
    rt: np.ndarray,
    *,
    center: float,
    width: float,
    scale: float,
) -> np.ndarray:
    return scale * np.exp(-0.5 * ((rt - center) / width) ** 2)
