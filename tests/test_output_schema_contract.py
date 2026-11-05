from xic_extractor.output.schema import (
    DIAGNOSTIC_HEADERS,
    LONG_ADVANCED_HEADERS,
    LONG_HEADERS,
    TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION,
    TARGETED_LONG_CSV_SCHEMA_VERSION,
    TARGETED_OUTPUT_SCHEMA_VERSION,
    TARGETED_PRODUCT_PROJECTION_HEADERS,
)


def test_targeted_output_schema_versions_are_explicit() -> None:
    assert TARGETED_OUTPUT_SCHEMA_VERSION == "targeted_output"
    assert TARGETED_LONG_CSV_SCHEMA_VERSION == "targeted_long_csv"
    assert TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION == "targeted_diagnostics_csv"


def test_long_schema_has_projection_columns() -> None:
    assert len(LONG_HEADERS) == 25
    assert LONG_HEADERS[0] == "SampleName"
    assert LONG_HEADERS[14] == "Reason"
    assert LONG_HEADERS[9:11] == ("Int", "IntSmoothed")
    assert "Int" in LONG_ADVANCED_HEADERS
    assert "IntSmoothed" not in LONG_ADVANCED_HEADERS
    assert LONG_HEADERS[-10:] == TARGETED_PRODUCT_PROJECTION_HEADERS
    assert "Confidence" not in LONG_ADVANCED_HEADERS
    assert set(TARGETED_PRODUCT_PROJECTION_HEADERS) <= LONG_ADVANCED_HEADERS
    assert "NL" in LONG_ADVANCED_HEADERS
    assert {"NL Evidence", "PeakStart", "PeakEnd", "Reason"}.isdisjoint(
        LONG_ADVANCED_HEADERS
    )


def test_diagnostic_schema_has_4_columns() -> None:
    assert DIAGNOSTIC_HEADERS == ("SampleName", "Target", "Issue", "Reason")
