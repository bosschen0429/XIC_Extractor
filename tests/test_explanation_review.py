"""Compact review preserves decisions while omitting unrequested audit and curves."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

import numpy as np
import pytest

from tests.test_cid_nl_explanation import _owner_run
from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationReportWriter,
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
from xic_workbench.acquisition import (
    CurveAcquisitionCancelled,
    export_explanation_curves,
)


@pytest.mark.parametrize("old_revision", (None, 1))
def test_index_existing_review_preserves_source_and_rejects_broken_index(
    tmp_path, monkeypatch, old_revision
):
    from tools.diagnostics.explain_cid_nl_run import main
    from xic_inspector.api import InspectorError

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    source = write_cid_nl_explanation(
        tmp_path / "old.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    with sqlite3.connect(source) as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'query_%'"
            )
        ]
        for table in tables:
            if old_revision is None or table == "query_review":
                db.execute(f'DROP TABLE "{table}"')
        summary = json.loads(
            db.execute("SELECT payload FROM metadata WHERE key='summary'").fetchone()[0]
        )
        summary.pop("query_index_version")
        if old_revision is not None:
            summary["query_index_version"] = old_revision
        db.execute(
            "UPDATE metadata SET payload=? WHERE key='summary'", (json.dumps(summary),)
        )
    before = source.read_bytes()
    target = tmp_path / "indexed.sqlite"
    assert main(["--report", str(source), "--index-query-to", str(target)]) == 0
    assert source.read_bytes() == before
    assert (
        read_explanation_metadata(source).candidates
        == read_explanation_metadata(target).candidates
    )
    assert (
        read_explanation_metadata(source).records
        == read_explanation_metadata(target).records
    )
    assert (
        read_explanation_metadata(source).links
        == read_explanation_metadata(target).links
    )
    assert main(["--report", str(source), "--index-query-to", str(target)]) == 2
    with sqlite3.connect(target) as db:
        db.execute("DROP TABLE query_witnesses")
    with pytest.raises(InspectorError, match="Invalid query index"):
        generate_report(
            target,
            SelectionRequest(strategy="query", mz_min=400),
            tmp_path / "broken.html",
        )


@pytest.mark.parametrize(
    "criteria,expected",
    [
        ({"mz_min": 500, "mz_max": 500, "rt_min": 2, "rt_max": 2}, (5, 5, 0, 1)),
        ({"mz_min": 500, "mz_max": 500}, (5, 5, 4, 0)),
        ({"sample_ids": ("S1",), "mz_min": 500, "mz_max": 500}, (5, 3, 3, 0)),
        ({"sample_ids": ("absent",), "mz_min": 500}, (5, 0, 0, 0)),
        ({"candidate_ids": ("C1",)}, (5, 5, 1, 0)),
        ({"group_ids": ("G1",), "sample_ids": ("S2",)}, (5, 3, 1, 0)),
        ({"mz_min": 500, "offset": 1, "limit": 2}, (5, 5, 4, 0)),
        ({"rt_min": 99}, (5, 5, 0, 1)),
        ({"strategy": "sample", "seed": 0, "limit": 2}, (5, 5, 5, 0)),
        ({"strategy": "sample", "seed": 19, "limit": 4}, (5, 5, 5, 0)),
        ({"strategy": "sample", "seed": 19, "sample_ids": ("S1",)}, (5, 3, 3, 0)),
        ({"strategy": "sample", "sample_ids": ("S2",), "limit": 1}, (5, 3, 3, 0)),
        ({"strategy": "sample", "sample_ids": ("absent",)}, (5, 0, 0, 0)),
        ({"strategy": "attention", "limit": 2}, (5, 5, 3, 0)),
        ({"strategy": "attention", "sample_ids": ("S1",)}, (5, 3, 2, 0)),
        ({"strategy": "attention", "sample_ids": ("S2",)}, (5, 3, 2, 0)),
    ],
)
def test_indexed_selection_keeps_witness_scope_counts_and_bounded_loading(
    tmp_path, monkeypatch, criteria, expected
):
    from tests.test_xic_inspector_api import _report_data

    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report, review_only=True) as writer:
        for cid, sid, mz, rt in (
            ("C1", "S1", 500, 1),
            ("C2", "S2", 600, 2),
            ("C3", "S1", 500, None),
            ("C4", "S2", 500, 3),
            ("C5", "S1", 500, 4),
        ):
            writer.add_candidate(
                cid,
                sid,
                {
                    "candidate_id": cid,
                    "precursor_mz": mz,
                    "canonical_sample_identity_id": sid,
                    "rt_apex_min": rt,
                },
            )
            observations = [
                {"peak_observation_content_id": cid + "-obs", "rt_apex_min": rt}
            ]
            if cid == "C5":
                observations.append(
                    {"peak_observation_content_id": "unrelated", "rt_apex_min": 99}
                )
            writer.add_record(
                "terminal_subjects", cid, {"observations": observations}, [(cid, sid)]
            )
        for gid, rid, keys in (
            ("G1", "row-α", [("C1", "S1"), ("C2", "S2")]),
            ("G2", None, [("C3", "S1")]),
            ("G3", 'row-"z', [("C5", "S1")]),
            ("G4", "empty", []),
        ):
            group = {"group_hypothesis_id": gid}
            if gid == "G3":
                group["member_subjects"] = [
                    {
                        "canonical_sample_identity_id": "S1",
                        "physical_observation_ids": ["C5-obs"],
                    }
                ]
            writer.add_record("final_groups", gid, group, keys)
            if rid is not None:
                writer.add_record(
                    "rows", rid, {"row_key_id": rid, "group_hypothesis_id": gid}, keys
                )
                writer.add_row_cells(
                    rid,
                    gid,
                    [
                        {
                            "canonical_sample_identity_id": sid,
                            "variant": "unresolved",
                            "value": None,
                        }
                        for sid in ({s for _, s in keys} or {"S2"})
                    ],
                    {"S1": 0, "S2": 1},
                )
        writer.add_record(
            "initial_edges",
            "edge-1",
            {"initial_evidence": {"decision": "blocked_edge"}},
            [("C1", "S1"), ("C4", "S2")],
        )
        writer.finalize(
            {
                "matrix_path": str(matrix),
                "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                "samples": [
                    {"canonical_sample_identity_id": sid} for sid in ("S1", "S2")
                ],
                "selection_evidence_capability": "recorded_decisions",
            }
        )
    request = SelectionRequest(**{"strategy": "query", **criteria})
    baseline = tmp_path / "baseline.html"
    with monkeypatch.context() as patch:
        patch.setattr(
            "xic_inspector.api.open_source", lambda path, **kw: CidNlSource(path, **kw)
        )
        old = generate_report(report, request, baseline)
    assert (
        old.run_total,
        old.eligible_count,
        old.matched_count,
        old.missing_rt_count,
    ) == expected

    def forbidden(*args, **kwargs):
        raise AssertionError("indexed query read the entire review population")

    monkeypatch.setattr(
        "xic_inspector.sources.cid_nl.read_explanation_metadata", forbidden
    )
    output = tmp_path / "indexed.html"
    result = generate_report(report, request, output)
    assert (
        result.run_total,
        result.eligible_count,
        result.matched_count,
        result.missing_rt_count,
    ) == expected
    assert _report_data(output) == _report_data(baseline)


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
        for detail in (old, new):
            for cell in detail["records"]["cell_decisions"]:
                assert cell["signal_quality"]
                assert all(
                    quality["status"] != "not_recorded"
                    for quality in cell["signal_quality"]
                )
        assert [c["signal_quality"] for c in old["records"]["cell_decisions"]] == [
            c["signal_quality"] for c in new["records"]["cell_decisions"]
        ]
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
    (tmp_path / "S1.raw").write_bytes(b"S1")
    opened = []

    class Raw:
        def extract_xic(self, mz, lower, upper, ppm):
            assert mz == candidates["S1"][0].precursor_mz
            assert upper - lower == 1.0
            rt = np.linspace(lower, upper, 51)
            return rt, 1000 * np.exp(-(((rt - 8) / 0.08) ** 2))

    @contextmanager
    def raw(path, dll, *, expected_acquisition_source, mass_recalibration):
        opened.append(path.name)
        assert expected_acquisition_source.content_sha256
        yield Raw()

    monkeypatch.setattr("xic_workbench.acquisition.open_raw", raw)
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


def _two_sample_curve_review(tmp_path, monkeypatch):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    review = write_cid_nl_explanation(
        tmp_path / "two-sample-review.sqlite",
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_cid_nl_explanation(review)
    sample_ids = {
        sample["sample_stem"]: sample["canonical_sample_identity_id"]
        for sample in summary["samples"]
    }
    detail = read_cid_nl_explanation(
        review, candidate_id=candidates["S1"][0].candidate_id
    )
    group_id = detail["records"]["final_groups"][0]["group_hypothesis_id"]
    for stem in sample_ids:
        (tmp_path / f"{stem}.raw").write_bytes(stem.encode())
    return review, group_id, sample_ids, candidates


def _install_distinct_curve_raws(monkeypatch, opened, cancelled=None):
    class Raw:
        def __init__(self, stem):
            self.stem = stem

        def extract_xic(self, mz, lower, upper, ppm):
            rt = np.linspace(lower, upper, 51)
            amplitude = 1000 if self.stem == "S1" else 2000
            result = rt, amplitude * np.exp(-(((rt - 8) / 0.08) ** 2))
            if cancelled is not None:
                cancelled["value"] = True
            return result

    @contextmanager
    def raw(path, dll, *, expected_acquisition_source, mass_recalibration):
        opened.append((path.name, expected_acquisition_source.acquisition_source_id))
        yield Raw(path.stem)

    monkeypatch.setattr("xic_workbench.acquisition.open_raw", raw)


def test_curve_export_acquires_two_distinct_sample_curves_with_progress(
    tmp_path, monkeypatch
):
    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    opened, progress = [], []
    _install_distinct_curve_raws(monkeypatch, opened)
    output = tmp_path / "two-sample-curves.json"

    result = export_explanation_curves(
        review,
        output,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        group_id=group_id,
        sample_ids=tuple(sample_ids.values()),
        on_progress=lambda completed, total, sid: progress.append(
            (completed, total, sid)
        ),
    )

    assert result["sample_count"] == 2
    assert result["source_binding_basis"] == (
        "report_sample_stem+report_acquisition_source_content"
    )
    assert set(result["acquisition_sources"]) == set(sample_ids.values())
    assert all(
        source_id.startswith("xic.acquisition-source:")
        for source_id in result["acquisition_sources"].values()
    )
    assert {name for name, _ in opened} == {"S1.raw", "S2.raw"}
    assert all(identity.startswith("xic.acquisition-source:") for _, identity in opened)
    assert progress == [
        (index, 2, sid)
        for index, sid in enumerate(sorted(sample_ids.values()), start=1)
    ]
    bundle = read_curve_bundle(output)
    traces = {trace.sample_label: trace for trace in bundle.transitions[0].traces}
    assert max(traces["S1"].raw.intensity) < max(traces["S2"].raw.intensity)
    assert all(trace.integration is None for trace in traces.values())
    assert bundle.transitions[0].quantifications == ()
    html = tmp_path / "two-sample-curves.html"
    rendered = generate_report(output, SelectionRequest(), html)
    assert rendered.evidence_capability == "retained_curves"
    embedded = html.read_text(encoding="utf-8").split(
        '<script id="inspector-data" type="application/json">', 1
    )[1]
    inspector_curves = json.loads(embedded.split("</script>", 1)[0])["items"][0][
        "curves"
    ]
    assert {curve["sample_id"] for curve in inspector_curves} == set(
        sample_ids.values()
    )
    assert all(
        curve["stage_label"] == "diagnostic_reacquisition"
        and curve["provenance"]["raw_identity"].startswith("xic.acquisition-source:")
        for curve in inspector_curves
    )


def test_curve_export_sample_scope_opens_only_selected_raw(tmp_path, monkeypatch):
    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    opened = []
    _install_distinct_curve_raws(monkeypatch, opened)

    result = export_explanation_curves(
        review,
        tmp_path / "selected-sample.json",
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        group_id=group_id,
        sample_ids=(sample_ids["S2"],),
    )

    assert result["sample_count"] == 1
    assert [name for name, _ in opened] == ["S2.raw"]


def test_curve_export_cancelled_before_or_during_acquisition_leaves_no_output(
    tmp_path, monkeypatch
):
    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    before = tmp_path / "cancelled-before.json"
    monkeypatch.setattr(
        "xic_workbench.acquisition.open_raw",
        lambda *_args, **_kwargs: pytest.fail("cancelled job must not open RAW"),
    )
    with pytest.raises(CurveAcquisitionCancelled):
        export_explanation_curves(
            review,
            before,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            group_id=group_id,
            sample_ids=tuple(sample_ids.values()),
            is_cancelled=lambda: True,
        )
    assert not before.exists()

    cancelled, opened = {"value": False}, []
    _install_distinct_curve_raws(monkeypatch, opened, cancelled)
    during = tmp_path / "cancelled-during.json"
    with pytest.raises(CurveAcquisitionCancelled):
        export_explanation_curves(
            review,
            during,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            group_id=group_id,
            sample_ids=tuple(sample_ids.values()),
            is_cancelled=lambda: cancelled["value"],
        )
    assert len(opened) == 1
    assert not during.exists()


def test_curve_export_rejects_identity_mismatch_before_raw_open(tmp_path, monkeypatch):
    review, _, sample_ids, candidates = _two_sample_curve_review(tmp_path, monkeypatch)
    (tmp_path / "S1.raw").write_bytes(b"not the report-bound source")
    monkeypatch.setattr(
        "xic_workbench.acquisition.open_raw",
        lambda *_args, **_kwargs: pytest.fail("mismatched RAW must not be opened"),
    )
    output = tmp_path / "identity-mismatch.json"

    with pytest.raises(ExplanationError, match="does not match exactly one"):
        export_explanation_curves(
            review,
            output,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            candidate_id=candidates["S1"][0].candidate_id,
            sample_ids=(sample_ids["S1"],),
        )
    assert not output.exists()


def test_curve_export_rejects_unknown_and_unobserved_explicit_samples_before_raw(
    tmp_path, monkeypatch
):
    review, _, sample_ids, candidates = _two_sample_curve_review(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "xic_workbench.acquisition.open_raw",
        lambda *_args, **_kwargs: pytest.fail("invalid sample scope must not open RAW"),
    )
    candidate_id = candidates["S1"][0].candidate_id

    with pytest.raises(ExplanationError, match="not present in the report"):
        export_explanation_curves(
            review,
            tmp_path / "unknown.json",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            candidate_id=candidate_id,
            sample_ids=("xic.sample:unknown",),
        )
    with pytest.raises(ExplanationError, match="no retained apex coordinates"):
        export_explanation_curves(
            review,
            tmp_path / "unobserved.json",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            candidate_id=candidate_id,
            sample_ids=(sample_ids["S2"],),
        )


@pytest.mark.parametrize("saved", (False, True))
def test_review_curves_use_each_samples_saved_mass_policy(tmp_path, monkeypatch, saved):
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.output.alignment.pipeline_outputs import _write_metadata_json

    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    summary = read_cid_nl_explanation(review)
    policies = {"S1": "easy_ic_probe" if saved else "none", "S2": "none"}
    if saved:
        _write_metadata_json(
            tmp_path / "alignment_run_metadata.json",
            {
                "processing_request_id": summary["processing_request_id"],
                "processing_attempt_id": summary["processing_attempt_id"],
                "mass_recalibration_by_sample": json.dumps(
                    {s: {"policy": p} for s, p in policies.items()}
                ),
            },
        )

    else:
        _write_metadata_json(tmp_path / "alignment_run_metadata.json", {})

    @contextmanager
    def raw(path, dll, *, mass_recalibration=MassRecalibration.NONE, **kwargs):
        class Raw:
            def extract_xic(self, mz, lower, upper, ppm):
                rt = np.linspace(lower, upper, 51)
                amplitude = 1000 if path.stem == "S1" else 2000
                return rt, amplitude * (
                    mass_recalibration.value == policies[path.stem]
                ) * np.exp(-(((rt - 8) / 0.08) ** 2))

        yield Raw()

    monkeypatch.setattr("xic_workbench.acquisition.open_raw", raw)
    output = tmp_path / "policy-curves.json"
    export_explanation_curves(
        review,
        output,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        group_id=group_id,
        sample_ids=tuple(sample_ids.values()),
    )
    traces = {
        t.sample_label: t for t in read_curve_bundle(output).transitions[0].traces
    }
    assert max(traces["S1"].raw.intensity) == pytest.approx(1000)
    assert max(traces["S2"].raw.intensity) == pytest.approx(2000)

    if saved:
        sidecar = tmp_path / "alignment_run_metadata.json"
        metadata = json.loads(sidecar.read_text())
        metadata["processing_attempt_id"] = "another-run"
        _write_metadata_json(sidecar, metadata)
        with pytest.raises(ExplanationError, match="metadata"):
            export_explanation_curves(
                review,
                tmp_path / "wrong-run.json",
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                group_id=group_id,
            )


def _quality_review_report(tmp_path):
    """Two samples in one feature: advisory facts traverse the actual review store."""
    from types import SimpleNamespace as NS

    from xic_extractor.diagnostics.explanation_review import review_cell
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    matrix = tmp_path / "quality-matrix.tsv"
    matrix.write_text(
        "cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\nrow-1\t500\t1\t1000\t2000\n",
        encoding="utf-8",
    )
    report = tmp_path / "quality-review.sqlite"
    cells = []
    for sid, value, ratio in (("S1", 1000.0, 20.0), ("S2", 2000.0, 2.0)):
        quality = SignalQuality(
            "available",
            noise_sigma=10.0,
            signal_to_noise=ratio,
            low_signal_to_noise=ratio < 10.0 / 1.4826,
        )
        observation = NS(
            content=NS(
                peak_observation_content_id=f"obs-{sid}",
                integration=NS(signal_quality=quality),
            ),
            capture_stage="initial_capture",
        )
        cell = NS(
            peak_observation=observation,
            content=NS(
                method_result="SUPPORT",
                reason_codes=(),
                peak_observation_content_id=f"obs-{sid}",
            ),
        )
        decision = NS(
            subject=NS(canonical_sample=NS(identity=NS(sample_identity_id=sid))),
            evidence=NS(effective_cells=(cell,), evidence_states=()),
            variant="quantified",
            value=value,
            source="raw_trapezoid",
        )
        cells.append(review_cell(decision))
    with ExplanationReportWriter(report, review_only=True) as writer:
        keys = [("C1", "S1"), ("C2", "S2")]
        for cid, sid in keys:
            writer.add_candidate(
                cid,
                sid,
                {
                    "candidate_id": cid,
                    "canonical_sample_identity_id": sid,
                    "precursor_mz": 500.0,
                    "rt_apex_min": 1.0,
                },
            )
        writer.add_record(
            "final_groups",
            "G1",
            {"group_hypothesis_id": "G1", "disposition": "completed"},
            keys,
        )
        writer.add_record(
            "rows", "row-1", {"row_key_id": "row-1", "group_hypothesis_id": "G1"}, keys
        )
        writer.add_row_cells("row-1", "G1", cells, {"S1": 0, "S2": 1})
        writer.finalize(
            {
                "processing_request_id": "quality-request",
                "processing_attempt_id": "quality-attempt",
                "matrix_path": str(matrix),
                "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                "samples": [
                    {"sample_stem": sid, "canonical_sample_identity_id": sid}
                    for sid in ("S1", "S2")
                ],
            }
        )
    return report, matrix


def test_signal_quality_survives_review_storage_without_changing_results(tmp_path):
    from xic_inspector.api import prepare_report

    report, matrix = _quality_review_report(tmp_path)
    original = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    rows = {
        row["sample_id"]: row for item in payload["items"] for row in item["samples"]
    }
    assert rows["S1"]["signal_quality"][0]["low_signal_to_noise"] is False
    assert rows["S2"]["signal_quality"][0]["low_signal_to_noise"] is True
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert rows["S1"]["status_kind"] == rows["S2"]["status_kind"] == "rest"
    assert matrix.read_bytes() == original


def test_old_review_cell_encoding_keeps_quality_not_recorded():
    import zlib

    from xic_extractor.diagnostics.explanation_review import unpack_cells

    old = [
        [0, "quantified", 123.0, "raw_trapezoid", [], [], "discovery_support", ["obs"]]
    ]
    decoded = unpack_cells(
        zlib.compress(json.dumps(old).encode()),
        row_id="r",
        group_id="g",
        sample_ids=("S1",),
    )
    assert decoded["review-cell:r:0"]["value"] == 123.0
    assert decoded["review-cell:r:0"]["signal_quality"] == []
