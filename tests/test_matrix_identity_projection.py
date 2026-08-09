from xic_extractor.diagnostics.matrix_identity_projection import (
    matrix_value_diffs,
    matrix_values_by_identity,
)


def test_matrix_values_by_identity_limits_family_alias_requested_keys() -> None:
    matrix_rows = (
        {"Mz": "300.3", "RT": "10.0", "S_KEEP": "111", "S_SKIP": "999"},
        {"Mz": "400.4", "RT": "11.0", "S_KEEP": "222", "S_SKIP": "888"},
    )
    identity_rows = (
        {
            "matrix_row_index": "1",
            "peak_hypothesis_id": "FAM_KEEP::mode_1",
            "source_feature_family_ids": "FAM_KEEP",
        },
        {
            "matrix_row_index": "2",
            "peak_hypothesis_id": "FAM_UNUSED",
            "source_feature_family_ids": "FAM_UNUSED",
        },
    )

    values = matrix_values_by_identity(
        matrix_rows=matrix_rows,
        matrix_identity_rows=identity_rows,
        key_mode="family_aliases",
        requested_keys={("FAM_KEEP", "S_KEEP")},
        duplicate_policy="last",
    )

    assert values == {("FAM_KEEP", "S_KEEP"): "111"}


def test_matrix_values_by_identity_can_return_written_public_values_only() -> None:
    values = matrix_values_by_identity(
        matrix_rows=(
            {
                "Mz": "200.2",
                "RT": "8.2",
                "S1": "",
                "S2": "321.5",
            },
        ),
        matrix_identity_rows=(
            {
                "matrix_row_index": "1",
                "peak_hypothesis_id": "FAM_ADD::mode_1",
            },
        ),
        include_blank=False,
    )

    assert values == {("FAM_ADD::mode_1", "S2"): "321.5"}


def test_matrix_value_diffs_reports_changed_cells() -> None:
    diffs = matrix_value_diffs(
        {("FAM001", "S1"): "", ("FAM002", "S2"): "202"},
        {("FAM001", "S1"): "101", ("FAM002", "S2"): "202"},
    )

    assert diffs == ((("FAM001", "S1"), "", "101"),)


def test_matrix_value_diffs_ignores_public_numeric_text_formatting() -> None:
    diffs = matrix_value_diffs(
        {("FAM001", "S1"): "100"},
        {("FAM001", "S1"): "1.00000e+02"},
    )

    assert diffs == ()
