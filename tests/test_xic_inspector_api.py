"""Tests for xic_inspector.api and cli: contracts, error codes, and cancellation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

from tests.alignment_explanation_fixtures import explanation_summary
from tests.inspector_fixtures import (
    NEUTRAL_LOSS_BY_TAG,
    add_witness,
    create_inspector_report,
    discovery,
    inspector_curve_payload,
    read_report_payload,
    witness_member,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)
from xic_inspector.cli import main as cli_main


def _ref_key(ref):
    """A record ref as a dict key; a cell's ref is its (row, sample) pair."""
    return tuple(ref) if isinstance(ref, list) else ref


def _readable(item):
    return {
        _ref_key(record["ref"]): record
        for record in item["details"]["readable_records"]
    }


def test_interactive_and_offline_review_share_sample_scope_and_values(tmp_path):
    from xic_inspector.api import prepare_report

    report, _, _ = create_inspector_report(tmp_path)
    selection = SelectionRequest(
        strategy="query", group_ids=("G1",), sample_ids=("S1",)
    )
    prepared = prepare_report(report, selection)
    assert prepared.payload["samples"] == [{"id": "S1", "label": "s1"}]
    assert any(
        row.get("quantitative_value") == 1000.0
        for item in prepared.payload["items"]
        for row in item["samples"]
    )
    output = tmp_path / "offline.html"
    generate_report(report, selection, output)
    assert read_report_payload(output) == json.loads(json.dumps(prepared.payload))


def test_normalized_links_preserve_candidate_query_and_inspector_scope(tmp_path):
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )

    report, _, _ = create_inspector_report(tmp_path)
    detail = read_alignment_explanation(report, candidate_id="C1")
    output = tmp_path / "candidate-query.html"
    result = generate_report(
        report,
        SelectionRequest(strategy="query", candidate_ids=("C1",)),
        output,
    )

    assert result.selected_count == 1
    item = read_report_payload(output)["items"][0]
    assert json.loads(item["id"])[-1] == "G1"
    assert item["details"]["readable_records"]
    assert [group["feature_id"] for group in detail["groups"]] == ["G1"]
    assert "destination" in detail["groups"][0]
    assert detail["groups"][0]["cells"]


def test_generate_report_sample_strategy_success() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, matrix_hash = create_inspector_report(tmp)
        out_html = tmp / "report.html"

        req = SelectionRequest(strategy="sample", limit=10, seed=0)
        progresses = []

        res = generate_report(
            report=db_path,
            selection=req,
            output=out_html,
            on_progress=lambda p: progresses.append(p.stage),
        )

        assert out_html.is_file()
        assert res.output_path == out_html.resolve()
        assert res.evidence_capability == "retained_curves"
        assert res.run_total >= 1
        assert res.selected_count >= 1
        assert res.sample_ids == ("S1", "S2")
        assert "metadata" in progresses
        assert "selection" in progresses
        assert "curves" in progresses
        assert "render" in progresses
        assert "write" in progresses

        content = out_html.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", content, re.S).group(1)
        metadata_start = re.search(r"\bmetadata\s*:\s*(\{)", content).start(1)
        metadata, _ = json.JSONDecoder().raw_decode(content[metadata_start:])
        assert title == metadata["title"] == "Inspector"
        assert "retained_curves" in content


def test_group_query_keeps_unlinked_stored_cells_scoped_and_readable(tmp_path):
    stored_cell = {"sample_key": "S2", "source": "", "value": None}

    def other_group_and_hold(writer):
        writer.add_final_group(
            "G2", feature_id="G2", disposition="completed", members=[]
        )
        writer.add_cells("G2", [{**stored_cell, "variant": "not_searched"}])
        writer.add_assessment(
            {
                "sample_key": "S1",
                "method_subject_id": "M1",
                "method_result": "HOLD",
                "reason_codes": [
                    "insufficient_method_evidence",
                    "no_unique_qualifying_relation",
                ],
            },
            candidate_physical_peak_keys=(),
        )

    report, _, _ = create_inspector_report(
        tmp_path,
        candidate={"method_subject_id": "M1", "neutral_loss_tag": "dR"},
        cells=[
            {"sample_key": "S1", "variant": "numeric", "value": 1000.0},
            {**stored_cell, "variant": "not_searched"},
        ],
        extend=other_group_and_hold,
    )

    output = tmp_path / "all-samples.html"
    generate_report(
        report,
        SelectionRequest(strategy="query", group_ids=("G1",)),
        output,
    )
    item = read_report_payload(output)["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}

    assert set(samples) == {"S1", "S2"}
    assert samples["S2"]["sample_stem"] == "s2"
    assert samples["S2"]["quantitative_value"] is None
    assert samples["S2"]["decision_refs"] == [["G1", "S2"]]
    assert samples["S2"]["status_kind"] == "null"
    assert samples["S2"]["decision_label"] == "本次未在這個群組位置搜尋。"
    assert samples["S2"]["decision_reason"] == "本次未在這個群組位置搜尋。"

    readable = _readable(item)
    assert readable["G1", "S2"]["sample_ids"] == ["S2"]
    assert readable["G1", "S2"]["candidate_ids"] == []
    assert ("G2", "S2") not in readable
    assert {witness["sample_key"] for witness in item["witnesses"]} == {"S1"}
    assert {curve["sample_id"] for curve in item["curves"]} == {"S1"}

    assert (
        item["reason_label"]
        == "2 個樣本：0 detect · 0 backfill · 1 空值 · 1 有值但來源未提供"
    )
    assert item["badge_label"] == "已記錄"
    trigger = next(
        trigger
        for trigger in item["details"]["attention_triggers"]
        if trigger["reason"] == "HOLD"
    )
    assert trigger["reason"] == "HOLD"
    assert trigger["reason_codes"] == [
        "insufficient_method_evidence",
        "no_unique_qualifying_relation",
    ]

    scoped_output = tmp_path / "s1-only.html"
    generate_report(
        report,
        SelectionRequest(strategy="query", group_ids=("G1",), sample_ids=("S1",)),
        scoped_output,
    )
    scoped_item = read_report_payload(scoped_output)["items"][0]
    assert [sample["sample_id"] for sample in scoped_item["samples"]] == ["S1"]
    assert ("G1", "S2") not in _readable(scoped_item)


@pytest.mark.parametrize("review_only", (False, True))
def test_unrelated_hold_does_not_become_a_cell_risk_after_record_renumbering(
    tmp_path, review_only
):
    from xic_inspector.api import prepare_report

    def assessments(writer):
        for code in ("no_unique_qualifying_relation", "insufficient_method_evidence"):
            writer.add_assessment(
                {
                    "sample_key": "S1",
                    "method_subject_id": "subject-C1",
                    "method_result": "HOLD",
                    "reason_codes": [code],
                },
                candidate_physical_peak_keys=(),
            )

    report, _, _ = create_inspector_report(
        tmp_path, extend=assessments, review_only=review_only
    )
    selection = SelectionRequest(strategy="query", group_ids=("G1",))
    before = prepare_report(report, selection).payload["items"][0]["reason_label"]
    with sqlite3.connect(report) as db:
        db.execute("UPDATE method_assessments SET id=100000-id")
    after = prepare_report(report, selection).payload["items"][0]["reason_label"]
    assert before == after
    assert "HOLD" not in before
    assert "insufficient_method_evidence" not in before


def test_review_candidate_scope_paginates_in_recorded_order(tmp_path):
    from xic_inspector.api import prepare_report

    def ungrouped(writer):
        for cid in ("last", "first", "middle"):
            writer.add_candidate(cid, "S2", precursor_mz=510.0, **discovery(cid))

    report, _, _ = create_inspector_report(tmp_path, extend=ungrouped, review_only=True)
    prepared = prepare_report(
        report,
        SelectionRequest(
            strategy="query",
            review_scope="candidates",
            sample_ids=("S1", "S2"),
            offset=1,
            limit=2,
        ),
    )
    assert [
        item["witnesses"][0]["candidate_id"] for item in prepared.payload["items"]
    ] == ["first", "middle"]


def test_error_code_2_invalid_request() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = create_inspector_report(tmp)
        out_html = tmp / "report.html"

        # Invalid limit
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(limit=0), out_html)
        assert exc_info.value.code == 2

        # Invalid offset
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(offset=-1), out_html)
        assert exc_info.value.code == 2

        # Non-query parameters passed to sample
        with pytest.raises(InspectorError) as exc_info:
            generate_report(
                db_path,
                SelectionRequest(strategy="sample", candidate_ids=("C1",)),
                out_html,
            )
        assert exc_info.value.code == 2

        # Query strategy with zero filters
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(strategy="query"), out_html)
        assert exc_info.value.code == 2

        # Empty/whitespace identifier
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(sample_ids=("  ",)), out_html)
        assert exc_info.value.code == 2


def test_error_code_3_source_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        missing_db = tmp / "missing.sqlite"
        out_html = tmp / "report.html"
        with pytest.raises(InspectorError) as exc_info:
            generate_report(missing_db, SelectionRequest(), out_html)
        assert exc_info.value.code == 3


def test_error_code_4_source_invalid_mismatched_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, _ = create_inspector_report(tmp)
        out_html = tmp / "report.html"

        # Tamper with matrix file
        matrix_path.write_text("tampered content", encoding="utf-8")

        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), out_html)
        assert exc_info.value.code == 4


def test_error_code_6_output_aliasing_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, _ = create_inspector_report(tmp)

        # Output aliases source DB
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), db_path)
        assert exc_info.value.code == 6

        # Output aliases matrix
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), matrix_path)
        assert exc_info.value.code == 6


def test_cancellation_preserves_old_html_and_code_130() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = create_inspector_report(tmp)
        out_html = tmp / "existing.html"
        out_html.write_text("Old unmodified HTML content", encoding="utf-8")

        with pytest.raises(InspectorCancelled) as exc_info:
            generate_report(
                db_path,
                SelectionRequest(),
                out_html,
                is_cancelled=lambda: True,
            )
        assert exc_info.value.code == 130
        # Check old HTML is untouched
        assert out_html.read_text(encoding="utf-8") == "Old unmodified HTML content"
        # Check no temporary files remained
        assert len(list(tmp.glob("*.tmp.*"))) == 0


def test_normalized_sample_scope_semantics() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = create_inspector_report(
            tmp,
            samples=[
                {"sample_stem": "b_sample", "sample_key": "S_B"},
                {"sample_stem": "a_sample", "sample_key": "S_A"},
            ],
        )
        out_html = tmp / "report.html"

        # 1. No sample filter -> All universe samples
        res1 = generate_report(db_path, SelectionRequest(strategy="sample"), out_html)
        assert res1.sample_ids == ("S_A", "S_B")

        # 2. Specific valid sample -> Subsets and sorted
        res2 = generate_report(
            db_path,
            SelectionRequest(strategy="sample", sample_ids=("S_B",)),
            out_html,
        )
        assert res2.sample_ids == ("S_B",)

        # 3. Unknown-only sample scope -> Empty tuple
        res3 = generate_report(
            db_path,
            SelectionRequest(strategy="sample", sample_ids=("UNKNOWN_S",)),
            out_html,
        )
        assert res3.sample_ids == ()
        assert res3.eligible_count == 0
        assert res3.selected_count == 0


def test_cli_execution() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = create_inspector_report(tmp)
        out_html = tmp / "cli_report.html"

        # CLI success
        code = cli_main(
            [
                "--report",
                str(db_path),
                "--output",
                str(out_html),
                "--strategy",
                "sample",
                "--limit",
                "5",
                "--no-open",
            ]
        )
        assert code == 0
        assert out_html.is_file()

        # CLI invalid request -> exit code 2
        bad_code = cli_main(
            [
                "--report",
                str(db_path),
                "--output",
                str(out_html),
                "--limit",
                "999",
            ]
        )
        assert bad_code == 2


def test_cli_opens_report_from_run_folder_with_default_output(
    tmp_path, monkeypatch
) -> None:
    alignment = tmp_path / "alignment"
    alignment.mkdir()
    create_inspector_report(alignment)
    opened = []
    monkeypatch.setattr("xic_inspector.cli.webbrowser.open", opened.append)

    assert cli_main([str(tmp_path)]) == 0

    report = alignment / "xic_inspector.html"
    assert report.is_file()
    assert opened == [report.resolve().as_uri()]

    assert cli_main([str(tmp_path), "--no-open"]) == 0
    assert len(opened) == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli_main([str(empty)]) == 3
    assert cli_main([]) == 2


@pytest.mark.parametrize("row_count", [2, 30, 50])
def test_row_curves_follow_exact_witnesses_and_shared_payload_is_read_once(
    tmp_path, monkeypatch, row_count, record_property
):
    from xic_inspector.api import read_evidence_summary
    from xic_inspector.sources import cid_nl

    def shared_witness_rows(writer):
        for index in range(2, row_count + 1):
            rid = f"row-{index:02d}"
            writer.add_final_group(
                rid,
                feature_id=rid,
                disposition="completed",
                # Every row's member holds C1's terminal peak.
                members=[witness_member("S1", f"{rid}-member", "obs-01")],
            )

    report, _, _ = create_inspector_report(tmp_path, extend=shared_witness_rows)
    reads = []
    real_reader = cid_nl.read_alignment_curves

    def read(*args, **kwargs):
        result = real_reader(*args, **kwargs)
        reads.append((kwargs, len(result)))
        return result

    monkeypatch.setattr(cid_nl, "read_alignment_curves", read)
    assert read_evidence_summary(report)["samples"][0]["sample_key"] == "S1"
    assert reads == []
    output = tmp_path / "shared.html"
    import time
    import tracemalloc

    tracemalloc.start()
    started = time.perf_counter()
    result = generate_report(report, SelectionRequest(limit=row_count), output)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    record_property("generation_seconds", elapsed)
    record_property("peak_python_bytes", peak)
    record_property("html_bytes", output.stat().st_size)
    record_property("selected_items", result.selected_count)
    assert result.selected_count == row_count
    items = read_report_payload(output)["items"]
    assert {json.loads(item["id"])[-1] for item in items} == {"G1"} | {
        f"row-{index:02d}" for index in range(2, row_count + 1)
    }
    assert all(
        item["curves"][0]["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]
        for item in items
    )
    assert reads == [({"sample_ids": ("S1",), "record_ids": ("obs-01",)}, 1)]
    # Scientific values come from each row's cell, never from the curve apex.
    assert (
        "quantitative_value"
        not in next(i for i in items if json.loads(i["id"])[-1] == "row-02")["samples"][
            0
        ]
    )


def test_real_source_query_uses_one_scoped_candidate_observation(tmp_path):
    def second_witness(writer):
        writer.add_peak("S2", "obs-02", (None, 8.0, None))
        writer.add_candidate("C2", "S2", precursor_mz=600.0, **discovery("C2"))
        writer.add_terminal(
            "C2",
            "S2",
            variant="observed",
            hypothesis_id="h2",
            physical_peak_key="obs-02",
        )

    # A final group without a result row, witnessed in two samples.
    report, _, _ = create_inspector_report(
        tmp_path,
        prepare=second_witness,
        group={
            "feature_id": None,
            "members": [
                witness_member("S1", "hypothesis-01", "obs-01"),
                witness_member("S2", "h2", "obs-02"),
            ],
        },
        cells=[],
    )
    result = generate_report(
        report,
        SelectionRequest(
            strategy="query",
            candidate_ids=("C1",),
            mz_min=500.0,
            mz_max=500.0,
            rt_min=8.0,
            rt_max=8.0,
        ),
        tmp_path / "empty.html",
    )
    assert result.selected_count == 0
    output = tmp_path / "match.html"
    result = generate_report(
        report,
        SelectionRequest(
            strategy="query",
            sample_ids=("S1",),
            candidate_ids=("C1",),
            mz_min=500.0,
            mz_max=500.0,
            rt_min=1.0,
            rt_max=1.0,
        ),
        output,
    )
    assert result.selected_count == 1
    item = read_report_payload(output)["items"][0]
    # A group without a row is named by its record number.
    assert json.loads(item["id"])[-1] == "1"
    assert [w["candidate_id"] for w in item["matched_refs"]] == ["C1"]
    assert [w["sample_key"] for w in item["witnesses"]] == ["S1"]


def test_source_strata_use_only_selected_samples_actual_cells(tmp_path):
    report, _, _ = create_inspector_report(
        tmp_path,
        cells=[
            {"sample_key": "S1", "variant": "numeric", "value": 1000.0},
            {"sample_key": "S2", "variant": "unavailable", "value": None},
        ],
    )
    full = tmp_path / "full.html"
    generate_report(report, SelectionRequest(), full)
    assert read_report_payload(full)["items"][0]["stratum"] == "null"
    full_samples = {
        s["sample_id"]: s for s in read_report_payload(full)["items"][0]["samples"]
    }
    assert full_samples["S1"]["status_kind"] == "rest"
    assert full_samples["S2"]["status_kind"] == "null"
    scoped = tmp_path / "scope.html"
    generate_report(report, SelectionRequest(sample_ids=("S1",)), scoped)
    item = read_report_payload(scoped)["items"][0]
    assert item["stratum"] == "rest"
    assert item["samples"][0]["quantitative_value"] == 1000.0
    assert [sample["sample_id"] for sample in item["samples"]] == ["S1"]
    assert all(
        ref[1] == "S1" for sample in item["samples"] for ref in sample["decision_refs"]
    )


@pytest.mark.parametrize("failure", ["missing", "attempt"])
def test_invalid_selected_payload_preserves_existing_report(tmp_path, failure):
    import zlib

    report, _, _ = create_inspector_report(tmp_path)
    output = tmp_path / "preserved.html"
    generate_report(report, SelectionRequest(), output)
    before = output.read_bytes()
    with sqlite3.connect(report) as connection:
        if failure == "missing":
            connection.execute("DELETE FROM curves")
        else:
            pid, blob = connection.execute(
                "SELECT payload_id,payload FROM curves"
            ).fetchone()
            payload = json.loads(zlib.decompress(blob))
            payload["provenance"]["processing_attempt_id"] = "another-attempt"
            connection.execute(
                "UPDATE curves SET payload=? WHERE payload_id=?",
                (zlib.compress(json.dumps(payload).encode()), pid),
            )
    with pytest.raises(InspectorError) as caught:
        generate_report(report, SelectionRequest(), output)
    assert caught.value.code == 4
    assert output.read_bytes() == before


def test_legitimate_candidate_without_observation_remains_available_as_metadata(
    tmp_path,
):
    report, _, _ = create_inspector_report(
        tmp_path,
        extend=lambda writer: writer.add_candidate(
            "not-acquired", "S2", precursor_mz=None, **discovery("not-acquired")
        ),
    )
    output = tmp_path / "unavailable.html"
    result = generate_report(
        report,
        SelectionRequest(strategy="query", candidate_ids=("not-acquired",)),
        output,
    )
    assert result.selected_count == 1
    item = read_report_payload(output)["items"][0]
    assert item["curves"] == []
    assert item["samples"][0]["evidence_status"] == "unavailable"
    assert item["samples"][0]["decision_refs"] == []


def test_candidate_presentation_ids_preserve_sample_identity(tmp_path):
    def shared_candidate_id(writer):
        for sid in ("S1", "S2"):
            writer.add_candidate(
                "shared-id", sid, precursor_mz=None, **discovery("shared-id")
            )

    report, _, _ = create_inspector_report(tmp_path, extend=shared_candidate_id)
    output = tmp_path / "identities.html"
    generate_report(
        report, SelectionRequest(strategy="query", candidate_ids=("shared-id",)), output
    )
    items = read_report_payload(output)["items"]
    assert len({i["id"] for i in items}) == 2
    assert {tuple(json.loads(i["id"])[-2:]) for i in items} == {
        ("S1", "shared-id"),
        ("S2", "shared-id"),
    }
    assert all("quantitative_value" not in i["samples"][0] for i in items)
    assert all(i["samples"][0]["status_kind"] == "unavailable" for i in items)


def test_selected_capture_stages_preserve_each_payload_and_reject_partial_loss(
    tmp_path,
):
    payload_ids = []

    def more_stages(writer):
        for kind, observation_id, record_id in (
            ("initial_capture", "obs-earlier", "obs-earlier"),
            ("backfill_observation", "obs-01", "obs-01"),
            ("backfill_observation", None, "C1"),
        ):
            payload_ids.append(
                writer.add_curve_payload(
                    "S1",
                    kind,
                    record_id,
                    inspector_curve_payload("S1", "C1", observation_id, kind=kind),
                )
            )

    report, _, _ = create_inspector_report(tmp_path, extend=more_stages)
    output = tmp_path / "stages.html"
    generate_report(report, SelectionRequest(), output)
    curves = read_report_payload(output)["items"][0]["curves"]
    assert len(curves) == 4
    assert {c["evidence_ref"] for c in curves} >= set(payload_ids)
    before = output.read_bytes()
    with sqlite3.connect(report) as connection:
        # The terminal obs still has its initial payload; an unreadable distinct
        # stage of that same observation must still fail completion.
        connection.execute(
            "UPDATE curves SET payload=x'00' WHERE payload_id=?", (payload_ids[1],)
        )
    with pytest.raises(InspectorError) as caught:
        generate_report(report, SelectionRequest(), output)
    assert caught.value.code == 4
    assert output.read_bytes() == before


@pytest.mark.parametrize(
    ("capture_kind", "invocation", "expected_kind", "expected_label"),
    (
        (
            "backfill_observation",
            "untargeted-cid-nl-backfill",
            "backfill_support",
            "backfill",
        ),
        (
            "initial_capture",
            "untargeted-cid-nl-real-data:local_minimum",
            "discovery_support",
            "detect",
        ),
    ),
)
def test_sample_support_origin_uses_exact_method_capture_and_observation(
    tmp_path, capture_kind, invocation, expected_kind, expected_label
):
    refs = {}

    def method_capture(writer):
        refs["capture"] = writer.add_curve_payload(
            "S1",
            capture_kind,
            "obs-01",
            inspector_curve_payload("S1", "C1", "obs-01", kind=capture_kind),
        )
        writer.add_assessment(
            {
                "sample_key": "S1",
                "method_subject_id": "M1",
                "detector_invocation_id": invocation,
                "method_result": "SUPPORT",
            },
            candidate_physical_peak_keys=("obs-01",),
        )
        # Records without a natural key share one numbering.
        refs["method"] = refs["capture"] + 1

    # Every stored cell records its own origin; without a cell the origin is
    # read from the exact method, capture and observation.
    report, _, _ = create_inspector_report(
        tmp_path,
        candidate={"method_subject_id": "M1", "neutral_loss_tag": "dR"},
        cells=[],
        curve=False,
        extend=method_capture,
    )

    output = tmp_path / "recovery-origin.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]

    assert item["review_summary"]["origin_kinds"] == [expected_kind]
    assert item["samples"][0]["support_origin"] == {
        "kind": expected_kind,
        "label": expected_label,
        "method_refs": [str(refs["method"])],
        "capture_refs": [str(refs["capture"])],
        "observation_refs": ["obs-01"],
    }


def test_recovery_origin_survives_zero_terminal_final_cell_binding(tmp_path):
    def backfill_recovery(writer):
        writer.add_curve_payload(
            "S1",
            "backfill_observation",
            "obs-01",
            inspector_curve_payload("S1", "C1", "obs-01", kind="backfill_observation"),
        )
        writer.add_assessment(
            {
                "sample_key": "S1",
                "method_subject_id": "M1",
                "detector_invocation_id": "untargeted-cid-nl-backfill",
                "method_result": "SUPPORT",
            },
            candidate_physical_peak_keys=("obs-01",),
        )

    # C1 has no terminal observation; the final group member and its cell hold
    # the recovered peak.
    report, _, _ = create_inspector_report(
        tmp_path,
        candidate={"method_subject_id": "M1", "neutral_loss_tag": "dR"},
        observed=False,
        group={
            "members": [
                {
                    "sample_key": "S1",
                    "subject_kind": "peak_hypothesis",
                    "subject_id": "hypothesis-01",
                    "physical_peak_keys": ["obs-01"],
                }
            ]
        },
        base_cell={
            "support_origin": "backfill_support",
            "observation_ids": ["obs-01"],
        },
        curve=False,
        extend=backfill_recovery,
    )

    output = tmp_path / "zero-terminal-recovery.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]

    assert item["witnesses"][0]["observation_id"] is None
    assert item["review_summary"]["observed_sample_count"] == 1
    assert item["review_summary"]["origin_kinds"] == ["backfill_support"]
    origin = item["samples"][0]["support_origin"]
    assert (origin["kind"], origin["label"], origin["observation_refs"]) == (
        "backfill_support",
        "backfill",
        ["obs-01"],
    )


def test_curve_replays_final_rt_estimate_including_boundary_shift(tmp_path):
    from xic_extractor.alignment.sample_rt_calibration import (
        BatchRtCalibration,
        RtCalibrationPolicy,
        RtEstimateSupport,
        SampleRtCalibration,
    )
    from xic_extractor.rt_calibration_codec import rt_calibration_payload

    sample = SampleRtCalibration(
        "s1",
        "calibrated",
        ((0.9, 1.8), (1.1, 2.2)),
        None,
        None,
        "unmeasured",
        estimate_support=RtEstimateSupport((0.9, 1.1), 1.0, 0.1, 0.0),
    )
    calibration = BatchRtCalibration(
        RtCalibrationPolicy(), (), {"s1": sample}, None, None, None
    )
    report, _, _ = create_inspector_report(
        tmp_path,
        summary={
            "sample_rt_calibration": rt_calibration_payload(calibration),
            "injection_order": {"s1": 7, "s2": 3},
        },
    )
    output = tmp_path / "aligned.html"
    generate_report(report, SelectionRequest(), output)
    payload = read_report_payload(output)
    curve = payload["items"][0]["curves"][0]
    assert curve["raw_rts"] == [0.8, 0.9, 1.0, 1.1, 1.2]
    # y=2*x inside [0.9,1.1]; hold endpoint shifts 0.9/1.1 outside.
    assert curve["aligned_rts"] == pytest.approx([1.7, 1.8, 2.0, 2.2, 2.3])
    assert curve["aligned_bound_left_rt"] == pytest.approx(1.8)
    assert curve["aligned_bound_right_rt"] == pytest.approx(2.2)
    assert curve["aligned_apex_rt"] == pytest.approx(2.0)
    assert curve["rt_alignment_methods"] == ["boundary_shift", "loess"]
    assert payload["summary"]["injection_order"] == {"s1": 7, "s2": 3}


def test_obsolete_istd_display_calibration_requires_rerun_alignment(tmp_path):
    report, _, _ = create_inspector_report(tmp_path)
    # An older run kept its display calibration in the ISTD reference.
    with sqlite3.connect(report) as connection:
        summary = json.loads(
            connection.execute("SELECT payload FROM run").fetchone()[0]
        )
        summary.pop("sample_rt_calibration")
        summary["known_istd_reference"] = {"calibration": {"samples": []}}
        connection.execute("UPDATE run SET payload=?", (json.dumps(summary),))
    with pytest.raises(InspectorError, match="rerun Alignment") as caught:
        generate_report(report, SelectionRequest(), tmp_path / "invalid.html")
    assert caught.value.code == 4


def test_readable_cell_keeps_value_and_curve_but_removes_internal_records(tmp_path):
    report, _, _ = create_inspector_report(tmp_path)
    output = tmp_path / "readable.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]
    readable = _readable(item)
    assert set(readable) == {("G1", "S1")}
    decision = readable["G1", "S1"]
    assert decision["sample_ids"] == ["S1"]
    assert decision["candidate_ids"] == []
    assert decision["title"] == "s1 · 1,000"
    assert [f["label"] for f in decision["fields"]] == [
        "值從哪裡來",
        "空值為什麼是空的",
        "需不需要擔心",
    ]
    assert "technical_refs" not in decision and "links" not in decision
    curve = item["curves"][0]
    assert curve["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]
    assert curve["integration"]["area_raw_counts_seconds"] == 2500.0
    assert item["samples"][0]["decision_refs"] == [["G1", "S1"]]


def test_fact_summary_and_readable_records_keep_null_zero_and_unknown_distinct(
    tmp_path,
):
    report, _, _ = create_inspector_report(
        tmp_path,
        cells=[
            {"sample_key": "S1", "variant": "unavailable", "value": None},
            {"sample_key": "S2", "variant": "numeric", "value": 0},
        ],
    )

    output = tmp_path / "null-zero.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}
    assert samples["S1"]["quantitative_value"] is None
    assert samples["S2"]["quantitative_value"] == 0
    assert samples["S1"]["decision_refs"] == [["G1", "S1"]]
    assert samples["S2"]["decision_refs"] == [["G1", "S2"]]
    assert (
        samples["S1"]["decision_reason"]
        == "沒有可用的觀察；保存的資料未提供更具體原因。"
    )
    assert samples["S2"]["decision_reason"] == "不是空值。"
    assert (
        item["reason_label"]
        == "2 個樣本：0 detect · 0 backfill · 1 空值 · 1 有值但來源未提供"
    )
    readable = _readable(item)
    assert readable["G1", "S1"]["sample_ids"] == ["S1"]
    assert readable["G1", "S2"]["sample_ids"] == ["S2"]
    assert readable["G1", "S1"]["title"] == "s1 · 空值"
    assert readable["G1", "S2"]["title"] == "s2 · 0"


def test_excluded_candidate_exposes_exact_exclusion_without_curve(tmp_path):
    def excluded_candidates(writer):
        writer.add_method_subject({"method_subject_id": "M1"})
        for candidate_id in ("C2", "C3"):
            writer.add_candidate(
                candidate_id,
                "S2",
                precursor_mz=510.0,
                peak_status="doubly_charged",
                method_subject_id="M1",
                neutral_loss_tag="dR",
            )
            writer.add_exclusion(
                candidate_id, "S2", "discovery_candidate_not_search_eligible"
            )

    report, _, _ = create_inspector_report(tmp_path, extend=excluded_candidates)

    output = tmp_path / "excluded-candidate.html"
    generate_report(report, SelectionRequest(), output)
    items = read_report_payload(output)["items"]
    candidates = {
        item["witnesses"][0]["candidate_id"]: item
        for item in items
        if item["kind"] == "candidate"
    }

    for candidate_id, other_id in (("C2", "C3"), ("C3", "C2")):
        item = candidates[candidate_id]
        sample = item["samples"][0]
        # An exclusion is named by its candidate and sample.
        expected_ref = json.dumps([candidate_id, "S2"], separators=(",", ":"))
        assert item["stratum"] == "excluded"
        assert item["curves"] == []
        assert sample["evidence_status"] == "unavailable"
        assert "quantitative_value" not in sample
        assert sample["decision_refs"] == [expected_ref]
        other_ref = json.dumps([other_id, "S2"], separators=(",", ":"))
        assert other_ref not in sample["decision_refs"]
        exclusion = _readable(item)[expected_ref]
        assert exclusion["sample_ids"] == ["S2"]
        assert exclusion["candidate_ids"] == [candidate_id]
        assert exclusion["fields"] == [
            {"label": "為什麼未採用", "value": "初始候選未符合後續搜尋條件"}
        ]

    quantitative_item = next(item for item in items if item["kind"] == "row")
    assert quantitative_item["samples"][0]["decision_refs"] == [["G1", "S1"]]


def test_readable_numeric_formatting_keeps_scientific_payload_precision(tmp_path):
    precursor_mz = 307.1023254394531
    apex_rt = 17.2623
    area = 28578.925063705414

    def precise_curve(writer):
        payload = inspector_curve_payload("S1", "C1", "obs-01", apex_rt=apex_rt)
        payload["integration"]["area_raw_counts_seconds"] = area
        writer.add_curve_payload("S1", "initial_capture", "obs-01", payload)

    report, _, _ = create_inspector_report(
        tmp_path,
        candidate={"method_subject_id": "M1", "neutral_loss_tag": "dR"},
        method_subject={"precursor_mz": precursor_mz},
        base_cell={"value": area},
        curve=False,
        extend=precise_curve,
    )

    output = tmp_path / "formatted-values.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]
    readable = _readable(item)
    assert readable["G1", "S1"]["title"] == "s1 · 28,578.9251"
    curve = item["curves"][0]
    assert item["samples"][0]["quantitative_value"] == area
    assert curve["apex_rt"] == apex_rt
    assert curve["area"] == area
    assert curve["integration"]["area_raw_counts_seconds"] == area
    assert curve["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]


@pytest.mark.parametrize(
    "state,destination,sample_count",
    [("established", "matrix", 2), ("provisional", "archive", 1)],
)
def test_source_labels_explain_recorded_group_and_sample_outcome(
    tmp_path, state, destination, sample_count
):
    source_code = "trapezoid_counts_seconds_on_canonical_signal"
    row = {"route": {"destination": destination, "reason": state}}
    cells = [
        {
            "sample_key": "S1",
            "variant": "numeric",
            "value": 1000.0,
            "source": source_code,
        },
        {
            "sample_key": "S2",
            "variant": "numeric",
            "value": 700.0,
            "source": source_code,
            "evidence_states": ["ms1_only_backfill_observation"],
        },
    ][:sample_count]
    report, _, _ = create_inspector_report(
        tmp_path,
        candidate={"method_subject_id": "M1", "neutral_loss_tag": "dR"},
        method_subject={"neutral_loss_da": 116.0474},
        group={"disposition": state},
        cells=cells,
        extend=lambda writer: writer.set_row_destination("G1", destination),
    )
    output = tmp_path / "labels.html"
    generate_report(report, SelectionRequest(), output)
    item = read_report_payload(output)["items"][0]
    assert len(item["samples"]) == sample_count
    assert ("已建立" if state == "established" else "暫定") in item["title"]
    assert ("定量矩陣" if destination == "matrix" else "封存紀錄") in item[
        "route_label"
    ]
    assert f"{sample_count} 有值但來源未提供" in item["reason_label"]
    assert item["method_label"] == "NL 116.0474"
    assert all(
        s["status_label"] == "有定量值" and "梯形積分面積" in s["decision_label"]
        for s in item["samples"]
    )
    assert item["curves"][0]["stage_label"] == "detect 曲線"
    labels = item["title"] + item["reason_label"] + item["route_label"]
    assert not any(
        token in labels
        for token in ("G1", "G1", "一致", "成功", "有效", "信心", "原始理由")
    )
    assert item["reason"] == state and item["route"] == row["route"]
    assert item["samples"][0]["decision_reason"] == "不是空值。"
    for record in _readable(item).values():
        # Old stored unavailable stays unknown, even with MS1 backfill evidence.
        assert record["fields"][0]["value"] == "來源未提供"
        assert record["fields"][2]["value"] == "未記錄需留意的風險。"
    assert item["curves"][0]["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]


def _create_nearest_mz_db(directory, rows):
    """Publish small matrix rows with independently chosen display coordinates."""
    report = directory / "nearby.sqlite"
    matrix = directory / "matrix.tsv"
    matrix.write_text("saved matrix", encoding="utf-8")
    samples = [{"sample_key": sid, "sample_stem": sid} for sid in ("S1", "S2")]
    with ExplanationReportWriter(report, review_only=True) as writer:
        writer.declare_samples(samples)
        for row in rows:
            rid = row["id"]
            members = [
                add_witness(writer, f"{rid}-{sid}", sid, rt=rt, precursor_mz=mz)
                for sid, mz, rt in row["witnesses"]
            ]
            writer.add_final_group(
                rid, feature_id=rid, disposition="recorded", members=members
            )
            writer.set_row_destination(rid, row.get("route", "matrix"))
            writer.add_cells(
                rid,
                [
                    {
                        "sample_key": sid,
                        "variant": "unresolved" if row.get("unresolved") else "numeric",
                        "value": None if row.get("unresolved") else 10,
                    }
                    for sid in row.get(
                        "samples", [sid for sid, _, _ in row["witnesses"]]
                    )
                ],
            )
        writer.finalize(
            explanation_summary(
                {
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": samples,
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def test_nearest_mz_finds_numeric_neighbors_without_rt_or_tolerance(tmp_path):
    from xic_inspector.api import prepare_report

    report, matrix = _create_nearest_mz_db(
        tmp_path,
        [
            {"id": "c", "witnesses": [("S1", 123.4567, None)]},
            {"id": "b", "witnesses": [("S1", 123.4200, 2)]},
            {"id": "a", "witnesses": [("S1", 123.3980, None)]},
        ],
    )
    original = [path.read_bytes() for path in (report, matrix)]
    for query in (123.4, 123.4000):
        prepared = prepare_report(
            report,
            SelectionRequest(strategy="query", review_scope="matrix", nearest_mz=query),
        )
        items = prepared.payload["items"]
        assert [item["precursor_mz"] for item in items] == [
            123.3980,
            123.4200,
            123.4567,
        ]
        assert [item["mz_distance"] for item in items] == pytest.approx(
            [0.0020, 0.0200, 0.0567]
        )
        assert prepared.matched_count == 3
        assert prepared.payload["selection"]["nearest_mz"] == query
        assert prepared.payload["selection"]["missing_mz_count"] == 0
    exact = prepare_report(
        report,
        SelectionRequest(strategy="query", review_scope="matrix", group_ids=("a",)),
    )
    assert "nearest_mz" not in exact.payload["selection"]
    assert "missing_mz_count" not in exact.payload["selection"]
    assert "mz_distance" not in exact.payload["items"][0]
    assert exact.payload["items"][0]["precursor_mz"] == 123.3980
    assert [path.read_bytes() for path in (report, matrix)] == original


def test_nearest_mz_ranks_unrounded_display_mz_not_closest_witness(tmp_path):
    from xic_inspector.api import prepare_report

    report, _ = _create_nearest_mz_db(
        tmp_path,
        [
            {"id": "multi", "witnesses": [("S1", 123.46, 1), ("S2", 123.4, 1)]},
            {"id": "near", "witnesses": [("S1", 123.42, 1)]},
            {"id": "round-a", "witnesses": [("S1", 123.400041, 1)]},
            {"id": "round-b", "witnesses": [("S1", 123.400049, 1)]},
        ],
    )
    prepared = prepare_report(
        report,
        SelectionRequest(
            strategy="query", review_scope="matrix", nearest_mz=123.400048
        ),
    )
    items = prepared.payload["items"]
    assert [json.loads(i["id"])[-1] for i in items] == [
        "round-b",
        "round-a",
        "near",
        "multi",
    ]
    assert [i["precursor_mz"] for i in items] == [
        123.400049,
        123.400041,
        123.42,
        123.46,
    ]
    assert items[-1]["mz_distance"] == pytest.approx(123.46 - 123.400048)


def test_nearest_mz_applies_rt_eligibility_without_replacing_display_witness(tmp_path):
    from xic_inspector.api import prepare_report

    report, _ = _create_nearest_mz_db(
        tmp_path,
        [
            {"id": "multi", "witnesses": [("S1", 140, 20), ("S2", 123.4, 5)]},
            {"id": "near", "witnesses": [("S1", 125, 5)]},
            {"id": "no-rt", "witnesses": [("S1", 123.4, None)]},
        ],
    )
    prepared = prepare_report(
        report,
        SelectionRequest(
            strategy="query", review_scope="matrix", nearest_mz=123.4, rt_max=10
        ),
    )
    near, multi = prepared.payload["items"]
    assert near["precursor_mz"] == 125
    assert multi["precursor_mz"] == 140
    assert multi["rt_apex_min"] == 20
    assert multi["mz_distance"] == pytest.approx(16.6)
    assert [w["sample_key"] for w in multi["matched_refs"]] == ["S2"]
    assert prepared.missing_rt_count == 1
    scoped = prepare_report(
        report,
        SelectionRequest(
            strategy="query",
            review_scope="matrix",
            nearest_mz=123.4,
            sample_ids=("S2",),
            rt_max=10,
        ),
    )
    assert len(scoped.payload["items"]) == 1
    assert scoped.payload["items"][0]["precursor_mz"] == 123.4
    assert scoped.payload["items"][0]["mz_distance"] == 0


def test_nearest_mz_sorts_full_population_before_stable_pagination(
    tmp_path, monkeypatch
):
    from xic_inspector.api import prepare_report
    from xic_inspector.sources import cid_nl_query

    coordinates = {f"item-{i:02}": 200 + (30 - i) / 100 for i in range(31)}
    coordinates.update({"tie-z": 201, "tie-a": 201})
    report, _ = _create_nearest_mz_db(
        tmp_path,
        [{"id": key, "witnesses": [("S1", mz, 1)]} for key, mz in coordinates.items()],
    )
    expected = sorted(coordinates, key=lambda key: (abs(coordinates[key] - 200), key))
    restored_rows = []
    read_selection = cid_nl_query.read_review_evidence

    def read_page(*args, **kwargs):
        restored_rows.append(len(kwargs["row_ids"]))
        return read_selection(*args, **kwargs)

    monkeypatch.setattr(cid_nl_query, "read_review_evidence", read_page)
    pages = []
    for offset in (0, 25):
        prepared = prepare_report(
            report,
            SelectionRequest(
                strategy="query",
                review_scope="matrix",
                nearest_mz=200,
                limit=25,
                offset=offset,
            ),
        )
        assert prepared.matched_count == 33
        pages.extend(json.loads(i["id"])[-1] for i in prepared.payload["items"])
    assert pages == expected
    assert len(set(pages)) == 33
    assert pages[0] == "item-30"
    assert restored_rows == [25, 8]


def test_nearest_mz_excludes_invalid_display_mz_and_respects_matrix_scope(tmp_path):
    from xic_inspector.api import prepare_report

    rows = [
        {"id": "valid", "witnesses": [("S1", 124, None)], "unresolved": True},
        {"id": "resolved", "witnesses": [("S1", 123.4, 1)]},
        {"id": "archive", "witnesses": [("S1", 123.4, 1)], "route": "archive"},
        {"id": "missing-first", "witnesses": [("S1", None, 1), ("S2", 123.4, 1)]},
        {"id": "without-witness", "witnesses": [], "samples": ["S1"]},
    ]
    rows.extend(
        {"id": label, "witnesses": [("S1", mz, 1)]}
        for label, mz in (("zero", 0), ("negative", -1), ("missing", None))
    )
    report, _ = _create_nearest_mz_db(tmp_path, rows)
    matrix = prepare_report(
        report,
        SelectionRequest(strategy="query", review_scope="matrix", nearest_mz=123.4),
    )
    assert [json.loads(i["id"])[-1] for i in matrix.payload["items"]] == [
        "resolved",
        "valid",
    ]
    assert matrix.payload["selection"]["missing_mz_count"] == 5
    assert matrix.matched_count == 2
    unresolved = prepare_report(
        report,
        SelectionRequest(strategy="query", review_scope="unresolved", nearest_mz=123.4),
    )
    assert [i["precursor_mz"] for i in unresolved.payload["items"]] == [124]
    assert unresolved.payload["selection"]["missing_mz_count"] == 0


@pytest.mark.parametrize(
    "value", [True, "123.4", "", 0, -1, float("nan"), float("inf")]
)
def test_nearest_mz_rejects_invalid_values(value):
    with pytest.raises(InspectorError) as exc:
        SelectionRequest(
            strategy="query", review_scope="matrix", nearest_mz=value
        ).validate()
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "conflict",
    [
        {"strategy": "sample"},
        {"review_scope": "all"},
        {"review_scope": "archive"},
        {"review_scope": "candidates"},
        {"mz_min": 100},
        {"mz_max": 200},
        {"group_ids": ("G1",)},
        {"candidate_ids": ("C1",)},
        {"target_ids": ("T1",)},
        {"target_role": "ISTD"},
        {"attention_only": True},
    ],
)
def test_nearest_mz_rejects_mixed_modes(conflict):
    values = dict(strategy="query", review_scope="matrix", nearest_mz=123.4)
    values.update(conflict)
    with pytest.raises(InspectorError) as exc:
        SelectionRequest(**values).validate()
    assert exc.value.code == 2


def test_nearest_mz_rejects_nonindexed_evidence_without_fallback(tmp_path):
    from xic_inspector.api import prepare_report

    report, _, _ = create_inspector_report(tmp_path)
    with pytest.raises(InspectorError, match="indexed Untargeted matrix") as exc:
        prepare_report(
            report,
            SelectionRequest(strategy="query", review_scope="matrix", nearest_mz=500),
        )
    assert exc.value.code == 2


def test_nearest_mz_rejects_portable_curve_bundles(tmp_path):
    from tests.xic_inspector_curve_bundle_fixtures import _fixture_bundle
    from xic_inspector.api import prepare_report
    from xic_inspector.sources.curve_bundle import write_curve_bundle

    source = tmp_path / "curves.json"
    write_curve_bundle(_fixture_bundle(), source)
    with pytest.raises(InspectorError, match="indexed Untargeted matrix") as exc:
        prepare_report(
            source,
            SelectionRequest(strategy="query", review_scope="matrix", nearest_mz=500),
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("role", ["", "analyte", "QC", 123, False, []])
def test_target_role_rejects_noncanonical_requests(role):
    with pytest.raises(InspectorError, match="target_role") as exc:
        SelectionRequest(strategy="query", target_role=role).validate()
    assert exc.value.code == 2


@pytest.mark.parametrize("strategy", ["sample", "attention"])
def test_target_role_requires_query_strategy(strategy):
    with pytest.raises(InspectorError, match="query strategy"):
        SelectionRequest(strategy=strategy, target_role="ISTD").validate()


def test_target_role_rejects_untargeted_evidence(tmp_path):
    from xic_inspector.api import prepare_report

    report, _, _ = create_inspector_report(tmp_path)
    with pytest.raises(InspectorError, match="Targeted XLSX") as exc:
        prepare_report(report, SelectionRequest(strategy="query", target_role="ISTD"))
    assert exc.value.code == 2


def test_target_role_rejects_portable_curve_bundles(tmp_path):
    from tests.xic_inspector_curve_bundle_fixtures import _fixture_bundle
    from xic_inspector.api import prepare_report
    from xic_inspector.sources.curve_bundle import write_curve_bundle

    source = tmp_path / "curves.json"
    write_curve_bundle(_fixture_bundle(), source)
    with pytest.raises(InspectorError, match="Targeted XLSX") as exc:
        prepare_report(source, SelectionRequest(strategy="query", target_role="ISTD"))
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "old_schema", ["alignment_explanation", "alignment_explanation_review"]
)
def test_old_explanation_requires_rerun(tmp_path: Path, old_schema: str) -> None:
    path, _matrix, _digest = create_inspector_report(tmp_path)
    # Older reports kept the summary in a key/value metadata table.
    with sqlite3.connect(path) as connection:
        summary = json.loads(
            connection.execute("SELECT payload FROM run").fetchone()[0]
        )
        summary["schema_version"] = old_schema
        summary["query_index_version"] = 2
        connection.execute("DROP TABLE run")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT)")
        connection.execute(
            "INSERT INTO metadata VALUES ('summary', ?)", (json.dumps(summary),)
        )
        connection.execute(
            "ALTER TABLE samples RENAME COLUMN sample_key "
            "TO canonical_sample_identity_id"
        )
    with pytest.raises(InspectorError, match="重新執行 Alignment") as error:
        generate_report(path, SelectionRequest(), tmp_path / "review.html")
    assert error.value.code == 4
    assert not (tmp_path / "review.html").exists()


def test_decision_names_a_sample_without_witness_by_its_report_label():
    from xic_inspector.sources import cid_nl_presentation as presentation
    from xic_inspector.sources.cid_nl import Card

    record = presentation.readable_record(
        Card(
            "cell_decisions",
            ("G1", "canonical-sample-id"),
            {"value": None, "sample_key": "canonical-sample-id"},
            frozenset(),
            frozenset(),
        ),
        scope=(["canonical-sample-id"], []),
        sample_labels={"canonical-sample-id": "BenignfatBC1055_DNA"},
    )
    assert record["title"] == "BenignfatBC1055_DNA · 空值"
