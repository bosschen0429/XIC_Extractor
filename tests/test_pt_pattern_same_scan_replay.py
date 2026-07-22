from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tests.pt_pattern_evidence_fixtures import pt_evidence_row
from xic_extractor.discovery import pt_pattern_same_scan_replay as replay
from xic_extractor.discovery.pt_pattern_same_scan_evidence import SameScanSettings
from xic_extractor.discovery.pt_pattern_same_scan_replay import run_same_scan_replay
from xic_extractor.ms1_scan_index import MS1Scan


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    prepared_rows = [
        _complete_candidate_assignment(row)
        if "base_feature_id" in row
        else dict(row)
        for row in rows
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(prepared_rows[0]))
        writer.writeheader()
        writer.writerows(prepared_rows)


def _complete_candidate_assignment(row: dict[str, str]) -> dict[str, str]:
    base_mz = float(row.get("base_mz", "500"))
    completed = {
        "sample_stem": row.get("sample_stem", "SampleA"),
        "method_label": row.get("method_label", "pt_pattern"),
        "pattern_id": row.get("pattern_id", "pt"),
        "candidate_id": row.get("candidate_id", "candidate-a"),
        "candidate_state": row.get(
            "candidate_state",
            "pt_pattern_candidate",
        ),
        "base_feature_id": row.get("base_feature_id", "f1"),
        "base_mz": str(base_mz),
        "base_rt_apex_min": row.get("base_rt_apex_min", "10.0"),
        "pt_plus_1_feature_id": row.get("pt_plus_1_feature_id", "f2"),
        "pt_plus_1_mz": str(base_mz + 1.002111),
        "pt_plus_1_expected_mz": str(base_mz + 1.002111),
        "pt_plus_2_feature_id": row.get("pt_plus_2_feature_id", "f3"),
        "pt_plus_2_mz": str(base_mz + 2.002271),
        "pt_plus_2_expected_mz": str(base_mz + 2.002271),
        "pt_plus_4_feature_id": row.get("pt_plus_4_feature_id", "f4"),
        "pt_plus_4_mz": str(base_mz + 4.005213),
        "pt_plus_4_expected_mz": str(base_mz + 4.005213),
    }
    completed.update(row)
    return completed


def _evidence_row(
    candidate_id: str,
    *,
    trace_rule_call: str = "accept",
    sample_stem: str = "SampleA",
    method_label: str = "pt_pattern",
    pattern_id: str = "pt",
    base_mz: str = "500.0",
    base_rt_apex_min: str = "10.0",
    base_feature_id: str = "f1",
    pt_plus_1_feature_id: str = "f2",
    pt_plus_2_feature_id: str = "f3",
    pt_plus_4_feature_id: str = "f4",
) -> dict[str, str]:
    candidate = _complete_candidate_assignment(
        {
            "sample_stem": sample_stem,
            "method_label": method_label,
            "pattern_id": pattern_id,
            "candidate_id": candidate_id,
            "base_feature_id": base_feature_id,
            "base_mz": base_mz,
            "base_rt_apex_min": base_rt_apex_min,
            "pt_plus_1_feature_id": pt_plus_1_feature_id,
            "pt_plus_2_feature_id": pt_plus_2_feature_id,
            "pt_plus_4_feature_id": pt_plus_4_feature_id,
        }
    )
    return {
        field: str(value)
        for field, value in pt_evidence_row(
            candidate,
            trace_rule_call=trace_rule_call,
        ).items()
    }


def _index() -> tuple[MS1Scan, ...]:
    scans = []
    for scan_number, rt_min, scale in ((100, 10.00, 1.0), (101, 10.02, 1.2)):
        masses = np.asarray(
            [
                500.0,
                501.002111,
                502.002271,
                504.005213,
                600.0,
                601.002111,
                602.002271,
            ],
            dtype=np.float64,
        )
        intensities = np.asarray(
            [
                90_000 * scale,
                120_000 * scale,
                80_000 * scale,
                40_000 * scale,
                90_000 * scale,
                120_000 * scale,
                80_000 * scale,
            ],
            dtype=np.float64,
        )
        scans.append(
            MS1Scan(
                scan_number=scan_number,
                rt=rt_min,
                masses=masses,
                intensities=intensities,
            )
        )
    return tuple(scans)


def test_same_scan_replay_writes_contract_comparison_and_conflict_audit(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    known_csv = tmp_path / "known.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.01",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            },
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "candidate-b",
                "base_mz": "520.0",
                "base_rt_apex_min": "10.01",
                "base_feature_id": "f4",
                "pt_plus_1_feature_id": "f5",
                "pt_plus_2_feature_id": "f6",
                "pt_plus_4_feature_id": "f7",
            },
        ],
    )
    _write_csv(
        evidence_csv,
        [
            _evidence_row("candidate-a", base_rt_apex_min="10.01"),
            _evidence_row(
                "candidate-b",
                base_mz="520.0",
                base_rt_apex_min="10.01",
                base_feature_id="f4",
                pt_plus_1_feature_id="f5",
                pt_plus_2_feature_id="f6",
                pt_plus_4_feature_id="f7",
            ),
        ],
    )
    _write_csv(
        known_csv,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate; feature_base_exists",
            },
            {
                "compound": "d3-dG-C8-MeIQx",
                "expected_base_mz": "600.0",
                "diagnostic_status": "no_candidate_within20",
            },
        ],
    )

    outputs = run_same_scan_replay(
        index=_index(),
        candidates_csv=candidates_csv,
        evidence_csv=evidence_csv,
        known_diagnosis_csv=known_csv,
        output_dir=tmp_path / "report",
        report_id="synthetic",
        settings=SameScanSettings(noise_level=25_000.0, minimum_joint_scans=2),
        candidate_rt_half_window_sec=45.0,
        source_sample_stem="SampleA",
        index_input=_write_index_identity(tmp_path),
    )

    with outputs["summary_json"].open(encoding="utf-8") as handle:
        summary = json.load(handle)
    with outputs["evidence_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with outputs["conflicts_csv"].open(newline="", encoding="utf-8") as handle:
        conflict_rows = list(csv.DictReader(handle))

    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["current_hypothesis_count"] == 2
    assert summary["known_cp_expected_positive_count"] == 1
    assert summary["known_cp_strict_rescue_count"] == 0
    assert summary["known_non_pt_control_count"] == 1
    assert summary["conflict_component_count"] == 1
    assert summary["uniquely_resolved_conflict_component_count"] == 1
    assert summary["d3_core_contract_supported"] is True
    assert summary["d3_strict_contract_supported"] is False
    assert summary["source_sample_stem"] == "SampleA"
    assert summary["index_input"]["sha256"]

    by_seed = {(row["source_set"], row["seed_id"]): row for row in rows}
    assert by_seed[("current_feature_hypothesis", "candidate-a")][
        "contract_b_strict_full_same_scan"
    ] == "TRUE"
    assert by_seed[("current_feature_hypothesis", "candidate-b")][
        "contract_b_strict_full_same_scan"
    ] == "FALSE"
    assert by_seed[("known_named", "known:base_mz=500")]["expected_class"] == (
        "expected_positive"
    )
    assert by_seed[("known_named", "known:base_mz=500")][
        "contract_a_current_feature_accept"
    ] == "TRUE"
    assert by_seed[("known_named", "known:base_mz=600")]["expected_class"] == (
        "non_pt_control"
    )
    assert conflict_rows[0]["resolution_status"] == "unique_strict_support"
    assert conflict_rows[0]["preferred_candidate_id"] == "candidate-a"

    html = outputs["html"].read_text(encoding="utf-8")
    assert "same-scan" in html
    assert "d3 control" in html
    assert "diagnostic_only" in html
    assert "Fullscan MS1 index" in html


@pytest.mark.parametrize("window", [True, "45"])
def test_same_scan_replay_rejects_invalid_outer_window(
    tmp_path: Path,
    window: object,
) -> None:
    with pytest.raises(ValueError, match="candidate_rt_half_window_sec"):
        run_same_scan_replay(
            index=_index(),
            candidates_csv=tmp_path / "missing-candidates.csv",
            evidence_csv=tmp_path / "missing-evidence.csv",
            known_diagnosis_csv=tmp_path / "missing-known.csv",
            output_dir=tmp_path / "report",
            report_id="synthetic",
            source_sample_stem="SampleA",
            index_input=tmp_path / "missing-index.npz",
            candidate_rt_half_window_sec=window,  # type: ignore[arg-type]
        )


def test_core_only_component_is_not_counted_as_discovery_resolution() -> None:
    summary = replay._summary(
        evidence_rows=(),
        conflict_rows=(
            {
                "resolution_status": "unique_core_support",
                "preferred_candidate_id": "",
            },
        ),
        index=(),
        settings=SameScanSettings(),
        report_id="synthetic",
        index_source="fixture",
        candidate_rt_half_window_sec=45.0,
        inputs={},
    )

    assert summary["schema_version"] == "pt_pattern_same_scan_replay.v3"
    assert summary["conflict_resolution_counts"] == {"unique_core_support": 1}
    assert summary["uniquely_resolved_conflict_component_count"] == 0


def test_same_scan_summary_keeps_input_roles_when_basenames_collide(
    tmp_path: Path,
) -> None:
    candidates = tmp_path / "candidates" / "input.csv"
    evidence = tmp_path / "evidence" / "input.csv"
    candidates.parent.mkdir()
    evidence.parent.mkdir()
    candidates.write_text("candidate\n", encoding="utf-8")
    evidence.write_text("evidence\n", encoding="utf-8")

    summary = replay._summary(
        evidence_rows=(),
        conflict_rows=(),
        index=(),
        settings=SameScanSettings(),
        report_id="colliding_basenames",
        index_source="fixture",
        candidate_rt_half_window_sec=45.0,
        inputs={
            "candidates_csv": candidates,
            "evidence_csv": evidence,
        },
    )

    assert set(summary["inputs"]) == {"candidates_csv", "evidence_csv"}
    assert summary["inputs"]["candidates_csv"]["path"] == str(candidates)
    assert summary["inputs"]["evidence_csv"]["path"] == str(evidence)


def test_candidate_contexts_fail_closed_on_missing_accepted_id(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(
        evidence_csv,
        [
            _evidence_row("candidate-a"),
            _evidence_row("candidate-missing"),
        ],
    )

    with pytest.raises(ValueError, match="extra evidence keys: candidate-missing"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_reject_stale_evidence_coordinates(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(
        evidence_csv,
        [
            _evidence_row("candidate-a", base_mz="501.0")
        ],
    )

    with pytest.raises(ValueError, match="base_mz.*does not match"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_reject_stale_partner_assignment(tmp_path: Path) -> None:
    candidate = _complete_candidate_assignment(
        {
            "sample_stem": "SampleA",
            "method_label": "pt_pattern",
            "pattern_id": "pt",
            "candidate_id": "candidate-a",
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "base_feature_id": "f1",
            "pt_plus_1_feature_id": "f2",
            "pt_plus_2_feature_id": "f3",
            "pt_plus_4_feature_id": "f8",
        }
    )
    stale_evidence = _evidence_row("candidate-a")
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(candidates_csv, [candidate])
    _write_csv(evidence_csv, [stale_evidence])

    with pytest.raises(ValueError, match="candidate_assignment_sha256"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_validate_rejected_evidence_before_filtering(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(
        evidence_csv,
        [
            _evidence_row(
                "candidate-a",
                trace_rule_call="reject",
                base_mz="501.0",
            )
        ],
    )

    with pytest.raises(ValueError, match="base_mz.*does not match"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_fail_closed_on_duplicate_accepted_rows(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    candidate = {
        "sample_stem": "SampleA",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "candidate-a",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "base_feature_id": "f1",
        "pt_plus_1_feature_id": "f2",
        "pt_plus_2_feature_id": "f3",
        "pt_plus_4_feature_id": "f4",
    }
    _write_csv(candidates_csv, [candidate, candidate])
    _write_csv(
        evidence_csv,
        [_evidence_row("candidate-a")],
    )

    with pytest.raises(
        ValueError,
        match="duplicate candidate candidate_id: candidate-a",
    ):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


@pytest.mark.parametrize(
    "evidence_rows",
    (
        (
            _evidence_row("candidate-a"),
            _evidence_row("candidate-a"),
        ),
        (
            _evidence_row("candidate-a"),
            _evidence_row("candidate-a", trace_rule_call="reject"),
        ),
        (
            _evidence_row("candidate-a", trace_rule_call="reject"),
            _evidence_row("candidate-a"),
        ),
    ),
)
def test_candidate_contexts_reject_duplicate_evidence_identity_before_filtering(
    tmp_path: Path,
    evidence_rows: tuple[dict[str, str], ...],
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(evidence_csv, list(evidence_rows))

    with pytest.raises(ValueError, match="duplicate evidence candidate_id"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_reject_blank_candidate_identity_before_filtering(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "candidate_id": " ",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(
        evidence_csv,
        [_evidence_row("candidate-a", trace_rule_call="reject")],
    )

    with pytest.raises(ValueError, match="candidate_id must be non-empty"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"pt_plus_4_feature_id": ""}, "pt_plus_4_feature_id must be non-empty"),
        ({"pt_plus_4_feature_id": "f3"}, "assignment IDs must be distinct"),
        ({"pt_plus_4_feature_id": " f3 "}, "assignment IDs must be distinct"),
    ),
)
def test_candidate_contexts_reject_incomplete_or_duplicate_feature_claims(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    candidate = {
        "sample_stem": "SampleA",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "candidate-a",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "base_feature_id": "f1",
        "pt_plus_1_feature_id": "f2",
        "pt_plus_2_feature_id": "f3",
        "pt_plus_4_feature_id": "f4",
        **overrides,
    }
    _write_csv(candidates_csv, [candidate])
    _write_csv(
        evidence_csv,
        [_evidence_row("candidate-a")],
    )

    with pytest.raises(ValueError, match=message):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
        )


def test_candidate_contexts_reject_source_sample_mismatch(tmp_path: Path) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    candidate = {
        "sample_stem": "SampleB",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "candidate-a",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "base_feature_id": "f1",
        "pt_plus_1_feature_id": "f2",
        "pt_plus_2_feature_id": "f3",
        "pt_plus_4_feature_id": "f4",
    }
    _write_csv(candidates_csv, [candidate])
    _write_csv(
        evidence_csv,
        [_evidence_row("candidate-a", sample_stem="SampleB")],
    )

    with pytest.raises(ValueError, match="source sample 'SampleA'"):
        replay._candidate_contexts(
            candidates_csv,
            evidence_csv,
            rt_half_window_sec=45.0,
            expected_sample_stem="SampleA",
        )


def test_same_scan_replay_rejects_duplicate_known_seed_identity(
    tmp_path: Path,
) -> None:
    known_csv = tmp_path / "known.csv"
    known_row = {
        "compound": "195CP-known",
        "expected_base_mz": "500.0",
        "diagnostic_status": "accepted_candidate",
    }
    _write_csv(known_csv, [known_row, known_row])

    with pytest.raises(
        ValueError,
        match="duplicate known hypothesis seed_id.*known:base_mz=500",
    ):
        replay._known_contexts(known_csv)


def test_same_scan_replay_accepts_same_compound_with_different_assignments(
    tmp_path: Path,
) -> None:
    known_csv = tmp_path / "known.csv"
    _write_csv(
        known_csv,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate",
            },
            {
                "compound": "195CP-known",
                "expected_base_mz": "600.0",
                "diagnostic_status": "no_candidate_within20",
            },
        ],
    )

    contexts = replay._known_contexts(known_csv)

    assert [context.seed.seed_id for context in contexts] == [
        "known:base_mz=500",
        "known:base_mz=600",
    ]
    assert [context.compound for context in contexts] == [
        "195CP-known",
        "195CP-known",
    ]


def test_same_scan_replay_namespaces_candidate_and_known_seed_identity(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "candidates.csv"
    evidence_csv = tmp_path / "evidence.csv"
    known_csv = tmp_path / "known.csv"
    _write_csv(
        candidates_csv,
        [
            {
                "sample_stem": "SampleA",
                "method_label": "pt_pattern",
                "pattern_id": "pt",
                "candidate_id": "known:base_mz=500",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.0",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(
        evidence_csv,
        [
            _evidence_row("known:base_mz=500")
        ],
    )
    _write_csv(
        known_csv,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate",
            }
        ],
    )

    outputs = run_same_scan_replay(
        index=_index(),
        candidates_csv=candidates_csv,
        evidence_csv=evidence_csv,
        known_diagnosis_csv=known_csv,
        output_dir=tmp_path / "report",
        report_id="identity_namespace",
        source_sample_stem="SampleA",
        index_input=_write_index_identity(tmp_path),
    )

    with outputs["evidence_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {
        (row["source_set"], row["seed_id"])
        for row in rows
        if row["seed_id"] == "known:base_mz=500"
    } == {
        ("current_feature_hypothesis", "known:base_mz=500"),
        ("known_named", "known:base_mz=500"),
    }


def _write_index_identity(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-index.npz"
    path.write_bytes(b"synthetic-index")
    return path
