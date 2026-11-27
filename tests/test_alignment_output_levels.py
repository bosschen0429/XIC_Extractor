import pytest

from xic_extractor.output.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
    artifact_names_for_output_level,
)


def test_production_output_level_is_matrix_and_rt_anchors_only():
    assert artifact_names_for_output_level("production") == (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
    )


def test_machine_output_level_adds_run_metadata():
    assert artifact_names_for_output_level("machine") == (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_run_metadata.json",
    )


def test_debug_output_level_adds_cell_status_and_owner_debug_tsvs():
    assert artifact_names_for_output_level("debug") == (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_run_metadata.json",
        "alignment_cells.tsv",
        "alignment_explanation.sqlite",
    )


def test_validation_minimal_output_level_is_machine_gate_surface_only():
    assert artifact_names_for_output_level("validation-minimal") == (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_run_metadata.json",
        "alignment_backfill_cell_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    )


@pytest.mark.parametrize("level", ["production", "machine"])
def test_non_diagnostic_levels_do_not_produce_the_explanation_report(level):
    """The report is a per-cell projection; only diagnostic levels pay for it."""
    assert EXPLANATION_ARTIFACT_NAME not in artifact_names_for_output_level(level)


@pytest.mark.parametrize("level", ["debug", "validation-minimal"])
def test_diagnostic_levels_produce_the_explanation_report(level):
    assert EXPLANATION_ARTIFACT_NAME in artifact_names_for_output_level(level)
