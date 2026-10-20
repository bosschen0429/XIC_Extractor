from pathlib import Path

from scripts import run_instrument_qc
from xic_extractor.instrument_qc.classification import InstrumentQCClass
from xic_extractor.instrument_qc.sequence_manifest import (
    ManifestMatchConfidence,
    ManifestMatchStatus,
    SequenceManifestRow,
)


def test_method_doc_cli_helper_writes_manifest_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    method_doc = tmp_path / "method.docx"
    method_doc.write_bytes(b"fake docx")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    output_dir = tmp_path / "out"

    def fake_build_sequence_manifest(**_kwargs):
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
                instrument_method="20260105 STDs_ddMS2_CIDwHCD_EASY-IC",
                activation_method="CIDwHCD",
            ),
        )

    monkeypatch.setattr(
        run_instrument_qc,
        "build_sequence_manifest",
        fake_build_sequence_manifest,
    )

    result = run_instrument_qc._build_method_doc_manifest(
        method_doc=method_doc,
        raw_dir=raw_dir,
        output_dir=output_dir,
    )

    assert not isinstance(result, str)
    assert (output_dir / "instrument_qc_sequence_manifest.tsv").exists()
    assert (output_dir / "instrument_qc_injection_order.csv").exists()
    assert (output_dir / "instrument_qc_sequence_manifest.json").exists()
    assert (output_dir / "instrument_qc_sequence_manifest.md").exists()
    header = (
        (output_dir / "instrument_qc_sequence_manifest.tsv")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert header.endswith("instrument_method\tactivation_method")


def test_method_doc_cli_helper_rejects_sampleinfo(tmp_path: Path) -> None:
    sample_info = tmp_path / "SampleInfo.xlsx"
    sample_info.write_bytes(b"not method doc")

    result = run_instrument_qc._build_method_doc_manifest(
        method_doc=sample_info,
        raw_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert isinstance(result, str)
    assert "SampleInfo" in result


def test_method_doc_cli_helper_reports_parse_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    method_doc = tmp_path / "method.docx"
    method_doc.write_bytes(b"fake docx")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    def fake_build_sequence_manifest(**_kwargs):
        raise ValueError("unable to parse method DOCX: method.docx")

    monkeypatch.setattr(
        run_instrument_qc,
        "build_sequence_manifest",
        fake_build_sequence_manifest,
    )

    result = run_instrument_qc._build_method_doc_manifest(
        method_doc=method_doc,
        raw_dir=raw_dir,
        output_dir=tmp_path / "out",
    )

    assert isinstance(result, str)
    assert "unable to parse method DOCX" in result
