from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_alignment_xlsx_writer import sample_alignment_matrix
from tests.test_untargeted_explanation_index import (
    _attempt,
    _build_complete_no_raw_index,
    _build_index,
    _request,
    _samples,
)
from xic_extractor.alignment.backfill_evidence_contract import (
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.output_levels import artifact_names_for_output_level
from xic_extractor.alignment.untargeted_evidence_report import (
    render_untargeted_evidence_report,
    write_untargeted_evidence_report,
)
from xic_extractor.alignment.untargeted_explanation_index import (
    BackfillExplanationInput,
    ExplanationLocator,
    ExplanationLocatorKind,
)


def test_renders_one_integrated_bounded_report_with_existing_gallery_shell() -> None:
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[2]), _request(samples[0])),
    )

    rendered = render_untargeted_evidence_report(index)

    assert rendered.count('<tr class="seed-decision-row" data-family-row') == 2
    assert "Untargeted Evidence Report" in rendered
    assert "Discovery、Alignment、Backfill" in rendered
    assert "Discovery" in rendered
    assert "Alignment" in rendered
    assert "Backfill" in rendered
    assert "Matrix" in rendered
    assert "written: 1.00000e+02" in rendered
    assert index.run_binding.processing_attempt.processing_attempt_id in rendered
    assert index.entries[0].subject.sample.identity.sample_identity_id in rendered
    assert index.entries[0].subject.matrix_row_binding_sha256 in rendered
    assert "alignment_matrix.tsv and alignment_matrix_identity.tsv" in rendered
    assert (
        "writer byte receipts and processing attempt identity do not prove" in rendered
    )
    assert "no owner-produced CID-NL reference spine was supplied" in rendered
    assert "no owner-reader Backfill query result was supplied" in rendered
    assert "gallery-hero" in rendered
    assert "data-filter-control" in rendered
    assert "data-search-control" in rendered
    assert "data-detail-toggle" in rendered
    assert '<span class="result-count" data-result-count' not in rendered
    assert "data-lightbox" in rendered
    assert "const applyFilters" in rendered
    assert "sample=s2" not in rendered


def test_renders_owner_rehydrated_discovery_alignment_and_backfill_content(
    tmp_path: Path,
) -> None:
    index = _build_complete_no_raw_index(tmp_path)

    rendered = render_untargeted_evidence_report(index)

    assert "available sections</span><strong>4</strong>" in rendered
    assert "Candidate universe:" in rendered
    assert "Candidate PeakContents" in rendered
    assert "Selection reasons" in rendered
    assert "Peak group:" in rendered
    assert "Member PeakHypotheses" in rendered
    assert "Alignment reasons" in rendered
    assert "Matrix row identity basis:" in rendered
    assert "no_split_peak_hypothesis" in rendered
    assert "Backfill disposition:" in rendered
    assert "Production decision:" in rendered
    assert "write Matrix=false" in rendered
    assert "Backfill reasons" in rendered
    assert "Matrix:</strong> not written" in rendered


def test_report_accepts_only_the_owner_minted_typed_index() -> None:
    with pytest.raises(TypeError, match="UntargetedExplanationIndex"):
        render_untargeted_evidence_report({})  # type: ignore[arg-type]


def test_owner_text_is_escaped_and_unavailable_claims_remain_explicit() -> None:
    malicious_reason = '<script>alert("x")</script> & "quoted"'
    claim_subjects = (
        "record_content",
        "group_assignment_binding",
        "public_family_binding",
        "cross_artifact_binding",
        "production_decision",
        "full_aligned_cell",
        "missing_key_absence",
    )
    query_result = EvidenceQueryResult(
        records=(),
        claims=tuple(
            EvidenceClaim(
                subject=subject,
                state=EvidenceClaimState.UNAVAILABLE,
                reason=(
                    malicious_reason if subject == "missing_key_absence" else "missing"
                ),
            )
            for subject in claim_subjects
        ),
    )
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0]),),
        backfill_inputs=(
            BackfillExplanationInput(
                route_key=(
                    "FAM000001",
                    samples[0].identity.sample_identity_id,
                ),
                query_result=query_result,
            ),
        ),
    )

    rendered = render_untargeted_evidence_report(index)

    assert malicious_reason not in rendered
    assert '&lt;script&gt;alert(&quot;' in rendered
    assert '&lt;/script&gt; &amp; &quot;' in rendered
    assert "unavailable" in rendered
    assert "Owner claim census" in rendered


def test_relative_locator_stays_offline_and_overlay_uses_existing_lightbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locator = ExplanationLocator(
        kind=ExplanationLocatorKind.OVERLAY,
        relative_path="overlays/FAM000001-s1.png",
    )
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(locator,)),),
    )
    source_dir = tmp_path / "unrelated-cwd"
    existing_overlay = source_dir / locator.relative_path
    existing_overlay.parent.mkdir(parents=True)
    existing_overlay.write_bytes(b"fixture")
    monkeypatch.chdir(source_dir)

    rendered = render_untargeted_evidence_report(index)

    assert 'href="overlays/FAM000001-s1.png"' in rendered
    assert 'data-lightbox-src="overlays/FAM000001-s1.png"' in rendered
    assert "file://" not in rendered
    assert "https://" not in rendered
    assert "http://" not in rendered


def test_writer_is_opt_in_and_emits_deterministic_utf8_lf_bytes(
    tmp_path: Path,
) -> None:
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0]),),
    )
    path = tmp_path / "nested" / "untargeted_evidence_report.html"

    assert write_untargeted_evidence_report(path, index) == path
    first = path.read_bytes()
    assert first.startswith(b"<!doctype html>\n")
    assert b"\r\n" not in first

    assert write_untargeted_evidence_report(path, index) == path
    assert path.read_bytes() == first
    assert "untargeted_evidence_report.html" not in artifact_names_for_output_level(
        "production"
    )
