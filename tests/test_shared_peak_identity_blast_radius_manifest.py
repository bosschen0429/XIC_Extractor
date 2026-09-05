from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from xic_extractor.alignment.shared_peak_identity_explanation.blast_radius import (
    build_blast_radius_manifest,
)


def test_manifest_records_counts_hashes_and_current_status(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    slice0_hashes = {
        str(paths["manual_oracle_fixture"]): _sha256(paths["manual_oracle_fixture"]),
        str(paths["slice0_explanations"]): _sha256(paths["slice0_explanations"]),
        str(paths["slice0_evidence_vectors"]): _sha256(
            paths["slice0_evidence_vectors"]
        ),
        str(paths["fixture_cohort_alignment_review"]): _sha256(
            paths["fixture_cohort_alignment_review"]
        ),
        str(paths["fixture_cohort_alignment_cells"]): _sha256(
            paths["fixture_cohort_alignment_cells"]
        ),
    }
    _write_evidence_vectors(paths["slice0_evidence_vectors"], slice0_hashes)
    expected_manifest = tmp_path / "expected_manifest.tsv"
    _write_expected_manifest(
        expected_manifest,
        [
            {
                "artifact_id": "full_cohort_alignment_review",
                "artifact_role": "alignment_review",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_review"]
                ),
            },
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_cells"]
                ),
            },
        ],
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
        expected_manifest_tsv=expected_manifest,
    )

    by_id = {row["artifact_id"]: row for row in rows}
    assert set(by_id) == {
        "manual_oracle_fixture",
        "slice0_explanations",
        "slice0_evidence_vectors",
        "fixture_cohort_alignment_review",
        "fixture_cohort_alignment_cells",
        "full_cohort_alignment_review",
        "full_cohort_alignment_cells",
    }
    cells = by_id["fixture_cohort_alignment_cells"]
    assert cells["artifact_sha256"] == _sha256(paths["fixture_cohort_alignment_cells"])
    assert cells["artifact_sha256"].isupper()
    assert cells["row_count"] == "2"
    assert cells["sample_count"] == "2"
    assert cells["family_count"] == "2"
    assert cells["missing_required_fields"] == ""
    assert cells["artifact_status"] == "present_current"
    assert cells["freshness_basis"] == "slice0_evidence_vector"
    assert by_id["slice0_explanations"]["artifact_role"] == "blast_radius_context"
    assert by_id["slice0_evidence_vectors"]["artifact_role"] == ("blast_radius_context")
    assert by_id["full_cohort_alignment_cells"]["artifact_status"] == "present_current"
    assert by_id["full_cohort_alignment_cells"]["freshness_basis"] == (
        "expected_blast_radius_manifest"
    )


def test_manifest_marks_unpinned_and_hash_mismatched_artifacts(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(
        paths["slice0_evidence_vectors"],
        {str(paths["fixture_cohort_alignment_cells"]): "0" * 64},
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
    )

    by_id = {row["artifact_id"]: row for row in rows}
    assert by_id["fixture_cohort_alignment_cells"]["artifact_status"] == (
        "present_stale_hash_mismatch"
    )
    assert by_id["full_cohort_alignment_cells"]["artifact_status"] == (
        "present_hash_unpinned"
    )
    assert by_id["full_cohort_alignment_cells"]["freshness_basis"] == "not_available"


def test_manifest_marks_missing_required_fields_before_current(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_tsv(
        paths["fixture_cohort_alignment_cells"],
        ("feature_family_id", "sample_stem", "status"),
        [{"feature_family_id": "FAM001", "sample_stem": "S1", "status": "selected"}],
    )
    _write_evidence_vectors(
        paths["slice0_evidence_vectors"],
        {
            str(paths["fixture_cohort_alignment_cells"]): _sha256(
                paths["fixture_cohort_alignment_cells"]
            )
        },
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
    )

    cells = {row["artifact_id"]: row for row in rows}["fixture_cohort_alignment_cells"]
    assert cells["artifact_status"] == "present_missing_required_fields"
    assert "reason" in cells["missing_required_fields"]


def test_manifest_handles_missing_optional_artifact_and_rejects_unknown_role(
    tmp_path: Path,
) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(paths["slice0_evidence_vectors"], {})

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
        optional_artifacts={
            "candidate_gate_fixture_cohort": tmp_path / "missing_candidate_gate.tsv"
        },
    )
    optional = {row["artifact_id"]: row for row in rows}[
        "candidate_gate_fixture_cohort"
    ]
    assert optional["artifact_role"] == "blast_radius_context"
    assert optional["artifact_status"] == "missing"

    with pytest.raises(ValueError, match="unknown optional artifact role"):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            optional_artifacts={"not_a_role": tmp_path / "context.tsv"},
        )


def test_expected_manifest_requires_legal_artifact_role(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(paths["slice0_evidence_vectors"], {})
    missing_role = tmp_path / "missing_role_manifest.tsv"
    _write_tsv(
        missing_role,
        ("artifact_id", "expected_artifact_sha256"),
        [
            {
                "artifact_id": "full_cohort_alignment_cells",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_cells"]
                ),
            }
        ],
    )

    with pytest.raises(ValueError, match="artifact_role"):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            expected_manifest_tsv=missing_role,
        )

    unknown_role = tmp_path / "unknown_role_manifest.tsv"
    _write_tsv(
        unknown_role,
        ("artifact_id", "artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "candidate_gate_full_cohort",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_cells"]
                ),
            }
        ],
    )

    with pytest.raises(ValueError, match="artifact_role"):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            expected_manifest_tsv=unknown_role,
        )


def test_expected_manifest_uses_only_expected_hash_authority(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(paths["slice0_evidence_vectors"], {})
    expected_manifest = tmp_path / "artifact_sha_only_manifest.tsv"
    _write_tsv(
        expected_manifest,
        ("artifact_id", "artifact_role", "artifact_sha256"),
        [
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "artifact_sha256": _sha256(paths["full_cohort_alignment_cells"]),
            }
        ],
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
        expected_manifest_tsv=expected_manifest,
    )

    cells = {row["artifact_id"]: row for row in rows}["full_cohort_alignment_cells"]
    assert cells["expected_artifact_sha256"] == ""
    assert cells["artifact_status"] == "present_hash_unpinned"
    assert cells["freshness_basis"] == "not_available"


def test_expected_hash_matching_does_not_use_basename_only(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(paths["slice0_evidence_vectors"], {})
    expected_manifest = tmp_path / "basename_only_manifest.tsv"
    _write_tsv(
        expected_manifest,
        ("artifact_path", "artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_path": "alignment_cells.tsv",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_cells"]
                ),
            }
        ],
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
        expected_manifest_tsv=expected_manifest,
    )

    by_id = {row["artifact_id"]: row for row in rows}
    assert by_id["fixture_cohort_alignment_cells"]["artifact_status"] == (
        "present_hash_unpinned"
    )
    assert by_id["full_cohort_alignment_cells"]["artifact_status"] == (
        "present_hash_unpinned"
    )


def test_manifest_path_artifact_is_opened_once_for_hash_and_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(
        paths["slice0_evidence_vectors"],
        {
            str(paths["full_cohort_alignment_cells"]): _sha256(
                paths["full_cohort_alignment_cells"]
            )
        },
    )
    target = paths["full_cohort_alignment_cells"]
    open_count = 0
    original_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal open_count
        if self == target:
            open_count += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
    )

    assert open_count == 1
    assert {row["artifact_id"]: row for row in rows}["full_cohort_alignment_cells"][
        "artifact_status"
    ] == "present_current"


def test_conflicting_expected_hashes_fail_fast(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    expected_manifest = tmp_path / "conflicting_manifest.tsv"
    _write_tsv(
        expected_manifest,
        ("artifact_id", "artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": "A" * 64,
            },
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": "B" * 64,
            },
        ],
    )

    with pytest.raises(ValueError, match="full_cohort_alignment_cells.*A{64}.*B{64}"):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            expected_manifest_tsv=expected_manifest,
        )


def test_conflicting_expected_hashes_across_authorities_fail_fast(
    tmp_path: Path,
) -> None:
    paths = _write_required_artifacts(tmp_path)
    _write_evidence_vectors(
        paths["slice0_evidence_vectors"],
        {str(paths["full_cohort_alignment_cells"]): "A" * 64},
    )
    expected_manifest = tmp_path / "conflicting_authority_manifest.tsv"
    _write_tsv(
        expected_manifest,
        ("artifact_path", "artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_path": str(paths["full_cohort_alignment_cells"]),
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": "B" * 64,
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="slice0_evidence_vector.*expected_blast_radius_manifest",
    ):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            expected_manifest_tsv=expected_manifest,
        )


def test_duplicate_same_expected_hash_is_accepted(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    expected_manifest = tmp_path / "duplicate_same_hash_manifest.tsv"
    expected_hash = _sha256(paths["full_cohort_alignment_cells"])
    _write_tsv(
        expected_manifest,
        ("artifact_id", "artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": expected_hash,
            },
            {
                "artifact_id": "full_cohort_alignment_cells",
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": expected_hash,
            },
        ],
    )

    rows = build_blast_radius_manifest(
        manual_oracle_tsv=paths["manual_oracle_fixture"],
        slice0_explanations_tsv=paths["slice0_explanations"],
        slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
        fixture_cohort_run_dir=tmp_path / "fixture_cohort",
        full_cohort_run_dir=tmp_path / "full_cohort",
        expected_manifest_tsv=expected_manifest,
    )

    assert {row["artifact_id"]: row for row in rows}["full_cohort_alignment_cells"][
        "artifact_status"
    ] == "present_current"


def test_expected_manifest_hash_requires_id_or_path_key(tmp_path: Path) -> None:
    paths = _write_required_artifacts(tmp_path)
    expected_manifest = tmp_path / "missing_key_manifest.tsv"
    _write_tsv(
        expected_manifest,
        ("artifact_role", "expected_artifact_sha256"),
        [
            {
                "artifact_role": "alignment_cells",
                "expected_artifact_sha256": _sha256(
                    paths["full_cohort_alignment_cells"]
                ),
            }
        ],
    )

    with pytest.raises(ValueError, match="artifact_id or artifact_path"):
        build_blast_radius_manifest(
            manual_oracle_tsv=paths["manual_oracle_fixture"],
            slice0_explanations_tsv=paths["slice0_explanations"],
            slice0_evidence_vectors_tsv=paths["slice0_evidence_vectors"],
            fixture_cohort_run_dir=tmp_path / "fixture_cohort",
            full_cohort_run_dir=tmp_path / "full_cohort",
            expected_manifest_tsv=expected_manifest,
        )


def _write_required_artifacts(tmp_path: Path) -> dict[str, Path]:
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    fixture_cohort.mkdir()
    full_cohort.mkdir()
    paths = {
        "manual_oracle_fixture": tmp_path / "manual_oracle.tsv",
        "slice0_explanations": tmp_path / "explanations.tsv",
        "slice0_evidence_vectors": tmp_path / "evidence_vectors.tsv",
        "fixture_cohort_alignment_review": fixture_cohort / "alignment_review.tsv",
        "fixture_cohort_alignment_cells": fixture_cohort / "alignment_cells.tsv",
        "full_cohort_alignment_review": full_cohort / "alignment_review.tsv",
        "full_cohort_alignment_cells": full_cohort / "alignment_cells.tsv",
    }
    _write_tsv(
        paths["manual_oracle_fixture"],
        ("oracle_row_id", "feature_family_id", "sample_id"),
        [
            {
                "oracle_row_id": "FAM001|S1",
                "feature_family_id": "FAM001",
                "sample_id": "S1",
            }
        ],
    )
    _write_tsv(
        paths["slice0_explanations"],
        ("oracle_row_id", "feature_family_id", "sample_id"),
        [
            {
                "oracle_row_id": "FAM001|S1",
                "feature_family_id": "FAM001",
                "sample_id": "S1",
            }
        ],
    )
    _write_tsv(
        paths["slice0_evidence_vectors"],
        ("source_artifact", "source_artifact_sha256"),
        [],
    )
    for key in ("fixture_cohort_alignment_review", "full_cohort_alignment_review"):
        _write_tsv(
            paths[key],
            ("feature_family_id", "identity_decision", "identity_reason", "row_flags"),
            [
                {
                    "feature_family_id": "FAM001",
                    "identity_decision": "pass",
                    "identity_reason": "ok",
                    "row_flags": "",
                }
            ],
        )
    for key in ("fixture_cohort_alignment_cells", "full_cohort_alignment_cells"):
        _write_tsv(
            paths[key],
            (
                "feature_family_id",
                "sample_stem",
                "status",
                "apex_rt",
                "peak_start_rt",
                "peak_end_rt",
                "rt_delta_sec",
                "trace_quality",
                "scan_support_score",
                "reason",
            ),
            [
                {
                    "feature_family_id": "FAM001",
                    "sample_stem": "S1",
                    "status": "selected",
                    "apex_rt": "1.0",
                    "peak_start_rt": "0.9",
                    "peak_end_rt": "1.1",
                    "rt_delta_sec": "0.0",
                    "trace_quality": "good",
                    "scan_support_score": "1.0",
                    "reason": "ok",
                },
                {
                    "feature_family_id": "FAM002",
                    "sample_stem": "S2",
                    "status": "missing",
                    "apex_rt": "",
                    "peak_start_rt": "",
                    "peak_end_rt": "",
                    "rt_delta_sec": "",
                    "trace_quality": "low",
                    "scan_support_score": "0.0",
                    "reason": "no_peak",
                },
            ],
        )
    return paths


def _write_evidence_vectors(path: Path, expected_hashes: dict[str, str]) -> None:
    rows = [
        {"source_artifact": artifact, "source_artifact_sha256": sha256}
        for artifact, sha256 in expected_hashes.items()
    ]
    _write_tsv(path, ("source_artifact", "source_artifact_sha256"), rows)


def _write_expected_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        ("artifact_id", "artifact_role", "expected_artifact_sha256"),
        rows,
    )


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
