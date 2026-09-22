import pytest

from xic_extractor.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
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
        "cid_nl_explanation.sqlite",
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
        "cid_nl_explanation.sqlite",
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
def test_all_output_levels_keep_same_provenance_filename_without_alias(
    output_level,
):
    artifacts = artifact_names_for_output_level(output_level)

    assert "alignment_owner_backfill_peak_fact_provenance.tsv" in artifacts
    assert artifacts.count("alignment_owner_backfill_peak_fact_provenance.tsv") == 1


def test_parse_alignment_output_level_rejects_unknown_value():
    with pytest.raises(ValueError, match="output_level"):
        parse_alignment_output_level("everything")


@pytest.mark.parametrize(
    "level", ["production", "machine", "validation-minimal"]
)
def test_non_diagnostic_levels_do_not_produce_the_explanation_report(level):
    """The report is a per-cell projection; only diagnostic levels pay for it."""
    assert EXPLANATION_ARTIFACT_NAME not in artifact_names_for_output_level(level)


@pytest.mark.parametrize("level", ["debug", "validation"])
def test_diagnostic_levels_produce_the_explanation_report(level):
    assert EXPLANATION_ARTIFACT_NAME in artifact_names_for_output_level(level)
