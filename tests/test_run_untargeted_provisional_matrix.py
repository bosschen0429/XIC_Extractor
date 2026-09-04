from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import scripts.run_untargeted_provisional_matrix as runner
import xic_extractor.alignment.artifact_write_receipts as alignment_receipt_owner
import xic_extractor.extraction.provisional_run_manifest as manifest_owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.pipeline import (
    _validate_untargeted_cid_nl_alignment_options,
)
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.untargeted_provisional_matrix_workflow import (
    UntargetedProvisionalMatrixRun,
)
from xic_extractor.processing_identity import fingerprint_file_content
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS, SampleMetadata
from xic_extractor.tabular_io import render_delimited_rows


def test_pyproject_registers_provisional_matrix_entrypoint() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert (
        pyproject["project"]["scripts"]["xic-untargeted-provisional-matrix-cli"]
        == "scripts.run_untargeted_provisional_matrix:main"
    )


@pytest.mark.parametrize("metadata_suffix", [".tsv", ".csv", ".txt"])
def test_runner_publishes_full_universe_outputs_atomically(
    tmp_path: Path,
    monkeypatch,
    metadata_suffix: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _inputs(tmp_path, metadata_suffix=metadata_suffix)
    output_dir = tmp_path / "final"
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "_resolve_inputs", lambda _args: inputs)

    def fake_publish(staging, final_output, **kwargs):
        captured["manifest"] = kwargs
        (staging / "run_manifest.json").write_text("{}\n", encoding="utf-8")
        manifest_owner._publish_directory_atomic(
            staging,
            final_output,
            before_attempt=lambda: None,
        )
        return final_output / "run_manifest.json"

    monkeypatch.setattr(
        runner,
        "publish_provisional_run_bundle_atomic",
        fake_publish,
    )

    def fake_workflow(**kwargs):
        captured.update(kwargs)
        # Exercise the retained route's actual option gate so this test catches
        # forwarding metadata that the pure owner must mint itself.
        _validate_untargeted_cid_nl_alignment_options(
            raw_workers=kwargs["raw_workers"],
            raw_xic_batch_size=kwargs["raw_xic_batch_size"],
            owner_backfill_xic_backend=kwargs["owner_backfill_xic_backend"],
            owner_backfill_window_strategy=kwargs[
                "owner_backfill_window_strategy"
            ],
            owner_backfill_superwindow_span_factor=kwargs[
                "owner_backfill_superwindow_span_factor"
            ],
            sample_column_injection_order=kwargs.get("sample_column_injection_order"),
        )
        workflow_dir = kwargs["output_dir"]
        alignment_dir = workflow_dir / "alignment"
        alignment_dir.mkdir(parents=True)
        paths = {
            "matrix_tsv": alignment_dir / "alignment_matrix.tsv",
            "matrix_identity_tsv": (alignment_dir / "alignment_matrix_identity.tsv"),
            "review_tsv": alignment_dir / "alignment_review.tsv",
            "backfill_cell_evidence_tsv": (
                alignment_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            "owner_backfill_peak_fact_provenance_tsv": (
                alignment_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
            ),
            "provisional_matrix_tsv": (
                alignment_dir / "provisional_alignment_matrix.tsv"
            ),
            "provisional_identity_tsv": alignment_dir / "provisional_row_identity.tsv",
            "provisional_cell_provenance_tsv": (
                alignment_dir / "provisional_cell_provenance.tsv"
            ),
            "provisional_summary_json": (
                alignment_dir / "provisional_matrix_summary.json"
            ),
        }
        for path in paths.values():
            path.write_text("ok\n", encoding="utf-8")
        fingerprint_batch = _alignment_fingerprint_batch(tuple(paths.values()))
        discovery_index = workflow_dir / "discovery" / "discovery_batch_index.csv"
        discovery_index.parent.mkdir()
        discovery_index.write_text("ok\n", encoding="utf-8")
        return UntargetedProvisionalMatrixRun(
            discovery_index=discovery_index,
            alignment_outputs=AlignmentRunOutputs(
                **paths,
                artifact_fingerprint_batch=fingerprint_batch,
            ),
        )

    monkeypatch.setattr(
        runner,
        "run_untargeted_provisional_matrix_workflow",
        fake_workflow,
    )

    exit_code = runner.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--dll-dir",
            str(tmp_path / "dll"),
            "--sample-metadata",
            str(inputs.sample_metadata_path),
            "--preset",
            "dna_dr",
            "--code-revision",
            "a" * 40,
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-sample-count",
            "2",
        ]
    )

    assert exit_code == 0
    assert output_dir.is_dir()
    assert not output_dir.with_name("final.attempt").exists()
    assert (
        output_dir / "workflow" / "alignment" / "provisional_alignment_matrix.tsv"
    ).is_file()
    assert (
        output_dir
        / "workflow"
        / "alignment"
        / "alignment_owner_backfill_peak_fact_provenance.tsv"
    ).is_file()
    retained_metadata = output_dir / "inputs" / f"sample_metadata{metadata_suffix}"
    assert retained_metadata.read_bytes() == inputs.sample_metadata_path.read_bytes()
    assert captured["raw_files"] == inputs.raw_files
    assert captured["raw_workers"] == 1
    assert captured["raw_xic_batch_size"] == 1
    assert captured["capture_alignment_artifact_fingerprints"] is True
    assert "signal_processing_profile" not in captured
    assert captured["sample_column_injection_order"] is None
    assert captured["sample_metadata"] == inputs.sample_metadata
    assert captured["sample_metadata"][1].sample_role == "blank"
    assert captured["sample_metadata"][1].excluded is True
    manifest = captured["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["code_revision"] == "a" * 40
    assert "validation_status" not in manifest
    assert "product_authority" not in manifest
    assert runner.PROVISIONAL_VALIDATION_STATUS == "diagnostic_only"
    assert manifest["runtime"].raw_workers == 1
    assert manifest["runtime"].raw_xic_batch_size == 1
    assert isinstance(
        manifest["retained_sample_metadata_fingerprint"],
        manifest_owner.RetainedSampleMetadataFingerprintReceipt,
    )
    assert isinstance(
        manifest["alignment_artifact_fingerprints"],
        alignment_receipt_owner.AlignmentArtifactFingerprintBatch,
    )
    fingerprints = manifest["alignment_artifact_fingerprints"]
    assert len(fingerprints.receipts) == 9
    artifact_names = {receipt.artifact_name for receipt in fingerprints.receipts}
    assert "alignment_owner_backfill_peak_fact_provenance.tsv" in artifact_names
    assert "alignment_owner_backfill_peak_fact_provenance_v1.tsv" not in artifact_names
    assert "Validation status: diagnostic_only" in capsys.readouterr().out
    timing = json.loads((output_dir / "timing.json").read_text(encoding="utf-8"))
    assert timing["measurement_contract"]["cache_state"] == (
        "uncontrolled_os_cache_no_eviction"
    )


def test_runner_preflight_does_not_start_raw_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inputs = _inputs(tmp_path)
    output_dir = tmp_path / "final"
    monkeypatch.setattr(runner, "_resolve_inputs", lambda _args: inputs)
    monkeypatch.setattr(
        runner,
        "run_untargeted_provisional_matrix_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("RAW workflow started")),
    )

    exit_code = runner.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--dll-dir",
            str(tmp_path / "dll"),
            "--sample-metadata",
            str(tmp_path / "samples.tsv"),
            "--preset",
            "dna_dr",
            "--code-revision",
            "a" * 40,
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--expected-sample-count",
            "2",
            "--preflight-only",
        ]
    )

    assert exit_code == 0
    assert not output_dir.exists()
    assert not output_dir.with_name("final.attempt").exists()


def test_manifest_owner_atomic_directory_publication_retries_transient_windows_lock(
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
    monkeypatch.setattr(manifest_owner.time, "sleep", lambda _seconds: None)

    manifest_owner._publish_directory_atomic(
        staging,
        output_dir,
        before_attempt=lambda: None,
    )

    # The injected first lock requires a retry; Windows may independently hold
    # the directory for another attempt under full-suite load.
    assert 2 <= attempts <= 5
    assert not staging.exists()
    assert (output_dir / "receipt.json").is_file()


def test_runner_preflight_rejects_invalid_thermo_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    metadata = tmp_path / "samples.tsv"
    metadata.write_text("raw_stem\n", encoding="utf-8")
    output_dir = tmp_path / "final"
    monkeypatch.setattr(
        runner,
        "preflight_raw_reader",
        lambda _path: ["required Thermo DLL is missing"],
    )

    exit_code = runner.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-metadata",
            str(metadata),
            "--preset",
            "dna_dr",
            "--code-revision",
            "a" * 40,
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert not output_dir.exists()
    assert not output_dir.with_name("final.attempt").exists()


def test_runner_preflight_rejects_raw_stem_case_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _inputs(tmp_path)
    rows = []
    for index, stem in enumerate(("Sample-A", "sample-b"), start=1):
        rows.append(
            {
                "schema_version": "sample_metadata_v1",
                "sample_name": stem,
                "raw_stem": stem,
                "injection_order": str(index),
                "sample_role": "study_sample",
                "batch_id": "synthetic",
                "prep_batch_id": "",
                "matrix_type": "synthetic",
                "group": "study",
                "excluded": "FALSE",
                "exclusion_reason": "",
            }
        )
    inputs.sample_metadata_path.write_text(
        render_delimited_rows(
            rows,
            SAMPLE_METADATA_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "final"
    monkeypatch.setattr(runner, "preflight_raw_reader", lambda _path: [])
    monkeypatch.setattr(
        runner,
        "_executing_checkout_revision",
        lambda _asserted: "a" * 40,
    )
    monkeypatch.setattr(
        runner,
        "run_untargeted_provisional_matrix_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("case-mismatched sample launched RAW")
        ),
    )

    exit_code = runner.main(
        [
            "--raw-dir",
            str(inputs.raw_files[0].parent),
            "--dll-dir",
            str(inputs.dll_dir),
            "--sample-metadata",
            str(inputs.sample_metadata_path),
            "--preset",
            "dna_dr",
            "--output-dir",
            str(output_dir),
            "--manifest-root",
            str(tmp_path),
            "--preflight-only",
        ]
    )

    assert exit_code == 2
    assert "RAW stem casing differs from sample metadata" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not output_dir.with_name("final.attempt").exists()


def test_runner_derives_revision_from_clean_executing_checkout(monkeypatch) -> None:
    repo_root = Path(runner.__file__).resolve().parents[1]

    def fake_run(command, **_kwargs):
        args = tuple(command)
        if args[-2:] == ("rev-parse", "--show-toplevel"):
            stdout = str(repo_root)
        elif args[-2:] == ("rev-parse", "HEAD"):
            stdout = "a" * 40
        elif args[-3:] == ("status", "--porcelain", "--untracked-files=all"):
            stdout = ""
        else:
            raise AssertionError(f"unexpected Git command: {args}")
        return runner.subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner._executing_checkout_revision(None) == "a" * 40
    assert runner._executing_checkout_revision("a" * 40) == "a" * 40


def test_runner_rejects_dirty_or_mismatched_executing_checkout(monkeypatch) -> None:
    repo_root = Path(runner.__file__).resolve().parents[1]
    dirty = False

    def fake_run(command, **_kwargs):
        args = tuple(command)
        if args[-2:] == ("rev-parse", "--show-toplevel"):
            stdout = str(repo_root)
        elif args[-2:] == ("rev-parse", "HEAD"):
            stdout = "a" * 40
        elif args[-3:] == ("status", "--porcelain", "--untracked-files=all"):
            stdout = " M scripts/run_untargeted_provisional_matrix.py" if dirty else ""
        else:
            raise AssertionError(f"unexpected Git command: {args}")
        return runner.subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="asserted code revision differs"):
        runner._executing_checkout_revision("b" * 40)
    dirty = True
    with pytest.raises(ValueError, match="checkout must be clean"):
        runner._executing_checkout_revision(None)


def test_runner_rejects_unsupported_sample_metadata_suffix() -> None:
    with pytest.raises(ValueError, match="must use .csv, .tsv, or .txt"):
        runner._retained_sample_metadata_suffix(Path("samples.data"))


def _alignment_fingerprint_batch(
    paths: tuple[Path, ...],
) -> alignment_receipt_owner.AlignmentArtifactFingerprintBatch:
    receipts = tuple(
        alignment_receipt_owner._mint_alignment_artifact_fingerprint_receipt(
            artifact_path=path,
            fingerprint=fingerprint_file_content(path),
        )
        for path in paths
    )
    return alignment_receipt_owner._mint_alignment_artifact_fingerprint_batch(
        artifact_paths=paths,
        receipts=receipts,
    )


def _inputs(
    tmp_path: Path,
    *,
    metadata_suffix: str = ".tsv",
) -> runner._OperatorInputs:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir(exist_ok=True)
    dll_dir.mkdir(exist_ok=True)
    raw_files = (raw_dir / "sample-a.raw", raw_dir / "sample-b.raw")
    for path in raw_files:
        path.write_bytes(b"raw")
    (dll_dir / "reader.dll").write_bytes(b"dll")
    delimiter = "," if metadata_suffix == ".csv" else "\t"
    metadata_path = tmp_path / f"samples{metadata_suffix}"
    metadata_path.write_text(
        delimiter.join(("sample_stem", "injection_order"))
        + "\n"
        + delimiter.join(("sample-a", "1"))
        + "\n"
        + delimiter.join(("sample-b", "2"))
        + "\n",
        encoding="utf-8",
    )
    return runner._OperatorInputs(
        raw_files=raw_files,
        dll_dir=dll_dir,
        discovery_settings=DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0473),
            ),
        ),
        alignment_config=AlignmentConfig(),
        sample_metadata_path=metadata_path,
        sample_metadata=(
            SampleMetadata(
                sample_name="sample-a",
                raw_stem="sample-a",
                injection_order=1,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="sample-b",
                raw_stem="sample-b",
                injection_order=2,
                sample_role="blank",
                excluded=True,
                exclusion_reason="operator exclusion",
            ),
        ),
        owner_build_xic_backend="raw",
        code_revision="a" * 40,
    )
