from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_alignment_xlsx_writer import sample_alignment_matrix
from tests.test_untargeted_explanation_index import (
    _attempt,
    _build_complete_no_raw_index,
    _build_index,
    _locator_request,
    _request,
    _samples,
)
from xic_extractor.alignment.backfill_evidence_contract import (
    EvidenceClaim,
    EvidenceClaimState,
)
from xic_extractor.alignment.output_levels import artifact_names_for_output_level
from xic_extractor.alignment.untargeted_explanation_contract import (
    _ENTRY_MINT_TOKEN,
    _INDEX_MINT_TOKEN,
    _SECTION_MINT_TOKEN,
)
from xic_extractor.alignment.untargeted_explanation_index import (
    ExplanationLocatorKind,
)
from xic_extractor.output.untargeted_evidence_report import (
    render_untargeted_evidence_report,
    write_untargeted_evidence_report,
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
    assert (
        '<span class="result-count" data-result-count '
        'data-result-label="routes">' in rendered
    )
    assert "${resultLabel}`" in rendered
    assert "data-lightbox" in rendered
    assert "const applyFilters" in rendered
    assert "sample=s2" not in rendered
    assert "m/z=242.1140" in rendered
    assert "RT=12.593" in rendered
    assert 'data-search="1 1 FAM000001' in rendered


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


def test_renders_selected_written_direct_support_route(tmp_path: Path) -> None:
    rendered = render_untargeted_evidence_report(
        _build_complete_no_raw_index(tmp_path, sample_stem="StudySupport")
    )

    assert "selected PeakHypothesis=xic.peak-hypothesis" in rendered
    assert "evaluated=false" in rendered
    assert "direct_support_no_recovery_required" in rendered
    assert "Matrix:</strong> written:" in rendered


def test_report_accepts_only_the_owner_minted_typed_index() -> None:
    with pytest.raises(TypeError, match="UntargetedExplanationIndex"):
        render_untargeted_evidence_report({})  # type: ignore[arg-type]


def test_owner_text_is_escaped_and_unavailable_claims_remain_explicit() -> None:
    malicious_reason = '<script>alert("x")</script> & "quoted"'
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0]),),
    )
    entry = index.entries[0]
    section = entry.sections[0]
    hostile_section = replace(
        section,
        claim=EvidenceClaim(
            subject=section.claim.subject,
            state=EvidenceClaimState.UNAVAILABLE,
            reason=malicious_reason,
        ),
        _mint_token=_SECTION_MINT_TOKEN,
    )
    hostile_entry = replace(
        entry,
        sections=(hostile_section, *entry.sections[1:]),
        _mint_token=_ENTRY_MINT_TOKEN,
    )
    hostile_index = replace(
        index,
        entries=(hostile_entry,),
        _mint_token=_INDEX_MINT_TOKEN,
    )

    rendered = render_untargeted_evidence_report(hostile_index)

    assert malicious_reason not in rendered
    assert "&lt;script&gt;alert(&quot;" in rendered
    assert "&lt;/script&gt; &amp; &quot;" in rendered
    assert "unavailable" in rendered


def test_relative_locator_stays_offline_and_overlay_uses_existing_lightbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = _samples()
    attempt = _attempt()
    report_root = tmp_path / "report-root"
    locator = _locator_request(
        report_root,
        attempt=attempt,
        sample=samples[0],
        kind=ExplanationLocatorKind.OVERLAY,
        artifact_name="FAM000001-s1.png",
    )
    index = _build_index(
        processing_attempt=attempt,
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(locator,)),),
    )
    source_dir = tmp_path / "unrelated-cwd"
    source_dir.mkdir()
    monkeypatch.chdir(source_dir)

    rendered = render_untargeted_evidence_report(index)

    assert f'href="{locator.relative_path}"' in rendered
    assert f'data-lightbox-src="{locator.relative_path}"' in rendered
    assert "file://" not in rendered
    assert "https://" not in rendered
    assert "http://" not in rendered


@pytest.mark.parametrize("mutation", ("missing", "tampered"))
def test_writer_rejects_missing_or_changed_locator_artifact(
    tmp_path: Path,
    mutation: str,
) -> None:
    samples = _samples()
    attempt = _attempt()
    report_root = tmp_path / "report-root"
    locator = _locator_request(
        report_root,
        attempt=attempt,
        sample=samples[0],
    )
    index = _build_index(
        processing_attempt=attempt,
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(locator,)),),
    )
    artifact = report_root.joinpath(*locator.relative_path.split("/"))
    if mutation == "missing":
        artifact.unlink()
    else:
        artifact.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="unavailable|changed"):
        write_untargeted_evidence_report(report_root / "report.html", index)


@pytest.mark.parametrize("alias_kind", ("hardlink", "symlink"))
def test_writer_rejects_output_aliasing_locator_artifact(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    samples = _samples()
    attempt = _attempt()
    report_root = tmp_path / "report-root"
    locator = _locator_request(
        report_root,
        attempt=attempt,
        sample=samples[0],
    )
    index = _build_index(
        processing_attempt=attempt,
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(locator,)),),
    )
    artifact = report_root.joinpath(*locator.relative_path.split("/"))
    output = report_root / "report.html"
    if alias_kind == "hardlink":
        output.hardlink_to(artifact)
    else:
        try:
            output.symlink_to(artifact)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")
    original = artifact.read_bytes()

    with pytest.raises(ValueError, match="output aliases"):
        write_untargeted_evidence_report(output, index)

    assert artifact.read_bytes() == original


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
