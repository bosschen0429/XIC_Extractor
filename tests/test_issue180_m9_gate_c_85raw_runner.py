from __future__ import annotations

from pathlib import Path

import pytest

from tests import issue180_m9_gate_c_85raw_runner as runner
from tests import issue180_m9_raw_validation_fixture as gate_c_fixture
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import render_delimited_rows


def test_preflight_closes_85_inputs_without_opening_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "a" * 40)
    output_dir = tmp_path / "gate-c"
    launch_path = tmp_path / "launch.json"
    path_read_bytes = Path.read_bytes

    def reject_raw_reads(path: Path) -> bytes:
        if path.suffix.lower() == ".raw":
            raise AssertionError("no-RAW preflight opened a RAW file")
        return path_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_raw_reads)
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=output_dir,
        launch_manifest_path=launch_path,
        code_sha="a" * 40,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)

    raw_manifest = inputs.launch_manifest["raw"]
    assert isinstance(raw_manifest, dict)
    assert len(inputs.raw_files) == 85
    assert raw_manifest["sample_count"] == 85
    assert launch_path.is_file()
    assert not output_dir.exists()


def test_gate_c_budget_counts_discovery_and_m7_raw_opens_separately() -> None:
    contract = gate_c_fixture.gate_c_launch_contract()
    assert contract["schema_version"] == "issue180_m9_gate_c_launch_contract_v2"
    assert contract["call_budget"] == {
        "discovery_raw_open_count_max": 85,
        "m7_raw_open_count_max": 85,
        "total_raw_open_count_max": 170,
        "m7_xic_batch_count_max": 85,
    }
    assert gate_c_fixture._gate_c_workload_call_cost(
        discovery_raw_open_count=85,
        m7_call_cost={
            "raw_open_count": 83,
            "xic_batch_count": 79,
            "logical_xic_request_count": 86,
            "adapter_chromatogram_call_count": 86,
            "trace_point_count": 1482,
        },
    ) == {
        "discovery_raw_open_count": 85,
        "m7_raw_open_count": 83,
        "total_raw_open_count": 168,
        "m7_xic_batch_count": 79,
        "m7_logical_xic_request_count": 86,
        "m7_adapter_chromatogram_call_count": 86,
        "m7_trace_point_count": 1482,
    }


def test_shared_validation_artifact_labels_are_gate_neutral() -> None:
    heartbeat = gate_c_fixture._heartbeat("started", 0.0)

    assert heartbeat["schema_version"] == "issue180_m9_validation_heartbeat_v1"
    assert (
        gate_c_fixture._RUNTIME_TELEMETRY_UNAVAILABLE_REASON
        == "not_collected_by_issue180_m9_validation"
    )
    assert "gate_b" not in str(heartbeat)
    assert "gate_b" not in gate_c_fixture._RUNTIME_TELEMETRY_UNAVAILABLE_REASON


def test_atomic_directory_publication_retries_transient_windows_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    output_dir = tmp_path / "published"
    staging.mkdir()
    (staging / "receipt.json").write_text("{}\n", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def replace_after_transient_lock(path: Path, target: Path) -> Path:
        nonlocal attempts
        if path == staging:
            attempts += 1
            if attempts == 1:
                raise PermissionError("transient Windows directory lock")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace_after_transient_lock)
    monkeypatch.setattr(gate_c_fixture.time, "sleep", lambda _seconds: None)

    gate_c_fixture._publish_directory_atomic(staging, output_dir)

    # The injected first lock requires a retry; Windows may independently hold
    # the directory for another attempt under full-suite load.
    assert 2 <= attempts <= 5
    assert not staging.exists()
    assert (output_dir / "receipt.json").is_file()


def test_frozen_launch_calls_shared_core_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "b" * 40)
    output_dir = tmp_path / "gate-c"
    launch_path = tmp_path / "launch.json"
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=output_dir,
        launch_manifest_path=launch_path,
        code_sha="b" * 40,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)
    calls = []

    def fake_run_gate_c(**kwargs) -> Path:
        calls.append(kwargs)
        receipt = output_dir / "proposal" / "proposal_receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}\n", encoding="utf-8")
        return receipt

    monkeypatch.setattr(runner.gate_c_fixture, "run_gate_c", fake_run_gate_c)
    receipt = runner.run_frozen_launch(
        inputs=inputs,
        launch_manifest_path=launch_path,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=output_dir,
        code_sha="b" * 40,
    )

    assert receipt == output_dir / "proposal" / "proposal_receipt.json"
    assert len(calls) == 1
    assert len(calls[0]["raw_files"]) == 85


def test_frozen_c2_launch_calls_fresh_owner_finalization_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    proposal_dir, review, replay, transfer, checked, final_output = _c2_inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "9" * 40)
    replay_output = tmp_path / "gate-c-c2-replay"
    launch_path = tmp_path / "launch-c2.json"
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=replay_output,
        launch_manifest_path=launch_path,
        code_sha="9" * 40,
        stage="c2",
        proposal_dir=proposal_dir,
        manual_review_path=review,
        manual_review_replay_path=replay,
        review_transfer_receipt_path=transfer,
        checked_manifest_path=checked,
        final_output_dir=final_output,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)
    calls = []

    def fake_run_gate_c_fresh_finalization(**kwargs) -> Path:
        calls.append(kwargs)
        receipt = final_output / "transaction_receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}\n", encoding="utf-8")
        return receipt

    monkeypatch.setattr(
        runner.gate_c_fixture,
        "run_gate_c_fresh_finalization",
        fake_run_gate_c_fresh_finalization,
    )
    receipt = runner.run_frozen_launch(
        inputs=inputs,
        launch_manifest_path=launch_path,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=replay_output,
        code_sha="9" * 40,
    )

    assert receipt == final_output / "transaction_receipt.json"
    assert len(calls) == 1
    assert len(calls[0]["raw_files"]) == 85
    assert calls[0]["proposal_dir"] == proposal_dir
    assert calls[0]["replay_output_dir"] == replay_output
    assert calls[0]["final_output_dir"] == final_output


def test_c2_preflight_requires_closed_inputs_and_new_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    proposal_dir, review, replay, transfer, checked, final_output = _c2_inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "8" * 40)

    with pytest.raises(ValueError, match="requires proposal"):
        runner.build_launch_inputs(
            metadata_path=metadata_path,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=tmp_path / "replay-missing",
            launch_manifest_path=tmp_path / "launch-missing.json",
            code_sha="8" * 40,
            stage="c2",
        )

    final_output.mkdir()
    with pytest.raises(ValueError, match="final output directory already exists"):
        runner.build_launch_inputs(
            metadata_path=metadata_path,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=tmp_path / "replay-existing",
            launch_manifest_path=tmp_path / "launch-existing.json",
            code_sha="8" * 40,
            stage="c2",
            proposal_dir=proposal_dir,
            manual_review_path=review,
            manual_review_replay_path=replay,
            review_transfer_receipt_path=transfer,
            checked_manifest_path=checked,
            final_output_dir=final_output,
        )


def test_frozen_c2_launch_rejects_review_mutation_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    proposal_dir, review, replay, transfer, checked, final_output = _c2_inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "7" * 40)
    replay_output = tmp_path / "gate-c-c2-replay"
    launch_path = tmp_path / "launch-c2.json"
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=replay_output,
        launch_manifest_path=launch_path,
        code_sha="7" * 40,
        stage="c2",
        proposal_dir=proposal_dir,
        manual_review_path=review,
        manual_review_replay_path=replay,
        review_transfer_receipt_path=transfer,
        checked_manifest_path=checked,
        final_output_dir=final_output,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)
    review.write_text('{"changed": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed input changed"):
        runner.run_frozen_launch(
            inputs=inputs,
            launch_manifest_path=launch_path,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=replay_output,
            code_sha="7" * 40,
        )


@pytest.mark.parametrize("failure", ["raw_stem", "injection_order"])
def test_preflight_rejects_unclosed_universe_before_core(
    tmp_path: Path,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_launch_identity(monkeypatch, tmp_path, "c" * 40)
    metadata_path, raw_dir, dll_dir = _inputs(
        tmp_path,
        duplicate_order=failure == "injection_order",
    )
    if failure == "raw_stem":
        (raw_dir / "s085.raw").unlink()

    with pytest.raises(ValueError, match="RAW stem universes|injection orders"):
        runner.build_launch_inputs(
            metadata_path=metadata_path,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=tmp_path / "gate-c",
            launch_manifest_path=tmp_path / "launch.json",
            code_sha="c" * 40,
        )


def test_preflight_rejects_dirty_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "e" * 40)
    monkeypatch.setattr(runner, "_worktree_status", lambda root: "M dirty.py")

    with pytest.raises(ValueError, match="clean Git worktree"):
        runner.build_launch_inputs(
            metadata_path=metadata_path,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=tmp_path / "gate-c",
            launch_manifest_path=tmp_path / "launch.json",
            code_sha="e" * 40,
        )


def test_frozen_launch_rejects_checkout_dirtied_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "f" * 40)
    output_dir = tmp_path / "gate-c"
    launch_path = tmp_path / "launch.json"
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=output_dir,
        launch_manifest_path=launch_path,
        code_sha="f" * 40,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)
    monkeypatch.setattr(runner, "_worktree_status", lambda root: "M dirty.py")

    with pytest.raises(ValueError, match="execution identity changed"):
        runner.run_frozen_launch(
            inputs=inputs,
            launch_manifest_path=launch_path,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=output_dir,
            code_sha="f" * 40,
        )


def test_frozen_launch_rejects_missing_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_path, raw_dir, dll_dir = _inputs(tmp_path)
    _patch_launch_identity(monkeypatch, tmp_path, "d" * 40)
    output_dir = tmp_path / "gate-c"
    launch_path = tmp_path / "launch.json"
    inputs = runner.build_launch_inputs(
        metadata_path=metadata_path,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        manifest_root=tmp_path,
        output_dir=output_dir,
        launch_manifest_path=launch_path,
        code_sha="d" * 40,
    )
    runner.freeze_preflight(launch_path, inputs.launch_manifest)
    monkeypatch.setattr(
        runner.gate_c_fixture,
        "run_gate_c",
        lambda **kwargs: output_dir / "proposal" / "proposal_receipt.json",
    )

    with pytest.raises(RuntimeError, match="terminal proposal receipt"):
        runner.run_frozen_launch(
            inputs=inputs,
            launch_manifest_path=launch_path,
            dll_dir=dll_dir,
            manifest_root=tmp_path,
            output_dir=output_dir,
            code_sha="d" * 40,
        )


def _inputs(
    tmp_path: Path,
    *,
    duplicate_order: bool = False,
) -> tuple[Path, Path, Path]:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    (dll_dir / "RawFileReader.dll").write_bytes(b"fixture")
    rows = []
    for index in range(1, 86):
        stem = f"s{index:03d}"
        (raw_dir / f"{stem}.raw").write_bytes(b"")
        rows.append(
            {
                "schema_version": "sample_metadata_v1",
                "sample_name": stem,
                "raw_stem": stem,
                "injection_order": 1 if duplicate_order and index == 2 else index,
                "sample_role": "study_sample",
                "batch_id": "issue180-m9-gate-c",
                "prep_batch_id": "",
                "matrix_type": "breast_tissue",
                "group": "tumor",
                "excluded": "FALSE",
                "exclusion_reason": "",
            }
        )
    metadata_path = tmp_path / "sample_metadata.tsv"
    metadata_path.write_text(
        render_delimited_rows(
            rows,
            SAMPLE_METADATA_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    return metadata_path, raw_dir, dll_dir


def _c2_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    proposal_dir = tmp_path / "gate-c-c1" / "proposal"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "proposal.json").write_text("{}\n", encoding="utf-8")
    (proposal_dir / "proposal_receipt.json").write_text("{}\n", encoding="utf-8")
    review = tmp_path / "manual_review.json"
    replay = tmp_path / "manual_review_replay.json"
    transfer = tmp_path / "review_transfer_receipt.json"
    checked = tmp_path / "checked_manifest.json"
    for path in (review, replay, transfer, checked):
        path.write_text("{}\n", encoding="utf-8")
    return proposal_dir, review, replay, transfer, checked, tmp_path / "gate-c-final"


def _patch_launch_identity(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    code_sha: str,
) -> None:
    executable = root / ".venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(runner, "_current_executable", lambda: executable.resolve())
    monkeypatch.setattr(runner, "_checkout_sha", lambda checkout: code_sha)
    monkeypatch.setattr(runner, "_worktree_status", lambda checkout: "")
