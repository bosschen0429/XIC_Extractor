from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import write_tsv

from .schema import (
    BLAST_RADIUS_MANIFEST_COLUMNS,
    BLAST_RADIUS_SUMMARY_COLUMNS,
    EVIDENCE_VECTOR_COLUMNS,
    EXPLANATION_COLUMNS,
    MACHINE_EVIDENCE_SUPPORT_COLUMNS,
    ORACLE_COLUMNS,
    RUN_FACTS_COLUMNS,
    SHADOW_ALIGNMENT_SUMMARY_COLUMNS,
    SHADOW_LABEL_COLUMNS,
    SHADOW_READINESS_COLUMNS,
    validate_source_row_ids,
)


def write_slice0_outputs(
    *,
    output_dir: Path,
    durable_oracle_path: Path,
    oracle_rows: Sequence[Mapping[str, str]],
    evidence_rows: Sequence[Mapping[str, str]],
    explanation_rows: Sequence[Mapping[str, str]],
    run_facts: Mapping[str, str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    oracle_path = output_dir / "shared_peak_identity_manual_oracle.tsv"
    evidence_path = output_dir / "shared_peak_identity_evidence_vectors.tsv"
    explanations_path = output_dir / "shared_peak_identity_explanations.tsv"
    run_facts_path = output_dir / "shared_peak_identity_run_facts.tsv"
    report_path = output_dir / "shared_peak_identity_explanation_report.md"
    valid_source_row_ids = {
        row["source_row_id"]
        for row in evidence_rows
        if row.get("source_role") != "manual_oracle" and row.get("source_row_id")
    }
    for row in explanation_rows:
        validate_source_row_ids(
            row.get("matched_source_row_ids", ""),
            valid_source_row_ids,
        )
    write_tsv(oracle_path, oracle_rows, ORACLE_COLUMNS, lineterminator="\n")
    write_tsv(
        evidence_path,
        evidence_rows,
        EVIDENCE_VECTOR_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        explanations_path,
        explanation_rows,
        EXPLANATION_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(run_facts_path, [run_facts], RUN_FACTS_COLUMNS, lineterminator="\n")
    report_path.write_text(
        render_report(explanation_rows=explanation_rows, run_facts=run_facts),
        encoding="utf-8",
    )
    return {
        "oracle": oracle_path,
        "evidence_vectors": evidence_path,
        "explanations": explanations_path,
        "run_facts": run_facts_path,
        "report": report_path,
    }


def write_slice1_outputs(
    *,
    output_dir: Path,
    slice0_outputs: Mapping[str, Path],
    manifest_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    run_facts: Mapping[str, str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "shared_peak_identity_blast_radius_manifest.tsv"
    summary_path = output_dir / "shared_peak_identity_blast_radius_summary.tsv"
    run_facts_path = output_dir / "shared_peak_identity_run_facts.tsv"
    report_path = output_dir / "shared_peak_identity_explanation_report.md"
    write_tsv(
        manifest_path,
        manifest_rows,
        BLAST_RADIUS_MANIFEST_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        summary_path,
        summary_rows,
        BLAST_RADIUS_SUMMARY_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(run_facts_path, [run_facts], RUN_FACTS_COLUMNS, lineterminator="\n")
    report_path.write_text(
        render_slice1_report(
            manifest_rows=manifest_rows,
            summary_rows=summary_rows,
            run_facts=run_facts,
        ),
        encoding="utf-8",
    )
    return {
        **dict(slice0_outputs),
        "run_facts": run_facts_path,
        "report": report_path,
        "blast_radius_manifest": manifest_path,
        "blast_radius_summary": summary_path,
    }


def write_shadow_outputs(
    *,
    output_dir: Path,
    prior_outputs: Mapping[str, Path],
    shadow_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    readiness_row: Mapping[str, str],
    machine_evidence_support_rows: Sequence[Mapping[str, str]],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    shadow_path = output_dir / "shared_peak_identity_shadow_labels.tsv"
    summary_path = output_dir / "shared_peak_identity_shadow_alignment_summary.tsv"
    readiness_path = output_dir / "shared_peak_identity_shadow_readiness.tsv"
    support_path = output_dir / "shared_peak_identity_machine_evidence_support.tsv"
    report_path = output_dir / "shared_peak_identity_shadow_report.md"
    write_tsv(
        shadow_path,
        shadow_rows,
        SHADOW_LABEL_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        summary_path,
        summary_rows,
        SHADOW_ALIGNMENT_SUMMARY_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        readiness_path,
        [readiness_row],
        SHADOW_READINESS_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        support_path,
        machine_evidence_support_rows,
        MACHINE_EVIDENCE_SUPPORT_COLUMNS,
        lineterminator="\n",
    )
    report_path.write_text(
        render_shadow_report(
            readiness_row=readiness_row,
            summary_rows=summary_rows,
            shadow_rows=shadow_rows,
            machine_evidence_support_rows=machine_evidence_support_rows,
        ),
        encoding="utf-8",
    )
    return {
        **dict(prior_outputs),
        "shadow_labels": shadow_path,
        "shadow_alignment_summary": summary_path,
        "shadow_readiness": readiness_path,
        "machine_evidence_support": support_path,
        "shadow_report": report_path,
    }


def render_report(
    *,
    explanation_rows: Sequence[Mapping[str, str]],
    run_facts: Mapping[str, str],
) -> str:
    class_counts = Counter(row["evidence_gap_class"] for row in explanation_rows)
    status_counts = Counter(row["explanation_status"] for row in explanation_rows)
    blocked = [
        row
        for row in explanation_rows
        if row["explanation_status"] != "explained"
        or row["evidence_gap_class"] == "unexplained_machine_manual_gap"
    ]
    vocabulary_held = (
        run_facts["seed_rows_explained"] == run_facts["seed_rows_total"]
        and run_facts["seed_rows_unexplained"] == "0"
        and run_facts["seed_rows_inconclusive"] == "0"
        and run_facts["vocabulary_special_casing_detected"] == "FALSE"
    )
    lines = [
        "# Shared Peak Identity Slice 0 Explanation Report",
        "",
        "## Decision Summary",
        "",
        "- readiness_label: `diagnostic_only`",
        f"- slice: `{run_facts['slice']}`",
        f"- vocabulary_held: `{'TRUE' if vocabulary_held else 'FALSE'}`",
        "- seed_rows_explained: "
        f"`{run_facts['seed_rows_explained']}` / "
        f"`{run_facts['seed_rows_total']}`",
        f"- seed_rows_unexplained: `{run_facts['seed_rows_unexplained']}`",
        f"- seed_rows_inconclusive: `{run_facts['seed_rows_inconclusive']}`",
        "- vocabulary_special_casing_detected: "
        f"`{run_facts['vocabulary_special_casing_detected']}`",
        f"- blast_radius_assessed: `{run_facts['blast_radius_assessed']}`",
        "- next_action: `plan_slice1_blast_radius`"
        if vocabulary_held
        else "- next_action: `revise_vocabulary_or_oracle_before_slice1`",
        "",
        "This report summarizes machine-readable TSV facts. "
        "It is not a gating verdict and does not claim production readiness.",
        "",
        "## Top Blocking Rows Or Classes",
        "",
    ]
    if blocked:
        for row in blocked[:10]:
            lines.append(
                f"- `{row['oracle_row_id']}`: {row['evidence_gap_class']} "
                f"({row['explanation_status']})"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Run-Level Readiness Facts",
            "",
        ]
    )
    for key in sorted(run_facts):
        lines.append(f"- `{key}`: `{run_facts[key]}`")
    lines.extend(["", "## Disagreements By Explanation Class", ""])
    for key, value in sorted(class_counts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Explanation Status Counts", ""])
    for key, value in sorted(status_counts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Machine Too Conservative Examples", ""])
    _append_examples(lines, explanation_rows, "machine_too_conservative")
    lines.extend(["", "## Machine Too Permissive Examples", ""])
    _append_examples(lines, explanation_rows, "machine_too_permissive")
    lines.extend(["", "## Shadow Candidates And Non-Goals", ""])
    lines.extend(
        [
            "- Shadow may use these facts for shadow label alignment planning.",
            "- Slice 0 emits no blast-radius manifest or summary.",
            "- Slice 0 does not change selected peaks, backfill rescue, "
            "Tier 2 support, workbooks, or the primary matrix.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_shadow_report(
    *,
    readiness_row: Mapping[str, str],
    summary_rows: Sequence[Mapping[str, str]],
    shadow_rows: Sequence[Mapping[str, str]],
    machine_evidence_support_rows: Sequence[Mapping[str, str]],
) -> str:
    status_counts = Counter(row["shadow_alignment_status"] for row in shadow_rows)
    label_counts = Counter(row["shadow_label"] for row in shadow_rows)
    promotion_gaps = Counter(
        gap
        for row in shadow_rows
        for gap in row.get("required_evidence_to_promote", "").split(";")
        if gap and gap != "none"
    )
    support_counts = Counter(
        row["evidence_support_status"] for row in machine_evidence_support_rows
    )
    basis_counts = Counter(
        basis
        for row in machine_evidence_support_rows
        for basis in (
            row.get("rt_basis_status", ""),
            row.get("shape_basis_status", ""),
            row.get("pattern_basis_status", ""),
            row.get("opportunity_basis_status", ""),
            row.get("scope_basis_status", ""),
        )
        if basis
    )
    lines = [
        "# Shared Peak Identity Shadow Label Alignment Report",
        "",
        "## Decision Summary",
        "",
        "- readiness_label: `diagnostic_only`",
        f"- shadow_mode: `{readiness_row['shadow_mode']}`",
        f"- shadow_gate_status: `{readiness_row['shadow_gate_status']}`",
        "- machine_only_labeler_ready: "
        f"`{readiness_row['machine_only_labeler_ready']}`",
        f"- alignment_fraction: `{readiness_row['alignment_fraction']}`",
        f"- blast_radius_assessed: `{readiness_row['blast_radius_assessed']}`",
        f"- max_overfit_risk: `{readiness_row['max_overfit_risk']}`",
        f"- machine_evidence_basis: `{readiness_row['machine_evidence_basis']}`",
        "- machine_observed_partial_rows: "
        f"`{readiness_row['machine_observed_partial_rows']}`",
        "- machine_observed_conflict_rows: "
        f"`{readiness_row['machine_observed_conflict_rows']}`",
        "- machine_evidence_coverage_fraction: "
        f"`{readiness_row['machine_evidence_coverage_fraction']}`",
        f"- next_action: `{readiness_row['next_action']}`",
        "",
        readiness_row["clear_answer"],
        "",
        "This is a shadow diagnostic. It does not mutate selected peaks, "
        "backfill, workbooks, matrices, or production labels.",
        "",
        "## Alignment Counts",
        "",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## Shadow Label Counts", ""])
    for label, count in sorted(label_counts.items()):
        lines.append(f"- `{label}`: `{count}`")
    lines.extend(["", "## Machine Evidence Provenance", ""])
    if support_counts:
        for status, count in sorted(support_counts.items()):
            lines.append(f"- evidence_support_status `{status}`: `{count}`")
    else:
        lines.append("- no machine evidence support rows")
    lines.extend(["", "## Evidence Basis Counts", ""])
    if basis_counts:
        for basis, count in sorted(basis_counts.items()):
            lines.append(f"- `{basis}`: `{count}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Machine Evidence Blockers", ""])
    blockers = [
        blocker
        for blocker in readiness_row.get("machine_evidence_blockers", "").split(";")
        if blocker
    ]
    if blockers:
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Manual Label Summary", ""])
    for row in summary_rows:
        if row["scope"] != "manual_label":
            continue
        lines.append(
            "- "
            f"`{row['manual_label']}`: rows `{row['row_count']}`, "
            f"aligned `{row['aligned_count']}`, "
            f"partial `{row['partial_count']}`, "
            f"contradicted `{row['contradicted_count']}`, "
            f"unresolved `{row['unresolved_count']}`"
        )
    lines.extend(["", "## Required Evidence To Promote", ""])
    if promotion_gaps:
        for gap, count in promotion_gaps.most_common():
            lines.append(f"- `{gap}`: `{count}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Top Rows To Inspect", ""])
    review_rows = [
        row
        for row in shadow_rows
        if row["shadow_alignment_status"] in {"contradicted", "unresolved", "partial"}
    ]
    if not review_rows:
        lines.append("- none")
    else:
        for row in review_rows[:10]:
            lines.append(
                "- "
                f"`{row['oracle_row_id']}`: "
                f"{row['shadow_label']} / {row['shadow_alignment_status']} "
                f"requires `{row['required_evidence_to_promote']}`"
            )
    return "\n".join(lines) + "\n"


def render_slice1_report(
    *,
    manifest_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    run_facts: Mapping[str, str],
) -> str:
    manifest_status_counts = Counter(row["artifact_status"] for row in manifest_rows)
    freshness_counts = Counter(row["freshness_basis"] for row in manifest_rows)
    missing_field_rows = [
        row for row in manifest_rows if row.get("missing_required_fields")
    ]
    stale_rows = [
        row
        for row in manifest_rows
        if row.get("artifact_status") == "present_stale_hash_mismatch"
    ]
    unpinned_rows = [
        row
        for row in manifest_rows
        if row.get("artifact_status") == "present_hash_unpinned"
    ]
    context_rows = [
        row for row in summary_rows if _int_value(row.get("seed_count")) == 0
    ]
    decision_rows = [
        row for row in summary_rows if _int_value(row.get("seed_count")) > 0
    ]
    lines = [
        "# Shared Peak Identity Slice 1 Blast Radius Report",
        "",
        "## Decision Summary",
        "",
        "- readiness_label: `diagnostic_only`",
        f"- slice: `{run_facts['slice']}`",
        f"- blast_radius_assessed: `{run_facts['blast_radius_assessed']}`",
        "- blast_radius_stale_artifact_count: "
        f"`{run_facts['blast_radius_stale_artifact_count']}`",
        f"- max_overfit_risk: `{run_facts['max_overfit_risk']}`",
        f"- next_action: `{_slice1_next_action(run_facts)}`",
        "",
        "This report summarizes machine-readable blast-radius facts only. "
        "It does not make production decisions, mutate the matrix, or assign "
        "manual labels to non-seed rows.",
        "",
        "non-seed rows are machine-side blast-radius context, not manual labels.",
        "",
        "## Manifest Freshness And Coverage",
        "",
        f"- stale_manifest_rows: `{len(stale_rows)}`",
        f"- missing_field_rows: `{len(missing_field_rows)}`",
        f"- unpinned_artifacts: `{len(unpinned_rows)}`",
    ]
    for status, count in sorted(manifest_status_counts.items()):
        lines.append(f"- artifact_status `{status}`: `{count}`")
    for basis, count in sorted(freshness_counts.items()):
        lines.append(f"- freshness_basis `{basis}`: `{count}`")
    _append_manifest_rows(lines, "Stale Manifest Rows", stale_rows)
    _append_manifest_rows(lines, "Unpinned Manifest Rows", unpinned_rows)
    _append_manifest_rows(lines, "Missing Required Field Rows", missing_field_rows)
    lines.extend(["", "## Blast-Radius Summary By Class And Scope", ""])
    for row in decision_rows:
        lines.append(
            "- "
            f"`{row['evidence_gap_class']}` / `{row['scope']}`: "
            f"assessed=`{row['assessed_row_count']}`, "
            f"compatible=`{row['compatible_row_count']}`, "
            f"contradictory=`{row['contradictory_count']}`, "
            f"ambiguous=`{row['ambiguous_machine_match_count']}`, "
            f"unavailable_fields=`{row['unavailable_field_count']}`, "
            f"risk=`{row['overfit_risk']}`"
        )
    if not decision_rows:
        lines.append("- none")
    lines.extend(["", "## Context-Only Rows", ""])
    if context_rows:
        for row in context_rows:
            lines.append(
                f"- `{row['evidence_gap_class']}` / `{row['scope']}`: "
                f"context_row_count=`{row['context_row_count']}`; "
                f"risk_excluded_from_run_max=`{row['overfit_risk']}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Machine Too Conservative Classes", ""])
    _append_summary_class_prefix(lines, decision_rows, "machine_too_conservative")
    lines.extend(["", "## Machine Too Permissive Classes", ""])
    _append_summary_class_prefix(lines, decision_rows, "machine_too_permissive")
    lines.extend(["", "## Exit Interpretation", ""])
    lines.extend(_exit_interpretation(run_facts))
    return "\n".join(lines) + "\n"


def _append_examples(
    lines: list[str],
    rows: Sequence[Mapping[str, str]],
    class_prefix: str,
) -> None:
    examples = [
        row for row in rows if row["evidence_gap_class"].startswith(class_prefix)
    ]
    if not examples:
        lines.append("- none")
        return
    for row in examples[:5]:
        lines.append(
            f"- `{row['oracle_row_id']}`: {row['evidence_gap_class']}; "
            f"next={row['recommended_next_action']}"
        )


def _append_manifest_rows(
    lines: list[str],
    title: str,
    rows: Sequence[Mapping[str, str]],
) -> None:
    lines.extend(["", f"### {title}", ""])
    if not rows:
        lines.append("- none")
        return
    for row in rows[:10]:
        lines.append(
            f"- `{row['artifact_id']}`: status=`{row['artifact_status']}`, "
            f"freshness_basis=`{row['freshness_basis']}`, "
            f"missing_required_fields=`{row.get('missing_required_fields', '')}`"
        )


def _append_summary_class_prefix(
    lines: list[str],
    rows: Sequence[Mapping[str, str]],
    class_prefix: str,
) -> None:
    matches = [
        row for row in rows if row["evidence_gap_class"].startswith(class_prefix)
    ]
    if not matches:
        lines.append("- none")
        return
    for row in matches:
        lines.append(
            f"- `{row['evidence_gap_class']}` / `{row['scope']}`: "
            f"risk=`{row['overfit_risk']}`, "
            f"compatible_fraction=`{row['compatible_fraction']}`, "
            f"contradictory_fraction=`{row['contradictory_fraction']}`"
        )


def _exit_interpretation(run_facts: Mapping[str, str]) -> list[str]:
    assessed = run_facts["blast_radius_assessed"]
    risk = run_facts["max_overfit_risk"]
    stale_count = _int_value(run_facts.get("blast_radius_stale_artifact_count"))
    if risk == "high":
        return ["- revise_or_kill: vocabulary overfit risk is high."]
    if risk == "medium" or assessed != "present_current" or stale_count:
        return [
            "- externalize_missing_evidence: complete pinned current artifacts "
            "before Shadow-label-alignment planning."
        ]
    return [
        "- allow_shadow_shadow_label_alignment_planning: Slice 1 facts are current "
        "and overfit risk is low or none."
    ]


def _slice1_next_action(run_facts: Mapping[str, str]) -> str:
    assessed = run_facts["blast_radius_assessed"]
    risk = run_facts["max_overfit_risk"]
    stale_count = _int_value(run_facts.get("blast_radius_stale_artifact_count"))
    if risk == "high":
        return "revise_or_kill_vocabulary"
    if risk == "medium" or assessed != "present_current" or stale_count:
        return "externalize_missing_evidence"
    return "allow_shadow_shadow_label_alignment_planning"


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0
