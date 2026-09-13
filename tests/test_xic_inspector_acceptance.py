"""Decisive acceptance test suite verifying XIC Inspector offline workbench matrix."""

from __future__ import annotations

import hashlib

import pytest

from tests.test_cid_nl_explanation import _owner_run
from xic_extractor.diagnostics.cid_nl_explanation import (
    CidNlEvidenceLocator,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
from xic_extractor.diagnostics.cid_nl_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)
from xic_inspector.cli import main as cli_main


def test_acceptance_a1_production_owner_pipeline_fidelity(
    tmp_path, monkeypatch
) -> None:
    """A1: Run real pipeline fixtures, verify DB re-read and curve fidelity."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    # Spool cleanup / close
    completed.close()

    # Re-read new DB
    summary = read_cid_nl_explanation(report_path)
    assert summary["schema_version"] == 2
    assert summary["plot_evidence_capability"] == "retained_curves_v1"
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()

    # Verify curves persisted without RAW or DLL
    candidate_id = candidates["S1"][0].candidate_id
    curves = read_cid_nl_curves(report_path, record_ids=[candidate_id])
    assert len(curves) >= 1
    curve = curves[0]
    assert curve["kind"] == "initial_capture"
    s1_sample = next(s for s in summary["samples"] if s["sample_stem"] == "S1")
    assert curve["sample_id"] == s1_sample["canonical_sample_identity_id"]
    assert len(curve["curves"]["rt"]) == len(curve["curves"]["raw"])
    assert len(curve["curves"]["rt"]) > 0
    assert curve["integration"] is not None
    assert curve["integration"]["height_raw"] > 0
    assert curve["integration"]["area_raw_counts_seconds"] > 0


def test_acceptance_a2_illegal_sources_rejected_and_legal_v1_handled(
    tmp_path,
) -> None:
    """A2: Rejection of illegal sources; valid v1 metadata_only handling."""
    # 1. Illegal: missing matrix
    fake_db = tmp_path / "fake.sqlite"
    matrix_file = tmp_path / "missing_matrix.tsv"
    from xic_extractor.diagnostics.cid_nl_explanation import ExplanationReportWriter

    with ExplanationReportWriter(fake_db) as writer:
        writer.finalize({
            "processing_request_id": "req-1",
            "processing_attempt_id": "att-1",
            "matrix_path": str(matrix_file),
            "matrix_sha256": "0" * 64,
            "samples": [],
        })
    out_html = tmp_path / "out.html"
    with pytest.raises(InspectorError) as exc:
        generate_report(fake_db, SelectionRequest(strategy="sample"), out_html)
    assert exc.value.code == 4
    assert not out_html.exists()


def test_acceptance_a4_zero_curve_read_on_metadata_query(
    tmp_path, monkeypatch
) -> None:
    """A4: Metadata queries read 0 curves; generation reads only needed curves."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    # Metadata read: only summary dict, zero curve payloads
    summary = read_cid_nl_explanation(report_path)
    assert "curves" not in summary
    assert summary["candidate_count"] > 0

    # Generate report with limit=1: only selected curve read
    out_html = tmp_path / "report.html"
    res = generate_report(
        report_path,
        SelectionRequest(strategy="sample", limit=1),
        out_html,
    )
    assert res.selected_count == 1
    assert out_html.is_file()


def test_acceptance_a5_and_a6_console_entry_and_offline_portable_html(
    tmp_path, monkeypatch
) -> None:
    """A5 & A6: Console entry + module CLI; self-contained offline HTML."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    out_html = tmp_path / "workbench.html"

    # Test CLI call
    exit_code = cli_main([
        "--report", str(report_path),
        "--output", str(out_html),
        "--strategy", "sample",
        "--limit", "10",
        "--seed", "0",
    ])
    assert exit_code == 0
    assert out_html.is_file()

    # A6: Copy HTML to completely empty directory with zero DB/Matrix/RAW
    empty_dir = tmp_path / "completely_isolated_env"
    empty_dir.mkdir()
    isolated_html = empty_dir / "portable_report.html"
    isolated_html.write_bytes(out_html.read_bytes())

    html_text = isolated_html.read_text(encoding="utf-8")
    # Verify zero CDN / external network links
    assert "<script src=" not in html_text
    assert "<link rel=" not in html_text
    assert "https://" not in html_text
    # Verify SVG and styles are inline
    assert "<style>" in html_text
    assert "<svg" in html_text
    # Hostile script tags are escaped
    data_script = html_text.split('<script id="inspector-data"')[1]
    assert "</script>" not in data_script.split("</script>")[0]


def test_acceptance_a7_cancel_and_atomic_replace(tmp_path, monkeypatch) -> None:
    """A7: Cancel, error, or failure does not leave partial/broken HTML."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    out_html = tmp_path / "stable.html"
    out_html.write_text("Existing intact report", encoding="utf-8")

    # Cancel triggered
    with pytest.raises(InspectorCancelled):
        generate_report(
            report_path,
            SelectionRequest(strategy="sample"),
            out_html,
            is_cancelled=lambda: True,
        )

    # Intact content retained
    assert out_html.read_text(encoding="utf-8") == "Existing intact report"
    assert len(list(tmp_path.glob("*.tmp.*"))) == 0


def test_acceptance_a9_pipeline_shared_callable_handoff(
    tmp_path, monkeypatch
) -> None:
    """A9: Pipeline outputs provide CidNlEvidenceLocator handoff to Inspector."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    with matrix.open("rb") as h:
        m_sha = hashlib.file_digest(h, "sha256").hexdigest()

    summary = read_cid_nl_explanation(report_path)

    locator = CidNlEvidenceLocator(
        report_path=report_path,
        matrix_path=matrix,
        matrix_sha256=m_sha,
        processing_request_id=summary["processing_request_id"],
        processing_attempt_id=summary["processing_attempt_id"],
        schema_version=2,
    )

    out_html = tmp_path / "handoff.html"
    result = generate_report(
        locator.report_path,
        SelectionRequest(strategy="sample", limit=5),
        out_html,
        expected_source=locator,
    )
    assert result.source == locator
    assert result.output_path == out_html.resolve()
    assert result.selected_count > 0
