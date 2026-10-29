"""Compact review preserves decisions while omitting unrequested audit and curves."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

import numpy as np
import pytest

from tests.test_cid_nl_explanation import _owner_run, _summary_fixture
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
from xic_workbench import acquisition
from xic_workbench.acquisition import (
    CurveAcquisitionCancelled,
    export_explanation_curves,
)


def test_old_query_index_is_rejected_without_conversion(tmp_path, monkeypatch):
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
        summary = json.loads(
            db.execute("SELECT payload FROM metadata WHERE key='summary'").fetchone()[0]
        )
        summary["query_index_version"] = 2
        db.execute(
            "UPDATE metadata SET payload=? WHERE key='summary'", (json.dumps(summary),)
        )
    before = source.read_bytes()
    with pytest.raises(ExplanationError, match="rerun"):
        read_explanation_metadata(source)
    assert source.read_bytes() == before


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
                    "sample_key": sid,
                    "rt_apex_min": rt,
                },
            )
            observations = [{"physical_peak_key": cid + "-obs", "rt_apex_min": rt}]
            if cid == "C5":
                observations.append(
                    {"physical_peak_key": "unrelated", "rt_apex_min": 99}
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
            group = {"feature_id": gid}
            if gid == "G3":
                group["member_subjects"] = [
                    {
                        "sample_key": "S1",
                        "physical_peak_keys": ["C5-obs"],
                    }
                ]
            writer.add_record("final_groups", gid, group, keys)
            if rid is not None:
                writer.add_record("rows", rid, {"feature_id": gid}, keys)
                writer.add_row_cells(
                    rid,
                    [
                        {
                            "sample_key": sid,
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
            _summary_fixture(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [{"sample_key": sid} for sid in ("S1", "S2")],
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
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
            decision.subject.canonical_sample.identity.sample_key,
            decision.variant,
            getattr(decision, "value", None),
        )
        for child in completed.application_result.children
        for row in child.completed_projection.rows
        for decision in row.decisions
    )
    actual = sorted(
        (row["sample_key"], row["variant"], row["value"])
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
    # Missing required columns cannot be supplied by compatibility reading.
    with sqlite3.connect(report) as db:
        db.execute("ALTER TABLE row_cells DROP COLUMN sample_mask")
    with pytest.raises(ExplanationError, match="rerun"):
        CidNlSource(report, query_only=True)


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
                    row["feature_id"],
                    row["sample_key"],
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
    def raw(path, dll, *, expected_acquisition_source, mass_recalibration, **_kwargs):
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
        sample["sample_stem"]: sample["sample_key"] for sample in summary["samples"]
    }
    detail = read_cid_nl_explanation(
        review, candidate_id=candidates["S1"][0].candidate_id
    )
    group_id = detail["records"]["final_groups"][0]["feature_id"]
    for stem in sample_ids:
        (tmp_path / f"{stem}.raw").write_bytes(stem.encode())
    return review, group_id, sample_ids, candidates


def _install_distinct_curve_raws(monkeypatch, opened, cancelled=None, empty=()):
    class Raw:
        def __init__(self, stem):
            self.stem = stem

        def extract_xic(self, mz, lower, upper, ppm):
            if self.stem in empty:
                return np.array([]), np.array([])
            rt = np.linspace(lower, upper, 51)
            amplitude = 1000 if self.stem == "S1" else 2000
            result = rt, amplitude * np.exp(-(((rt - 8) / 0.08) ** 2))
            if cancelled is not None:
                cancelled["value"] = True
            return result

    @contextmanager
    def raw(path, dll, *, expected_acquisition_source, mass_recalibration, **_kwargs):
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
        source_id.startswith("sha256:")
        for source_id in result["acquisition_sources"].values()
    )
    assert {name for name, _ in opened} == {"S1.raw", "S2.raw"}
    assert all(identity.startswith("sha256:") for _, identity in opened)
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
        and curve["provenance"]["raw_identity"].startswith("sha256:")
        for curve in inspector_curves
    )


def test_curve_export_skips_a_sample_without_usable_signal(tmp_path, monkeypatch):
    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    _install_distinct_curve_raws(monkeypatch, [], empty={"S1"})
    output = tmp_path / "partial.json"

    result = export_explanation_curves(
        review,
        output,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        group_id=group_id,
        sample_ids=tuple(sample_ids.values()),
    )

    assert result["unusable_samples"] == ["S1"]
    traces = read_curve_bundle(output).transitions[0].traces
    assert [trace.sample_label for trace in traces] == ["S2"]

    _install_distinct_curve_raws(monkeypatch, [], empty={"S1", "S2"})
    with pytest.raises(ExplanationError, match="都沒有可用訊號"):
        export_explanation_curves(
            review,
            tmp_path / "none.json",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            group_id=group_id,
            sample_ids=tuple(sample_ids.values()),
        )
    assert not (tmp_path / "none.json").exists()


def test_curve_export_reuses_content_verification_while_raw_is_unchanged(
    tmp_path, monkeypatch
):
    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    _install_distinct_curve_raws(monkeypatch, [])
    hashed = []
    real = acquisition.fingerprint_file_content
    monkeypatch.setattr(
        acquisition,
        "fingerprint_file_content",
        lambda path, **kwargs: hashed.append(path.name) or real(path, **kwargs),
    )
    verified = {}
    for name in ("first.json", "second.json"):
        export_explanation_curves(
            review,
            tmp_path / name,
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            group_id=group_id,
            sample_ids=tuple(sample_ids.values()),
            verified_raws=verified,
        )
    assert sorted(hashed) == ["S1.raw", "S2.raw"]

    # A changed file is verified again, and mismatched content still fails.
    (tmp_path / "S1.raw").write_bytes(b"another acquisition")
    with pytest.raises(ExplanationError, match="分析時使用的 RAW 內容不同"):
        export_explanation_curves(
            review,
            tmp_path / "third.json",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            group_id=group_id,
            sample_ids=tuple(sample_ids.values()),
            verified_raws=verified,
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

    with pytest.raises(ExplanationError, match="分析時使用的 RAW 內容不同"):
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
    from xic_extractor.configuration.mass_recalibration import (
        MassRecalibration,
        MassRecalibrationResult,
        serialize_mass_recalibration_result,
    )
    from xic_extractor.output.alignment.pipeline_outputs import _write_metadata_json

    review, group_id, sample_ids, _ = _two_sample_curve_review(tmp_path, monkeypatch)
    summary = read_cid_nl_explanation(review)
    policies = {"S1": "easy_ic_probe" if saved else "none", "S2": "none"}
    if saved:
        _write_metadata_json(
            tmp_path / "alignment_run_metadata.json",
            {
                "processing_attempt_id": summary["processing_attempt_id"],
                # The producer publishes each sample's complete probe result.
                "mass_recalibration_by_sample": json.dumps(
                    {
                        s: json.loads(
                            serialize_mass_recalibration_result(
                                MassRecalibrationResult(
                                    "unavailable", policy=MassRecalibration(p)
                                )
                            )
                        )
                        for s, p in policies.items()
                    }
                ),
            },
        )

    else:
        _write_metadata_json(tmp_path / "alignment_run_metadata.json", {})

    replayed = {}

    @contextmanager
    def raw(path, dll, *, mass_recalibration=MassRecalibration.NONE, **kwargs):
        prepared = kwargs.get("prepared_mass_recalibration")
        replayed[path.stem] = prepared and prepared.result.outcome

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
    # A saved probe result is replayed; the RAW is not probed again.
    assert replayed == {"S1": "unavailable" if saved else None, "S2": None}

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


def _quality_review_report(tmp_path, *, membership_risks=None):
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
                physical_peak_key=f"obs-{sid}",
                integration=NS(signal_quality=quality),
            ),
            capture_stage="initial_capture",
        )
        cell = NS(
            peak_observation=observation,
            content=NS(
                method_result="SUPPORT",
                reason_codes=(),
                physical_peak_key=f"obs-{sid}",
            ),
        )
        decision = NS(
            subject=NS(canonical_sample=NS(identity=NS(sample_key=sid))),
            evidence=NS(effective_cells=(cell,), evidence_states=()),
            variant="quantified",
            value=value,
            source="raw_trapezoid",
        )
        cells.append(review_cell(decision))
        if membership_risks is not None:
            cells[-1]["membership_risks"] = membership_risks.get(sid, [])
    with ExplanationReportWriter(report, review_only=True) as writer:
        keys = [("C1", "S1"), ("C2", "S2")]
        for cid, sid in keys:
            writer.add_candidate(
                cid,
                sid,
                {
                    "candidate_id": cid,
                    "sample_key": sid,
                    "precursor_mz": 500.0,
                    "rt_apex_min": 1.0,
                },
            )
        writer.add_record(
            "final_groups",
            "G1",
            {"feature_id": "G1", "disposition": "completed"},
            keys,
        )
        writer.add_record("rows", "G1", {"feature_id": "G1"}, keys)
        writer.add_row_cells("G1", cells, {"S1": 0, "S2": 1})
        writer.finalize(
            _summary_fixture(
                {
                    "processing_attempt_id": "quality-attempt",
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [
                        {"sample_stem": sid, "sample_key": sid} for sid in ("S1", "S2")
                    ],
                },
                review_only=writer.review_only,
            )
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


@pytest.mark.parametrize("field_count", [8, 9, 11])
def test_review_cell_encoding_rejects_missing_or_extra_fields(field_count):
    import zlib

    from xic_extractor.diagnostics.explanation_review import unpack_cells

    cell = [
        0,
        "quantified",
        123.0,
        "raw_trapezoid",
        [],
        [],
        "discovery_support",
        ["obs"],
        [],
        [],
        "unexpected",
    ][:field_count]
    with pytest.raises(ValueError, match="rerun Alignment"):
        unpack_cells(
            zlib.compress(json.dumps([cell]).encode()),
            row_id="r",
            group_id="g",
            sample_ids=("S1",),
        )


def _competition_review_report(
    tmp_path, *, phase="initial", flag="accepted_competition"
):
    risk = {
        "status": "evaluated_alternative",
        "phase": phase,
        "selected_owners": [["S1", 500.0, 1.0], ["S2", 500.0, 1.1]],
        "alternative_partition": [
            [["S1", 500.0, 1.0]],
            [["S2", 500.0, 1.1], ["S3", 500.0, 1.2]],
        ],
        "equivalent_alternatives": [],
        "selected_cost": 1.2,
        "alternative_cost": 1.23,
        "cost_gap": 0.03,
        "rt_basis": "raw",
        "evidence_basis": "bounded_assignment",
        "soft_contradictions": [],
        "risk_flags": [flag],
        "selected_peer_fit": 0.25,
        "alternative_peer_fit": 0.4,
        "peer_fit_gap": 0.15,
        "rt_estimate": {
            "rt_min": 8.0,
            "method": "median_shift",
            "anchor_count": 2,
            "anchor_range_min": [7.0, 9.0],
            "residual_spread_min": 0.02,
            "nearest_anchor_distance_min": 1.0,
            "outside_anchor_range_min": 0.0,
            "fit_status": "insufficient_anchors",
        },
    }
    return _quality_review_report(tmp_path, membership_risks={"S2": [risk]})


def test_accepted_competition_roundtrip_keeps_sample_attribution_and_values(tmp_path):
    from xic_inspector.api import prepare_report

    report, matrix = _competition_review_report(tmp_path)
    original = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    rows = {
        row["sample_id"]: row for item in payload["items"] for row in item["samples"]
    }
    assert rows["S1"]["membership_risks"] == []
    risk = rows["S2"]["membership_risks"][0]
    assert risk["cost_gap"] == 0.03
    assert risk["alternative_partition"] == [
        [["S1", 500.0, 1.0]],
        [["S2", 500.0, 1.1], ["S3", 500.0, 1.2]],
    ]
    assert risk["rt_estimate"]["method"] == "median_shift"
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert matrix.read_bytes() == original


@pytest.mark.parametrize("full_audit", (False, True))
def test_produced_membership_risk_reaches_final_samples_and_review_tsv(
    tmp_path, monkeypatch, full_audit
):
    import csv
    from dataclasses import asdict, replace

    from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
    from xic_extractor.alignment.feature_membership_inference import (
        infer_feature_memberships,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame
    from xic_extractor.alignment.sample_rt_calibration import (
        RtCalibrationPolicy,
        build_batch_rt_calibration,
    )
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        project_completed_untarget_batch_writer_inputs,
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    completed, _, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    # Re-evaluate the real owners with a frozen, independently measured sparse
    # reference. The unchanged partition lets this test isolate query-fact
    # projection rather than fabricate risk payloads or new final memberships.
    calibration = build_batch_rt_calibration(
        {"early": {"S1": 7, "S2": 7.1, "S3": 7}, "late": {"S1": 9, "S2": 9.1, "S3": 9}},
        sample_stems=("S1", "S2", "S3"),
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    groups = []
    for group in completed.initial_groups:
        inferred = infer_feature_memberships(
            group.owners,
            rules=CID_NL_GROUPING_RULES,
            config=config,
            rt_frame=SampleRtFrame().with_sample_calibration(calibration),
        )
        assert len(inferred.groups) == 1
        assert set(inferred.groups[0].owner_ids) == {o.owner_id for o in group.owners}
        groups.append(replace(group, inference_decisions=inferred.sparse_decisions))
    completed = replace(completed, initial_groups=tuple(groups))
    risks = {
        d.owner_id: d.accepted_risk
        for group in completed.initial_groups
        for d in group.inference_decisions
        if d.accepted_risk is not None
    }
    assert len(risks) == 2
    assert all(risk.selected_peer_fit is not None for risk in risks.values())
    for group in completed.initial_groups:
        for owner in group.owners:
            estimate = risks[owner.owner_id].rt_estimate
            assert estimate.method == "median_shift"
            assert estimate.anchor_count == 2
            assert estimate.rt_min == pytest.approx(
                owner.owner_apex_rt - (0.1 if owner.sample_stem == "S2" else 0)
            )
    expected = {
        member.sample_key: json.loads(json.dumps(asdict(risks[member.subject_id])))
        for group in completed.initial_groups
        for member in group.member_subjects
    }
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result, membership_risks_by_owner=risks
    )
    original = matrix.read_bytes()
    report = write_cid_nl_explanation(
        tmp_path / "produced.sqlite",
        full_audit=full_audit,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    cells = read_explanation_metadata(report).records["cell_decisions"].values()
    assert {
        cell["sample_key"]: cell["membership_risks"][0] for cell in cells
    } == expected
    review = tmp_path / "review.tsv"
    write_alignment_review_tsv(
        review,
        writer.matrix,
        alignment_config=config,
        production_decisions=writer.production_decisions,
    )
    with review.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    actual = {}
    for row in rows:
        assert row["membership_risk_status"] == "recorded"
        for risk in json.loads(row["membership_risks_json"]):
            actual[risk.pop("sample_key")] = risk
    assert actual == expected
    assert matrix.read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    (
        ("cost_gap", "0.03"),
        ("selected_cost", True),
        ("selected_peer_fit", "0.25"),
        ("alternative_peer_fit", True),
        ("peer_fit_gap", "0.15"),
        ("alternative_partition", ["not-a-group"]),
        ("soft_contradictions", "shape_disagreement"),
        ("rt_estimate", "median_shift"),
        ("rt_estimate", {"method": "median_shift", "rt_min": True}),
    ),
)
def test_persisted_membership_fields_reject_wrong_types(tmp_path, field, value):
    import zlib

    report, _ = _competition_review_report(tmp_path)
    with sqlite3.connect(report) as db:
        payload = db.execute("SELECT payload FROM row_cells").fetchone()[0]
        cells = json.loads(zlib.decompress(payload))
        cells[1][9][0][field] = value
        db.execute(
            "UPDATE row_cells SET payload=?",
            (zlib.compress(json.dumps(cells).encode()),),
        )
    with pytest.raises((ExplanationError, ValueError), match="membership risk"):
        read_explanation_metadata(report)


def test_production_publication_automatically_includes_recorded_risk(
    tmp_path, monkeypatch
):
    from xic_extractor.alignment.owner_build_route_receipt import (
        _mint_retained_untargeted_cid_nl_route_receipt,
    )
    from xic_extractor.applications.untargeted.publication import (
        UntargetedPublicationPolicy,
        publish_untargeted_outputs,
    )
    from xic_extractor.execution.timing import TimingRecorder

    completed, _, candidates, _, config = _owner_run(tmp_path, monkeypatch, "positive")
    completed = completed.bind_publication_inputs(
        candidates_by_sample=candidates, alignment_config=config
    )
    policy = UntargetedPublicationPolicy()
    directory = tmp_path / "published"
    outputs = publish_untargeted_outputs(
        completed,
        output_dir=directory,
        outputs=policy.output_paths(directory),
        policy=policy,
        metadata={},
        timing_recorder=TimingRecorder("alignment", enabled=False),
        should_cancel=None,
        owner_build_route_receipt=_mint_retained_untargeted_cid_nl_route_receipt(
            configured_backend="raw"
        ),
    )
    assert outputs.evidence is not None
    assert outputs.evidence.matrix_path == outputs.matrix_tsv
    metadata = read_explanation_metadata(outputs.evidence.report_path)
    assert metadata.summary["retention"] == "review"
    cells = list(metadata.records["cell_decisions"].values())
    assert len(cells) == 2
    assert all(cell["membership_risks"] for cell in cells)
    assert all(cell["value"] > 0 for cell in cells)


def test_compact_cells_preserve_group_sample_location_across_payload_order():
    from xic_extractor.diagnostics.explanation_review import pack_cells, unpack_cells

    cells = [
        {"sample_key": "S1", "variant": "quantified", "value": 11.0},
        {"sample_key": "S2", "variant": "quantified", "value": 22.0},
    ]
    sample_ordinals = {"S1": 0, "S2": 1}
    outputs = [
        unpack_cells(
            pack_cells(ordered, sample_ordinals),
            row_id="F000001",
            group_id="F000001",
            sample_ids=("S1", "S2"),
        )
        for ordered in (cells, cells[::-1])
    ]
    assert outputs[0] == outputs[1]
    assert {key: cell["value"] for key, cell in outputs[0].items()} == {
        ("F000001", "S1"): 11.0,
        ("F000001", "S2"): 22.0,
    }


def test_compact_cells_reject_duplicate_group_sample_location():
    from xic_extractor.diagnostics.explanation_review import pack_cells, unpack_cells

    cells = [
        {"sample_key": "S1", "variant": "quantified", "value": 11.0},
        {"sample_key": "S1", "variant": "quantified", "value": 22.0},
    ]
    with pytest.raises(ValueError, match="duplicate review cell.*rerun Alignment"):
        unpack_cells(
            pack_cells(cells, {"S1": 0}),
            row_id="F000001",
            group_id="F000001",
            sample_ids=("S1",),
        )
