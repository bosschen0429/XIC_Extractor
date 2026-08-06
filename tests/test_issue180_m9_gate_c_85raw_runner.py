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
