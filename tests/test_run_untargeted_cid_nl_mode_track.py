from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_untargeted_cid_nl_mode_track as runner
from tests.test_alignment_mode_track_conservative_backfill import (
    _closed_writer_proposal,
)
from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
    ModeTrackWorkflowRun,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import render_delimited_rows


@pytest.fixture(autouse=True)
def _valid_raw_reader_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "preflight_raw_reader",
        lambda _dll_dir: [],
    )


def test_help_declares_debug_review_role_without_product_authority(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        runner.main(["--help"])

    assert exc_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "debug or batch review" in help_text
    assert "no product authority" in help_text


def test_one_command_publishes_reviewed_mode_track_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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
    assert (output_dir / "publication_receipt.json").is_file()
    assert (tmp_path / "operator-output.attempt/mode_track_source.json").is_file()


def test_failed_stage_never_publishes_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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

    heartbeat.progress({"event": "runtime_sample_complete", "sample_stem": "s1"})

    assert not (tmp_path / "timing.live.json").exists()
    assert "warning: could not update heartbeat (runtime_sample_complete)" in (
        capsys.readouterr().err
    )


def test_preflight_closes_inputs_without_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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
    assert "debug or batch review; no product authority" in (
        capsys.readouterr().out
    )


def test_preflight_preserves_multi_tag_error_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)
    preset_path = tmp_path / "multi-tag.toml"
    preset_path.write_text(
        "\n".join(
            [
                'name = "Two CID-NL tags"',
                'description = "Invalid bounded ModeTrack preset"',
                'combine_mode = "union"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "A"',
                "value = 100.0",
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "B"',
                "value = 120.0",
                "",
                "[discovery]",
                "",
                "[alignment]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid preset launched RAW")
        ),
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
            str(preset_path),
            "--precursor-mz",
            "349.215",
            "--review-bundle-dir",
            str(review_bundle),
            "--output-dir",
            str(tmp_path / "multi-tag-output"),
            "--manifest-root",
            str(tmp_path),
            "--sample-identity-namespace",
            "synthetic-mode-track",
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "ValueError: ModeTrack runner requires a single-tag CID-NL preset" in stderr
    assert "PresetError:" not in stderr


def test_preflight_rejects_invalid_thermo_runtime_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)
    monkeypatch.setattr(
        runner,
        "preflight_raw_reader",
        lambda _dll_dir: ["required Thermo DLL is missing"],
    )
    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid Thermo runtime launched RAW")
        ),
    )
    output_dir = tmp_path / "invalid-thermo-output"

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
    assert "Thermo RAW reader preflight failed" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not (tmp_path / "invalid-thermo-output.attempt").exists()


def test_preflight_rejects_raw_stem_case_mismatch_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)
    metadata_text = metadata_path.read_text(encoding="utf-8")
    metadata_path.write_text(
        metadata_text.replace("s1\ts1\t", "s1\tS1\t", 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("case-mismatched sample launched RAW")
        ),
    )
    output_dir = tmp_path / "case-mismatch-output"

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
    assert "RAW stem casing differs from sample metadata" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not (tmp_path / "case-mismatch-output.attempt").exists()


def test_preflight_rejects_missing_injection_order_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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


@pytest.mark.parametrize("namespace", ["", "   "])
def test_preflight_rejects_empty_sample_identity_namespace_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    namespace: str,
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
    review_bundle = _review_bundle(tmp_path, projection)
    raw_dir, dll_dir, metadata_path = _operator_inputs(tmp_path)

    def reject_workflow(**_kwargs):
        raise AssertionError("invalid namespace launched the RAW workflow")

    monkeypatch.setattr(
        runner,
        "run_untargeted_cid_nl_mode_track_workflow",
        reject_workflow,
    )
    output_dir = tmp_path / "invalid-namespace-output"
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
            namespace,
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert "identity_namespace must be non-empty owner-normalized text" in (
        capsys.readouterr().err
    )
    assert not output_dir.exists()
    assert not (tmp_path / "invalid-namespace-output.attempt").exists()


def test_preflight_rejects_review_hash_drift_before_running_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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
    projection, _proposal_dir = _closed_writer_proposal(tmp_path)
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
                "schema_version": (
                    "mode_track_closed_writer_observation_replay_v1"
                ),
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
                "schema_version": (
                    "mode_track_closed_writer_manual_review_v1"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle
