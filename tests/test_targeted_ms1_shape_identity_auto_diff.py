from __future__ import annotations

import csv
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.applications.targeted import limited_identity_workflow as workflow
from xic_extractor.applications.targeted.application import (
    TargetedApplicationExecution,
    build_targeted_application_request,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff import (
    write_targeted_ms1_shape_identity_auto_diff_artifacts,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer import (
    TargetedMs1ShapeIdentitySupportOutputs,
)
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.extraction.targeted_ms1_shape_identity_projection import (
    TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
)
from xic_extractor.output.method_manifest import (
    MethodManifestContext,
    load_method_manifest_for_replay,
    write_method_manifest,
)


def test_auto_diff_artifacts_pass_limited_expected_diff_gate(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    optin_dir = tmp_path / "optin"
    output_dir = tmp_path / "diff"
    support_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    _write_method_gate_inputs(baseline_dir, optin_dir, support_tsv)

    outputs = write_targeted_ms1_shape_identity_auto_diff_artifacts(
        baseline_output_dir=baseline_dir,
        optin_output_dir=optin_dir,
        support_tsv=support_tsv,
        output_dir=output_dir,
    )

    assert outputs.gate_status == "pass"
    assert outputs.expected_diff_row_count == 1
    assert outputs.matrix_diff_cell_count == 2
    assert outputs.expected_diff_summary_tsv.is_file()
    assert outputs.matrix_diff_summary_tsv.is_file()
    gate_rows = _read_tsv(outputs.expected_diff_gate_summary_tsv)
    assert {"metric": "long_changed_rows", "value": "1"} in gate_rows
    assert {"metric": "matrix_changed_cells", "value": "2"} in gate_rows
    assert {"metric": "support_tsv_supported_rows", "value": "1"} in gate_rows


@pytest.mark.parametrize(
    "mode",
    [
        "pass",
        "forbidden_change",
        "cancel_support",
        "cancel_final",
        "cancel_gate",
        "collision",
    ],
)
def test_limited_workflow_publishes_only_gated_completed_outputs(
    tmp_path: Path,
    monkeypatch,
    mode: str,
) -> None:
    """Use the real expected-diff gate and manifest reader; replace only RAW science."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text(
        "key,value\nsignal_processing_window_points,15\n", encoding="utf-8"
    )
    (config_dir / "targets.csv").write_text(
        "label,mz,rt_min,rt_max,ppm_tol\n", encoding="utf-8"
    )
    raw_dir, dll_dir = tmp_path / "raw", tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    config = ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        targeted_ms1_shape_identity_activation_policy="limited_5hmdc_5medc",
    )
    fixtures = tmp_path / "fixtures"
    calls = []
    cancelled = False

    def progress(_done, _total, message):
        nonlocal cancelled
        cancelled = mode == f"cancel_{message.rsplit(' ', 1)[-1]}" or (
            mode == "cancel_gate" and message.endswith("gate")
        )

    class Application:
        def execute_application(self, admitted):
            calls.append(admitted)
            directory = admitted.config.output_csv.parent
            if admitted.output_projection == "acquisition_baseline":
                _write_method_gate_inputs(
                    directory, fixtures / "final", fixtures / "support.tsv"
                )
                _write_wide_csv(
                    directory / "xic_results.csv",
                    {"5-hmdC_RT": "ND", "5-hmdC_Area": "ND", "Other_Area": "0"},
                )
                _write_wide_csv(
                    fixtures / "final" / "xic_results.csv",
                    {"5-hmdC_RT": "9.10", "5-hmdC_Area": "12345.67", "Other_Area": "0"},
                )
            else:
                shutil.copytree(fixtures / "final", directory)
                if mode == "forbidden_change":
                    _write_wide_csv(
                        directory / "xic_results.csv",
                        {
                            "5-hmdC_RT": "9.10",
                            "5-hmdC_Area": "12345.67",
                            "Other_Area": "1",
                        },
                    )
            manifest = write_method_manifest(
                admitted.config,
                list(admitted.targets),
                context=admitted.method_manifest_context,
            )
            return TargetedApplicationExecution(
                active_output=RunOutput(file_results=[], diagnostics=[]),
                published_paths=(admitted.config.output_csv, manifest),
            )

    def support(**kwargs):
        assert kwargs["targets"] == ()  # Admitted targets, not active config_dir.
        path = kwargs["output_tsv"]
        path.parent.mkdir(parents=True)
        shutil.copyfile(fixtures / "support.tsv", path)
        return TargetedMs1ShapeIdentitySupportOutputs(path, 1, 1, 2)

    monkeypatch.setattr(
        workflow, "run_build_targeted_ms1_shape_identity_supports", support
    )
    request = build_targeted_application_request(
        config,
        [],
        progress_callback=progress,
        should_stop=lambda: cancelled,
        method_manifest_context=MethodManifestContext(
            entrypoint="consumer",
            base_dir=tmp_path,
            config_dir=config_dir,
            output_mode="csv_only",
        ),
    )
    root = tmp_path / "run"
    if mode == "collision":
        prior_result = root / "final" / "output" / "xic_results.csv"
        prior_result.parent.mkdir(parents=True)
        prior_result.write_text("previous valid result", encoding="utf-8")
        with pytest.raises(ConfigError, match="output already exists"):
            workflow.execute_limited_identity_workflow(
                request, application=Application(), output_root=root
            )
        assert prior_result.read_text(encoding="utf-8") == "previous valid result"
        assert not calls
        return
    if mode != "pass":
        with pytest.raises(
            InterruptedError if mode.startswith("cancel") else ValueError
        ):
            workflow.execute_limited_identity_workflow(
                request, application=Application(), output_root=root
            )
        assert not (root / "final" / "output").exists()
        return
    result = workflow.execute_limited_identity_workflow(
        request, application=Application(), output_root=root
    )
    assert result.diff_outputs.gate_status == "pass"
    assert result.diff_outputs.expected_diff_row_count == 1
    assert result.diff_outputs.matrix_diff_cell_count == 2
    assert len(calls) == 2
    assert (
        calls[0].model_selection_expected_diff_approvals
        is calls[1].model_selection_expected_diff_approvals
    )
    assert (
        result.final_config.output_csv == root / "final" / "output" / "xic_results.csv"
    )
    assert all(path.is_file() for path in result.final_execution.published_paths)
    assert not (root / "final_unverified" / "output").exists()
    replay = load_method_manifest_for_replay(
        result.final_config.output_csv.with_name("method_manifest.json")
    )
    assert replay.settings_overrides["targeted_ms1_shape_identity_support_tsv"] == str(
        result.support_outputs.evidence_tsv.resolve()
    )
    assert not workflow.requires_limited_identity_workflow(
        replace(
            config,
            targeted_ms1_shape_identity_support_tsv=result.support_outputs.evidence_tsv,
        )
    )


def _write_method_gate_inputs(
    baseline_dir: Path, optin_dir: Path, support_tsv: Path
) -> None:
    _write_long_csv(
        baseline_dir / "xic_results_long.csv",
        [
            _long_row(
                rt="ND",
                area="ND",
                intensity="ND",
                peak_start="ND",
                peak_end="ND",
                peak_width="ND",
                confidence="VERY_LOW",
                product_state="not_counted",
                counted_detection="FALSE",
                reason=(
                    "decision: not_counted; support: paired_area_ratio_support; "
                    "not_counted: analyte_nl_fail_requires_policy"
                ),
                support_reasons="paired_area_ratio_support",
                not_counted_reasons="analyte_nl_fail_requires_policy",
            )
        ],
    )
    _write_long_csv(
        optin_dir / "xic_results_long.csv",
        [
            _long_row(
                rt="9.10",
                area="12345.67",
                intensity="100",
                peak_start="8.90",
                peak_end="9.30",
                peak_width="0.40",
                confidence="MEDIUM",
                product_state="detected_flagged",
                counted_detection="TRUE",
                reason=(
                    "decision: detected_flagged; support: paired_area_ratio_support, "
                    "own_max_same_peak_support"
                ),
                support_reasons=(
                    "paired_area_ratio_support; own_max_same_peak_support"
                ),
                not_counted_reasons="",
            )
        ],
    )
    _write_wide_csv(
        baseline_dir / "xic_results.csv",
        {"5-hmdC_RT": "ND", "5-hmdC_Area": "ND"},
    )
    _write_wide_csv(
        optin_dir / "xic_results.csv",
        {"5-hmdC_RT": "9.10", "5-hmdC_Area": "12345.67"},
    )
    _write_support_tsv(support_tsv)


def _long_row(
    *,
    rt: str,
    area: str,
    intensity: str,
    peak_start: str,
    peak_end: str,
    peak_width: str,
    confidence: str,
    product_state: str,
    counted_detection: str,
    reason: str,
    support_reasons: str,
    not_counted_reasons: str,
) -> dict[str, str]:
    return {
        "SampleName": "SampleA",
        "Target": "5-hmdC",
        "Role": "Analyte",
        "RT": rt,
        "Area": area,
        "Int": intensity,
        "PeakStart": peak_start,
        "PeakEnd": peak_end,
        "PeakWidth": peak_width,
        "Confidence": confidence,
        "NL": "NL_FAIL",
        "Reason": reason,
        "Product State": product_state,
        "Counted Detection": counted_detection,
        "Projection Support Reasons": support_reasons,
        "Projection Not Counted Reasons": not_counted_reasons,
    }


def _write_long_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_wide_csv(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"SampleName": "SampleA", **values}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(row))
        writer.writeheader()
        writer.writerow(row)


def _write_support_tsv(path: Path) -> None:
    row = {
        "schema_version": "targeted_ms1_shape_identity",
        "validation_label": "diagnostic_only",
        "decision_authority": "diagnostic_only_no_product_write",
        "sample_name": "SampleA",
        "target_name": "5-hmdC",
        "target_role": "analyte",
        "paired_istd": "d3-5-hmdC",
        "source_row_id": "SampleA|5-hmdC",
        "candidate_state": "NL_FAIL",
        "reference_source": "unit",
        "candidate_rt_min": "9.10",
        "reference_rt_min": "9.10",
        "candidate_anchor_rt_delta_min": "0",
        "paired_istd_rt_min": "9.00",
        "candidate_pair_rt_delta_min": "0.10",
        "target_window_status": "candidate_inside_target_window",
        "own_max_same_peak_status": "own_max_same_peak_supported",
        "own_max_same_peak_supported": "TRUE",
        "own_max_same_peak_support_reason": "own_max_same_peak_support",
        "own_max_same_peak_similarity": "0.95",
        "own_max_compared_point_count": "31",
        "strongest_peak_rt_min": "9.10",
        "strongest_peak_own_max_ratio": "1.0",
        "strongest_competing_peak_rt_min": "",
        "strongest_competing_peak_own_max_ratio": "",
        "competing_peak_status": "no_competing_peak_observed",
        "reason": "own max same-peak support",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(row)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
