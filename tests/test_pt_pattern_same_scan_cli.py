from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from tests.pt_pattern_evidence_fixtures import pt_evidence_row
from tools.diagnostics import pt_pattern_same_scan_replay
from xic_extractor.ms1_scan_index import (
    MS1Scan,
    MS1ScanIndexProvenance,
    write_ms1_scan_index_npz,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _candidate_row(*, duplicate_feature_claim: bool = False) -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "candidate-a",
        "candidate_state": "pt_pattern_candidate",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.01",
        "base_feature_id": "f1",
        "pt_plus_1_feature_id": "f2",
        "pt_plus_2_feature_id": "f3",
        "pt_plus_4_feature_id": "f3" if duplicate_feature_claim else "f4",
        "pt_plus_1_mz": "501.002111",
        "pt_plus_1_expected_mz": "501.002111",
        "pt_plus_2_mz": "502.002271",
        "pt_plus_2_expected_mz": "502.002271",
        "pt_plus_4_mz": "504.005213",
        "pt_plus_4_expected_mz": "504.005213",
    }


def _evidence_row(candidate: dict[str, str]) -> dict[str, str]:
    assignment_candidate = dict(candidate)
    if (
        assignment_candidate["pt_plus_4_feature_id"]
        == assignment_candidate["pt_plus_2_feature_id"]
    ):
        assignment_candidate["pt_plus_4_feature_id"] = "f4"
    return {
        field: str(value)
        for field, value in pt_evidence_row(assignment_candidate).items()
    }


def test_same_scan_cli_runs_from_npz_without_raw(tmp_path: Path) -> None:
    index_path = tmp_path / "sample.ms1_index.npz"
    write_ms1_scan_index_npz(
        index_path,
        (
            MS1Scan(
                scan_number=100,
                rt=10.0,
                masses=np.asarray(
                    [500.0, 501.002111, 502.002271, 504.005213],
                    dtype=np.float64,
                ),
                intensities=np.asarray(
                    [90_000.0, 120_000.0, 80_000.0, 40_000.0],
                    dtype=np.float64,
                ),
            ),
            MS1Scan(
                scan_number=101,
                rt=10.02,
                masses=np.asarray(
                    [500.0, 501.002111, 502.002271, 504.005213],
                    dtype=np.float64,
                ),
                intensities=np.asarray(
                    [100_000.0, 130_000.0, 90_000.0, 45_000.0],
                    dtype=np.float64,
                ),
            ),
        ),
        provenance=MS1ScanIndexProvenance(
            sample_stem="SampleA",
            source_path="C:/raw/SampleA.raw",
            source_sha256="a" * 64,
        ),
    )
    candidates = tmp_path / "candidates.csv"
    evidence = tmp_path / "evidence.csv"
    known = tmp_path / "known.csv"
    candidate = _candidate_row()
    _write_csv(candidates, [candidate])
    _write_csv(evidence, [_evidence_row(candidate)])
    _write_csv(
        known,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate",
            }
        ],
    )
    output_dir = tmp_path / "output"

    code = pt_pattern_same_scan_replay.main(
        [
            "--ms1-index-npz",
            str(index_path),
            "--candidates-csv",
            str(candidates),
            "--evidence-csv",
            str(evidence),
            "--known-diagnosis-csv",
            str(known),
            "--output-dir",
            str(output_dir),
            "--report-id",
            "cli-synthetic",
        ]
    )

    assert code == 0
    assert (output_dir / "pt_pattern_same_scan_summary.json").is_file()
    assert (output_dir / "pt_pattern_same_scan_report.html").is_file()
    summary = json.loads(
        (output_dir / "pt_pattern_same_scan_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["source_sample_stem"] == "SampleA"
    assert summary["index_input"]["path"] == str(index_path.resolve())
    assert summary["index_input"]["sha256"]
    assert summary["index_input"]["source_provenance"]["source_sha256"] == (
        "a" * 64
    )


def test_same_scan_cli_rejects_npz_without_source_provenance(
    tmp_path: Path,
    capsys,
) -> None:
    index_path = tmp_path / "unbound.ms1_index.npz"
    write_ms1_scan_index_npz(index_path, ())
    candidates = tmp_path / "candidates.csv"
    evidence = tmp_path / "evidence.csv"
    known = tmp_path / "known.csv"
    _write_csv(
        candidates,
        [
            {
                "sample_stem": "SampleA",
                "candidate_id": "candidate-a",
                "base_mz": "500.0",
                "base_rt_apex_min": "10.01",
                "base_feature_id": "f1",
                "pt_plus_1_feature_id": "f2",
                "pt_plus_2_feature_id": "f3",
                "pt_plus_4_feature_id": "f4",
            }
        ],
    )
    _write_csv(evidence, [{"candidate_id": "candidate-a", "trace_rule_call": "accept"}])
    _write_csv(
        known,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate",
            }
        ],
    )

    code = pt_pattern_same_scan_replay.main(
        [
            "--ms1-index-npz",
            str(index_path),
            "--candidates-csv",
            str(candidates),
            "--evidence-csv",
            str(evidence),
            "--known-diagnosis-csv",
            str(known),
            "--output-dir",
            str(tmp_path / "output"),
            "--report-id",
            "unbound",
        ]
    )

    assert code == 2
    assert "source provenance" in capsys.readouterr().err


def test_same_scan_cli_translates_invalid_feature_claims_to_exit_2(
    tmp_path: Path,
    capsys,
) -> None:
    index_path = tmp_path / "sample.ms1_index.npz"
    write_ms1_scan_index_npz(
        index_path,
        (
            MS1Scan(
                scan_number=100,
                rt=10.0,
                masses=np.asarray(
                    [500.0, 501.002111, 502.002271, 504.005213],
                    dtype=np.float64,
                ),
                intensities=np.asarray(
                    [90_000.0, 120_000.0, 80_000.0, 40_000.0],
                    dtype=np.float64,
                ),
            ),
        ),
        provenance=MS1ScanIndexProvenance(
            sample_stem="SampleA",
            source_path="C:/raw/SampleA.raw",
            source_sha256="a" * 64,
        ),
    )
    candidates = tmp_path / "candidates.csv"
    evidence = tmp_path / "evidence.csv"
    known = tmp_path / "known.csv"
    candidate = _candidate_row(duplicate_feature_claim=True)
    _write_csv(candidates, [candidate])
    _write_csv(evidence, [_evidence_row(candidate)])
    _write_csv(
        known,
        [
            {
                "compound": "195CP-known",
                "expected_base_mz": "500.0",
                "diagnostic_status": "accepted_candidate",
            }
        ],
    )
    output_dir = tmp_path / "output"

    code = pt_pattern_same_scan_replay.main(
        [
            "--ms1-index-npz",
            str(index_path),
            "--candidates-csv",
            str(candidates),
            "--evidence-csv",
            str(evidence),
            "--known-diagnosis-csv",
            str(known),
            "--output-dir",
            str(output_dir),
            "--report-id",
            "invalid-claims",
        ]
    )

    assert code == 2
    assert "feature assignment IDs must be distinct" in capsys.readouterr().err
    assert not output_dir.exists()
