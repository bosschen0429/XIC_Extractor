import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from xic_extractor.instrument_qc.lifecycle import (
    DuplicateLifecycleRunError,
    append_lifecycle_dataset,
)
from xic_extractor.instrument_qc.models import InstrumentQCRunOutput, SDOLEKTrendRow


def _write_artifacts(
    output_dir: Path,
    *,
    hcd_payload: str | None = None,
) -> InstrumentQCRunOutput:
    output_dir.mkdir(parents=True, exist_ok=True)
    trend_tsv = output_dir / "instrument_qc_sdolek_trend.tsv"
    trend_json = output_dir / "instrument_qc_sdolek_trend.json"
    diagnostics_tsv = output_dir / "instrument_qc_sdolek_diagnostics.tsv"
    workbook = output_dir / "instrument_qc_trend_sdolek.xlsx"
    for path in (trend_tsv, trend_json, diagnostics_tsv, workbook):
        path.write_text(path.name, encoding="utf-8")
    hcd_audit_tsv = None
    hcd_audit_json = None
    if hcd_payload is not None:
        hcd_audit_tsv = output_dir / "instrument_qc_hcd_audit.tsv"
        hcd_audit_json = output_dir / "instrument_qc_hcd_audit.json"
        hcd_audit_tsv.write_text(f"{hcd_payload}\n", encoding="utf-8")
        hcd_audit_json.write_text(f'{{"payload":"{hcd_payload}"}}\n', encoding="utf-8")
    return InstrumentQCRunOutput(
        trend_rows=(_row(output_dir / "SDOLEK.raw", sample_name="SDOLEK"),),
        diagnostics=(),
        trend_tsv=trend_tsv,
        trend_json=trend_json,
        diagnostics_tsv=diagnostics_tsv,
        workbook=workbook,
        mixstds_rows=(_row(output_dir / "Mix_STDs.raw", sample_name="Mix_STDs"),),
        hcd_audit_tsv=hcd_audit_tsv,
        hcd_audit_json=hcd_audit_json,
    )


def test_append_lifecycle_dataset_writes_run_and_row_files(tmp_path: Path) -> None:
    output = _write_artifacts(tmp_path / "out")

    result = append_lifecycle_dataset(
        output=output,
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "out",
        lifecycle_root=tmp_path / "life",
        instrument_id="Orbitrap-1",
        method_doc=tmp_path / "method.docx",
        code_version="abc123",
        timestamp_utc="2026-05-20T00:00:00Z",
    )

    assert result.runs_tsv.exists()
    assert result.sdolek_tsv.exists()
    assert result.mixstds_tsv.exists()
    assert result.blank_tsv.exists()
    with result.runs_tsv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["instrument_id"] == "Orbitrap-1"
    assert rows[0]["code_version"] == "abc123"
    assert rows[0]["run_fingerprint"] == result.run_fingerprint
    with result.sdolek_tsv.open(encoding="utf-8", newline="") as handle:
        sdolek_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert sdolek_rows[0]["area"] == "123.4"
    assert sdolek_rows[0]["trend_flags"] == ""
    assert result.blank_tsv.read_text(encoding="utf-8").splitlines() == [
        "run_id\tsample_name\tstatus\ttic_area\tbpc_height"
    ]


def test_append_lifecycle_dataset_blocks_duplicate_by_default(
    tmp_path: Path,
) -> None:
    output = _write_artifacts(tmp_path / "out")
    kwargs: dict[str, Any] = {
        "output": output,
        "raw_dir": tmp_path / "raw",
        "output_dir": tmp_path / "out",
        "lifecycle_root": tmp_path / "life",
        "instrument_id": "Orbitrap-1",
        "method_doc": tmp_path / "method.docx",
        "code_version": "abc123",
        "timestamp_utc": "2026-05-20T00:00:00Z",
    }
    append_lifecycle_dataset(**kwargs)

    with pytest.raises(DuplicateLifecycleRunError):
        append_lifecycle_dataset(**kwargs)


def test_append_lifecycle_dataset_can_explicitly_allow_duplicate(
    tmp_path: Path,
) -> None:
    output = _write_artifacts(tmp_path / "out")
    kwargs: dict[str, Any] = {
        "output": output,
        "raw_dir": tmp_path / "raw",
        "output_dir": tmp_path / "out",
        "lifecycle_root": tmp_path / "life",
        "instrument_id": "Orbitrap-1",
        "method_doc": tmp_path / "method.docx",
        "code_version": "abc123",
        "timestamp_utc": "2026-05-20T00:00:00Z",
    }
    append_lifecycle_dataset(**kwargs)
    append_lifecycle_dataset(**kwargs, allow_duplicate=True)

    with (tmp_path / "life" / "instrument_qc_lifecycle_runs.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 2


def test_append_lifecycle_dataset_fingerprint_includes_hcd_audit_outputs(
    tmp_path: Path,
) -> None:
    first_output = _write_artifacts(tmp_path / "out", hcd_payload="hcd")
    kwargs: dict[str, Any] = {
        "raw_dir": tmp_path / "raw",
        "output_dir": tmp_path / "out",
        "lifecycle_root": tmp_path / "life",
        "instrument_id": "Orbitrap-1",
        "method_doc": tmp_path / "method.docx",
        "code_version": "abc123",
        "timestamp_utc": "2026-05-20T00:00:00Z",
    }
    first = append_lifecycle_dataset(output=first_output, **kwargs)
    second_output = _write_artifacts(tmp_path / "out", hcd_payload="changed-hcd")
    second = append_lifecycle_dataset(output=second_output, **kwargs)

    assert second.run_fingerprint != first.run_fingerprint
    with (tmp_path / "life" / "instrument_qc_lifecycle_runs.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 2


@pytest.mark.parametrize("existing_history", [False, True])
@pytest.mark.parametrize("failure_at", ["rows", "summary", "publish"])
def test_failed_lifecycle_append_preserves_history_and_can_retry(
    tmp_path: Path, monkeypatch, existing_history: bool, failure_at: str
) -> None:
    from xic_extractor.instrument_qc import lifecycle

    root = tmp_path / "life"
    root.mkdir()
    kwargs = {
        "raw_dir": tmp_path / "raw",
        "lifecycle_root": root,
        "instrument_id": "Orbitrap-1",
        "code_version": "abc123",
    }
    previous = None
    if existing_history:
        previous_output = _write_artifacts(tmp_path / "previous")
        previous = append_lifecycle_dataset(
            output=previous_output,
            output_dir=tmp_path / "previous",
            timestamp_utc="2026-05-20T00:00:00Z",
            **kwargs,
        )
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    output = _write_artifacts(tmp_path / "new")
    output = replace(
        output,
        trend_rows=(replace(output.trend_rows[0], sample_name="new-qc", area=456.7),),
        mixstds_rows=(
            replace(output.mixstds_rows[0], sample_name="new-mix", area=765.4),
        ),
    )
    append_kwargs = {
        **kwargs,
        "output": output,
        "output_dir": tmp_path / "new",
        "timestamp_utc": "2026-05-21T00:00:00Z",
    }
    write_tsv, write_text, replace_path = (
        lifecycle.write_tsv,
        Path.write_text,
        Path.replace,
    )
    failed = False

    def fail_once():
        nonlocal failed
        failed = True
        raise OSError("injected lifecycle write failure")

    def write_rows(path, *args, **values):
        if not failed and failure_at == "rows" and "sdolek.tsv" in path.name:
            fail_once()
        return write_tsv(path, *args, **values)

    def write_summary(path, *args, **values):
        if (
            not failed
            and failure_at == "summary"
            and path.name.endswith("summary.json")
        ):
            fail_once()
        return write_text(path, *args, **values)

    def publish(path, target):
        target = Path(target)
        if (
            not failed
            and failure_at == "publish"
            and target == root / "instrument_qc_lifecycle_mixstds.tsv"
        ):
            fail_once()
        return replace_path(path, target)

    monkeypatch.setattr(lifecycle, "write_tsv", write_rows)
    monkeypatch.setattr(Path, "write_text", write_summary)
    monkeypatch.setattr(Path, "replace", publish)
    with pytest.raises(OSError, match="injected lifecycle write failure"):
        append_lifecycle_dataset(**append_kwargs)
    assert failed
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before

    result = append_lifecycle_dataset(**append_kwargs)
    with result.runs_tsv.open(encoding="utf-8", newline="") as handle:
        runs = list(csv.DictReader(handle, delimiter="\t"))
    expected_ids = ([previous.run_id] if previous else []) + [result.run_id]
    assert [row["run_id"] for row in runs] == expected_ids
    for path, new_name, new_area in (
        (result.sdolek_tsv, "new-qc", "456.7"),
        (result.mixstds_tsv, "new-mix", "765.4"),
    ):
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        assert [row["run_id"] for row in rows] == expected_ids
        assert rows[-1]["sample_name"] == new_name
        assert rows[-1]["area"] == new_area
        if previous:
            assert rows[0]["area"] == "123.4"
    assert json.loads(result.summary_json.read_text(encoding="utf-8")) == {
        "run_count": len(expected_ids),
        "instrument_counts": {"Orbitrap-1": len(expected_ids)},
    }
    with pytest.raises(DuplicateLifecycleRunError):
        append_lifecycle_dataset(**append_kwargs)


def test_lifecycle_rollback_failure_retains_original_history(tmp_path, monkeypatch):
    root = tmp_path / "life"
    previous = append_lifecycle_dataset(
        output=_write_artifacts(tmp_path / "previous"),
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "previous",
        lifecycle_root=root,
        instrument_id="Orbitrap-1",
        code_version="abc123",
        timestamp_utc="2026-05-20T00:00:00Z",
    )
    previous_rows = previous.sdolek_tsv.read_bytes()
    previous_runs = previous.runs_tsv.read_bytes()
    replace_path = Path.replace

    def fail_publication_and_restore(path, target):
        target = Path(target)
        if target == previous.mixstds_tsv and path.suffix == ".tsv":
            raise PermissionError("publication denied")
        if target == previous.sdolek_tsv and path.suffix == ".previous":
            raise PermissionError("restore denied")
        return replace_path(path, target)

    monkeypatch.setattr(Path, "replace", fail_publication_and_restore)
    with pytest.raises(OSError, match="rollback failed; recoverable files retained at"):
        append_lifecycle_dataset(
            output=_write_artifacts(tmp_path / "new"),
            raw_dir=tmp_path / "raw",
            output_dir=tmp_path / "new",
            lifecycle_root=root,
            instrument_id="Orbitrap-1",
            code_version="abc123",
            timestamp_utc="2026-05-21T00:00:00Z",
        )
    assert previous.runs_tsv.read_bytes() == previous_runs
    assert previous.sdolek_tsv.read_bytes() != previous_rows
    # Recovery must still be able to recover the original, even when replacing
    # the published file is denied. Do not pin the private backup directory name.
    assert previous_rows in [
        path.read_bytes() for path in root.rglob("*") if path.is_file()
    ]


def _row(raw_path: Path, *, sample_name: str) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name=sample_name,
        raw_path=raw_path,
        injection_order=4,
        compound="SDO",
        precursor_mz=311.0814,
        identity_evidence="MS1_ONLY",
        reference_rt_min=6.26,
        rt_delta_to_reference_min=0.01,
        apex_rt_min=6.27,
        area=123.4,
        base_width_min=0.83,
        reference_base_width_min=0.83,
        base_width_ratio_to_reference=1.0,
        peak_start_rt_min=5.90,
        peak_end_rt_min=6.73,
        trend_confidence="clean",
        trend_flags=(),
        status="detected",
        reason="OK",
    )
