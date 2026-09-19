"""Write shared peak identity explanation and shadow-label diagnostics."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.alignment.shared_peak_identity_explanation import (
    activation_contract,
    candidate_ms2_pattern,
    hypothesis_consistency,
    machine_evidence_support,
    matrix_rt_drift_policy,
    mode_hypothesis_assignment,
    ms1_pattern_coherence,
    peak_hypothesis_selection,
    rt_mode_evidence,
)
from xic_extractor.alignment.shared_peak_identity_explanation.assembler import (
    assemble_evidence_vectors,
)
from xic_extractor.alignment.shared_peak_identity_explanation.blast_radius import (
    CELLS_REQUIRED_FIELDS,
    OPTIONAL_ARTIFACT_ROLES,
    REVIEW_REQUIRED_FIELDS,
    build_blast_radius_manifest,
    build_blast_radius_summary,
    build_class_profiles,
    preflight_tsv_artifact,
)
from xic_extractor.alignment.shared_peak_identity_explanation.classifier import (
    build_slice0_run_facts,
    build_slice1_run_facts,
    classify_explanations,
)
from xic_extractor.alignment.shared_peak_identity_explanation.machine_artifacts import (
    MachineMatch,
    load_machine_matches,
)
from xic_extractor.alignment.shared_peak_identity_explanation.oracle import (
    load_manual_oracle,
    oracle_rows_as_dicts,
)
from xic_extractor.alignment.shared_peak_identity_explanation.shadow_labels import (
    build_shadow_alignment_summary,
    build_shadow_label_rows,
    build_v2_readiness,
)
from xic_extractor.alignment.shared_peak_identity_explanation.writers import (
    write_activation_outputs,
    write_slice0_outputs,
    write_slice1_outputs,
    write_v2_outputs,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_explanation(**vars(args))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def run_explanation(
    *,
    manual_oracle_tsv: Path,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    output_dir: Path,
    candidate_gate_tsv: Path | None = None,
    enable_blast_radius: bool = False,
    blast_radius_preflight_only: bool = False,
    blast_radius_sample_row_limit: int = 1000,
    blast_radius_fixture_cohort_run: Path | None = None,
    blast_radius_full_cohort_run: Path | None = None,
    expected_blast_radius_manifest: Path | None = None,
    optional_blast_radius_artifacts: Sequence[str] | None = None,
    enable_shadow_label_alignment: bool = False,
    cwt_shape_evidence_tsv: Path | None = None,
    tier2_trace_evidence_tsv: Path | None = None,
    candidate_ms2_pattern_evidence_tsv: Path | None = None,
    candidate_ms2_pattern_batch_index: Path | None = None,
    candidate_ms2_pattern_raw_dll_dir: Path | None = None,
    ms1_pattern_coherence_evidence_tsv: Path | None = None,
    qc_ms1_pattern_reference_evidence_tsv: Path | None = None,
    sample_negative_evidence_tsv: Path | None = None,
    rt_mode_evidence_tsv: Path | None = None,
    generate_rt_mode_evidence: bool = False,
    rt_mode_evidence_mode_assignment_tsv: Path | None = None,
    rt_mode_evidence_mode_summary_tsv: Path | None = None,
    rt_mode_evidence_family_id: str | None = None,
    rt_mode_evidence_overlay_trace_data_json: Sequence[Path] | None = None,
    peak_hypothesis_selection_tsv: Path | None = None,
    generate_peak_hypothesis_selection: bool = False,
    generate_mode_hypothesis_assignment: bool = False,
    generate_hypothesis_consistency: bool = False,
    generate_ms1_pattern_coherence_evidence: bool = False,
    matrix_rt_drift_policy_tsv: Path | None = None,
    generate_matrix_rt_drift_policy: bool = False,
    matrix_rt_drift_policy_owner_edge_tsv: Path | None = None,
    matrix_rt_drift_policy_rt_normalization_families_tsv: Path | None = None,
    matrix_rt_drift_policy_targeted_istd_summary_tsv: Path | None = None,
    matrix_rt_drift_policy_rt_normalization_leave_one_out_tsv: Path | None = None,
    matrix_rt_drift_policy_istd_rt_trend_tsv: Path | None = None,
    matrix_rt_drift_policy_istd_phase_summary_tsv: Path | None = None,
    ms1_pattern_coherence_overlay_trace_data_json: Sequence[Path] | None = None,
    enable_product_activation_contract: bool = False,
    activation_must_not_regress_status: str = "not_assessed",
    activation_must_not_regress_tsv: Path | None = None,
) -> Mapping[str, Path | str]:
    optional_artifacts = _parse_optional_blast_radius_artifacts(
        optional_blast_radius_artifacts
    )
    if enable_blast_radius and (
        blast_radius_fixture_cohort_run is None or blast_radius_full_cohort_run is None
    ):
        raise ValueError(
            "--enable-blast-radius requires --blast-radius-fixture-cohort-run and "
            "--blast-radius-full-cohort-run"
        )
    if blast_radius_preflight_only and not enable_blast_radius:
        raise ValueError("--blast-radius-preflight-only requires --enable-blast-radius")
    if blast_radius_sample_row_limit < 0:
        raise ValueError("--blast-radius-sample-row-limit must be non-negative")
    if candidate_ms2_pattern_evidence_tsv and candidate_ms2_pattern_batch_index:
        raise ValueError(
            "use either --candidate-ms2-pattern-evidence-tsv or "
            "--candidate-ms2-pattern-batch-index, not both"
        )
    if candidate_ms2_pattern_batch_index and not enable_shadow_label_alignment:
        raise ValueError(
            "--candidate-ms2-pattern-batch-index requires "
            "--enable-shadow-label-alignment"
        )
    if ms1_pattern_coherence_evidence_tsv and generate_ms1_pattern_coherence_evidence:
        raise ValueError(
            "use either --ms1-pattern-coherence-evidence-tsv or "
            "--generate-ms1-pattern-coherence-evidence, not both"
        )
    if generate_ms1_pattern_coherence_evidence and not enable_shadow_label_alignment:
        raise ValueError(
            "--generate-ms1-pattern-coherence-evidence requires "
            "--enable-shadow-label-alignment"
        )
    rt_mode_evidence_generation_requested = (
        generate_rt_mode_evidence
        or rt_mode_evidence_mode_assignment_tsv is not None
        or rt_mode_evidence_mode_summary_tsv is not None
        or rt_mode_evidence_family_id is not None
        or bool(rt_mode_evidence_overlay_trace_data_json)
    )
    if rt_mode_evidence_tsv and rt_mode_evidence_generation_requested:
        raise ValueError(
            "use either --rt-mode-evidence-tsv or RT mode evidence producer "
            "inputs, not both"
        )
    if rt_mode_evidence_generation_requested and not enable_shadow_label_alignment:
        raise ValueError(
            "RT mode evidence producer inputs require --enable-shadow-label-alignment"
        )
    if rt_mode_evidence_generation_requested and (
        rt_mode_evidence_mode_assignment_tsv is None
        and not rt_mode_evidence_overlay_trace_data_json
    ):
        raise ValueError(
            "RT mode evidence generation requires "
            "--rt-mode-evidence-mode-assignment-tsv or "
            "--rt-mode-evidence-overlay-trace-data-json"
        )
    if rt_mode_evidence_mode_assignment_tsv is None and (
        rt_mode_evidence_mode_summary_tsv is not None
        or rt_mode_evidence_family_id is not None
    ):
        raise ValueError(
            "RT mode summary/family inputs require "
            "--rt-mode-evidence-mode-assignment-tsv"
        )
    peak_selection_generation_count = int(generate_peak_hypothesis_selection) + int(
        generate_mode_hypothesis_assignment
    )
    if peak_hypothesis_selection_tsv and peak_selection_generation_count:
        raise ValueError(
            "use either --peak-hypothesis-selection-tsv or "
            "a PeakHypothesis selection producer, not both"
        )
    if peak_selection_generation_count > 1:
        raise ValueError(
            "use either --generate-peak-hypothesis-selection or "
            "--generate-mode-hypothesis-assignment, not both"
        )
    if peak_selection_generation_count and not enable_shadow_label_alignment:
        raise ValueError(
            "PeakHypothesis selection producer inputs require "
            "--enable-shadow-label-alignment"
        )
    if peak_selection_generation_count and not (
        rt_mode_evidence_tsv or rt_mode_evidence_generation_requested
    ):
        raise ValueError(
            "PeakHypothesis selection producers require RT mode evidence from "
            "--rt-mode-evidence-tsv or RT mode evidence producer inputs"
        )
    if generate_hypothesis_consistency and not enable_shadow_label_alignment:
        raise ValueError(
            "--generate-hypothesis-consistency requires --enable-shadow-label-alignment"
        )
    if generate_hypothesis_consistency and not (
        peak_hypothesis_selection_tsv or peak_selection_generation_count
    ):
        raise ValueError(
            "--generate-hypothesis-consistency requires "
            "--peak-hypothesis-selection-tsv or "
            "a PeakHypothesis selection producer"
        )
    if enable_product_activation_contract and not enable_shadow_label_alignment:
        raise ValueError(
            "--enable-product-activation-contract requires "
            "--enable-shadow-label-alignment"
        )
    if activation_must_not_regress_tsv and (
        activation_must_not_regress_status != "not_assessed"
    ):
        raise ValueError(
            "use either --activation-must-not-regress-tsv or "
            "--activation-must-not-regress-status, not both"
        )
    if candidate_ms2_pattern_raw_dll_dir and not candidate_ms2_pattern_batch_index:
        raise ValueError(
            "--candidate-ms2-pattern-raw-dll-dir requires "
            "--candidate-ms2-pattern-batch-index"
        )
    if ms1_pattern_coherence_overlay_trace_data_json and (
        not generate_ms1_pattern_coherence_evidence
    ):
        raise ValueError(
            "--ms1-pattern-coherence-overlay-trace-data-json requires "
            "--generate-ms1-pattern-coherence-evidence"
        )
    matrix_rt_drift_policy_generation_requested = (
        generate_matrix_rt_drift_policy
        or matrix_rt_drift_policy_owner_edge_tsv is not None
        or matrix_rt_drift_policy_rt_normalization_families_tsv is not None
        or matrix_rt_drift_policy_targeted_istd_summary_tsv is not None
        or matrix_rt_drift_policy_rt_normalization_leave_one_out_tsv is not None
        or matrix_rt_drift_policy_istd_rt_trend_tsv is not None
        or matrix_rt_drift_policy_istd_phase_summary_tsv is not None
    )
    if (matrix_rt_drift_policy_targeted_istd_summary_tsv is None) != (
        matrix_rt_drift_policy_rt_normalization_leave_one_out_tsv is None
    ):
        raise ValueError(
            "targeted ISTD anchor-local trend matrix RT drift evidence requires "
            "both --matrix-rt-drift-policy-targeted-istd-summary-tsv and "
            "--matrix-rt-drift-policy-rt-normalization-leave-one-out-tsv"
        )
    if matrix_rt_drift_policy_tsv and matrix_rt_drift_policy_generation_requested:
        raise ValueError(
            "use either --matrix-rt-drift-policy-tsv or matrix RT drift "
            "producer inputs, not both"
        )
    if (
        matrix_rt_drift_policy_generation_requested
        and not enable_shadow_label_alignment
    ):
        raise ValueError(
            "matrix RT drift policy producer inputs require "
            "--enable-shadow-label-alignment"
        )
    if generate_mode_hypothesis_assignment:
        if not (
            candidate_ms2_pattern_evidence_tsv
            or candidate_ms2_pattern_batch_index is not None
        ):
            raise ValueError(
                "--generate-mode-hypothesis-assignment requires candidate MS2 "
                "tag/opportunity evidence"
            )
        if not (
            ms1_pattern_coherence_evidence_tsv
            or generate_ms1_pattern_coherence_evidence
        ):
            raise ValueError(
                "--generate-mode-hypothesis-assignment requires MS1 pattern "
                "coherence evidence"
            )
        if not (
            matrix_rt_drift_policy_tsv or matrix_rt_drift_policy_generation_requested
        ):
            raise ValueError(
                "--generate-mode-hypothesis-assignment requires matrix RT drift "
                "policy evidence"
            )
    if blast_radius_preflight_only:
        if (
            blast_radius_fixture_cohort_run is None
            or blast_radius_full_cohort_run is None
        ):
            raise AssertionError("blast-radius run paths were validated above")
        return _preflight_blast_radius_inputs(
            fixture_cohort_run_dir=blast_radius_fixture_cohort_run,
            full_cohort_run_dir=blast_radius_full_cohort_run,
            sample_row_limit=blast_radius_sample_row_limit,
        )

    oracle_rows = load_manual_oracle(manual_oracle_tsv)
    matches = load_machine_matches(
        oracle_rows=oracle_rows,
        alignment_review_tsv=alignment_review_tsv,
        alignment_cells_tsv=alignment_cells_tsv,
        candidate_gate_tsv=candidate_gate_tsv,
    )
    cwt_shape_evidence = machine_evidence_support.load_cwt_shape_evidence(
        cwt_shape_evidence_tsv
    )
    tier2_trace_evidence = machine_evidence_support.load_tier2_trace_evidence(
        tier2_trace_evidence_tsv
    )
    generated_evidence_outputs: dict[str, Path] = {}
    if candidate_ms2_pattern_batch_index is not None:
        generated_candidate_ms2_path = (
            output_dir / "shared_peak_identity_candidate_ms2_pattern_evidence.tsv"
        )
        candidate_ms2_pattern.write_candidate_ms2_pattern_rows(
            generated_candidate_ms2_path,
            candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
                alignment_cells_tsv=alignment_cells_tsv,
                alignment_review_tsv=alignment_review_tsv,
                discovery_batch_index_csv=candidate_ms2_pattern_batch_index,
                raw_dll_dir=candidate_ms2_pattern_raw_dll_dir,
                oracle_keys=(
                    (row.feature_family_id, row.sample_id)
                    for row in oracle_rows
                    if not row.is_sentinel
                ),
            ),
        )
        candidate_ms2_pattern_evidence_tsv = generated_candidate_ms2_path
        generated_evidence_outputs["candidate_ms2_pattern_evidence"] = (
            generated_candidate_ms2_path
        )
    if rt_mode_evidence_generation_requested:
        generated_rt_mode_path = (
            output_dir / "shared_peak_identity_rt_mode_evidence.tsv"
        )
        rt_mode_row_groups = []
        if rt_mode_evidence_mode_assignment_tsv is not None:
            rt_mode_row_groups.append(
                rt_mode_evidence.build_rt_mode_evidence_rows(
                    mode_assignment_tsv=rt_mode_evidence_mode_assignment_tsv,
                    mode_summary_tsv=rt_mode_evidence_mode_summary_tsv,
                    feature_family_id=rt_mode_evidence_family_id,
                    candidate_ms2_pattern_evidence_tsv=(
                        candidate_ms2_pattern_evidence_tsv
                    ),
                    oracle_keys=(
                        (row.feature_family_id, row.sample_id)
                        for row in oracle_rows
                        if not row.is_sentinel
                    ),
                )
            )
        if rt_mode_evidence_overlay_trace_data_json:
            rt_mode_row_groups.append(
                rt_mode_evidence.build_rt_mode_evidence_rows_from_overlay_trace_data(
                    overlay_trace_data_jsons=rt_mode_evidence_overlay_trace_data_json,
                    candidate_ms2_pattern_evidence_tsv=candidate_ms2_pattern_evidence_tsv,
                    oracle_keys=(
                        (row.feature_family_id, row.sample_id)
                        for row in oracle_rows
                        if not row.is_sentinel
                    ),
                )
            )
        rt_mode_evidence.write_rt_mode_evidence_rows(
            generated_rt_mode_path,
            rt_mode_evidence.merge_rt_mode_evidence_rows(*rt_mode_row_groups),
        )
        rt_mode_evidence_tsv = generated_rt_mode_path
        generated_evidence_outputs["rt_mode_evidence"] = generated_rt_mode_path
    if matrix_rt_drift_policy_generation_requested:
        generated_matrix_rt_drift_path = (
            output_dir / "shared_peak_identity_matrix_rt_drift_policy.tsv"
        )
        matrix_rt_drift_policy.write_matrix_rt_drift_policy_rows(
            generated_matrix_rt_drift_path,
            matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
                alignment_cells_tsv=alignment_cells_tsv,
                alignment_review_tsv=alignment_review_tsv,
                owner_edge_evidence_tsv=matrix_rt_drift_policy_owner_edge_tsv,
                rt_normalization_families_tsv=(
                    matrix_rt_drift_policy_rt_normalization_families_tsv
                ),
                targeted_istd_benchmark_summary_tsv=(
                    matrix_rt_drift_policy_targeted_istd_summary_tsv
                ),
                rt_normalization_leave_one_anchor_out_tsv=(
                    matrix_rt_drift_policy_rt_normalization_leave_one_out_tsv
                ),
                istd_rt_trend_tsv=matrix_rt_drift_policy_istd_rt_trend_tsv,
                istd_phase_summary_tsv=(matrix_rt_drift_policy_istd_phase_summary_tsv),
                oracle_keys=(
                    (row.feature_family_id, row.sample_id)
                    for row in oracle_rows
                    if not row.is_sentinel
                ),
            ),
        )
        matrix_rt_drift_policy_tsv = generated_matrix_rt_drift_path
        generated_evidence_outputs["matrix_rt_drift_policy"] = (
            generated_matrix_rt_drift_path
        )
    if generate_ms1_pattern_coherence_evidence:
        generated_ms1_pattern_path = (
            output_dir / "shared_peak_identity_ms1_pattern_coherence_evidence.tsv"
        )
        ms1_pattern_coherence.write_ms1_pattern_coherence_rows(
            generated_ms1_pattern_path,
            ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
                alignment_cells_tsv=alignment_cells_tsv,
                matrix_rt_drift_policy_tsv=matrix_rt_drift_policy_tsv,
                family_ms1_overlay_trace_data_jsons=(
                    ms1_pattern_coherence_overlay_trace_data_json or ()
                ),
                oracle_keys=(
                    (row.feature_family_id, row.sample_id)
                    for row in oracle_rows
                    if not row.is_sentinel
                ),
            ),
        )
        ms1_pattern_coherence_evidence_tsv = generated_ms1_pattern_path
        generated_evidence_outputs["ms1_pattern_coherence_evidence"] = (
            generated_ms1_pattern_path
        )
    if generate_peak_hypothesis_selection or generate_mode_hypothesis_assignment:
        if rt_mode_evidence_tsv is None:
            raise AssertionError("RT mode evidence input was validated above")
        generated_peak_hypothesis_path = (
            output_dir / "shared_peak_identity_peak_hypothesis_selection.tsv"
        )
        oracle_keys = (
            (row.feature_family_id, row.sample_id)
            for row in oracle_rows
            if not row.is_sentinel
        )
        if generate_mode_hypothesis_assignment:
            mode_hypothesis_assignment.write_mode_hypothesis_assignment_rows(
                generated_peak_hypothesis_path,
                mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
                    rt_mode_rows=mode_hypothesis_assignment.load_rt_mode_rows(
                        rt_mode_evidence_tsv
                    ),
                    candidate_ms2_pattern_rows=(
                        mode_hypothesis_assignment.load_candidate_ms2_pattern_rows(
                            candidate_ms2_pattern_evidence_tsv
                        )
                    ),
                    ms1_pattern_coherence_rows=(
                        mode_hypothesis_assignment.load_ms1_pattern_coherence_rows(
                            ms1_pattern_coherence_evidence_tsv
                        )
                    ),
                    qc_ms1_pattern_reference_rows=(
                        mode_hypothesis_assignment.load_qc_ms1_pattern_reference_rows(
                            qc_ms1_pattern_reference_evidence_tsv
                        )
                    ),
                    matrix_rt_drift_policy_rows=(
                        mode_hypothesis_assignment.load_matrix_rt_drift_policy_rows(
                            matrix_rt_drift_policy_tsv
                        )
                    ),
                    oracle_keys=oracle_keys,
                ),
            )
        else:
            peak_hypothesis_selection.write_peak_hypothesis_selection_rows(
                generated_peak_hypothesis_path,
                peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
                    rt_mode_rows=peak_hypothesis_selection.load_rt_mode_rows(
                        rt_mode_evidence_tsv
                    ),
                    oracle_keys=oracle_keys,
                ),
            )
        peak_hypothesis_selection_tsv = generated_peak_hypothesis_path
        generated_evidence_outputs["peak_hypothesis_selection"] = (
            generated_peak_hypothesis_path
        )
    candidate_ms2_pattern_evidence = (
        machine_evidence_support.load_candidate_ms2_pattern_evidence(
            candidate_ms2_pattern_evidence_tsv
        )
    )
    ms1_pattern_coherence_evidence = (
        machine_evidence_support.load_ms1_pattern_coherence_evidence(
            ms1_pattern_coherence_evidence_tsv
        )
    )
    qc_ms1_pattern_reference_evidence = (
        machine_evidence_support.load_qc_ms1_pattern_reference_evidence(
            qc_ms1_pattern_reference_evidence_tsv
        )
    )
    matrix_rt_drift_policy_evidence = (
        machine_evidence_support.load_matrix_rt_drift_policy_evidence(
            matrix_rt_drift_policy_tsv
        )
    )
    sample_negative_evidence = machine_evidence_support.load_sample_negative_evidence(
        sample_negative_evidence_tsv
    )
    rt_mode_evidence_rows = machine_evidence_support.load_rt_mode_evidence(
        rt_mode_evidence_tsv
    )
    peak_hypothesis_selection_rows = (
        machine_evidence_support.load_peak_hypothesis_selection(
            peak_hypothesis_selection_tsv
        )
    )
    if generate_hypothesis_consistency:
        generated_hypothesis_consistency_path = (
            output_dir / "shared_peak_identity_hypothesis_consistency.tsv"
        )
        generated_hypothesis_consistency_summary_path = (
            output_dir / "shared_peak_identity_hypothesis_consistency_summary.tsv"
        )
        hypothesis_consistency_rows = (
            hypothesis_consistency.build_hypothesis_consistency_rows(
                peak_hypothesis_selection=peak_hypothesis_selection_rows,
                ms1_pattern_coherence_evidence=ms1_pattern_coherence_evidence,
                qc_ms1_pattern_reference_evidence=qc_ms1_pattern_reference_evidence,
                matrix_rt_drift_policy_evidence=matrix_rt_drift_policy_evidence,
                candidate_ms2_pattern_evidence=candidate_ms2_pattern_evidence,
            )
        )
        hypothesis_consistency.write_hypothesis_consistency_rows(
            generated_hypothesis_consistency_path,
            hypothesis_consistency_rows,
        )
        hypothesis_consistency.write_hypothesis_consistency_summary(
            generated_hypothesis_consistency_summary_path,
            hypothesis_consistency.build_hypothesis_consistency_summary(
                hypothesis_consistency_rows,
            ),
        )
        generated_evidence_outputs["hypothesis_consistency"] = (
            generated_hypothesis_consistency_path
        )
        generated_evidence_outputs["hypothesis_consistency_summary"] = (
            generated_hypothesis_consistency_summary_path
        )
    must_not_regress_expectations = (
        activation_contract.load_must_not_regress_expectations(
            activation_must_not_regress_tsv
        )
        if activation_must_not_regress_tsv is not None
        else ()
    )
    evidence_rows = assemble_evidence_vectors(oracle_rows, matches)
    explanations = classify_explanations(oracle_rows, evidence_rows)
    run_facts = build_slice0_run_facts(
        explanations,
        durable_oracle_path=manual_oracle_tsv,
    )
    slice0_outputs = write_slice0_outputs(
        output_dir=output_dir,
        durable_oracle_path=manual_oracle_tsv,
        oracle_rows=oracle_rows_as_dicts(oracle_rows),
        evidence_rows=evidence_rows,
        explanation_rows=explanations,
        run_facts=run_facts,
    )
    outputs = slice0_outputs
    summary_rows: Sequence[Mapping[str, str]] = ()
    if enable_blast_radius:
        if (
            blast_radius_fixture_cohort_run is None
            or blast_radius_full_cohort_run is None
        ):
            raise AssertionError("blast-radius run paths were validated above")
        manifest_rows = build_blast_radius_manifest(
            manual_oracle_tsv=manual_oracle_tsv,
            slice0_explanations_tsv=slice0_outputs["explanations"],
            slice0_evidence_vectors_tsv=slice0_outputs["evidence_vectors"],
            fixture_cohort_run_dir=blast_radius_fixture_cohort_run,
            full_cohort_run_dir=blast_radius_full_cohort_run,
            expected_manifest_tsv=expected_blast_radius_manifest,
            optional_artifacts=optional_artifacts,
        )
        summary_rows = build_blast_radius_summary(
            class_profiles=build_class_profiles(explanations, evidence_rows),
            manifest_rows=manifest_rows,
            fixture_cohort_run_dir=blast_radius_fixture_cohort_run,
            full_cohort_run_dir=blast_radius_full_cohort_run,
        )
        run_facts = build_slice1_run_facts(
            slice0_run_facts=run_facts,
            manifest_rows=manifest_rows,
            summary_rows=summary_rows,
        )
        outputs = write_slice1_outputs(
            output_dir=output_dir,
            slice0_outputs=slice0_outputs,
            manifest_rows=manifest_rows,
            summary_rows=summary_rows,
            run_facts=run_facts,
        )
    if enable_shadow_label_alignment:
        return _write_v2_from_current_outputs(
            output_dir=output_dir,
            prior_outputs=outputs,
            explanations=explanations,
            run_facts=run_facts,
            machine_matches=matches,
            cwt_shape_evidence=cwt_shape_evidence,
            tier2_trace_evidence=tier2_trace_evidence,
            candidate_ms2_pattern_evidence=candidate_ms2_pattern_evidence,
            ms1_pattern_coherence_evidence=ms1_pattern_coherence_evidence,
            qc_ms1_pattern_reference_evidence=qc_ms1_pattern_reference_evidence,
            matrix_rt_drift_policy_evidence=matrix_rt_drift_policy_evidence,
            sample_negative_evidence=sample_negative_evidence,
            rt_mode_evidence=rt_mode_evidence_rows,
            peak_hypothesis_selection=peak_hypothesis_selection_rows,
            extra_outputs=generated_evidence_outputs,
            blast_radius_summary_rows=summary_rows,
            enable_product_activation_contract=enable_product_activation_contract,
            activation_must_not_regress_status=activation_must_not_regress_status,
            activation_must_not_regress_expectations=must_not_regress_expectations,
        )
    return outputs


def _write_v2_from_current_outputs(
    *,
    output_dir: Path,
    prior_outputs: Mapping[str, Path],
    explanations: Sequence[Mapping[str, str]],
    run_facts: Mapping[str, str],
    machine_matches: Mapping[str, Sequence[MachineMatch]],
    cwt_shape_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    tier2_trace_evidence: Mapping[str, Mapping[str, str]],
    candidate_ms2_pattern_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    ms1_pattern_coherence_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    qc_ms1_pattern_reference_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    matrix_rt_drift_policy_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    sample_negative_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    rt_mode_evidence: Mapping[tuple[str, str], Mapping[str, str]],
    peak_hypothesis_selection: Mapping[tuple[str, str], Mapping[str, str]],
    extra_outputs: Mapping[str, Path] | None = None,
    blast_radius_summary_rows: Sequence[Mapping[str, str]] = (),
    enable_product_activation_contract: bool = False,
    activation_must_not_regress_status: str = "not_assessed",
    activation_must_not_regress_expectations: Sequence[Mapping[str, str]] = (),
) -> Mapping[str, Path | str]:
    shadow_rows = build_shadow_label_rows(explanations)
    shadow_summary_rows = build_shadow_alignment_summary(shadow_rows)
    machine_evidence_support_rows = (
        machine_evidence_support.build_machine_evidence_support_rows(
            explanations=explanations,
            shadow_rows=shadow_rows,
            machine_matches=machine_matches,
            cwt_shape_evidence=cwt_shape_evidence,
            tier2_trace_evidence=tier2_trace_evidence,
            candidate_ms2_pattern_evidence=candidate_ms2_pattern_evidence,
            ms1_pattern_coherence_evidence=ms1_pattern_coherence_evidence,
            qc_ms1_pattern_reference_evidence=qc_ms1_pattern_reference_evidence,
            matrix_rt_drift_policy_evidence=matrix_rt_drift_policy_evidence,
            sample_negative_evidence=sample_negative_evidence,
            rt_mode_evidence=rt_mode_evidence,
            peak_hypothesis_selection=peak_hypothesis_selection,
        )
    )
    readiness_row = build_v2_readiness(
        run_facts=run_facts,
        shadow_rows=shadow_rows,
        machine_evidence_support_rows=machine_evidence_support_rows,
    )
    outputs = write_v2_outputs(
        output_dir=output_dir,
        prior_outputs={**dict(prior_outputs), **dict(extra_outputs or {})},
        shadow_rows=shadow_rows,
        summary_rows=shadow_summary_rows,
        readiness_row=readiness_row,
        machine_evidence_support_rows=machine_evidence_support_rows,
    )
    if not enable_product_activation_contract:
        return outputs
    activation_rows = activation_contract.build_activation_decision_rows(
        machine_evidence_support_rows,
    )
    assessed_full_cohort_rows, assessed_rows_basis = (
        activation_contract.infer_full_cohort_assessed_rows(blast_radius_summary_rows)
    )
    must_not_regress_basis = "manual_status_flag"
    must_not_regress_failure_reasons: tuple[str, ...] = ()
    if activation_must_not_regress_expectations:
        (
            activation_must_not_regress_status,
            must_not_regress_failure_reasons,
        ) = activation_contract.evaluate_must_not_regress(
            activation_rows,
            activation_must_not_regress_expectations,
        )
        must_not_regress_basis = "activation_must_not_regress_tsv"
    acceptance_row = activation_contract.summarize_activation_acceptance(
        activation_rows,
        blast_radius_current=(
            run_facts.get("blast_radius_assessed") == "present_current"
            and run_facts.get("blast_radius_stale_artifact_count") == "0"
        ),
        assessed_full_cohort_rows=assessed_full_cohort_rows,
        assessed_rows_basis=assessed_rows_basis,
        must_not_regress_status=activation_must_not_regress_status,
        must_not_regress_basis=must_not_regress_basis,
        must_not_regress_failure_reasons=must_not_regress_failure_reasons,
    )
    return write_activation_outputs(
        output_dir=output_dir,
        prior_outputs=outputs,
        activation_rows=activation_rows,
        acceptance_row=acceptance_row,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-oracle-tsv", type=Path, required=True)
    parser.add_argument("--alignment-review-tsv", type=Path, required=True)
    parser.add_argument("--alignment-cells-tsv", type=Path, required=True)
    parser.add_argument("--candidate-gate-tsv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--enable-blast-radius", action="store_true")
    parser.add_argument("--blast-radius-preflight-only", action="store_true")
    parser.add_argument("--blast-radius-sample-row-limit", type=int, default=1000)
    parser.add_argument("--blast-radius-fixture-cohort-run", type=Path)
    parser.add_argument("--blast-radius-full-cohort-run", type=Path)
    parser.add_argument("--expected-blast-radius-manifest", type=Path)
    parser.add_argument("--enable-shadow-label-alignment", action="store_true")
    parser.add_argument("--enable-product-activation-contract", action="store_true")
    parser.add_argument(
        "--activation-must-not-regress-status",
        default="not_assessed",
        choices=("not_assessed", "pass", "fail"),
    )
    parser.add_argument("--activation-must-not-regress-tsv", type=Path)
    parser.add_argument("--cwt-shape-evidence-tsv", type=Path)
    parser.add_argument("--tier2-trace-evidence-tsv", type=Path)
    parser.add_argument("--candidate-ms2-pattern-evidence-tsv", type=Path)
    parser.add_argument("--candidate-ms2-pattern-batch-index", type=Path)
    parser.add_argument("--candidate-ms2-pattern-raw-dll-dir", type=Path)
    parser.add_argument("--ms1-pattern-coherence-evidence-tsv", type=Path)
    parser.add_argument("--qc-ms1-pattern-reference-evidence-tsv", type=Path)
    parser.add_argument("--sample-negative-evidence-tsv", type=Path)
    parser.add_argument("--rt-mode-evidence-tsv", type=Path)
    parser.add_argument("--generate-rt-mode-evidence", action="store_true")
    parser.add_argument("--rt-mode-evidence-mode-assignment-tsv", type=Path)
    parser.add_argument("--rt-mode-evidence-mode-summary-tsv", type=Path)
    parser.add_argument("--rt-mode-evidence-family-id")
    parser.add_argument(
        "--rt-mode-evidence-overlay-trace-data-json",
        action="append",
        type=Path,
    )
    parser.add_argument("--peak-hypothesis-selection-tsv", type=Path)
    parser.add_argument("--generate-peak-hypothesis-selection", action="store_true")
    parser.add_argument(
        "--generate-mode-hypothesis-assignment",
        action="store_true",
        help=(
            "Generate shared_peak_identity_peak_hypothesis_selection.tsv from "
            "typed RT/iRT mode evidence plus candidate MS2 tag/opportunity, "
            "MS1 pattern, optional QC reference, and matrix RT drift sidecars."
        ),
    )
    parser.add_argument("--generate-hypothesis-consistency", action="store_true")
    parser.add_argument(
        "--generate-ms1-pattern-coherence-evidence",
        action="store_true",
    )
    parser.add_argument("--matrix-rt-drift-policy-tsv", type=Path)
    parser.add_argument("--generate-matrix-rt-drift-policy", action="store_true")
    parser.add_argument("--matrix-rt-drift-policy-owner-edge-tsv", type=Path)
    parser.add_argument(
        "--matrix-rt-drift-policy-rt-normalization-families-tsv",
        type=Path,
    )
    parser.add_argument(
        "--matrix-rt-drift-policy-targeted-istd-summary-tsv",
        type=Path,
    )
    parser.add_argument(
        "--matrix-rt-drift-policy-rt-normalization-leave-one-out-tsv",
        type=Path,
    )
    parser.add_argument("--matrix-rt-drift-policy-istd-rt-trend-tsv", type=Path)
    parser.add_argument("--matrix-rt-drift-policy-istd-phase-summary-tsv", type=Path)
    parser.add_argument(
        "--ms1-pattern-coherence-overlay-trace-data-json",
        action="append",
        default=[],
        type=Path,
    )
    parser.add_argument(
        "--optional-blast-radius-artifact",
        dest="optional_blast_radius_artifacts",
        action="append",
        default=[],
        metavar="ROLE=PATH",
    )
    return parser.parse_args(argv)


def _parse_optional_blast_radius_artifacts(
    values: Sequence[str] | None,
) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values or ():
        if "=" not in value:
            raise ValueError("--optional-blast-radius-artifact requires ROLE=PATH")
        role, path_text = value.split("=", 1)
        if not role or not path_text:
            raise ValueError("--optional-blast-radius-artifact requires ROLE=PATH")
        if role in artifacts:
            raise ValueError(f"duplicate optional blast-radius role: {role}")
        if role not in OPTIONAL_ARTIFACT_ROLES:
            raise ValueError(f"unknown optional blast-radius artifact role: {role}")
        artifacts[role] = Path(path_text)
    return artifacts


def _preflight_blast_radius_inputs(
    *,
    fixture_cohort_run_dir: Path,
    full_cohort_run_dir: Path,
    sample_row_limit: int,
) -> dict[str, str]:
    artifacts = {
        "fixture_cohort_alignment_review": (
            fixture_cohort_run_dir / "alignment_review.tsv",
            REVIEW_REQUIRED_FIELDS,
        ),
        "fixture_cohort_alignment_cells": (
            fixture_cohort_run_dir / "alignment_cells.tsv",
            CELLS_REQUIRED_FIELDS,
        ),
        "full_cohort_alignment_review": (
            full_cohort_run_dir / "alignment_review.tsv",
            REVIEW_REQUIRED_FIELDS,
        ),
        "full_cohort_alignment_cells": (
            full_cohort_run_dir / "alignment_cells.tsv",
            CELLS_REQUIRED_FIELDS,
        ),
    }
    results: dict[str, str] = {}
    for artifact_id, (path, required_fields) in artifacts.items():
        inspection = _preflight_one_artifact(
            path,
            required_fields=required_fields,
            sample_row_limit=sample_row_limit,
        )
        for field in (
            "artifact_status",
            "row_count",
            "sample_count",
            "family_count",
            "missing_required_fields",
        ):
            results[f"preflight_{artifact_id}_{field}"] = inspection[field]
    return results


def _preflight_one_artifact(
    path: Path,
    *,
    required_fields: frozenset[str],
    sample_row_limit: int,
) -> Mapping[str, str]:
    if not path.exists():
        return {
            "artifact_status": "missing",
            "row_count": "0",
            "sample_count": "0",
            "family_count": "0",
            "available_required_fields": "",
            "missing_required_fields": ";".join(sorted(required_fields)),
        }
    return preflight_tsv_artifact(
        path,
        required_fields=required_fields,
        sample_row_limit=sample_row_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
