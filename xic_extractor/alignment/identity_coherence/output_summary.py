from __future__ import annotations

from collections.abc import Mapping, Sequence

from .output_formatting import counter_table
from .output_models import IdentityCoherenceOutputContext, IdentityCoherenceOutputRecord
from .output_summary_model import build_identity_coherence_summary_model


def render_identity_coherence_summary(
    records: Sequence[IdentityCoherenceOutputRecord],
    *,
    context: IdentityCoherenceOutputContext,
    control_rows: Sequence[Mapping[str, object]] = (),
) -> str:
    model = build_identity_coherence_summary_model(
        records,
        context=context,
        control_rows=control_rows,
    )

    lines = [
        "# Untargeted Identity Coherence Summary",
        "",
        "This diagnostic is non-mutating. It reports identity-family evidence only; "
        "it may retrieve RAW/XIC traces for diagnostic identity evidence, but it "
        "does not mutate Backfill or final-matrix outputs and does not perform "
        "final-matrix filtering, background filtering, area correction, "
        "normalization, or statistics.",
        "",
        "## Run Context",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| command | `{context.command}` |",
        f"| mode | `{context.mode}` |",
        f"| input_source | `{context.input_source}` |",
        f"| input_row_count | {model.input_row_count} |",
        f"| control_manifest_path | `{context.control_manifest_path}` |",
        "",
        "## Input Hashes",
        "",
    ]
    lines.extend(_hash_lines(context.input_hashes))
    lines.extend(
        [
            "",
            "## Request Status Counts",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.request_identity_completeness_status_counts,
            "request_identity_completeness_status",
        )
    )
    lines.extend(
        counter_table(
            model.request_candidate_identity_status_counts,
            "request_candidate_identity_status",
        )
    )
    lines.extend(
        [
            "",
            "## Evidence Firewall",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            "| `promotion_used_forbidden_evidence` | `false` |",
            (
                "| `forbidden_evidence_seen_count` | "
                f"{model.forbidden_seen_count} |"
            ),
            "",
            "## Seed Gate Counts",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.seed_gate_class_counts,
            "seed_gate_class",
        )
    )
    lines.extend(
        [
            "",
            "## Decision Counts",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.decision_counts,
            "decision",
        )
    )
    lines.extend(
        [
            "",
            "## Tier Support Counts",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| tier1_fragment_confirmed_sample_count | {model.tier1_count} |",
            f"| tier2_shape_supported_sample_count | {model.tier2_count} |",
            (
                "| tier2_seed_shape_fallback_sample_count | "
                f"{model.tier2_fallback_count} |"
            ),
            f"| tier3_width_only_sample_count | {model.tier3_count} |",
            "",
            "## RT-Only Candidate Counts",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.rt_only_cell_identity_tier_counts,
            "rt_only_cell_identity_tier",
        )
    )
    lines.extend(
        [
            "",
            "## Shape And Width Review",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.shape_reference_basis_counts,
            "shape_reference_basis",
        )
    )
    lines.extend(
        counter_table(
            model.width_status_counts,
            "width_status",
        )
    )
    lines.extend(
        [
            "",
            "## Per-Sample Evidence Coverage",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| assessed_non_seed_cell_count | {model.assessed_non_seed_cell_count} |",
            (
                "| missing_shape_basis_count | "
                f"{model.missing_shape_basis_count} |"
            ),
            (
                "| missing_width_basis_count | "
                f"{model.missing_width_basis_count} |"
            ),
            "",
            "## Infrastructure And Data Quality",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            (
                "| infrastructure_blocked_sample_count | "
                f"{model.infrastructure_blocked_count} |"
            ),
            f"| data_quality_reject_sample_count | {model.data_quality_reject_count} |",
            "",
            "## Threshold Count And Fraction Summaries",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| min_total_coherent_samples | {model.min_total_coherent_samples} |",
            (
                "| min_non_seed_coherent_samples | "
                f"{model.min_non_seed_coherent_samples} |"
            ),
            (
                "| min_non_seed_tier12_identity_samples | "
                f"{model.min_non_seed_tier12_identity_samples} |"
            ),
            "",
            "## Weak Basis Counts",
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.weak_basis_reason_counts,
            "weak_basis_reason",
        )
    )
    lines.extend(
        [
            "",
            "## Identity Controls",
            "",
            (
                "Control fields validate identity diagnostic behavior only; they "
                "do not promote identities or filter the final matrix."
            ),
            "",
        ]
    )
    lines.extend(
        counter_table(
            model.control_type_counts,
            "control_type",
        )
    )
    lines.extend(
        counter_table(
            model.control_status_counts,
            "control_status",
        )
    )
    lines.extend(
        counter_table(
            model.control_pass_counts,
            "control_pass",
        )
    )
    lines.extend(
        counter_table(
            model.positive_control_mapping_status_counts,
            "positive_control_mapping_status",
        )
    )
    lines.extend(
        counter_table(
            model.decoy_generation_method_counts,
            "decoy_generation_method",
        )
    )
    lines.extend(
        counter_table(
            model.control_failure_reason_counts,
            "control_failure_reason",
        )
    )
    lines.extend(
        [
            "| Metric | Value |",
            "| --- | ---: |",
            (
                "| positive_control_pass_fraction | "
                f"{model.positive_control_pass_fraction} |"
            ),
            (
                "| decoy_correctly_rejected_count | "
                f"{model.decoy_correctly_rejected_count} |"
            ),
            "",
            "",
            "## Engineering Go / No-Go",
            "",
        ]
    )
    lines.extend(model.engineering_go_no_go_rows)
    lines.extend(
        [
            "## Cost Counters",
            "",
            "| Counter | Value |",
            "| --- | ---: |",
            f"| raw_xic_request_count | {model.raw_xic_request_count} |",
            f"| xic_point_count | {model.xic_point_count} |",
            (
                "| projected_full_cohort_identity_request_count | "
                f"{model.projected_full_cohort_identity_request_count} |"
            ),
            "",
            "## Writer Contract Checks",
            "",
            "| Check | Result |",
            "| --- | --- |",
            "| forbidden_evidence_used | enforced: writer raises before emission |",
            "| schema_projection | Proceed when TSV headers match schema constants |",
            (
                "| controls | evaluated rows are rendered; identity decisions "
                "remain immutable |"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _hash_lines(input_hashes: tuple[tuple[str, str], ...]) -> list[str]:
    if not input_hashes:
        return [
            "| Input | Hash |",
            "| --- | --- |",
            "| `not_provided` | `not_provided` |",
        ]
    return [
        "| Input | Hash |",
        "| --- | --- |",
        *[f"| `{name}` | `{digest}` |" for name, digest in input_hashes],
    ]
