import pytest

from xic_extractor.output.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
    artifact_names_for_output_level,
)


def test_production_output_level_is_matrix_only():
    assert artifact_names_for_output_level("production") == ("alignment_matrix.tsv",)


def test_machine_output_level_adds_review_and_matrix_tsv():
    assert artifact_names_for_output_level("machine") == (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
    )


def test_debug_output_level_adds_cell_status_and_owner_debug_tsvs():
    assert artifact_names_for_output_level("debug") == (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_explanation.sqlite",
    )


def test_validation_output_level_adds_cells_and_explanation():
    assert artifact_names_for_output_level("validation") == (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_explanation.sqlite",
    )


def test_validation_minimal_output_level_is_machine_gate_surface_only():
    assert artifact_names_for_output_level("validation-minimal") == (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    )


@pytest.mark.parametrize("level", ["production", "machine"])
def test_non_diagnostic_levels_do_not_produce_the_explanation_report(level):
    """The report is a per-cell projection; only diagnostic levels pay for it."""
    assert EXPLANATION_ARTIFACT_NAME not in artifact_names_for_output_level(level)


@pytest.mark.parametrize("level", ["debug", "validation", "validation-minimal"])
def test_diagnostic_levels_produce_the_explanation_report(level):
    assert EXPLANATION_ARTIFACT_NAME in artifact_names_for_output_level(level)


@pytest.mark.parametrize(
    "level", ["production", "machine", "debug", "validation", "validation-minimal"]
)
def test_no_level_produces_the_status_matrix(level):
    """It has no reader; emit_alignment_status_matrix is the only way to get it."""
    assert "alignment_matrix_status.tsv" not in artifact_names_for_output_level(level)
