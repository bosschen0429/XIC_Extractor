from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tests import issue180_m9_raw_validation_fixture as gate_c_fixture
from tests.test_alignment_mode_track_conservative_backfill import (
    _gate_c_proposal,
    _gate_c_review,
    _gate_c_run_context,
)
from xic_extractor.alignment import mode_track_table_finalization as finalization
from xic_extractor.alignment.matrix_output_contract import (
    validate_matrix_identity_rows,
)


def test_committed_review_bundle_preserves_hash_and_reviewed_census() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    review_bundle = (
        repo_root
        / "docs/superpowers/validation/issue180_m9_gate_c_closed_writer_v3"
        / "review_transfer"
    )
    review_path = review_bundle / "manual_review_v1.json"
    replay_path = review_bundle / "manual_review_observation_replay_v1.json"

    assert hashlib.sha256(review_path.read_bytes()).hexdigest() == (
        "a7edc540ea634b03b0f0f525730867b6432a5c6ea535518cd3da73410fb47a89"
    )
    assert hashlib.sha256(replay_path.read_bytes()).hexdigest() == (
        "698d414d53241e593aac1af5d1456dd0406ef4ee0caeb37a763a279c6546a1cd"
    )
    summary = finalization.inspect_mode_track_review_bundle(
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
    )

    assert summary.reviewed_cell_count == 26
    assert dict(summary.feature_family_counts) == {
        "FAM000004": 10,
        "FAM000007": 11,
        "FAM000009": 5,
    }


def test_reviewed_mode_track_projection_writes_atomic_tsv_and_workbook(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"

    result = finalization.finalize_reviewed_mode_track_projection(
        projection=projection,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        output_dir=tmp_path / "final",
    )

    assert result.validation_status == "diagnostic_only"
    assert result.adopted_cell_count == 2
    assert result.rollback_status == "baseline_exact"
    assert result.matrix_tsv.is_file()
    assert result.matrix_identity_tsv.is_file()
    assert result.matrix_xlsx.is_file()
    assert result.checked_manifest_tsv.is_file()
    assert result.publication_receipt_json.is_file()
    receipt = finalization.validate_mode_track_table_publication_receipt(
        output_dir=result.matrix_tsv.parent,
    )
    assert receipt["claim_level"] == "byte_bound"
    assert receipt["authenticity_authority"] is False
    assert receipt["review_claim"] == (
        "owner_validated_physical_subject_binding_only"
    )
    assert receipt["review_evidence"] == {
        "manual_review_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
        "manual_review_replay_sha256": hashlib.sha256(
            replay_path.read_bytes()
        ).hexdigest(),
        "reviewed_cell_count": 2,
    }
    assert result.matrix_tsv.read_text(encoding="utf-8").splitlines()[0].split(
        "\t"
    ) == list(projection.matrix_header)
    with result.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        matrix_rows = list(csv.DictReader(handle, delimiter="\t"))
    with result.matrix_identity_tsv.open(encoding="utf-8", newline="") as handle:
        identity_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert all(row["Mz"] == f"{float(row['Mz']):.4f}" for row in matrix_rows)
    assert all(
        value == f"{float(value):.5e}"
        for row in matrix_rows
        for sample in projection.matrix_header[2:]
        if (value := row[sample])
    )
    validate_matrix_identity_rows(
        matrix_header=projection.matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=tuple(identity_rows[0]),
        identity_rows=identity_rows,
    )

    workbook = load_workbook(result.matrix_xlsx, read_only=True, data_only=True)
    matrix = workbook["Matrix"]
    assert tuple(cell.value for cell in next(matrix.iter_rows())) == (
        *projection.matrix_header,
    )
    assert matrix.max_row == len(projection.input_quant_matrix_rows) + 1


def test_mode_track_publication_receipt_survives_copy_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    result = finalization.finalize_reviewed_mode_track_projection(
        projection=projection,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        output_dir=tmp_path / "final",
    )
    copied = tmp_path / "copied"
    shutil.copytree(result.matrix_tsv.parent, copied)

    receipt = finalization.validate_mode_track_table_publication_receipt(
        output_dir=copied,
    )

    assert receipt["review_evidence"]["manual_review_sha256"] == hashlib.sha256(
        review_path.read_bytes()
    ).hexdigest()
    (copied / "alignment_matrix.tsv").write_bytes(b"mutated\n")
    with pytest.raises(ValueError, match="output bytes changed"):
        finalization.validate_mode_track_table_publication_receipt(
            output_dir=copied,
        )


def test_reviewed_mode_track_projection_rejects_changed_physical_area(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay["rows"][0]["proposal_quant_value"] = "999"
    replay_path.write_text(
        json.dumps(replay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["observation_replay_sha256"] = hashlib.sha256(
        replay_path.read_bytes()
    ).hexdigest()
    review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="physical replay candidate binding changed"):
        finalization.finalize_reviewed_mode_track_projection(
            projection=projection,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            output_dir=tmp_path / "final",
        )

    assert not (tmp_path / "final").exists()


def test_reviewed_mode_track_projection_rejects_resealed_cross_key_subjects(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    expected_keys = {
        (row["peak_hypothesis_id"], row["sample_stem"])
        for row in replay["rows"]
    }

    for rows in (review["rows"], replay["rows"]):
        first_key = (
            rows[0]["peak_hypothesis_id"],
            rows[0]["sample_stem"],
        )
        second_key = (
            rows[1]["peak_hypothesis_id"],
            rows[1]["sample_stem"],
        )
        rows[0]["peak_hypothesis_id"], rows[0]["sample_stem"] = second_key
        rows[1]["peak_hypothesis_id"], rows[1]["sample_stem"] = first_key
    for row in replay["rows"]:
        row["feature_family_id"] = row["peak_hypothesis_id"]
    assert {
        (row["peak_hypothesis_id"], row["sample_stem"])
        for row in replay["rows"]
    } == expected_keys

    _write_json(replay_path, replay)
    review["observation_replay_sha256"] = hashlib.sha256(
        replay_path.read_bytes()
    ).hexdigest()
    _write_json(review_path, review)

    with pytest.raises(
        ValueError,
        match="validated review differs from fresh physical subject",
    ):
        finalization.finalize_reviewed_mode_track_projection(
            projection=projection,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            output_dir=tmp_path / "final",
        )

    assert not (tmp_path / "final").exists()


def test_resealed_audit_digests_cannot_change_mode_track_writer_cells(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    baseline = finalization.finalize_reviewed_mode_track_projection(
        projection=projection,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        output_dir=tmp_path / "baseline",
    )

    proposal_sha = "1" * 64
    source_sha = "2" * 64
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay["proposal_sha256"] = proposal_sha
    replay["source_artifact_sha256"] = source_sha
    _write_json(replay_path, replay)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["observation_replay_sha256"] = hashlib.sha256(
        replay_path.read_bytes()
    ).hexdigest()
    for row in review["rows"]:
        row["proposal_sha256"] = proposal_sha
        row["source_artifact_sha256"] = source_sha
        row["candidate_key_sha256"] = "3" * 64
    _write_json(review_path, review)

    resealed = finalization.finalize_reviewed_mode_track_projection(
        projection=projection,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        output_dir=tmp_path / "resealed",
    )

    assert resealed.matrix_tsv.read_bytes() == baseline.matrix_tsv.read_bytes()
    baseline_workbook = load_workbook(
        baseline.matrix_xlsx,
        read_only=True,
        data_only=True,
    )
    resealed_workbook = load_workbook(
        resealed.matrix_xlsx,
        read_only=True,
        data_only=True,
    )
    assert tuple(baseline_workbook["Matrix"].values) == tuple(
        resealed_workbook["Matrix"].values
    )


def test_reviewed_mode_track_projection_does_not_publish_partial_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"

    def fail_workbook(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic workbook failure")

    monkeypatch.setattr(finalization, "write_quant_matrix_version_xlsx", fail_workbook)

    with pytest.raises(OSError, match="synthetic workbook failure"):
        finalization.finalize_reviewed_mode_track_projection(
            projection=projection,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            output_dir=tmp_path / "final",
        )

    assert not (tmp_path / "final").exists()
    assert not (tmp_path / "final.staging").exists()


def test_reviewed_mode_track_projection_retries_transient_windows_publish_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path: Path, target: Path) -> Path:
        nonlocal attempts
        if path.name == "final.staging" and attempts == 0:
            attempts += 1
            raise PermissionError(5, "synthetic transient directory denial")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    result = finalization.finalize_reviewed_mode_track_projection(
        projection=projection,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        output_dir=tmp_path / "final",
    )

    assert attempts == 1
    assert result.matrix_tsv.is_file()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
