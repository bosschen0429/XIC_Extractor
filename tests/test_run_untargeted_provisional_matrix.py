from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import scripts.run_untargeted_provisional_matrix as runner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.untargeted_provisional_matrix_workflow import (
    UntargetedProvisionalMatrixRun,
)


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
) -> None:
    inputs = _inputs(tmp_path, metadata_suffix=metadata_suffix)
    output_dir = tmp_path / "final"
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "_resolve_inputs", lambda _args: inputs)

    def fake_manifest(path, **kwargs):
        captured["manifest"] = kwargs
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(runner, "write_provisional_run_manifest", fake_manifest)

    def fake_workflow(**kwargs):
        captured.update(kwargs)
        workflow_dir = kwargs["output_dir"]
        alignment_dir = workflow_dir / "alignment"
        alignment_dir.mkdir(parents=True)
        paths = {
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
        discovery_index = workflow_dir / "discovery" / "discovery_batch_index.csv"
        discovery_index.parent.mkdir()
        discovery_index.write_text("ok\n", encoding="utf-8")
        return UntargetedProvisionalMatrixRun(
            discovery_index=discovery_index,
            alignment_outputs=AlignmentRunOutputs(**paths),
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
            "--raw-workers",
            "3",
            "--raw-xic-batch-size",
            "16",
        ]
    )

    assert exit_code == 0
    assert output_dir.is_dir()
    assert not output_dir.with_name("final.attempt").exists()
    assert (
        output_dir / "workflow" / "alignment" / "provisional_alignment_matrix.tsv"
    ).is_file()
    retained_metadata = (
        output_dir / "inputs" / f"sample_metadata{metadata_suffix}"
    )
    assert retained_metadata.read_bytes() == inputs.sample_metadata_path.read_bytes()
    assert captured["raw_files"] == inputs.raw_files
    assert captured["raw_workers"] == 3
    assert captured["raw_xic_batch_size"] == 16
    assert captured["sample_column_injection_order"] == (
        output_dir.with_name("final.attempt")
        / "inputs"
        / f"sample_metadata{metadata_suffix}"
    )
    manifest = captured["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["code_revision"] == "a" * 40
    assert manifest["runtime"].raw_workers == 3
    assert manifest["runtime"].raw_xic_batch_size == 16


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
        owner_build_xic_backend="raw",
        code_revision="a" * 40,
    )
