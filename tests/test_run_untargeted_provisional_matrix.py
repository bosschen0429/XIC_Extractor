from __future__ import annotations

import tomllib
from pathlib import Path

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


def test_runner_publishes_full_universe_outputs_atomically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inputs = _inputs(tmp_path)
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
    assert captured["raw_files"] == inputs.raw_files
    assert captured["raw_workers"] == 3
    assert captured["raw_xic_batch_size"] == 16
    assert captured["sample_column_injection_order"] == (
        output_dir.with_name("final.attempt") / "inputs" / "sample_metadata.tsv"
    )
    manifest = captured["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["code_revision"] == "a" * 40


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


def _inputs(tmp_path: Path) -> runner._OperatorInputs:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir(exist_ok=True)
    dll_dir.mkdir(exist_ok=True)
    raw_files = (raw_dir / "sample-a.raw", raw_dir / "sample-b.raw")
    for path in raw_files:
        path.write_bytes(b"raw")
    (dll_dir / "reader.dll").write_bytes(b"dll")
    (tmp_path / "samples.tsv").write_text(
        "sample_stem\tinjection_order\nsample-a\t1\nsample-b\t2\n",
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
        sample_metadata_path=tmp_path / "samples.tsv",
        owner_build_xic_backend="raw",
    )
