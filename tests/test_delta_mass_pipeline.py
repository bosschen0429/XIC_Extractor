"""Contract tests for the Pt pattern discovery pipeline.

Covers ``run_pt_pattern_discovery()`` from
``xic_extractor.discovery.delta_mass_pipeline``: end-to-end from a Detected
Features CSV (and optional CID-NL(dR) candidates CSV for the composite
strategy) to ``pt_pattern_candidates.csv``, ``pt_pattern_review.csv``,
``pt_pattern_evidence.csv``, and ``pt_pattern_sidecar.json``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import (
    full_context_trace_summary_row,
    write_trace_summary_lineage_bundle,
)
from xic_extractor.discovery.csv_writer import format_discovery_csv_value
from xic_extractor.discovery.delta_mass_patterns import (
    match_delta_mass_pattern as real_match_delta_mass_pattern,
)
from xic_extractor.discovery.delta_mass_pipeline import (
    DeltaMassDiscoveryOutputs,
    run_pt_pattern_discovery,
)
from xic_extractor.discovery.detected_features import (
    CID_NL_SHARED_IDENTITY_SOURCE,
    DeltaMassPatternSettings,
)
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    pt_pattern_candidate_assignment_fingerprint,
)

DETECTED_FEATURES_COLUMNS = (
    "sample_stem",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "height",
    "area",
    "trace_quality",
    "shape_score",
    "source",
)


def _write_detected_features_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETECTED_FEATURES_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {column: row.get(column, "") for column in DETECTED_FEATURES_COLUMNS}
            )


def _write_external_feature_table(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
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
        "area",
        "trace_quality",
        "shape_score",
        "mz_tolerance_unit",
        "rt_tolerance_unit",
        "duplicate_feature_policy",
        "missing_value_policy",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            source_row = {
                "external_tool": "MZmine",
                "external_run_id": "run1",
                "external_tool_version": "4.9.14",
                "external_adapter_version": "contract",
                "mz_tolerance_unit": "ppm",
                "rt_tolerance_unit": "minutes",
                "duplicate_feature_policy": "source_unique",
                "missing_value_policy": "blank_is_missing",
                "area_or_intensity_semantics": "height",
                **row,
            }
            writer.writerow(
                {column: source_row.get(column, "") for column in fieldnames}
            )


def _write_mzmine_wide_feature_table(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "row ID",
        "row m/z",
        "row retention time",
        "Sample_A.mzML Peak area",
        "Sample_B.mzML Peak area",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in fieldnames})


def _full_pattern_rows(sample_stem: str = "Sample_1") -> list[dict[str, object]]:
    base_mz = 500.0
    return [
        {
            "sample_stem": sample_stem,
            "feature_id": "base-1",
            "mz": base_mz,
            "rt_apex_min": 10.000,
            "height": 1000.0,
            "area": 500.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus1-1",
            "mz": base_mz + 1.002111,
            "rt_apex_min": 10.001,
            "height": 800.0,
            "area": 400.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus2-1",
            "mz": base_mz + 2.002271,
            "rt_apex_min": 10.002,
            "height": 200.0,
            "area": 100.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
        {
            "sample_stem": sample_stem,
            "feature_id": "plus4-1",
            "mz": base_mz + 4.005213,
            "rt_apex_min": 10.003,
            "height": 50.0,
            "area": 25.0,
            "trace_quality": "GOOD",
            "shape_score": 0.9,
            "source": "targeted",
        },
    ]


def test_run_pt_pattern_discovery_writes_pt_only_outputs(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    assert isinstance(outputs, DeltaMassDiscoveryOutputs)
    assert outputs.candidates_csv == output_dir / "pt_pattern_candidates.csv"
    assert outputs.review_csv == output_dir / "pt_pattern_review.csv"
    assert outputs.evidence_csv == output_dir / "pt_pattern_evidence.csv"
    assert outputs.sidecar_json == output_dir / "pt_pattern_sidecar.json"
    assert outputs.candidates_csv.exists()
    assert outputs.review_csv.exists()
    assert outputs.evidence_csv.exists()
    assert outputs.sidecar_json.exists()

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["method_label"] == "pt_pattern"
    assert rows[0]["candidate_state"] == "pt_pattern_candidate"

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert evidence_rows[0]["trace_rule_call"] == "not_evaluated"
    assert evidence_rows[0]["trace_detection_status"] == "not_evaluated"
    assert evidence_rows[0]["matrix_write_authority"] == "FALSE"


def test_run_pt_pattern_discovery_accepts_external_feature_table_source(
    tmp_path: Path,
) -> None:
    external_table = tmp_path / "external_features.tsv"
    external_rows = [
        {
            "sample_id": row["sample_stem"],
            "feature_id": row["feature_id"],
            "source_feature_row_id": row.get("source_feature_row_id", ""),
            "mz": row["mz"],
            "rt": row["rt_apex_min"],
            "rt_start_min": row.get("rt_start_min", ""),
            "rt_end_min": row.get("rt_end_min", ""),
            "area_or_intensity": row["height"],
            "area": row.get("area", ""),
            "trace_quality": row["trace_quality"],
            "shape_score": row.get("shape_score", ""),
        }
        for row in _full_pattern_rows()
    ]
    _write_external_feature_table(external_table, external_rows)
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        external_feature_table_tsv=external_table,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    prepared_csv = output_dir / "detected_features.csv"
    assert prepared_csv.exists()
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(prepared_csv)
    assert payload["inputs"]["external_feature_table_tsv"] == str(external_table)
    assert payload["inputs"]["mzmine_feature_table_csv"] is None


def test_run_pt_pattern_discovery_accepts_mzmine_wide_feature_table_source(
    tmp_path: Path,
) -> None:
    mzmine_table = tmp_path / "mzmine_features.csv"
    base_mz = 500.0
    _write_mzmine_wide_feature_table(
        mzmine_table,
        [
            {
                "row ID": "1",
                "row m/z": base_mz,
                "row retention time": 10.000,
                "Sample_A.mzML Peak area": 1000.0,
                "Sample_B.mzML Peak area": 900.0,
            },
            {
                "row ID": "2",
                "row m/z": base_mz + 1.002111,
                "row retention time": 10.001,
                "Sample_A.mzML Peak area": 800.0,
                "Sample_B.mzML Peak area": 700.0,
            },
            {
                "row ID": "3",
                "row m/z": base_mz + 2.002271,
                "row retention time": 10.002,
                "Sample_A.mzML Peak area": 200.0,
                "Sample_B.mzML Peak area": 180.0,
            },
            {
                "row ID": "4",
                "row m/z": base_mz + 4.005213,
                "row retention time": 10.003,
                "Sample_A.mzML Peak area": 50.0,
                "Sample_B.mzML Peak area": 0,
            },
        ],
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        mzmine_feature_table_csv=mzmine_table,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    prepared_csv = output_dir / "detected_features.csv"
    assert prepared_csv.exists()
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_A"
    assert rows[0]["base_area"] == "1000"
    assert rows[0]["local_signal_support_status"] == "feature_source_detected"
    assert rows[0]["evidence_tier"] == "B"
    assert rows[0]["evidence_score"] == "70"
    assert "feature source support only" in rows[0]["reason"]
    with outputs.review_csv.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.DictReader(handle))
    assert "feature-source support only" in review_rows[0]["review_note"]

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(prepared_csv)
    assert payload["inputs"]["external_feature_table_tsv"] is None
    assert payload["inputs"]["mzmine_feature_table_csv"] == str(mzmine_table)


def test_composite_mzmine_feature_source_uses_mz_rt_fallback(
    tmp_path: Path,
) -> None:
    mzmine_table = tmp_path / "mzmine_features.csv"
    base_mz = 500.0
    mzmine_table.write_text(
        "row ID,row m/z,row retention time,Sample_1.raw Peak area\n"
        f"1,{base_mz},10.000,1000.0\n"
        f"2,{base_mz + 1.002111},10.001,800.0\n"
        f"3,{base_mz + 2.002271},10.002,200.0\n"
        f"4,{base_mz + 4.005213},10.003,50.0\n",
        encoding="utf-8",
    )
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state,"
        "precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-mzmine,Sample_1|DNA_dR|500.000000|10.000000,DNA_dR,"
        "ms1_feature_nl_supported,500.0,10.000,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        mzmine_feature_table_csv=mzmine_table,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_1"
    assert rows[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows[0]["cid_nl_base_join_mode"] == "mz_rt_fallback"
    assert rows[0]["cid_nl_base_candidate_id"] == "cid-mzmine"


def test_composite_identity_less_external_source_uses_mz_rt_fallback(
    tmp_path: Path,
) -> None:
    external_table = tmp_path / "external_features.tsv"
    external_rows = [
        {
            "external_tool": "OpenMS",
            "sample_id": row["sample_stem"],
            "feature_id": row["feature_id"],
            "source_feature_row_id": "",
            "mz": row["mz"],
            "rt": row["rt_apex_min"],
            "rt_start_min": row.get("rt_start_min", ""),
            "rt_end_min": row.get("rt_end_min", ""),
            "area_or_intensity": row["height"],
            "area": row.get("area", ""),
            "trace_quality": row["trace_quality"],
            "shape_score": row.get("shape_score", ""),
        }
        for row in _full_pattern_rows()
    ]
    _write_external_feature_table(external_table, external_rows)
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state,"
        "precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-openms,Sample_1|unrelated-detector-id,DNA_dR,"
        "ms1_feature_nl_supported,"
        "500.0,10.0,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        external_feature_table_tsv=external_table,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows[0]["cid_nl_base_join_mode"] == "mz_rt_fallback"
    assert rows[0]["cid_nl_base_candidate_id"] == "cid-openms"


def test_composite_external_detector_local_id_uses_mz_rt_fallback(
    tmp_path: Path,
) -> None:
    external_table = tmp_path / "external_features.tsv"
    external_rows = [
        {
            "external_tool": "OpenMS",
            "sample_id": row["sample_stem"],
            "feature_id": row["feature_id"],
            "source_feature_row_id": f"openms-local-{index}",
            "mz": row["mz"],
            "rt": row["rt_apex_min"],
            "rt_start_min": row.get("rt_start_min", ""),
            "rt_end_min": row.get("rt_end_min", ""),
            "area_or_intensity": row["height"],
            "area": row.get("area", ""),
            "trace_quality": row["trace_quality"],
            "shape_score": row.get("shape_score", ""),
        }
        for index, row in enumerate(_full_pattern_rows(), start=1)
    ]
    _write_external_feature_table(external_table, external_rows)
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state,"
        "precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-openms,Sample_1|unrelated-detector-id,DNA_dR,"
        "ms1_feature_nl_supported,500.0,10.0,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        external_feature_table_tsv=external_table,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["cid_nl_base_join_mode"] == "mz_rt_fallback"
    assert rows[0]["cid_nl_base_candidate_id"] == "cid-openms"


def test_composite_rejects_duplicate_cid_nl_headers(tmp_path: Path) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt,evidence_score\n"
        "first,last,Sample_1|base,DNA_dR,ms1_feature_nl_supported,500,10,90\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate columns: candidate_id"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=tmp_path / "out",
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=cid_nl_candidates_csv,
        )


def test_composite_local_detector_identity_uses_sample_local_mz_rt_fallback(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    sample_a = _full_pattern_rows("Sample_A")
    for row in sample_a:
        row["source"] = "local_ms1_feature"
        row["trace_quality"] = "OK"
        # Backward-compatible old local artifacts carried this detector-local
        # pointer even though it is not comparable to CID-NL row identity.
        row["source_feature_row_id"] = row["feature_id"]
    sample_b = _full_pattern_rows("Sample_B")
    for row in sample_b:
        row["source_feature_row_id"] = f"Sample_B|source|{row['feature_id']}"
    _write_detected_features_csv(detected_features_csv, [*sample_a, *sample_b])
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-a,Sample_A|unrelated-cid-id,DNA_dR,ms1_feature_nl_supported,"
        "500.0,10.0,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_A"
    assert rows[0]["cid_nl_base_join_mode"] == "mz_rt_fallback"
    assert rows[0]["cid_nl_base_candidate_id"] == "cid-a"


@pytest.mark.parametrize(
    "source",
    (
        "local_ms1_feature",
        "external_feature_table:MZmine:mzmine_wide_area_proxy",
        "external_feature_table:OpenMS:contract",
    ),
)
def test_composite_detector_local_matching_string_cannot_bypass_mz_rt_fallback(
    tmp_path: Path,
    source: str,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows("Sample_A")
    collision_id = "Sample_A|detector-local-collision"
    rows[0]["feature_id"] = collision_id
    for row in rows:
        row["source"] = source
        row["source_feature_row_id"] = row["feature_id"]
        row["trace_quality"] = "OK"
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt,evidence_score\n"
        f"cid-far,{collision_id},DNA_dR,ms1_feature_nl_supported,900.0,99.0,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []


@pytest.mark.parametrize(
    ("raw_candidate_id", "raw_ms1_feature_row_id", "raw_sample_stem", "field"),
    (
        ("+cid", "Sample_1|DNA_dR|500|10", "Sample_1", "candidate_id"),
        ("cid", "+Sample|DNA_dR|500|10", None, "ms1_feature_row_id"),
        ("cid", "Sample_1|DNA_dR|500|10", "+Sample", "sample_stem"),
    ),
)
def test_composite_rejects_formula_escaped_legacy_machine_identity(
    tmp_path: Path,
    raw_candidate_id: str,
    raw_ms1_feature_row_id: str,
    raw_sample_stem: str | None,
    field: str,
) -> None:
    """The legacy producer escapes formula-like strings with an apostrophe.

    The consumer cannot distinguish that output from a genuine leading
    apostrophe, so identity joins must fail closed instead of unescaping or
    silently producing zero matches.
    """

    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    fieldnames = (
        "candidate_id",
        "sample_stem",
        "ms1_feature_row_id",
        "neutral_loss_tag",
        "discovery_candidate_state",
        "precursor_mz",
        "ms1_apex_rt",
    )
    source_values: dict[str, object] = {
        "candidate_id": raw_candidate_id,
        "sample_stem": raw_sample_stem,
        "ms1_feature_row_id": raw_ms1_feature_row_id,
        "neutral_loss_tag": "DNA_dR",
        "discovery_candidate_state": "ms1_feature_nl_supported",
        "precursor_mz": 500.0,
        "ms1_apex_rt": 10.0,
    }
    with cid_nl_candidates_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                column: format_discovery_csv_value(column, source_values[column])
                for column in fieldnames
            }
        )

    with pytest.raises(
        ValueError,
        match=rf"formula-escaped machine identity.*{field}",
    ):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=tmp_path / "out",
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=cid_nl_candidates_csv,
        )


def test_composite_rejects_conflicting_explicit_and_derived_sample_identity(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows("Sample_A"))
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,sample_stem,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt\n"
        "cid,Sample_A,Sample_B|DNA_dR|500|10,DNA_dR,"
        "ms1_feature_nl_supported,500.0,10.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sample_stem.*does not match"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=tmp_path / "out",
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=cid_nl_candidates_csv,
        )


@pytest.mark.parametrize(
    "contents",
    (
        (
            "ms1_feature_row_id,neutral_loss_tag,discovery_candidate_state\n"
            "Sample_1|DNA_dR|500|10,DNA_dR,ms1_feature_nl_supported\n"
        ),
        (
            "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
            "discovery_candidate_state\n"
            ",Sample_1|DNA_dR|500|10,DNA_dR,ms1_feature_nl_supported\n"
        ),
    ),
)
def test_composite_requires_nonblank_cid_nl_candidate_id(
    tmp_path: Path,
    contents: str,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_id"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=tmp_path / "out",
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=cid_nl_candidates_csv,
        )


def test_run_pt_pattern_discovery_requires_cid_nl_csv_for_composite_method(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="cid_nl_candidates_csv"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=output_dir,
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=None,
        )


def test_run_pt_pattern_discovery_rejects_non_pt_pattern_preset(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="pt_pattern"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=output_dir,
            preset_name="dna_dr",
            cid_nl_candidates_csv=None,
        )


def test_run_pt_pattern_discovery_composite_marks_matched_base_as_supported(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    rows[0]["source"] = CID_NL_SHARED_IDENTITY_SOURCE
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,ms1_apex_rt,evidence_score\n"
        "cid-lower,Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,"
        "ms1_feature_nl_supported,10.0,50\n"
        "cid-selected,Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,"
        "ms1_feature_nl_supported,10.0,90\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows_out[0]["cid_nl_base_status"] == "matched"
    assert rows_out[0]["cid_nl_base_candidate_id"] == "cid-selected"
    assert rows_out[0]["cid_nl_base_join_mode"] == "same_feature_id"

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert evidence_rows[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert evidence_rows[0]["trace_rule_call"] == "not_evaluated"


def test_composite_declared_identity_mismatch_does_not_fall_back(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source"] = CID_NL_SHARED_IDENTITY_SOURCE
    rows[0]["source_feature_row_id"] = "cid-shared-a"
    _write_detected_features_csv(detected_features_csv, rows)
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-row,cid-shared-b,DNA_dR,ms1_feature_nl_supported,500.0,10.0,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []


def test_composite_rejects_candidate_without_detected_feature_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state,precursor_mz,ms1_apex_rt,evidence_score\n"
        "cid-row,Sample_1|unrelated,DNA_dR,ms1_feature_nl_supported,"
        "500.0,10.0,90\n",
        encoding="utf-8",
    )

    def _unowned_match(*args: object, **kwargs: object) -> object:
        candidates = real_match_delta_mass_pattern(*args, **kwargs)  # type: ignore[arg-type]
        return tuple(
            replace(candidate, base_feature_id="missing-feature-owner")
            for candidate in candidates
        )

    monkeypatch.setattr(
        "xic_extractor.discovery.delta_mass_pipeline.match_delta_mass_pattern",
        _unowned_match,
    )

    with pytest.raises(ValueError, match="no DetectedFeature identity owner"):
        run_pt_pattern_discovery(
            detected_features_csv=detected_features_csv,
            output_dir=tmp_path / "out",
            preset_name="pt_pattern_cid_nl_dr",
            cid_nl_candidates_csv=cid_nl_candidates_csv,
        )


def test_composite_declared_feature_id_can_join_without_source_row_id(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["feature_id"] = "Sample_1|cid-shared-feature"
    rows[0]["source"] = CID_NL_SHARED_IDENTITY_SOURCE
    _write_detected_features_csv(detected_features_csv, rows)
    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,sample_stem,neutral_loss_tag,"
        "discovery_candidate_state,evidence_score\n"
        "cid-row,Sample_1|cid-shared-feature,Sample_1,DNA_dR,"
        "ms1_feature_nl_supported,90\n",
        encoding="utf-8",
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        output_rows = list(csv.DictReader(handle))
    assert len(output_rows) == 1
    assert output_rows[0]["cid_nl_base_join_mode"] == "same_feature_id"


def test_composite_uses_cid_nl_base_seeds_before_matching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    rows[0]["source"] = CID_NL_SHARED_IDENTITY_SOURCE
    # Decoy full pattern: Pt-only would match it, but composite seed-first must
    # not even offer this base to the matcher because CID-NL does not seed it.
    decoy_rows = _full_pattern_rows()
    for row in decoy_rows:
        row["mz"] = float(row["mz"]) + 200.0
        row["feature_id"] = f"decoy-{row['feature_id']}"
    rows.extend(decoy_rows)
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state\n"
        "cid-winner,Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,"
        "ms1_feature_nl_supported\n",
        encoding="utf-8",
    )
    seen_base_feature_ids: set[str | tuple[str, str]] | None = None

    def _capturing_match_delta_mass_pattern(
        features: object,
        *,
        settings: object,
        base_feature_ids: set[str | tuple[str, str]] | None = None,
    ) -> object:
        nonlocal seen_base_feature_ids
        seen_base_feature_ids = set(base_feature_ids or ())
        return real_match_delta_mass_pattern(
            features,  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            base_feature_ids=base_feature_ids,
        )

    monkeypatch.setattr(
        "xic_extractor.discovery.delta_mass_pipeline.match_delta_mass_pattern",
        _capturing_match_delta_mass_pattern,
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    assert seen_base_feature_ids == {("Sample_1", "base-1")}
    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["base_feature_id"] == "base-1"
    assert rows_out[0]["cid_nl_base_candidate_id"] == "cid-winner"


def test_run_pt_pattern_discovery_sidecar_records_input_paths_as_provided_strings(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["detected_features_csv"] == str(detected_features_csv)
    assert payload["inputs"]["external_feature_table_tsv"] is None
    assert payload["inputs"]["mzmine_feature_table_csv"] is None
    assert payload["inputs"]["cid_nl_candidates_csv"] is None
    assert payload["inputs"]["trace_summary_csv"] is None


def test_run_pt_pattern_discovery_sidecar_records_effective_matcher_tolerances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    monkeypatch.setattr(
        "xic_extractor.discovery.delta_mass_pipeline.apply_to_delta_mass_discovery",
        lambda _preset: DeltaMassPatternSettings(
            mz_tolerance_ppm=7.5,
            rt_tolerance_sec=12.0,
        ),
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern",
    )

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["pattern"]["mz_tolerance_ppm"] == 7.5
    assert payload["pattern"]["rt_tolerance_sec"] == 12.0


def test_run_pt_pattern_discovery_applies_optional_trace_summary_evidence(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    _write_detected_features_csv(detected_features_csv, _full_pattern_rows())
    trace_summary_csv = tmp_path / "trace_summary.csv"
    candidate_id = "Sample_1|pt|base:base-1|mz:500.000000|rt:10.000000"
    trace_candidate_row = {
        "sample_stem": "Sample_1",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": candidate_id,
        "base_feature_id": "base-1",
        "pt_plus_1_feature_id": "plus1-1",
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_4_feature_id": "plus4-1",
        "base_mz": 500.0,
        "base_rt_apex_min": 10.0,
        "pt_plus_1_mz": 501.002111,
        "pt_plus_1_expected_mz": 501.002111,
        "pt_plus_2_mz": 502.002271,
        "pt_plus_2_expected_mz": 502.002271,
        "pt_plus_4_mz": 504.005213,
        "pt_plus_4_expected_mz": 504.005213,
    }
    assignment_sha256 = pt_pattern_candidate_assignment_fingerprint(
        trace_candidate_row
    )
    trace_bundle = write_trace_summary_lineage_bundle(
        trace_summary_csv=trace_summary_csv,
        summary_rows=[
            full_context_trace_summary_row(
                candidate_id=candidate_id,
                candidate_assignment_sha256=assignment_sha256,
                    sample_stem="Sample_1",
                    trace_ambiguity_status="review_required",
                    trace_ambiguity_flags="position_shift_direction_mixed",
            )
        ],
        candidate_rows=[trace_candidate_row],
    )

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=tmp_path / "out",
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
        trace_summary_csv=trace_summary_csv,
    )

    with outputs.evidence_csv.open(newline="", encoding="utf-8") as handle:
        evidence_rows = list(csv.DictReader(handle))
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["trace_evidence_status"] == "evaluated"
    assert evidence_rows[0]["trace_rule_call"] == "accept"
    assert evidence_rows[0]["trace_detection_status"] == "detected"
    assert evidence_rows[0]["trace_rule_reason"] == (
        "full_pattern_close_apex_shape_overlap"
    )
    assert evidence_rows[0]["trace_ambiguity_status"] == "review_required"
    assert evidence_rows[0]["trace_ambiguity_flags"] == (
        "position_shift_direction_mixed"
    )
    assert evidence_rows[0]["shifted_only_position_labels"] == "none"
    assert evidence_rows[0]["trace_schema_version"] == (
        "pt_pattern_trace_diagnosis"
    )
    assert evidence_rows[0]["trace_artifact_role"] == "full_context"
    assert evidence_rows[0]["trace_extraction_sha256"]
    assert evidence_rows[0]["trace_manifest_sha256"]

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["inputs"]["trace_summary_csv"] == str(trace_summary_csv)
    assert payload["inputs"]["trace_metrics_csv"] == str(
        trace_bundle["trace_metrics_csv"]
    )
    assert payload["inputs"]["trace_manifest_json"] == str(
        trace_bundle["trace_manifest_json"]
    )
    assert payload["inputs"]["trace_artifact_role"] == "full_context"
    assert payload["summary"]["trace_rule_call_counts"] == {"accept": 1}


def test_run_pt_pattern_discovery_ignores_review_only_orphan_nl_rows(
    tmp_path: Path,
) -> None:
    """Regression test: real ``discovery_candidates.csv`` files from the
    existing ``scripts.run_discovery`` CLI contain a mix of join-eligible
    rows (``ms1_feature_nl_supported``/``ms1_feature_nl_rescued``, with a
    populated ``ms1_feature_row_id``) and ``review_only_orphan_nl`` rows
    (blank ``ms1_feature_row_id`` by design, since they are not backed by
    any MS1 feature). The composite pipeline must not raise on the orphan
    rows and must simply exclude them from join consideration.
    """

    detected_features_csv = tmp_path / "detected_features.csv"
    rows = _full_pattern_rows()
    rows[0]["source_feature_row_id"] = "Sample_1|DNA_dR|258.108512|7.841234"
    rows[0]["source"] = CID_NL_SHARED_IDENTITY_SOURCE
    _write_detected_features_csv(detected_features_csv, rows)

    cid_nl_candidates_csv = tmp_path / "discovery_candidates.csv"
    cid_nl_candidates_csv.write_text(
        "candidate_id,ms1_feature_row_id,neutral_loss_tag,"
        "discovery_candidate_state\n"
        "cid-supported,Sample_1|DNA_dR|258.108512|7.841234,DNA_dR,"
        "ms1_feature_nl_supported\n"
        "cid-orphan-1,,DNA_dR,review_only_orphan_nl\n"
        "cid-orphan-2,,DNA_dR,review_only_orphan_nl\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern_cid_nl_dr",
        cid_nl_candidates_csv=cid_nl_candidates_csv,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows_out = list(csv.DictReader(handle))
    assert len(rows_out) == 1
    assert rows_out[0]["candidate_state"] == "pt_pattern_cid_nl_supported"
    assert rows_out[0]["cid_nl_base_status"] == "matched"
    assert rows_out[0]["cid_nl_base_candidate_id"] == "cid-supported"
    assert rows_out[0]["cid_nl_base_join_mode"] == "same_feature_id"


def test_run_pt_pattern_discovery_writes_header_only_outputs_when_no_pattern_found(
    tmp_path: Path,
) -> None:
    detected_features_csv = tmp_path / "detected_features.csv"
    partial_rows = _full_pattern_rows()[:2]  # base + plus1 only, missing plus2/plus4
    _write_detected_features_csv(detected_features_csv, partial_rows)
    output_dir = tmp_path / "out"

    outputs = run_pt_pattern_discovery(
        detected_features_csv=detected_features_csv,
        output_dir=output_dir,
        preset_name="pt_pattern",
        cid_nl_candidates_csv=None,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1  # header only, no candidate rows

    payload = json.loads(outputs.sidecar_json.read_text(encoding="utf-8"))
    assert payload["summary"]["candidate_count"] == 0
