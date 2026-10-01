"""Owner-backed storage, publication and shared-callable regression coverage."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict

import pytest

from tests.test_cid_nl_explanation import _owner_run
from xic_extractor.diagnostics.alignment_explanation import read_cid_nl_explanation
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
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
        full_audit=True,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    expected_observations = {
        ref.content.peak_observation_content_id: asdict(ref.content.integration)
        for child in completed.application_result.children
        for terminal in child.scientific_core.terminal_subjects
        if getattr(terminal, "hypothesis", None) is not None
        for ref in terminal.hypothesis.peak_observation_refs
    }
    # Spool cleanup / close
    completed.close()

    # Re-read new DB
    summary = read_cid_nl_explanation(report_path)
    assert summary["schema_version"] == "alignment_explanation"
    assert summary["plot_evidence_capability"] == "retained_curves"
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()

    # Verify curves persisted without RAW or DLL
    reread = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from xic_extractor.diagnostics.alignment_explanation "
            "import read_cid_nl_curves; "
            "print(json.dumps(read_cid_nl_curves(sys.argv[1])))",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    curves = json.loads(reread.stdout)
    assert len(curves) >= 1
    for retained in curves:
        observation_id = retained["peak_observation_content_id"]
        if observation_id in expected_observations:
            assert retained["integration"] == json.loads(
                json.dumps(expected_observations[observation_id])
            )
    assert expected_observations.keys() <= {
        retained["peak_observation_content_id"] for retained in curves
    }
    s1_sample = next(s for s in summary["samples"] if s["sample_stem"] == "S1")
    curve = next(
        c for c in curves if c["sample_id"] == s1_sample["canonical_sample_identity_id"]
    )
    assert curve["kind"] == "initial_capture"
    assert curve["sample_id"] == s1_sample["canonical_sample_identity_id"]
    assert len(curve["curves"]["rt"]) == len(curve["curves"]["raw"])
    assert len(curve["curves"]["rt"]) > 0
    assert curve["integration"] is not None
    assert curve["integration"]["height_raw"] > 0
    assert curve["integration"]["area_raw_counts_seconds"] > 0


def test_missing_bound_matrix_rejects_generation(
    tmp_path,
) -> None:
    """An unavailable bound Matrix cannot produce a complete report."""
    # 1. Illegal: missing matrix
    fake_db = tmp_path / "fake.sqlite"
    matrix_file = tmp_path / "missing_matrix.tsv"
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

    with ExplanationReportWriter(fake_db) as writer:
        writer.finalize(
            {
                "processing_request_id": "req-1",
                "processing_attempt_id": "att-1",
                "matrix_path": str(matrix_file),
                "matrix_sha256": "0" * 64,
                "samples": [],
            }
        )
    out_html = tmp_path / "out.html"
    with pytest.raises(InspectorError) as exc:
        generate_report(fake_db, SelectionRequest(strategy="sample"), out_html)
    assert exc.value.code == 4
    assert not out_html.exists()


def test_acceptance_a4_zero_curve_read_on_metadata_query(tmp_path, monkeypatch) -> None:
    """A4: Metadata queries read 0 curves; generation reads only needed curves."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        full_audit=True,
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


def test_cli_callable_emits_embedded_html(tmp_path, monkeypatch) -> None:
    """The CLI callable embeds its resources; browser execution is separate."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        full_audit=True,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    out_html = tmp_path / "workbench.html"

    # Test CLI call
    exit_code = cli_main(
        [
            "--report",
            str(report_path),
            "--output",
            str(out_html),
            "--strategy",
            "sample",
            "--limit",
            "10",
            "--seed",
            "0",
            "--no-open",
        ]
    )
    assert exit_code == 0
    assert out_html.is_file()

    # Copy only HTML to inspect its embedded resources independently.
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
    # The embedded JSON occupies one script element.
    data_script = html_text.split('<script id="inspector-data"')[1]
    assert "</script>" not in data_script.split("</script>")[0]


def test_acceptance_a7_cancel_and_atomic_replace(tmp_path, monkeypatch) -> None:
    """A7: Cancel, error, or failure does not leave partial/broken HTML."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_cid_nl_explanation(
        tmp_path / "cid_nl_explanation.sqlite",
        full_audit=True,
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


def test_acceptance_a9_pipeline_shared_callable_handoff(tmp_path, monkeypatch) -> None:
    """A9: Pipeline outputs provide EvidenceLocator handoff to Inspector."""
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    import xic_extractor.applications.untargeted.application as application
    from tests.alignment_pipeline_helpers import peak_config, write_batch
    from tests.cid_nl_raw_fixture import _settings

    batch_index = write_batch(tmp_path, ("S1", "S2"))
    # The pipeline reads candidates through DiscoveryInputSource; the CSV
    # reader it used to call by name lives elsewhere now.
    monkeypatch.setattr(
        application.DiscoveryInputSource,
        "feature_view",
        lambda _source: candidates,
    )
    # The runtime fixture supplies actual completed science; exercise the real
    # public publication interface, SQLite writer, locator mint and close path.
    monkeypatch.setattr(
        application,
        "complete_untargeted_alignment",
        lambda **kwargs: completed,
    )
    outputs = application.run_untargeted_application(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "published",
        alignment_config=config,
        peak_config=peak_config(),
        discovery_settings=_settings(),
        emit_cid_nl_explanation=True,
    )
    locator = outputs.evidence
    assert isinstance(locator, EvidenceLocator)
    summary = read_cid_nl_explanation(locator.report_path)
    assert locator.processing_request_id == summary["processing_request_id"]
    assert locator.processing_attempt_id == summary["processing_attempt_id"]
    assert (
        locator.matrix_sha256
        == hashlib.sha256(locator.matrix_path.read_bytes()).hexdigest()
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
