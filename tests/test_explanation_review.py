"""Compact review preserves decisions while omitting unrequested audit and curves."""

import sqlite3
from contextlib import contextmanager

import numpy as np
import pytest

from tests.test_cid_nl_explanation import _owner_run
from tools.diagnostics.explanation_curves import export_explanation_curves
from xic_extractor.diagnostics.alignment_explanation import (
    read_cid_nl_curves,
    read_cid_nl_explanation,
    read_explanation_metadata,
)
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_extractor.diagnostics.explanation_conversion import compact_explanation
from xic_inspector.api import SelectionRequest, generate_report
from xic_inspector.sources.cid_nl import CidNlSource
from xic_inspector.sources.curve_bundle import read_curve_bundle


@pytest.mark.parametrize("scenario", ("positive", "all_hold", "review_only"))
def test_default_review_keeps_values_and_query_without_curves(
    tmp_path, monkeypatch, scenario
):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, scenario
    )
    report = write_cid_nl_explanation(
        tmp_path / "review.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_cid_nl_explanation(report)
    assert summary["retention"] == "review"
    assert summary["plot_evidence_capability"] == "metadata_only"
    candidate_id = candidates["S1"][0].candidate_id
    records = read_cid_nl_explanation(report, candidate_id=candidate_id)["records"]
    assert "cell_observations" not in records
    assert all("artifact" not in row for row in records["method_assessments"])
    expected = sorted(
        (
            decision.subject.canonical_sample.identity.sample_identity_id,
            decision.variant,
            getattr(decision, "value", None),
        )
        for child in completed.application_result.children
        for row in child.completed_projection.rows
        for decision in row.decisions
    )
    actual = sorted(
        (row["canonical_sample_identity_id"], row["variant"], row["value"])
        for row in read_explanation_metadata(report)
        .records.get("cell_decisions", {})
        .values()
    )
    assert actual == expected
    assert read_cid_nl_curves(report) == []
    with sqlite3.connect(report) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM records "
                "WHERE kind IN ('cell_observations', 'cell_decisions')"
            ).fetchone()[0]
            == 0
        )
    result = generate_report(
        report,
        selection=SelectionRequest(strategy="query", candidate_ids=(candidate_id,)),
        output=tmp_path / "review.html",
    )
    assert result.selected_count > 0
    assert (tmp_path / "review.html").is_file()
    # Selection summaries must agree with fully hydrated facts: they determine
    # sampling coverage and attention, even when presentation is deferred.
    compact = CidNlSource(report)
    hydrated = CidNlSource(report, metadata=read_explanation_metadata(report))
    scope = set(compact.sample_labels)
    actual_items = compact.load_items(scope)
    expected_items = hydrated.load_items(scope)
    assert [
        (item.serialized_ref, item.stratum, item.details["review_summary"])
        for item in actual_items
    ] == [
        (item.serialized_ref, item.stratum, item.details["review_summary"])
        for item in expected_items
    ]

    # Query coordinates and eligibility must not construct presentation or
    # decode every row's values before filtering. This guards the measured
    # all-population work that made one selected item expensive.
    def forbidden(*args, **kwargs):
        raise AssertionError("query population loaded presentation/value payloads")

    with monkeypatch.context() as query_patch:
        query_patch.setattr(CidNlSource, "_presentation_item", forbidden)
        query_patch.setattr(
            "xic_extractor.diagnostics.alignment_explanation.unpack_cells", forbidden
        )
        query_source = CidNlSource(report, query_only=True)
        query_items = query_source.load_items(scope)
        assert "method_assessments" not in query_source.records
        assert [
            (item.serialized_ref, item.sample_ids, item.witnesses)
            for item in query_items
        ] == [
            (item.serialized_ref, item.sample_ids, item.witnesses)
            for item in expected_items
        ]
    # The prior compact schema is already used by retained reports.
    with sqlite3.connect(report) as db:
        db.execute("ALTER TABLE row_cells DROP COLUMN sample_mask")
    old_schema_items = CidNlSource(report, query_only=True).load_items(scope)
    assert old_schema_items == query_items


def test_selected_curves_are_explicit_and_other_ranges_stay_small(
    tmp_path, monkeypatch
):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    arguments = dict(
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    selected = write_cid_nl_explanation(
        tmp_path / "selected.sqlite", curve_mz_ranges=((499, 501),), **arguments
    )
    assert read_cid_nl_curves(selected)
    assert read_cid_nl_explanation(selected)["curve_retention"] == "selected"
    other = write_cid_nl_explanation(
        tmp_path / "other.sqlite", curve_mz_ranges=((200, 201),), **arguments
    )
    assert read_cid_nl_curves(other) == []
    full = write_cid_nl_explanation(
        tmp_path / "full.sqlite", full_audit=True, **arguments
    )
    assert read_cid_nl_explanation(full)["retention"] == "full_audit"
    assert full.stat().st_size > other.stat().st_size


def test_existing_full_report_conversion_preserves_sample_values(tmp_path, monkeypatch):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    full = write_cid_nl_explanation(
        tmp_path / "full.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
        full_audit=True,
    )
    before = full.read_bytes()
    review = tmp_path / "compact.sqlite"
    result = compact_explanation(full, review)
    assert full.read_bytes() == before
    assert result["review_bytes"] < result["source_bytes"]
    for candidate in (candidates["S1"][0], candidates["S2"][0]):
        old = read_cid_nl_explanation(full, candidate_id=candidate.candidate_id)
        new = read_cid_nl_explanation(review, candidate_id=candidate.candidate_id)

        def values(detail):
            return sorted(
                (
                    row["group_hypothesis_id"],
                    row["canonical_sample_identity_id"],
                    row["variant"],
                    row.get("value"),
                )
                for row in detail["records"].get("cell_decisions", [])
            )

        assert values(old) == values(new)
        assert (
            old["records"]["final_groups"][0]["member_subjects"]
            == new["records"]["final_groups"][0]["member_subjects"]
        )


def test_curve_export_opens_only_requested_sample_and_does_not_reintegrate(
    tmp_path, monkeypatch
):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    review = write_cid_nl_explanation(
        tmp_path / "review.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    source_before = review.read_bytes()
    (tmp_path / "S1.raw").write_bytes(b"test raw")
    opened = []

    class Raw:
        def extract_xic(self, mz, lower, upper, ppm):
            assert mz == candidates["S1"][0].precursor_mz
            assert upper - lower == 1.0
            rt = np.linspace(lower, upper, 51)
            return rt, 1000 * np.exp(-(((rt - 8) / 0.08) ** 2))

    @contextmanager
    def raw(path, dll):
        opened.append(path.name)
        yield Raw()

    monkeypatch.setattr("tools.diagnostics.explanation_curves.open_raw", raw)
    output = tmp_path / "selected.json"
    export_explanation_curves(
        review,
        output,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        candidate_id=candidates["S1"][0].candidate_id,
    )
    assert opened == ["S1.raw"]
    assert review.read_bytes() == source_before
    bundle = read_curve_bundle(output)
    assert len(bundle.transitions[0].traces) == 1
    assert bundle.transitions[0].traces[0].integration is None
    assert bundle.transitions[0].quantifications == ()
