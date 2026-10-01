from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_replay as replay_cli
from xic_extractor.discovery.detected_features import DETECTED_FEATURES_COLUMNS
from xic_extractor.discovery.pt_pattern_conflict_graph_replay import (
    run_conflict_graph_replay,
)
from xic_extractor.tabular_io import write_delimited_rows


def test_conflict_graph_replay_writes_pairwise_sidecars_and_legacy_comparison(
    tmp_path: Path,
) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    legacy_csv = tmp_path / "legacy_conflicts.csv"
    output_dir = tmp_path / "output"
    feature_ids = (
        "a0",
        "shared-ab",
        "a2",
        "a4",
        "b0",
        "shared-bc",
        "b4",
        "c0",
        "c1",
        "c4",
    )
    _write_features(features_csv, feature_ids)
    _write_candidates(
        candidates_csv,
        (
            ("A", ("a0", "shared-ab", "a2", "a4")),
            ("B", ("b0", "shared-ab", "shared-bc", "b4")),
            ("C", ("c0", "c1", "shared-bc", "c4")),
        ),
    )
    write_delimited_rows(
        legacy_csv,
        (
            {
                "component_id": "legacy-1",
                "candidate_ids": "A;B;C",
                "resolution_status": "ambiguous_multiple_strict_support",
                "preferred_candidate_id": "",
            },
        ),
        (
            "component_id",
            "candidate_ids",
            "resolution_status",
            "preferred_candidate_id",
        ),
    )

    outputs = run_conflict_graph_replay(
        candidates_csv=candidates_csv,
        detected_features_csv=features_csv,
        legacy_conflicts_csv=legacy_csv,
        output_dir=output_dir,
        report_id="fixture_graph",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    components = _read_csv(outputs["components_csv"])
    legacy = _read_csv(outputs["legacy_comparison_csv"])

    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["vertex_count"] == 3
    assert summary["edge_count"] == 2
    assert summary["component_count"] == 1
    assert summary["non_clique_multi_row_component_count"] == 1
    assert summary["pairwise_compatible_pair_count"] == 1
    assert summary["legacy_component_wide_at_most_one_overconstraint_count"] == 1
    assert summary["legacy_pairwise_compatible_pair_count"] == 1
    assert summary["legacy_ambiguous_component_count"] == 1
    assert summary["legacy_preferred_candidate_count"] == 0
    assert summary["selects_hypothesis"] is False
    assert summary["changes_current_machine_call"] is False
    assert components[0]["pairwise_compatible_pair_count"] == "1"
    assert components[0]["is_clique"] == "FALSE"
    assert (
        components[0]["component_wide_at_most_one_would_overconstrain"]
        == "TRUE"
    )
    assert legacy[0]["induced_edge_count"] == "2"
    assert legacy[0]["induced_pairwise_compatible_pair_count"] == "1"
    assert legacy[0]["legacy_preferred_candidate_id"] == ""


def test_conflict_graph_replay_fails_closed_when_claim_is_missing_from_source(
    tmp_path: Path,
) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    _write_features(features_csv, ("f1", "f2", "f3"))
    _write_candidates(
        candidates_csv,
        (("missing", ("f1", "f2", "f3", "not-in-source")),),
    )

    with pytest.raises(ValueError, match="not found in detected features"):
        run_conflict_graph_replay(
            candidates_csv=candidates_csv,
            detected_features_csv=features_csv,
            output_dir=tmp_path / "output",
            report_id="invalid_fixture",
        )


def test_conflict_graph_replay_resolves_sample_local_feature_identity(
    tmp_path: Path,
) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    feature_ids = ("base", "plus1", "plus2", "plus4")
    rows = []
    for sample_index, sample_stem in enumerate(("SampleA", "SampleB")):
        for feature_index, feature_id in enumerate(feature_ids):
            rows.append(
                {
                    "sample_stem": sample_stem,
                    "feature_id": feature_id,
                    "source_feature_row_id": "",
                    "mz": 500.0 + sample_index * 100.0 + feature_index,
                    "rt_apex_min": 10.0,
                    "rt_start_min": 9.9,
                    "rt_end_min": 10.1,
                    "height": 1000.0,
                    "area": 5000.0,
                    "trace_quality": "OK",
                    "shape_score": "",
                    "source": "local_ms1_feature",
                }
            )
    write_delimited_rows(features_csv, rows, DETECTED_FEATURES_COLUMNS)
    _write_candidates(candidates_csv, (("A", feature_ids),))

    outputs = run_conflict_graph_replay(
        candidates_csv=candidates_csv,
        detected_features_csv=features_csv,
        output_dir=tmp_path / "output",
        report_id="sample_local_identity",
    )

    vertices = _read_csv(outputs["vertices_csv"])
    assert len(vertices) == 1
    assert vertices[0]["sample_stem"] == "SampleA"


def test_conflict_graph_replay_cli_writes_summary(tmp_path: Path) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    output_dir = tmp_path / "output"
    _write_features(features_csv, ("f1", "f2", "f3", "f4"))
    _write_candidates(candidates_csv, (("single", ("f1", "f2", "f3", "f4")),))

    exit_code = replay_cli.main(
        (
            "conflicts",
            "--candidates-csv",
            str(candidates_csv),
            "--detected-features-csv",
            str(features_csv),
            "--output-dir",
            str(output_dir),
            "--report-id",
            "cli_fixture",
        )
    )

    assert exit_code == 0
    summary_path = output_dir / "pt_pattern_conflict_graph_summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "no_non_clique_component_observed"


def test_conflict_graph_summary_is_path_and_report_label_independent(
    tmp_path: Path,
) -> None:
    features_csv = tmp_path / "inputs" / "detected_features.csv"
    candidates_csv = tmp_path / "inputs" / "pt_pattern_candidates.csv"
    features_csv.parent.mkdir()
    _write_features(features_csv, ("f1", "f2", "f3", "f4"))
    _write_candidates(candidates_csv, (("single", ("f1", "f2", "f3", "f4")),))

    first = run_conflict_graph_replay(
        candidates_csv=candidates_csv,
        detected_features_csv=features_csv,
        output_dir=tmp_path / "first",
        report_id="first_label",
    )
    second = run_conflict_graph_replay(
        candidates_csv=candidates_csv,
        detected_features_csv=features_csv,
        output_dir=tmp_path / "second",
        report_id="second_label",
    )

    assert first["summary_json"].read_bytes() == second["summary_json"].read_bytes()
    first_metadata = json.loads(
        first["run_metadata_json"].read_text(encoding="utf-8")
    )
    second_metadata = json.loads(
        second["run_metadata_json"].read_text(encoding="utf-8")
    )
    assert first_metadata["report_id"] == "first_label"
    assert second_metadata["report_id"] == "second_label"


def test_conflict_graph_replay_rejects_unknown_candidate_schema(
    tmp_path: Path,
) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    _write_features(features_csv, ("f1", "f2", "f3", "f4"))
    _write_candidates(
        candidates_csv,
        (("single", ("f1", "f2", "f3", "f4")),),
        schema_version="unsupported_pt_pattern_candidates",
    )

    with pytest.raises(ValueError, match="unsupported schema_version"):
        run_conflict_graph_replay(
            candidates_csv=candidates_csv,
            detected_features_csv=features_csv,
            output_dir=tmp_path / "output",
            report_id="invalid_schema",
        )


@pytest.mark.parametrize(
    ("resolution_status", "preferred_candidate_id", "message"),
    (
        ("made_up", "", "unsupported resolution_status"),
        (
            "unique_strict_support",
            "not-a-member",
            "preferred candidate is not a member",
        ),
        (
            "ambiguous_multiple_strict_support",
            "A",
            "must not name a preferred candidate",
        ),
        (
            "unique_core_support",
            "A",
            "must not name a preferred candidate",
        ),
    ),
)
def test_conflict_graph_replay_validates_legacy_resolution_semantics(
    tmp_path: Path,
    resolution_status: str,
    preferred_candidate_id: str,
    message: str,
) -> None:
    features_csv = tmp_path / "detected_features.csv"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    legacy_csv = tmp_path / "legacy.csv"
    _write_features(features_csv, ("shared", "a1", "a2", "a4", "b1", "b2", "b4"))
    _write_candidates(
        candidates_csv,
        (
            ("A", ("shared", "a1", "a2", "a4")),
            ("B", ("shared", "b1", "b2", "b4")),
        ),
    )
    write_delimited_rows(
        legacy_csv,
        (
            {
                "component_id": "legacy-1",
                "candidate_ids": "A;B",
                "resolution_status": resolution_status,
                "preferred_candidate_id": preferred_candidate_id,
            },
        ),
        (
            "component_id",
            "candidate_ids",
            "resolution_status",
            "preferred_candidate_id",
        ),
    )

    with pytest.raises(ValueError, match=message):
        run_conflict_graph_replay(
            candidates_csv=candidates_csv,
            detected_features_csv=features_csv,
            legacy_conflicts_csv=legacy_csv,
            output_dir=tmp_path / "output",
            report_id="invalid_legacy",
        )


def _write_features(path: Path, feature_ids: tuple[str, ...]) -> None:
    rows = []
    for index, feature_id in enumerate(feature_ids, start=1):
        rows.append(
            {
                "sample_stem": "SampleA",
                "feature_id": feature_id,
                "source_feature_row_id": feature_id,
                "mz": 500.0 + index,
                "rt_apex_min": 10.0,
                "rt_start_min": 9.9,
                "rt_end_min": 10.1,
                "height": 1000.0,
                "area": 5000.0,
                "trace_quality": "OK",
                "shape_score": "",
                "source": "local_ms1_feature",
            }
        )
    write_delimited_rows(path, rows, DETECTED_FEATURES_COLUMNS)


def _write_candidates(
    path: Path,
    assignments: tuple[tuple[str, tuple[str, str, str, str]], ...],
    *,
    schema_version: str = "pt_pattern_candidates",
) -> None:
    rows = [
        {
            "schema_version": schema_version,
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "base_feature_id": feature_ids[0],
            "pt_plus_1_feature_id": feature_ids[1],
            "pt_plus_2_feature_id": feature_ids[2],
            "pt_plus_4_feature_id": feature_ids[3],
        }
        for candidate_id, feature_ids in assignments
    ]
    write_delimited_rows(
        path,
        rows,
        (
            "schema_version",
            "sample_stem",
            "candidate_id",
            "base_feature_id",
            "pt_plus_1_feature_id",
            "pt_plus_2_feature_id",
            "pt_plus_4_feature_id",
        ),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
