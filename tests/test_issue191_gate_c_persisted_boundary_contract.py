from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_gate_c_persisted_boundary_contract as boundary
from tests import issue180_m9_gate_c_85raw_runner as runner


def test_current_gate_c_persisted_boundary_contract_is_complete() -> None:
    assert boundary.check_gate_c_persisted_boundary_contract() == []

    rows = boundary.enumerate_gate_c_boundary()
    assert {row["artifact_id"] for row in rows} == {
        spec.artifact_id for spec in boundary.ARTIFACT_SPECS
    }
    assert {row["resolution"] for row in rows} == {
        "fixed",
        "verified_unaffected",
    }


def test_launch_manifest_declared_shape_matches_current_c1_and_c2_runner(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_root = tmp_path.resolve()
    executable = manifest_root / ".venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    metadata_path = manifest_root / "metadata.csv"
    metadata_path.write_text("fixture\n", encoding="utf-8")
    raw_dir = manifest_root / "raw"
    raw_dir.mkdir()
    raw_stems = [f"sample{index:03d}" for index in range(1, 86)]
    for raw_stem in raw_stems:
        (raw_dir / f"{raw_stem}.raw").write_bytes(b"")
    dll_dir = manifest_root / "dll"
    dll_dir.mkdir()
    (dll_dir / "fixture.dll").write_bytes(b"fixture")
    code_sha = "a" * 40

    monkeypatch.setattr(runner, "_current_executable", lambda: executable)
    monkeypatch.setattr(runner, "_checkout_sha", lambda _root: code_sha)
    monkeypatch.setattr(runner, "_worktree_status", lambda _root: "")
    monkeypatch.setattr(
        runner,
        "load_sample_metadata",
        lambda _path: [
            SimpleNamespace(
                excluded=False,
                injection_order=index,
                raw_stem=raw_stem,
            )
            for index, raw_stem in enumerate(raw_stems, start=1)
        ],
    )

    c1 = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=manifest_root,
        output_dir=manifest_root / "c1_output",
        launch_manifest_path=manifest_root / "c1_launch.json",
        code_sha=code_sha,
    )

    proposal_dir = manifest_root / "proposal"
    proposal_dir.mkdir()
    (proposal_dir / "proposal.json").write_text("{}", encoding="utf-8")
    (proposal_dir / "proposal_receipt.json").write_text("{}", encoding="utf-8")
    c2_inputs = {}
    for name in (
        "manual_review.json",
        "manual_review_replay.json",
        "review_transfer_receipt.json",
        "checked_manifest.tsv",
    ):
        path = manifest_root / name
        path.write_text("fixture", encoding="utf-8")
        c2_inputs[name] = path
    c2 = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=manifest_root,
        output_dir=manifest_root / "c2_output",
        launch_manifest_path=manifest_root / "c2_launch.json",
        code_sha=code_sha,
        stage="c2",
        proposal_dir=proposal_dir,
        manual_review_path=c2_inputs["manual_review.json"],
        manual_review_replay_path=c2_inputs["manual_review_replay.json"],
        review_transfer_receipt_path=c2_inputs["review_transfer_receipt.json"],
        checked_manifest_path=c2_inputs["checked_manifest.tsv"],
        final_output_dir=manifest_root / "c2_final",
    )

    observed_types: dict[str, set[str]] = {}
    for launch in (c1.launch_manifest, c2.launch_manifest):
        for path, observed_type in boundary.canonical_json_fields(
            launch,
            artifact_id="launch_manifest",
        ).items():
            observed_types.setdefault(path, set()).update(observed_type.split("|"))
    observed = {
        path: "|".join(sorted(types)) for path, types in observed_types.items()
    }

    assert observed == boundary.declared_launch_manifest_fields()


def test_source_numeric_values_and_identity_components_are_separate_claims() -> None:
    rows = boundary.enumerate_gate_c_boundary()
    by_key = {
        (
            row["artifact_id"],
            row["canonical_field_path"],
            row["semantic_claim"],
        ): row
        for row in rows
    }
    for field in ("rt", "intensity", "mz"):
        path = f"$.rows[].peak_observation.content.{field}"
        assert by_key[
            ("c1_mode_track_source", path, "numeric_value")
        ]["semantic_class"] == "locally_rederivable"
        assert by_key[
            (
                "c1_mode_track_source",
                path,
                "observation_content_identity_component",
            )
        ]["semantic_class"] == "locally_rederivable"

    physical_path = "$.rows[].physical_ids"
    assert by_key[
        ("c1_mode_track_source", physical_path, "identity_collection")
    ]["semantic_class"] == "audit_only"
    assert by_key[
        (
            "c1_mode_track_source",
            physical_path,
            "index_2_observation_content_identity",
        )
    ]["semantic_class"] == "raw_owner_only"


def test_external_manifest_stays_audit_only_while_terminal_manifest_is_fresh() -> None:
    rows = boundary.enumerate_gate_c_boundary()
    review_rows = [
        row for row in rows if row["artifact_id"] == "review_checked_manifest"
    ]
    terminal_rows = [
        row for row in rows if row["artifact_id"] == "terminal_checked_manifest"
    ]

    assert review_rows and terminal_rows
    assert {row["semantic_class"] for row in review_rows} == {"audit_only"}
    assert {row["semantic_class"] for row in terminal_rows} == {"raw_owner_only"}


def test_qmv_composite_has_no_invented_root_schema_claim() -> None:
    rows = [
        row
        for row in boundary.enumerate_gate_c_boundary()
        if row["artifact_id"] == "result_qmv"
    ]

    assert rows
    assert {row["artifact_schema"] for row in rows} == {
        boundary.RESULT_QMV_SCHEMA_DESCRIPTION
    }
    assert "$.schema_version" not in {
        row["canonical_field_path"] for row in rows
    }


def test_qmv_component_schema_drift_is_rejected() -> None:
    spec = next(
        spec for spec in boundary.ARTIFACT_SPECS if spec.artifact_id == "result_qmv"
    )
    payload = json.loads(
        (boundary.ROOT / spec.enumeration_source).read_text(encoding="utf-8")
    )
    payload["cell_provenance_rows"][0]["schema_version"] = "resealed_v2"

    with pytest.raises(ValueError, match="cell_provenance_rows schema changed"):
        boundary._validate_result_qmv_components(
            payload,
            source=spec.enumeration_source,
        )


def test_contract_rejects_unknown_resolution(tmp_path: Path) -> None:
    rows = boundary.enumerate_gate_c_boundary()
    rows[0]["resolution"] = "unknown"
    contract_path = tmp_path / "field_contract.tsv"
    with contract_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=boundary.CONTRACT_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    problems = boundary.check_gate_c_persisted_boundary_contract(
        contract_path=contract_path
    )

    assert any("unresolved claim" in problem for problem in problems)
    assert any("retained contract differs" in problem for problem in problems)
