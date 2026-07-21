from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.discovery.detected_features import (
    CID_NL_SHARED_IDENTITY_SOURCE,
    DETECTED_FEATURES_COLUMNS,
    DetectedFeature,
    comparable_cid_nl_identity,
    load_detected_features_csv,
    load_external_feature_table_tsv,
    load_mzmine_wide_feature_table_csv,
    write_detected_features_csv,
)

EXTERNAL_HEADER = (
    "external_tool",
    "external_run_id",
    "external_tool_version",
    "external_adapter_version",
    "sample_id",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt",
    "rt_start_min",
    "rt_end_min",
    "area_or_intensity",
    "area_or_intensity_semantics",
    "height",
    "ms1_height",
    "area",
    "ms1_area",
    "trace_quality",
    "shape_score",
    "mz_tolerance_unit",
    "rt_tolerance_unit",
    "duplicate_feature_policy",
    "missing_value_policy",
)


def _write_detected_csv(path: Path, row: dict[str, object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETECTED_FEATURES_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {column: row.get(column, "") for column in DETECTED_FEATURES_COLUMNS}
        )


def _local_detected_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_stem": "Sample_1",
        "feature_id": "Sample_1|local_ms1_feature|000001",
        "source_feature_row_id": "Sample_1|local_ms1_feature|000001",
        "mz": "500.0",
        "rt_apex_min": "10.0",
        "rt_start_min": "9.95",
        "rt_end_min": "10.05",
        "height": "1000.0",
        "area": "2500.0",
        "trace_quality": "OK",
        "shape_score": "",
        "source": "local_ms1_feature",
    }
    row.update(overrides)
    return row


def _local_detected_feature(**overrides: object) -> DetectedFeature:
    values = _local_detected_row(**overrides)
    return DetectedFeature(
        sample_stem=str(values["sample_stem"]),
        feature_id=str(values["feature_id"]),
        source_feature_row_id=str(values["source_feature_row_id"]),
        mz=float(values["mz"]),
        rt_apex_min=float(values["rt_apex_min"]),
        rt_start_min=float(values["rt_start_min"]),
        rt_end_min=float(values["rt_end_min"]),
        height=float(values["height"]),
        area=float(values["area"]),
        trace_quality=str(values["trace_quality"]),
        shape_score=None,
        source=str(values["source"]),
    )


def _write_external_table(path: Path, row: dict[str, object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXTERNAL_HEADER, delimiter="\t")
        writer.writeheader()
        writer.writerow({column: row.get(column, "") for column in EXTERNAL_HEADER})


def _external_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "external_tool": "MZmine",
        "external_run_id": "run1",
        "external_tool_version": "4.9.14",
        "external_adapter_version": "contract_v1",
        "sample_id": "Sample_1",
        "feature_id": "F123",
        "source_feature_row_id": "Sample_1|feature|F123",
        "mz": "500.0",
        "rt": "10.0",
        "rt_start_min": "9.95",
        "rt_end_min": "10.05",
        "area_or_intensity": "1000.0",
        "area_or_intensity_semantics": "height",
        "area": "5000.0",
        "trace_quality": "GOOD",
        "shape_score": "0.91",
        "mz_tolerance_unit": "ppm",
        "rt_tolerance_unit": "minutes",
        "duplicate_feature_policy": "source_unique",
        "missing_value_policy": "blank_is_missing",
    }
    row.update(overrides)
    return row


def test_local_ms1_feature_detected_csv_accepts_ok_and_weak_support(
    tmp_path: Path,
) -> None:
    ok_csv = tmp_path / "ok.csv"
    weak_csv = tmp_path / "weak.csv"
    _write_detected_csv(ok_csv, _local_detected_row(trace_quality="OK"))
    _write_detected_csv(weak_csv, _local_detected_row(trace_quality="WEAK_SUPPORT"))

    assert load_detected_features_csv(ok_csv)[0].trace_quality == "OK"
    assert load_detected_features_csv(weak_csv)[0].trace_quality == "WEAK_SUPPORT"


def test_local_ms1_feature_detected_csv_rejects_fail_and_unknown_quality(
    tmp_path: Path,
) -> None:
    fail_csv = tmp_path / "fail.csv"
    typo_csv = tmp_path / "typo.csv"
    _write_detected_csv(fail_csv, _local_detected_row(trace_quality="FAIL"))
    _write_detected_csv(typo_csv, _local_detected_row(trace_quality="GOOD"))

    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(fail_csv)
    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(typo_csv)


def test_external_feature_table_maps_to_detected_features(tmp_path: Path) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(table, _external_row())

    features = load_external_feature_table_tsv(table)

    assert len(features) == 1
    feature = features[0]
    assert feature.sample_stem == "Sample_1"
    assert feature.feature_id == "F123"
    assert feature.source_feature_row_id == "Sample_1|feature|F123"
    assert feature.mz == 500.0
    assert feature.rt_apex_min == 10.0
    assert feature.rt_start_min == 9.95
    assert feature.rt_end_min == 10.05
    assert feature.height == 1000.0
    assert feature.area == 5000.0
    assert feature.trace_quality == "GOOD"
    assert feature.shape_score == 0.91
    assert feature.source == "external_feature_table:MZmine:contract_v1"
    assert comparable_cid_nl_identity(feature) is None


def test_external_feature_table_accepts_shape_score_without_trace_quality(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(table, _external_row(trace_quality="", shape_score="0.93"))

    feature = load_external_feature_table_tsv(table)[0]

    assert feature.trace_quality == "UNSPECIFIED"
    assert feature.shape_score == 0.93


def test_external_feature_table_rejects_duplicate_optional_header(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external.tsv"
    row = _external_row()
    header = (*EXTERNAL_HEADER, "shape_score")
    values = [str(row.get(column, "")) for column in EXTERNAL_HEADER]
    table.write_text(
        "\t".join(header) + "\n" + "\t".join([*values, "0.5"]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate columns: shape_score"):
        load_external_feature_table_tsv(table)


def test_external_feature_table_accepts_area_only_as_intensity_proxy(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(
        table,
        _external_row(
            area_or_intensity="5000.0",
            area_or_intensity_semantics="area",
            area="",
        ),
    )

    feature = load_external_feature_table_tsv(table)[0]

    assert feature.height == 5000.0
    assert feature.area == 5000.0
    assert feature.trace_quality == "GOOD"


@pytest.mark.parametrize("area_or_intensity", ["0", "-1"])
def test_external_feature_table_rejects_non_positive_required_signal_value(
    tmp_path: Path,
    area_or_intensity: str,
) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(
        table,
        _external_row(
            area_or_intensity=area_or_intensity,
            area_or_intensity_semantics="area",
            height="1000",
            area="",
        ),
    )

    with pytest.raises(ValueError, match="area_or_intensity.*must be > 0"):
        load_external_feature_table_tsv(table)


def test_external_feature_table_rejects_unsupported_semantics_with_height(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(
        table,
        _external_row(
            area_or_intensity_semantics="unknown_signal",
            height="1000",
        ),
    )

    with pytest.raises(ValueError, match="unsupported area_or_intensity_semantics"):
        load_external_feature_table_tsv(table)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("area", "0"),
        ("area", "-1"),
        ("ms1_area", "0"),
        ("ms1_area", "-1"),
    ],
)
def test_external_feature_table_rejects_non_positive_optional_area(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    table = tmp_path / "external_features.tsv"
    overrides = {"area": "", "ms1_area": "", field: value}
    _write_external_table(table, _external_row(**overrides))

    with pytest.raises(ValueError, match=rf"{field}.*must be > 0"):
        load_external_feature_table_tsv(table)


def test_external_feature_table_records_unspecified_when_signal_support_is_missing(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(table, _external_row(trace_quality="", shape_score=""))

    feature = load_external_feature_table_tsv(table)[0]

    assert feature.trace_quality == "UNSPECIFIED"
    assert feature.shape_score is None


def test_mzmine_wide_feature_table_maps_positive_peak_area_rows(
    tmp_path: Path,
) -> None:
    table = tmp_path / "mzmine.csv"
    table.write_text(
        "row ID,row m/z,row retention time,"
        "Sample_A.mzML Peak area,Sample_B.mzML Peak area\n"
        "7,504.1234,3.25,1200.5,0\n"
        "8,505.1255,3.26,,300.0\n",
        encoding="utf-8",
    )

    features = load_mzmine_wide_feature_table_csv(table, external_run_id="run42")

    assert len(features) == 2
    assert features[0].sample_stem == "Sample_A"
    assert features[0].feature_id == "Sample_A|mzmine_row|7"
    assert features[0].source_feature_row_id == "run42|row|7"
    assert features[0].mz == 504.1234
    assert features[0].rt_apex_min == 3.25
    assert features[0].height == 1200.5
    assert features[0].area == 1200.5
    assert features[0].trace_quality == "UNSPECIFIED"
    assert comparable_cid_nl_identity(features[0]) is None
    assert (
        features[0].source
        == "external_feature_table:MZmine:mzmine_wide_area_proxy_v1"
    )
    assert features[1].sample_stem == "Sample_B"
    assert features[1].feature_id == "Sample_B|mzmine_row|8"


def test_mzmine_wide_feature_table_rejects_duplicate_headers(
    tmp_path: Path,
) -> None:
    table = tmp_path / "mzmine.csv"
    table.write_text(
        "row ID,row m/z,row m/z,row retention time,Sample_A Peak area\n"
        "7,504.1,999.9,3.25,1200.5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate columns: row m/z"):
        load_mzmine_wide_feature_table_csv(table)


@pytest.mark.parametrize(
    ("source", "expected_comparable"),
    (
        (CID_NL_SHARED_IDENTITY_SOURCE, True),
        ("external_feature_table:OpenMS:contract_v1", False),
        ("external_feature_table:MZmine:contract_v1", False),
        ("local_ms1_feature", False),
        ("unknown_legacy_source", False),
    ),
)
def test_cid_nl_identity_capability_survives_detected_feature_round_trip(
    tmp_path: Path,
    source: str,
    expected_comparable: bool,
) -> None:
    feature = _local_detected_feature(
        source=source,
        trace_quality="OK" if source == "local_ms1_feature" else "GOOD",
        feature_id="feature-local-id",
        source_feature_row_id="cid-shared-id",
    )

    path = write_detected_features_csv(tmp_path / "detected.csv", (feature,))
    reloaded = load_detected_features_csv(path)[0]

    comparable = comparable_cid_nl_identity(reloaded)
    assert (comparable is not None) is expected_comparable
    if comparable is not None:
        assert comparable.values == frozenset(
            {"feature-local-id", "cid-shared-id"}
        )


def test_mzmine_wide_feature_table_strips_raw_suffix_from_sample_stem(
    tmp_path: Path,
) -> None:
    table = tmp_path / "mzmine_raw.csv"
    table.write_text(
        "row ID,row m/z,row retention time,Sample_A.raw Peak area\n"
        "7,504.1234,3.25,1200.5\n",
        encoding="utf-8",
    )

    features = load_mzmine_wide_feature_table_csv(table, external_run_id="run42")

    assert len(features) == 1
    assert features[0].sample_stem == "Sample_A"
    assert features[0].feature_id == "Sample_A|mzmine_row|7"


def test_write_detected_features_csv_emits_public_header(tmp_path: Path) -> None:
    table = tmp_path / "external_features.tsv"
    _write_external_table(table, _external_row())
    features = load_external_feature_table_tsv(table)
    output = tmp_path / "detected_features.csv"

    write_detected_features_csv(output, features)

    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert tuple(next(reader)) == DETECTED_FEATURES_COLUMNS


@pytest.mark.parametrize("trace_quality", ["FAIL", "GOOD"])
def test_write_detected_features_csv_rejects_invalid_local_ms1_trace_quality(
    tmp_path: Path, trace_quality: str
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature(trace_quality=trace_quality)

    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        write_detected_features_csv(output, [feature])

    assert not output.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sample_stem": ""}, "sample_stem"),
        ({"feature_id": ""}, "feature_id"),
        ({"mz": float("nan")}, "mz"),
        ({"rt_apex_min": float("inf")}, "rt_apex_min"),
        ({"height": 0.0}, "height"),
        ({"area": float("nan")}, "area"),
        ({"source": ""}, "source"),
    ],
)
def test_write_detected_features_csv_rejects_rows_loader_would_reject(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature(**overrides)

    with pytest.raises(ValueError, match=message):
        write_detected_features_csv(output, [feature])

    assert not output.exists()


def test_written_detected_features_round_trip_through_public_loader(
    tmp_path: Path,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature()

    write_detected_features_csv(output, [feature])

    assert load_detected_features_csv(output) == (feature,)


@pytest.mark.parametrize("field", ["mz", "area", "shape_score"])
def test_write_detected_features_csv_rejects_boolean_numeric_values(
    tmp_path: Path,
    field: str,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = replace(_local_detected_feature(), **{field: True})

    with pytest.raises(ValueError, match=field):
        write_detected_features_csv(output, [feature])

    assert not output.exists()
