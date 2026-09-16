from xic_extractor.output.schema import (
    DIAGNOSTIC_HEADERS,
    LONG_ADVANCED_HEADERS,
    LONG_HEADERS,
    SCORE_BREAKDOWN_HEADERS,
    TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION,
    TARGETED_LONG_CSV_SCHEMA_VERSION,
    TARGETED_OUTPUT_SCHEMA_VERSION,
    TARGETED_PRODUCT_PROJECTION_HEADERS,
    TARGETED_PRODUCT_VISIBLE_HEADERS,
    TARGETED_SCORE_BREAKDOWN_CSV_SCHEMA_VERSION,
)


def test_targeted_output_schema_versions_are_explicit() -> None:
    assert TARGETED_OUTPUT_SCHEMA_VERSION == "targeted_output_v2"
    assert TARGETED_LONG_CSV_SCHEMA_VERSION == "targeted_long_csv_v2"
    assert TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION == "targeted_diagnostics_csv_v1"
    assert (
        TARGETED_SCORE_BREAKDOWN_CSV_SCHEMA_VERSION == "targeted_score_breakdown_csv_v1"
    )


def test_long_schema_has_projection_columns() -> None:
    assert len(LONG_HEADERS) == 26
    assert LONG_HEADERS[0] == "SampleName"
    assert LONG_HEADERS[14] == "Reason"
    assert LONG_HEADERS[8:10] == ("Int", "IntSmoothed")
    assert "Int" in LONG_ADVANCED_HEADERS
    assert "IntSmoothed" not in LONG_ADVANCED_HEADERS
    assert LONG_HEADERS[-11:] == TARGETED_PRODUCT_PROJECTION_HEADERS
    assert "Confidence" in LONG_ADVANCED_HEADERS
    assert TARGETED_PRODUCT_VISIBLE_HEADERS.isdisjoint(LONG_ADVANCED_HEADERS)
    assert (
        set(TARGETED_PRODUCT_PROJECTION_HEADERS) - TARGETED_PRODUCT_VISIBLE_HEADERS
        <= LONG_ADVANCED_HEADERS
    )


def test_diagnostic_schema_has_4_columns() -> None:
    assert DIAGNOSTIC_HEADERS == ("SampleName", "Target", "Issue", "Reason")


def test_score_breakdown_schema_has_projection_columns() -> None:
    assert len(SCORE_BREAKDOWN_HEADERS) == 28
    assert SCORE_BREAKDOWN_HEADERS[:8] == (
        "SampleName",
        "Target",
        "Final Confidence",
        "Detection Counted",
        "Product State",
        "Review State",
        "Projection Reason",
        "Legacy Authority Status",
    )
