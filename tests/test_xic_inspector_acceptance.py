"""Owner-backed storage, publication and shared-callable regression coverage."""

from __future__ import annotations

import pytest

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    explanation_summary,
    run_alignment_fixture,
)
from tests.untargeted_partition_fixtures import children_of
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
)
from xic_extractor.applications.untargeted.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedRequest,
)
from xic_extractor.configuration.execution_profile import (
    UntargetedExecutionPolicy,
    in_process_execution,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)
from xic_inspector.cli import main as cli_main


def test_missing_bound_matrix_rejects_generation(
    tmp_path,
) -> None:
    """An unavailable bound Matrix cannot produce a complete report."""
    fake_db = tmp_path / "fake.sqlite"
    matrix_file = tmp_path / "missing_matrix.tsv"
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    with ExplanationReportWriter(fake_db) as writer:
        writer.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": "att-1",
                    "matrix_path": str(matrix_file),
                    "matrix_sha256": "0" * 64,
                    "samples": [],
                },
            )
        )
    out_html = tmp_path / "out.html"
    with pytest.raises(InspectorError) as exc:
        generate_report(fake_db, SelectionRequest(strategy="sample"), out_html)
    assert exc.value.code == 4
    assert not out_html.exists()


def test_acceptance_a4_loads_only_selected_evidence(tmp_path, monkeypatch) -> None:
    """A4: Select from metadata, then load only the selected review evidence."""
    from xic_inspector.sources import cid_nl

    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_alignment_explanation(
        tmp_path / "alignment_explanation.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    summary = read_alignment_explanation(report_path)
    assert "curves" not in summary
    assert summary["candidate_count"] > 0

    read_evidence = cid_nl.read_review_evidence

    def read_page(path, *, candidate_keys, row_ids, summary):
        assert candidate_keys
        assert len(row_ids) <= 1
        return read_evidence(
            path, candidate_keys=candidate_keys, row_ids=row_ids, summary=summary
        )

    monkeypatch.setattr(cid_nl, "read_review_evidence", read_page)
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
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_alignment_explanation(
        tmp_path / "alignment_explanation.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    out_html = tmp_path / "workbench.html"

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

    empty_dir = tmp_path / "completely_isolated_env"
    empty_dir.mkdir()
    isolated_html = empty_dir / "portable_report.html"
    isolated_html.write_bytes(out_html.read_bytes())

    html_text = isolated_html.read_text(encoding="utf-8")
    assert "<script src=" not in html_text
    assert "<link rel=" not in html_text
    assert "https://" not in html_text
    assert "<style>" in html_text
    assert "<svg" in html_text
    data_script = html_text.split('<script id="inspector-data"')[1]
    assert "</script>" not in data_script.split("</script>")[0]


def test_acceptance_a7_cancel_and_atomic_replace(tmp_path, monkeypatch) -> None:
    """A7: Cancel, error, or failure does not leave partial/broken HTML."""
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    report_path = write_alignment_explanation(
        tmp_path / "alignment_explanation.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()

    out_html = tmp_path / "stable.html"
    out_html.write_text("Existing intact report", encoding="utf-8")

    with pytest.raises(InspectorCancelled):
        generate_report(
            report_path,
            SelectionRequest(strategy="sample"),
            out_html,
            is_cancelled=lambda: True,
        )

    assert out_html.read_text(encoding="utf-8") == "Existing intact report"
    assert len(list(tmp_path.glob("*.tmp.*"))) == 0


def test_acceptance_a9_pipeline_shared_callable_handoff(tmp_path, monkeypatch) -> None:
    """A9: Pipeline outputs provide EvidenceLocator handoff to Inspector."""
    from dataclasses import replace

    from xic_extractor.configuration.mass_recalibration import MassRecalibration

    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    import xic_extractor.applications.untargeted.application as application
    from tests.alignment_pipeline_helpers import write_batch
    from tests.cid_nl_raw_fixture import _settings

    batch_index = write_batch(tmp_path, ("S1", "S2"))
    monkeypatch.setattr(
        application.DiscoveryInputSource,
        "feature_view",
        lambda _source: candidates,
    )
    monkeypatch.setattr(
        application,
        "complete_untargeted_alignment",
        lambda **kwargs: completed.bind_publication_inputs(
            peaks_by_sample=kwargs["peaks_by_sample"],
            **explanation_discovery_facts(completed),
            alignment_config=kwargs["alignment_config"],
        ),
    )
    outputs = application.run_untargeted_application(
        UntargetedRequest(
            execution=UntargetedExecutionPolicy(profile=in_process_execution()),
            discovery=batch_index,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "published",
            alignment_config=config,
            discovery_settings=replace(
                _settings(), mass_recalibration=MassRecalibration.NONE
            ),
            publication=UntargetedPublicationPolicy(emit_alignment_explanation=True),
        )
    )
    locator = outputs.evidence
    assert isinstance(locator, EvidenceLocator)
    summary = read_alignment_explanation(locator.report_path)
    assert locator.processing_attempt_id == summary["processing_attempt_id"]

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
