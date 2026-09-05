from pathlib import Path

from scripts import run_instrument_qc
from xic_extractor.instrument_qc.classification import InstrumentQCClass
from xic_extractor.instrument_qc.models import InstrumentQCRunOutput
from xic_extractor.instrument_qc.sequence_manifest import (
    ManifestMatchConfidence,
    ManifestMatchStatus,
    SequenceManifestRow,
)


def _fake_output(output_dir: Path) -> InstrumentQCRunOutput:
    trend_tsv = output_dir / "instrument_qc_sdolek_trend.tsv"
    trend_json = output_dir / "instrument_qc_sdolek_trend.json"
    diagnostics_tsv = output_dir / "instrument_qc_sdolek_diagnostics.tsv"
    workbook = output_dir / "instrument_qc_trend_sdolek.xlsx"
    output_dir.mkdir(parents=True, exist_ok=True)
    trend_tsv.write_text("sample_name\n", encoding="utf-8")
    trend_json.write_text("{}\n", encoding="utf-8")
    diagnostics_tsv.write_text(
        "sample_name\traw_path\tissue\tdetail\n",
        encoding="utf-8",
    )
    workbook.write_text("xlsx\n", encoding="utf-8")
    return InstrumentQCRunOutput(
        trend_rows=(),
        diagnostics=(),
        trend_tsv=trend_tsv,
        trend_json=trend_json,
        diagnostics_tsv=diagnostics_tsv,
        workbook=workbook,
    )


def test_run_instrument_qc_cli_calls_sdolek_pipeline(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls: dict[str, object] = {}

    def fake_run_sdolek_pipeline(**kwargs):
        calls.update(kwargs)
        return _fake_output(kwargs["output_dir"])

    monkeypatch.setattr(
        run_instrument_qc, "run_sdolek_pipeline", fake_run_sdolek_pipeline
    )

    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--mode",
            "sdolek",
        ]
    )

    assert rc == 0
    assert calls["raw_dir"] == tmp_path / "raw"
    assert calls["output_dir"] == tmp_path / "out"
    assert calls["emit_mixstds"] is False
    output = capsys.readouterr().out
    assert "instrument_qc_sdolek_trend.tsv" in output
    assert "instrument_qc_trend_sdolek.xlsx" in output


def test_run_instrument_qc_cli_passes_mixstds_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_run_sdolek_pipeline(**kwargs):
        calls.update(kwargs)
        return _fake_output(kwargs["output_dir"])

    monkeypatch.setattr(
        run_instrument_qc, "run_sdolek_pipeline", fake_run_sdolek_pipeline
    )

    registry = tmp_path / "mixstds.csv"
    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--mode",
            "sdolek",
            "--emit-mixstds",
            "--mixstds-target-registry",
            str(registry),
        ]
    )

    assert rc == 0
    assert calls["emit_mixstds"] is True
    assert calls["mixstds_target_registry"] == registry


def test_run_instrument_qc_cli_passes_hcd_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_run_sdolek_pipeline(**kwargs):
        calls.update(kwargs)
        return _fake_output(kwargs["output_dir"])

    monkeypatch.setattr(
        run_instrument_qc, "run_sdolek_pipeline", fake_run_sdolek_pipeline
    )

    registry = tmp_path / "hcd.csv"
    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--emit-hcd-audit",
            "--hcd-product-registry",
            str(registry),
        ]
    )

    assert rc == 0
    assert calls["emit_hcd_audit"] is True
    assert calls["hcd_product_registry"] == registry


def test_run_instrument_qc_cli_generates_manifest_from_method_doc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}
    method_doc = tmp_path / "method.docx"
    method_doc.write_bytes(b"fake docx")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    def fake_build_sequence_manifest(**kwargs):
        assert kwargs["method_doc"] == method_doc
        assert kwargs["raw_dir"] == raw_dir
        return (
            SequenceManifestRow(
                source_doc=str(method_doc),
                source_section="table:1:row:2",
                doc_display_name="SDOLEK-pretest",
                raw_stem="SDOLEK-pretest",
                injection_order=4,
                instrument_qc_class=InstrumentQCClass.SDOLEK,
                match_status=ManifestMatchStatus.MATCHED,
                match_confidence=ManifestMatchConfidence.HIGH,
                match_reason="matched",
            ),
        )

    def fake_run_sdolek_pipeline(**kwargs):
        calls.update(kwargs)
        return _fake_output(kwargs["output_dir"])

    monkeypatch.setattr(
        run_instrument_qc,
        "build_sequence_manifest",
        fake_build_sequence_manifest,
    )
    monkeypatch.setattr(
        run_instrument_qc,
        "run_sdolek_pipeline",
        fake_run_sdolek_pipeline,
    )

    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(raw_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--mode",
            "sdolek",
            "--method-doc",
            str(method_doc),
        ]
    )

    assert rc == 0
    assert calls["injection_order_source"] == (
        tmp_path / "out" / "instrument_qc_injection_order.csv"
    )
    assert calls["sequence_manifest_source"] == (
        tmp_path / "out" / "instrument_qc_sequence_manifest.tsv"
    )
    assert (tmp_path / "out" / "instrument_qc_sequence_manifest.tsv").exists()
    assert (tmp_path / "out" / "instrument_qc_sample_metadata.tsv").exists()
    assert (tmp_path / "out" / "instrument_qc_sequence_manifest.json").exists()
    assert (tmp_path / "out" / "instrument_qc_sequence_manifest.md").exists()


def test_run_instrument_qc_cli_rejects_sampleinfo_method_doc(
    tmp_path: Path,
    capsys,
) -> None:
    sample_info = tmp_path / "SampleInfo.xlsx"
    sample_info.write_bytes(b"not a method doc")

    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--method-doc",
            str(sample_info),
        ]
    )

    assert rc == 2
    assert "SampleInfo" in capsys.readouterr().err


def test_run_instrument_qc_cli_requires_lifecycle_options(
    tmp_path: Path,
    capsys,
) -> None:
    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--append-lifecycle",
        ]
    )

    assert rc == 2
    assert "--instrument-id" in capsys.readouterr().err


def test_run_instrument_qc_cli_appends_lifecycle_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_run_sdolek_pipeline(**kwargs):
        return _fake_output(kwargs["output_dir"])

    def fake_append_lifecycle_dataset(**kwargs):
        calls.update(kwargs)
        return type(
            "Result",
            (),
            {
                "runs_tsv": tmp_path / "life" / "instrument_qc_lifecycle_runs.tsv",
                "sdolek_tsv": tmp_path / "life" / "instrument_qc_lifecycle_sdolek.tsv",
                "mixstds_tsv": (
                    tmp_path / "life" / "instrument_qc_lifecycle_mixstds.tsv"
                ),
                "blank_tsv": tmp_path / "life" / "instrument_qc_lifecycle_blank.tsv",
                "summary_json": (
                    tmp_path / "life" / "instrument_qc_lifecycle_summary.json"
                ),
            },
        )()

    monkeypatch.setattr(
        run_instrument_qc, "run_sdolek_pipeline", fake_run_sdolek_pipeline
    )
    monkeypatch.setattr(
        run_instrument_qc,
        "append_lifecycle_dataset",
        fake_append_lifecycle_dataset,
    )

    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--append-lifecycle",
            "--instrument-id",
            "Orbitrap-1",
            "--lifecycle-root",
            str(tmp_path / "life"),
        ]
    )

    assert rc == 0
    assert calls["instrument_id"] == "Orbitrap-1"
    assert calls["lifecycle_root"] == tmp_path / "life"


def test_run_instrument_qc_cli_rejects_unknown_mode(capsys) -> None:
    rc = run_instrument_qc.main(
        [
            "--raw-dir",
            "raw",
            "--output-dir",
            "out",
            "--mode",
            "blank",
        ]
    )

    assert rc == 2
    assert "unsupported mode" in capsys.readouterr().err


def test_run_instrument_qc_help_lists_supported_outputs() -> None:
    help_text = run_instrument_qc.build_parser().format_help()

    assert "instrument_qc_trend_sdolek.xlsx" in help_text
    assert "SDOLEK subfolder" in help_text
    assert "--emit-mixstds" in help_text
    assert "--method-doc" in help_text
    assert "--injection-order-source" in help_text
    assert "--append-lifecycle" in help_text
    assert "--emit-hcd-audit" in help_text
