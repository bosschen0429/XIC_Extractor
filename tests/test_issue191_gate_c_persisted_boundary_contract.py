from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from scripts import check_gate_c_persisted_boundary_contract as boundary
from tests import issue180_m9_gate_c_85raw_runner as runner
from tests import issue180_m9_raw_validation_fixture as gate_c_fixture
from tests import test_alignment_mode_track_conservative_backfill as gate_c_test


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


def test_all_other_artifact_shapes_match_current_no_raw_producers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TemporaryDirectory(prefix="xic-gate-c-live-shape-") as temp_dir:
        _assert_all_other_artifact_shapes(Path(temp_dir), monkeypatch)


def _assert_all_other_artifact_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = gate_c_test._gate_c_proposal(tmp_path)
    initial_run_context = gate_c_test._gate_c_run_context(projection)
    initial_call_cost = initial_run_context["call_cost"]
    assert isinstance(initial_call_cost, dict)
    initial_call_cost.update(
        {
            "m7_logical_xic_request_count": 0,
            "m7_adapter_chromatogram_call_count": 0,
            "m7_trace_point_count": 0,
        }
    )
    initial_candidate_census = initial_run_context["candidate_census"]
    assert isinstance(initial_candidate_census, dict)
    initial_candidate_census.update(
        {
            "metadata_sha256": "synthetic-metadata",
            "discovery_index_sha256": "synthetic-discovery-index",
            "track_count": len(projection.mode_track_facts),
        }
    )
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=initial_run_context,
    )
    review_path = gate_c_test._gate_c_review(tmp_path, projection).replace(
        tmp_path / "manual_review_v1.json"
    )
    replay_source_path = tmp_path / "manual_review_replay.json"
    replay_payload = json.loads(replay_source_path.read_text(encoding="utf-8"))
    replay_payload.update(
        {
            "discovery_index_sha256": "synthetic-discovery-index",
            "raw_open_count": 0,
        }
    )
    for row in replay_payload["rows"]:
        row.update(
            {
                "assessment_artifact_id": "synthetic-assessment",
                "eic_overlay_locator": "synthetic-overlay",
                "source_candidate_ids": ["synthetic-candidate"],
                "trace_data_locator": "synthetic-trace",
            }
        )
    replay_source_path.write_text(
        json.dumps(replay_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    replay_path = replay_source_path.replace(
        tmp_path / "manual_review_observation_replay_v1.json"
    )
    gate_c_test._checked_manifest(tmp_path, projection).replace(
        tmp_path / "checked_manifest_v1.tsv"
    )
    gate_c_test._bind_review_replay(review_path, replay_path, tmp_path)

    transfer_dir = tmp_path / "review_transfer"
    gate_c_fixture.transfer_gate_c_review(
        reviewed_packet_dir=tmp_path,
        proposal_dir=proposal_dir,
        manifest_root=tmp_path,
        output_dir=transfer_dir,
        code_sha="synthetic-live-shape",
    )

    proposal_receipt = json.loads(
        (proposal_dir / "proposal_receipt.json").read_text(encoding="utf-8")
    )
    run_context = proposal_receipt["run_context"]
    fake_run = gate_c_fixture.ModeTrackValidationRun(
        projection=projection,
        facts=projection.mode_track_facts,
        call_cost=run_context["call_cost"],
        discovery_index=tmp_path / "synthetic-discovery-index.csv",
        settings=SimpleNamespace(),
        owners=SimpleNamespace(
            request=SimpleNamespace(
                processing_request_id=run_context["processing_request_id"]
            ),
            attempt=SimpleNamespace(
                processing_attempt_id=run_context["processing_attempt_id"]
            ),
        ),
        started=0.0,
    )

    def fake_run_mode_track_batch(
        **kwargs: object,
    ) -> gate_c_fixture.ModeTrackValidationRun:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        output_dir.mkdir()
        return fake_run

    monkeypatch.setattr(
        gate_c_fixture,
        "_run_mode_track_batch",
        fake_run_mode_track_batch,
    )
    replay_dir = tmp_path / "c2_replay"
    final_dir = tmp_path / "c2_final"
    gate_c_fixture.run_gate_c_fresh_finalization(
        raw_files=tuple(tmp_path / f"sample-{index}.raw" for index in range(85)),
        sample_metadata=tuple(SimpleNamespace() for _ in range(85)),
        dll_dir=tmp_path,
        manifest_root=tmp_path,
        replay_output_dir=replay_dir,
        final_output_dir=final_dir,
        proposal_dir=proposal_dir,
        manual_review_path=transfer_dir / "manual_review_v1.json",
        manual_review_replay_path=(
            transfer_dir / "manual_review_observation_replay_v1.json"
        ),
        review_transfer_receipt_path=(transfer_dir / "review_transfer_receipt.json"),
        manifest_path=transfer_dir / "checked_manifest_v1.tsv",
        code_sha="synthetic-live-shape",
        metadata_sha256="synthetic-metadata",
    )

    live_paths = {
        "c1_mode_track_source": proposal_dir / "mode_track_source.json",
        "c1_proposal": proposal_dir / "proposal.json",
        "c1_proposal_receipt": proposal_dir / "proposal_receipt.json",
        "timing_payload": replay_dir / "timing.live.json",
        "manual_review": transfer_dir / "manual_review_v1.json",
        "physical_replay": (transfer_dir / "manual_review_observation_replay_v1.json"),
        "review_transfer_receipt": transfer_dir / "review_transfer_receipt.json",
        "review_checked_manifest": transfer_dir / "checked_manifest_v1.tsv",
        "fresh_mode_track_source": replay_dir / "fresh_mode_track_source.json",
        "fresh_owner_receipt": replay_dir / "fresh_owner_receipt.json",
        "terminal_checked_manifest": final_dir / "checked_manifest_v1.tsv",
        "result_qmv": final_dir / "result_qmv.json",
        "final_receipt": final_dir / "final_receipt.json",
        "transaction_receipt": final_dir / "transaction_receipt.json",
    }
    specs = {
        spec.artifact_id: spec
        for spec in boundary.ARTIFACT_SPECS
        if spec.artifact_id != "launch_manifest"
    }

    assert live_paths.keys() == specs.keys()
    for artifact_id, spec in specs.items():
        live_fields = boundary.artifact_fields_from_path(
            spec,
            live_paths[artifact_id],
            source_label=f"live:{artifact_id}",
        )
        assert live_fields == boundary._artifact_fields(spec), artifact_id


def test_owner_declared_nullable_numeric_type_normalizes_live_values(
    tmp_path: Path,
) -> None:
    spec = next(
        spec
        for spec in boundary.ARTIFACT_SPECS
        if spec.artifact_id == "c1_mode_track_source"
    )
    source_payload = json.loads(
        (boundary.ROOT / spec.enumeration_source).read_text(encoding="utf-8")
    )
    target_path = (
        "$.rows[].peak_observation.content.integration."
        "area_baseline_corrected"
    )

    normalized_fields = []
    for label, value in (("null", None), ("number", 1.0)):
        payload = json.loads(json.dumps(source_payload))
        for row in payload["rows"]:
            row["peak_observation"]["content"]["integration"][
                "area_baseline_corrected"
            ] = value
        source_path = tmp_path / f"mode_track_source_{label}.json"
        source_path.write_text(json.dumps(payload), encoding="utf-8")
        normalized_fields.append(
            boundary.artifact_fields_from_path(
                spec,
                source_path,
                source_label=f"test:{label}",
            )
        )

    assert normalized_fields[0][target_path] == "null|number"
    assert normalized_fields[1][target_path] == "null|number"
    assert normalized_fields[0] == normalized_fields[1]


def test_owner_declared_numeric_field_rejects_same_path_string(
    tmp_path: Path,
) -> None:
    spec = next(
        spec
        for spec in boundary.ARTIFACT_SPECS
        if spec.artifact_id == "c1_mode_track_source"
    )
    payload = json.loads(
        (boundary.ROOT / spec.enumeration_source).read_text(encoding="utf-8")
    )
    for row in payload["rows"]:
        row["peak_observation"]["content"]["integration"][
            "area_baseline_corrected"
        ] = "not-a-number"
    source_path = tmp_path / "mode_track_source_string.json"
    source_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="incompatible live type string"):
        boundary.artifact_fields_from_path(
            spec,
            source_path,
            source_label="test:string",
        )


def test_empty_array_adds_no_element_type_when_live_elements_exist() -> None:
    fields = boundary.canonical_json_fields(
        {"rows": [{"values": []}, {"values": [{"id": "one"}]}]},
        artifact_id="test",
    )

    assert fields["$.rows[].values"] == "array[object]"


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
