from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.alignment.shared_peak_identity_explanation.classifier import (
    build_slice1_run_facts,
)
from xic_extractor.alignment.shared_peak_identity_explanation.writers import (
    write_slice1_outputs,
)


def test_slice1_writer_outputs_run_facts_and_report(tmp_path: Path) -> None:
    slice0_outputs = _write_slice0_outputs(tmp_path)
    manifest_rows = _manifest_rows(status="present_current")
    summary_rows = [
        _summary_row(
            "machine_too_conservative_low_opportunity",
            "overall",
            seed_count="1",
            context_row_count="0",
            overfit_risk="low",
        ),
        _summary_row(
            "delta_mass_related_context_only",
            "overall",
            seed_count="0",
            context_row_count="1",
            overfit_risk="high",
        ),
        _summary_row(
            "machine_too_permissive_rt_pattern_conflict",
            "all_available_full_cohort",
            seed_count="1",
            context_row_count="0",
            overfit_risk="medium",
        ),
    ]
    run_facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=manifest_rows,
        summary_rows=summary_rows,
    )

    outputs = write_slice1_outputs(
        output_dir=tmp_path,
        slice0_outputs=slice0_outputs,
        manifest_rows=manifest_rows,
        summary_rows=summary_rows,
        run_facts=run_facts,
    )

    assert outputs["blast_radius_manifest"].name == (
        "shared_peak_identity_blast_radius_manifest.tsv"
    )
    assert outputs["blast_radius_summary"].name == (
        "shared_peak_identity_blast_radius_summary.tsv"
    )
    facts = _read_tsv(tmp_path / "shared_peak_identity_run_facts.tsv")[0]
    assert facts["slice"] == "slice1"
    assert facts["blast_radius_assessed"] == "present_current"
    assert facts["blast_radius_stale_artifact_count"] == "0"
    assert facts["max_overfit_risk"] == "medium"

    report = (tmp_path / "shared_peak_identity_explanation_report.md").read_text(
        encoding="utf-8"
    )
    assert "diagnostic_only" in report
    assert "present_current" in report
    assert "Context-Only" in report
    assert "Machine Too Conservative" in report
    assert "Machine Too Permissive" in report
    assert "non-seed rows are machine-side blast-radius context" in report
    assert "production_ready" not in report
    assert "gating verdict" not in report


def test_slice1_run_facts_classify_missing_unpinned_and_stale_surfaces() -> None:
    missing_full_cohort = _manifest_rows(status="present_current")
    for row in missing_full_cohort:
        if row["artifact_id"] == "full_cohort_alignment_cells":
            row["artifact_status"] = "present_missing_required_fields"
            row["missing_required_fields"] = "reason"

    facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=missing_full_cohort,
        summary_rows=[_summary_row("machine_agrees_with_manual", "overall")],
    )
    assert facts["blast_radius_assessed"] == "full_cohort_not_assessed"

    unpinned = _manifest_rows(status="present_current")
    for row in unpinned:
        if row["artifact_id"] == "full_cohort_alignment_review":
            row["artifact_status"] = "present_hash_unpinned"
    facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=unpinned,
        summary_rows=[_summary_row("machine_agrees_with_manual", "overall")],
    )
    assert facts["blast_radius_assessed"] == "not_assessed"

    stale = _manifest_rows(status="present_current")
    for row in stale:
        if row["artifact_id"] in {
            "fixture_cohort_alignment_review",
            "full_cohort_alignment_cells",
        }:
            row["artifact_status"] = "present_stale_hash_mismatch"
    facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=stale,
        summary_rows=[_summary_row("machine_agrees_with_manual", "overall")],
    )
    assert facts["blast_radius_assessed"] == "stale_hash_mismatch"
    assert facts["blast_radius_stale_artifact_count"] == "2"


def test_slice1_run_facts_keep_seeded_unassessed_max_risk() -> None:
    facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=_manifest_rows(status="present_current"),
        summary_rows=[
            _summary_row(
                "machine_too_conservative_low_opportunity",
                "overall",
                seed_count="1",
                context_row_count="0",
                overfit_risk="unassessed",
            ),
            _summary_row(
                "delta_mass_related_context_only",
                "overall",
                seed_count="0",
                context_row_count="1",
                overfit_risk="high",
            ),
        ],
    )

    assert facts["max_overfit_risk"] == "unassessed"


def test_slice1_run_facts_exclude_seed_scope_from_max_risk(
    tmp_path: Path,
) -> None:
    slice0_outputs = _write_slice0_outputs(tmp_path)
    manifest_rows = _manifest_rows(status="present_current")
    summary_rows = [
        _summary_row(
            "machine_too_conservative_low_opportunity",
            "seed",
            seed_count="1",
            context_row_count="0",
            overfit_risk="medium",
        ),
        _summary_row(
            "machine_too_conservative_low_opportunity",
            "overall",
            seed_count="1",
            context_row_count="0",
            overfit_risk="low",
        ),
        _summary_row(
            "delta_mass_related_context_only",
            "overall",
            seed_count="0",
            context_row_count="1",
            overfit_risk="high",
        ),
    ]
    run_facts = build_slice1_run_facts(
        slice0_run_facts=_slice0_run_facts(),
        manifest_rows=manifest_rows,
        summary_rows=summary_rows,
    )

    assert run_facts["max_overfit_risk"] == "low"

    write_slice1_outputs(
        output_dir=tmp_path,
        slice0_outputs=slice0_outputs,
        manifest_rows=manifest_rows,
        summary_rows=summary_rows,
        run_facts=run_facts,
    )

    report = (tmp_path / "shared_peak_identity_explanation_report.md").read_text(
        encoding="utf-8"
    )
    assert "next_action: `allow_shadow_shadow_label_alignment_planning`" in report


def _write_slice0_outputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "oracle": tmp_path / "shared_peak_identity_manual_oracle.tsv",
        "evidence_vectors": tmp_path / "shared_peak_identity_evidence_vectors.tsv",
        "explanations": tmp_path / "shared_peak_identity_explanations.tsv",
        "run_facts": tmp_path / "shared_peak_identity_run_facts.tsv",
        "report": tmp_path / "shared_peak_identity_explanation_report.md",
    }
    for path in paths.values():
        path.write_text("placeholder\n", encoding="utf-8")
    return paths


def _slice0_run_facts() -> dict[str, str]:
    return {
        "run_facts_schema_version": "shared_peak_identity_run_facts",
        "slice": "slice0",
        "seed_rows_total": "7",
        "seed_rows_explained": "7",
        "seed_rows_unexplained": "0",
        "seed_rows_inconclusive": "0",
        "vocabulary_special_casing_detected": "FALSE",
        "blast_radius_assessed": "not_run_slice0",
        "blast_radius_stale_artifact_count": "0",
        "max_overfit_risk": "unassessed",
        "durable_oracle_path": "oracle.tsv",
        "durable_oracle_sha256": "A" * 64,
    }


def _manifest_rows(*, status: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for artifact_id in (
        "fixture_cohort_alignment_review",
        "fixture_cohort_alignment_cells",
        "full_cohort_alignment_review",
        "full_cohort_alignment_cells",
    ):
        rows.append(
            {
                "manifest_schema_version": (
                    "shared_peak_identity_blast_radius_manifest"
                ),
                "artifact_id": artifact_id,
                "artifact_role": "alignment_cells"
                if artifact_id.endswith("_cells")
                else "alignment_review",
                "artifact_path": f"{artifact_id}.tsv",
                "artifact_sha256": "B" * 64,
                "expected_artifact_sha256": "B" * 64,
                "freshness_basis": "expected_blast_radius_manifest",
                "artifact_schema_version": "",
                "artifact_status": status,
                "row_count": "2",
                "sample_count": "2",
                "family_count": "1",
                "available_required_fields": "feature_family_id",
                "missing_required_fields": "",
                "generated_from_existing_artifact": "TRUE",
                "notes": "",
            }
        )
    return rows


def _summary_row(
    evidence_gap_class: str,
    scope: str,
    *,
    seed_count: str = "1",
    context_row_count: str = "0",
    overfit_risk: str = "low",
) -> dict[str, str]:
    return {
        "summary_schema_version": "shared_peak_identity_blast_radius_summary",
        "scope": scope,
        "artifact_id": "combined_alignment_cells",
        "evidence_gap_class": evidence_gap_class,
        "seed_count": seed_count,
        "context_row_count": context_row_count,
        "non_seed_same_family_count": "0",
        "assessed_row_count": "1" if seed_count != "0" else "0",
        "all_available_row_count": "1" if seed_count != "0" else "0",
        "compatible_row_count": "1" if overfit_risk == "low" else "0",
        "unavailable_field_count": "0",
        "contradictory_count": "1" if overfit_risk == "high" else "0",
        "ambiguous_machine_match_count": "0",
        "compatible_fraction": "1.000000" if overfit_risk == "low" else "0.000000",
        "contradictory_fraction": "1.000000" if overfit_risk == "high" else "0.000000",
        "ambiguous_fraction": "0.000000",
        "unavailable_fraction": "0.000000",
        "overfit_risk": overfit_risk,
        "example_oracle_row_ids": "FAM001|S1",
        "example_feature_family_ids": "FAM001",
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
