from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_untargeted_cid_nl_mode_track as runner
from tests.test_alignment_mode_track_conservative_backfill import (
    _gate_c_proposal,
)
from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
    ModeTrackWorkflowRun,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import render_delimited_rows


def test_one_command_publishes_reviewed_mode_track_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)
    output_dir = tmp_path / "operator-output"
    calls: list[dict[str, object]] = []

    def fake_workflow(**kwargs):
        calls.append(kwargs)
        return ModeTrackWorkflowRun(
            projection=projection,
            discovery_index=tmp_path / "discovery_batch_index.csv",
            call_cost={"total_raw_open_count": 0},
        )

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        fake_workflow,
    )

    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-sample-count",
            "3",
            "--expected-backfill-cell-count",
            "2",
            "--sample-identity-namespace",
            "synthetic-mode-track",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert (output_dir / "alignment_matrix.tsv").is_file()
    assert (output_dir / "alignment_matrix.xlsx").is_file()
    assert (tmp_path / "operator-output.attempt/mode_track_source.json").is_file()


def test_failed_stage_never_publishes_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)
    output_dir = tmp_path / "failed-output"

    def fail_workflow(**_kwargs):
        raise RuntimeError("synthetic Discovery failure")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        fail_workflow,
    )
    original_write = runner._Heartbeat.write

    def fail_only_failure_heartbeat(self, *, event: str, **values: object) -> None:
        if event == "failed":
            raise OSError("synthetic heartbeat failure")
        original_write(self, event=event, **values)

    monkeypatch.setattr(runner._Heartbeat, "write", fail_only_failure_heartbeat)

    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--sample-identity-namespace",
            "synthetic-mode-track",
        ]
    )

    assert exit_code == 2
    assert not output_dir.exists()
    assert (tmp_path / "failed-output.attempt/timing.live.json").is_file()
    stderr = capsys.readouterr().err
    assert "warning: could not update heartbeat (failed)" in stderr
    assert "RuntimeError: synthetic Discovery failure" in stderr


def test_heartbeat_retries_transient_windows_replace_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "timing.live.json"
    heartbeat = runner._Heartbeat(path, started=0.0)
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(source: Path, target: Path) -> Path:
        nonlocal attempts
        if source.name == "timing.live.tmp" and attempts == 0:
            attempts += 1
            raise PermissionError(5, "synthetic transient heartbeat denial")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    heartbeat.write(event="sample_complete", sample_stem="s1")

    assert attempts == 1
    assert json.loads(path.read_text(encoding="utf-8"))["event"] == (
        "sample_complete"
    )


def test_heartbeat_io_failure_cannot_abort_workflow_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    heartbeat = runner._Heartbeat(tmp_path / "timing.live.json", started=0.0)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(5, "synthetic persistent heartbeat denial")

    monkeypatch.setattr(runner, "_write_json_atomic", fail_write)

    heartbeat.progress({"event": "m7_sample_complete", "sample_stem": "s1"})

    assert not (tmp_path / "timing.live.json").exists()
    assert "warning: could not update heartbeat (m7_sample_complete)" in (
        capsys.readouterr().err
    )


def test_preflight_closes_inputs_without_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)

    def reject_workflow(**_kwargs):
        raise AssertionError("preflight launched the RAW workflow")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        reject_workflow,
    )
    output_dir = tmp_path / "preflight-output"
    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-sample-count",
            "3",
            "--expected-backfill-cell-count",
            "2",
            "--sample-identity-namespace",
            "synthetic-mode-track",
            "--preflight-only",
        ]
    )

    assert exit_code == 0
    assert not output_dir.exists()
    assert not (tmp_path / "preflight-output.attempt").exists()


def test_preflight_rejects_missing_injection_order_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(
        tmp_path,
        missing_injection_order_stem="s2",
    )

    def reject_workflow(**_kwargs):
        raise AssertionError("invalid preflight launched the RAW workflow")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        reject_workflow,
    )
    output_dir = tmp_path / "missing-injection-order-output"
    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--sample-identity-namespace",
            "synthetic-mode-track",
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert "requires injection_order" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not (tmp_path / "missing-injection-order-output.attempt").exists()


def test_preflight_rejects_review_hash_drift_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    replay_path = review_bundle / "manual_review_observation_replay_v1.json"
    replay_path.write_bytes(replay_path.read_bytes() + b"\n")
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)

    def reject_workflow(**_kwargs):
        raise AssertionError("invalid preflight launched the RAW workflow")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        reject_workflow,
    )
    output_dir = tmp_path / "invalid-preflight-output"
    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-backfill-cell-count",
            "2",
            "--sample-identity-namespace",
            "synthetic-mode-track",
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert not output_dir.exists()
    assert not (tmp_path / "invalid-preflight-output.attempt").exists()


def test_preflight_rejects_wrong_reviewed_family_distribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _gate_c_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)

    def reject_workflow(**_kwargs):
        raise AssertionError("invalid preflight launched the RAW workflow")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        reject_workflow,
    )
    output_dir = tmp_path / "wrong-family-output"
    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata_path),
            "--preset",
            "dna_dr",
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-backfill-family-count",
            "FAM_WRONG=2",
            "--sample-identity-namespace",
            "synthetic-mode-track",
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert not output_dir.exists()
    assert not (tmp_path / "wrong-family-output.attempt").exists()


def _operator_inputs(
    tmp_path: Path,
    *,
    missing_injection_order_stem: str | None = None,
) -> tuple[Path, Path, Path]:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    (dll_dir / "RawFileReader.dll").write_bytes(b"fixture")
    rows = []
    for index, stem in enumerate(("s1", "s2", "s3"), start=1):
        (raw_dir / f"{stem}.raw").write_bytes(b"")
        rows.append(
            {
                "schema_version": "sample_metadata_v1",
                "sample_name": stem,
                "raw_stem": stem,
                "injection_order": (
                    "" if stem == missing_injection_order_stem else str(index)
                ),
                "sample_role": "study_sample",
                "batch_id": "synthetic",
                "prep_batch_id": "",
                "matrix_type": "synthetic",
                "group": "study",
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
    return raw_dir, dll_dir, metadata_path


def _review_bundle(tmp_path: Path, projection) -> Path:
    bundle = tmp_path / "review-bundle"
    bundle.mkdir()
    source_rows = json.loads(projection.source_artifact_bytes)["rows"]
    source_by_key = {
        (row["group_hypothesis_id"], row["sample_stem"]): row
        for row in source_rows
    }
    proposal_sha = "a" * 64
    source_sha = "b" * 64
    replay_rows = []
    review_rows = []
    for pending in projection.pending_acceptance_rows:
        key = (pending["peak_hypothesis_id"], pending["sample_stem"])
        source = source_by_key[key]
        replay_rows.append(
            {
                "area_parity": "pass",
                "feature_family_id": pending["feature_family_id"],
                "observation_content": source["peak_observation"]["content"],
                "peak_hypothesis_id": key[0],
                "peak_observation_content_id": source["physical_ids"][2],
                "proposal_quant_value": pending["quant_value"],
                "sample_stem": key[1],
            }
        )
        review_rows.append(
            {
                "candidate_key_sha256": "c" * 64,
                "evidence_locator": f"synthetic:{key[0]}:{key[1]}",
                "peak_hypothesis_id": key[0],
                "proposal_sha256": proposal_sha,
                "reviewed_at": "2026-08-07T00:00:00+08:00",
                "reviewer_id": "synthetic-reviewer",
                "sample_stem": key[1],
                "source_artifact_sha256": source_sha,
                "verdict": "pass",
            }
        )
    replay_path = bundle / "manual_review_observation_replay_v1.json"
    replay_path.write_text(
        json.dumps(
            {
                "proposal_sha256": proposal_sha,
                "row_count": len(replay_rows),
                "rows": replay_rows,
                "schema_version": "issue180_m9_gate_c_observation_replay_v1",
                "source_artifact_sha256": source_sha,
                "validation_status": "diagnostic_only",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle / "manual_review_v1.json").write_text(
        json.dumps(
            {
                "observation_replay_relpath": replay_path.name,
                "observation_replay_sha256": hashlib.sha256(
                    replay_path.read_bytes()
                ).hexdigest(),
                "rows": review_rows,
                "schema_version": "issue180_m9_gate_c_manual_review_v1",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle
