import pytest

from xic_extractor.alignment.output_levels import (
    artifact_names_for_output_level,
    parse_alignment_output_level,
)


def test_production_output_level_is_matrix_only():
    assert artifact_names_for_output_level("production") == (
        "alignment_matrix.tsv",
    )


def test_machine_output_level_adds_review_and_matrix_tsv():
    assert artifact_names_for_output_level("machine") == (
        "alignment_results.xlsx",
        "review_report.html",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    )


def test_debug_output_level_adds_cell_status_and_owner_debug_tsvs():
    assert artifact_names_for_output_level("debug") == (
        "alignment_results.xlsx",
        "review_report.html",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_matrix_status.tsv",
        "event_to_ms1_owner.tsv",
        "ambiguous_ms1_owners.tsv",
        "owner_edge_evidence.tsv",
    )


def test_validation_output_level_adds_owner_edge_evidence_tsv():
    assert artifact_names_for_output_level("validation") == (
        "alignment_results.xlsx",
        "review_report.html",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_matrix_status.tsv",
        "event_to_ms1_owner.tsv",
        "ambiguous_ms1_owners.tsv",
        "owner_edge_evidence.tsv",
    )


def test_validation_minimal_output_level_is_machine_gate_surface_only():
    assert artifact_names_for_output_level("validation-minimal") == (
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
    )


@pytest.mark.parametrize(
    "output_level",
    ("machine", "debug", "validation", "validation-minimal"),
)
def test_all_output_levels_keep_same_provenance_filename_without_v1_alias(
    output_level,
):
    artifacts = artifact_names_for_output_level(output_level)

    assert "alignment_owner_backfill_peak_fact_provenance.tsv" in artifacts
    assert "alignment_owner_backfill_peak_fact_provenance_v1.tsv" not in artifacts


def test_parse_alignment_output_level_rejects_unknown_value():
    with pytest.raises(ValueError, match="output_level"):
        parse_alignment_output_level("everything")
