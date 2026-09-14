"""Tests for xic_inspector.api and cli: contracts, error codes, and cancellation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

from xic_extractor.diagnostics.cid_nl_explanation import (
    ExplanationReportWriter,
)
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)
from xic_inspector.cli import main as cli_main


def _report_data(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', text, re.S
    )
    assert match is not None
    return json.loads(match.group(1))


def _create_mock_v2_db(
    directory: Path,
    *,
    request_id: str = "req-01",
    attempt_id: str = "att-01",
    samples: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, str]:
    db_path = directory / "cid_nl_explanation.sqlite"
    matrix_path = directory / "alignment_matrix.tsv"

    matrix_content = (
        b"cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\nrow-01\t500.0\t1.0\t1000\t0\n"
    )
    matrix_path.write_bytes(matrix_content)
    with matrix_path.open("rb") as handle:
        matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()

    if samples is None:
        samples = [
            {"sample_stem": "s1", "canonical_sample_identity_id": "S1"},
            {"sample_stem": "s2", "canonical_sample_identity_id": "S2"},
        ]

    with ExplanationReportWriter(db_path) as writer:
        writer.add_candidate(
            "C1",
            "S1",
            {
                "candidate_id": "C1",
                "canonical_sample_identity_id": "S1",
                "precursor_mz": 500.0,
            },
        )
        writer.add_record(
            "rows",
            "row-01",
            {
                "row_key_id": "row-01",
                "group_hypothesis_id": "G1",
                "reason": "Passed alignment",
            },
            [("C1", "S1")],
        )
        writer.add_record(
            "final_groups",
            "G1",
            {"group_hypothesis_id": "G1", "disposition": "completed"},
            [("C1", "S1")],
        )
        writer.add_record(
            "cell_decisions",
            "cd-01",
            {"row_key_id": "row-01", "sample_id": "S1", "quantitative_value": 1000.0},
            [("C1", "S1")],
        )
        writer.add_record(
            "terminal_subjects",
            "terminal-01",
            {
                "observations": [
                    {"peak_observation_content_id": "obs-01", "rt_apex_min": 1.0}
                ]
            },
            [("C1", "S1")],
        )
        # Add a curve payload for C1
        curve_envelope = {
            "schema_version": "curve_payload_v1",
            "kind": "initial_capture",
            "sample_id": "S1",
            "candidate_id": "C1",
            "peak_observation_content_id": "obs-01",
            "trace_coordinate": {
                "mz": 500.0,
                "rt_min": 0.8,
                "rt_max": 1.2,
                "ppm_tol": 10.0,
            },
            "curves": {
                "rt": [0.8, 0.9, 1.0, 1.1, 1.2],
                "raw": [10.0, 50.0, 1000.0, 40.0, 5.0],
                "baseline": None,
                "smoothed": None,
                "positive": None,
            },
            "integration": {
                "apex_rt_min": 1.0,
                "raw_apex_rt_min": 1.0,
                "rt_left_min": 0.9,
                "rt_right_min": 1.1,
                "height_raw": 1000.0,
                "area_raw_counts_seconds": 2500.0,
                "area_profile_selected": None,
                "integration_method": "raw_trapezoid",
                "boundary_source": "candidate_interval",
            },
            "provenance": {
                "profile_id": None,
                "trace_content_id": None,
                "processing_request_id": request_id,
                "processing_attempt_id": attempt_id,
            },
        }
        writer.add_curve_payload(
            payload_id="S1:initial_capture:C1",
            sample_id="S1",
            kind="initial_capture",
            record_id="obs-01",
            payload=curve_envelope,
        )
        writer.finalize(
            {
                "processing_request_id": request_id,
                "processing_attempt_id": attempt_id,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "samples": samples,
            }
        )

    return db_path, matrix_path, matrix_hash


def test_generate_report_sample_strategy_success() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, matrix_hash = _create_mock_v2_db(tmp)
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
        assert res.evidence_capability == "retained_curves_v1"
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
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.S).group(1)
        assert title == heading == "Inspector"
        assert res.source.processing_request_id not in title + heading
        assert "retained_curves_v1" in content


def test_group_query_keeps_unlinked_stored_cells_scoped_and_readable(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    stored_cell = {
        "cell_observation_id": "cell-observation-02",
        "decision_id": "decision-02",
        "group_hypothesis_id": "G1",
        "row_key_id": "row-01",
        "canonical_sample_identity_id": "S2",
        "source": "",
        "value": None,
        "variant": "not_searched",
    }
    stored_observation = {
        "cell_observation_id": "cell-observation-02",
        "group_hypothesis_id": "G1",
        "canonical_sample_identity_id": "S2",
        "processing_request_id": "req-01",
        "evidence_states": [],
        "predecessor_cells": [],
    }
    with sqlite3.connect(report) as connection:
        for kind, ref, facts in (
            ("cell_decisions", "cd-02", stored_cell),
            ("cell_observations", "cell-observation-02", stored_observation),
            (
                "cell_decisions",
                "wrong-row",
                {**stored_cell, "decision_id": "wrong-row", "row_key_id": "row-02"},
            ),
            (
                "cell_decisions",
                "wrong-group",
                {
                    **stored_cell,
                    "decision_id": "wrong-group",
                    "group_hypothesis_id": "G2",
                },
            ),
            (
                "cell_decisions",
                "wrong-sample",
                {
                    **stored_cell,
                    "decision_id": "wrong-sample",
                    "canonical_sample_identity_id": "S3",
                },
            ),
            (
                "method_assessments",
                "assessment-hold",
                {
                    "canonical_sample_identity_id": "S1",
                    "method_result": "HOLD",
                    "reason_codes": [
                        "insufficient_method_evidence",
                        "no_unique_qualifying_relation",
                    ],
                },
            ),
        ):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (kind, ref, json.dumps(facts)),
            )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            ("C1", "S1", "method_assessments", "assessment-hold"),
        )

    output = tmp_path / "all-samples.html"
    generate_report(
        report,
        SelectionRequest(strategy="query", group_ids=("G1",)),
        output,
    )
    item = _report_data(output)["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}

    assert set(samples) == {"S1", "S2"}
    assert samples["S2"]["sample_stem"] == "s2"
    assert samples["S2"]["quantitative_value"] is None
    assert samples["S2"]["decision_refs"] == ["cd-02"]
    assert samples["S2"]["status_kind"] == "null"
    assert samples["S2"]["decision_label"] == "本次未搜尋，定量結果保留為空值。"
    assert "「本次未搜尋」（not_searched）" in samples["S2"]["decision_reason"]

    readable = {record["ref"]: record for record in item["details"]["readable_records"]}
    assert "cell-observation-02" in readable
    assert readable["cell-observation-02"]["sample_ids"] == ["S2"]
    assert readable["cell-observation-02"]["candidate_ids"] == []
    assert not {"wrong-row", "wrong-group", "wrong-sample"} & readable.keys()
    assert {
        witness["canonical_sample_identity_id"] for witness in item["witnesses"]
    } == {"S1"}
    assert {curve["sample_id"] for curve in item["curves"]} == {"S1"}

    assert "s1" in item["reason_label"]
    assert "方法證據不足（insufficient_method_evidence）" in item["reason_label"]
    assert "沒有唯一合格關係（no_unique_qualifying_relation）" in item["reason_label"]
    assert "不代表正式定量採用" in item["reason_label"]
    trigger = next(
        trigger
        for trigger in item["details"]["attention_triggers"]
        if trigger["ref"] == "assessment-hold"
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
    scoped_item = _report_data(scoped_output)["items"][0]
    assert [sample["sample_id"] for sample in scoped_item["samples"]] == ["S1"]
    scoped_records = {
        record["ref"] for record in scoped_item["details"]["readable_records"]
    }
    assert "cd-02" not in scoped_records
    assert "cell-observation-02" not in scoped_records


def test_error_code_2_invalid_request() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = _create_mock_v2_db(tmp)
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
        db_path, matrix_path, _ = _create_mock_v2_db(tmp)
        out_html = tmp / "report.html"

        # Tamper with matrix file
        matrix_path.write_text("tampered content", encoding="utf-8")

        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), out_html)
        assert exc_info.value.code == 4


def test_error_code_6_output_aliasing_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, _ = _create_mock_v2_db(tmp)

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
        db_path, _, _ = _create_mock_v2_db(tmp)
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
        db_path, _, _ = _create_mock_v2_db(
            tmp,
            samples=[
                {"sample_stem": "b_sample", "canonical_sample_identity_id": "S_B"},
                {"sample_stem": "a_sample", "canonical_sample_identity_id": "S_A"},
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
        db_path, _, _ = _create_mock_v2_db(tmp)
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


@pytest.mark.parametrize("row_count", [2, 30, 50])
def test_row_curves_follow_exact_witnesses_and_shared_payload_is_read_once(
    tmp_path, monkeypatch, row_count, record_property
):
    from xic_inspector.api import read_evidence_summary
    from xic_inspector.sources import cid_nl

    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        for index in range(2, row_count + 1):
            rid = f"row-{index:02d}"
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "rows",
                    rid,
                    json.dumps({"row_key_id": rid, "group_hypothesis_id": "G1"}),
                ),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                ("C1", "S1", "rows", rid),
            )
    reads = []
    real_reader = cid_nl.read_cid_nl_curves

    def read(*args, **kwargs):
        result = real_reader(*args, **kwargs)
        reads.append((kwargs, len(result)))
        return result

    monkeypatch.setattr(cid_nl, "read_cid_nl_curves", read)
    assert (
        read_evidence_summary(report)["samples"][0]["canonical_sample_identity_id"]
        == "S1"
    )
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
    items = _report_data(output)["items"]
    assert {json.loads(item["id"])[-1] for item in items} == {
        f"row-{index:02d}" for index in range(1, row_count + 1)
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
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute("DELETE FROM records WHERE kind='rows'")
        connection.execute("DELETE FROM links WHERE kind='rows'")
        connection.execute("DELETE FROM records WHERE kind='terminal_subjects'")
        connection.execute("DELETE FROM links WHERE kind='terminal_subjects'")
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            ("C2", "S2", json.dumps({"candidate_id": "C2", "precursor_mz": 600.0})),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)", ("C2", "S2", "final_groups", "G1")
        )
        for cid, sid, rt in (("C1", "S1", 1.0), ("C2", "S2", 8.0)):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "terminal_subjects",
                    cid,
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "peak_observation_content_id": "obs-01"
                                    if cid == "C1"
                                    else "obs-02",
                                    "rt_apex_min": rt,
                                }
                            ]
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (cid, sid, "terminal_subjects", cid),
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
    item = _report_data(output)["items"][0]
    assert json.loads(item["id"])[-1] == "G1"
    assert [w["candidate_id"] for w in item["matched_refs"]] == ["C1"]
    assert [w["canonical_sample_identity_id"] for w in item["witnesses"]] == ["S1"]


def test_source_strata_use_only_selected_samples_actual_cells(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            ("C2", "S2", json.dumps({"candidate_id": "C2", "precursor_mz": 600.0})),
        )
        for kind, rid in (
            ("rows", "row-01"),
            ("final_groups", "G1"),
            ("cell_decisions", "cd-01"),
        ):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)", ("C2", "S2", kind, rid)
            )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "cell_decisions",
                "cd-02",
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "group_hypothesis_id": "G1",
                        "canonical_sample_identity_id": "S2",
                        "value": None,
                        "variant": "missing",
                    }
                ),
            ),
        )
        for cid, sid in (("C1", "S1"), ("C2", "S2")):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (cid, sid, "cell_decisions", "cd-02"),
            )
    full = tmp_path / "full.html"
    generate_report(report, SelectionRequest(), full)
    assert _report_data(full)["items"][0]["stratum"] == "null"
    full_samples = {
        s["sample_id"]: s for s in _report_data(full)["items"][0]["samples"]
    }
    assert full_samples["S1"]["status_kind"] == "rest"
    assert full_samples["S2"]["status_kind"] == "null"
    scoped = tmp_path / "scope.html"
    generate_report(report, SelectionRequest(sample_ids=("S1",)), scoped)
    item = _report_data(scoped)["items"][0]
    assert item["stratum"] == "rest"
    assert item["samples"][0]["quantitative_value"] == 1000.0
    assert all(
        c.get("canonical_sample_identity_id", c.get("sample_id")) == "S1"
        for c in item["details"]["cells"]
    )


@pytest.mark.parametrize("failure", ["missing", "request", "attempt", "sample"])
def test_invalid_selected_payload_preserves_existing_report(tmp_path, failure):
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    output = tmp_path / "preserved.html"
    generate_report(report, SelectionRequest(), output)
    before = output.read_bytes()
    with sqlite3.connect(report) as connection:
        if failure == "missing":
            connection.execute("DELETE FROM curve_payloads")
        else:
            pid, blob = connection.execute(
                "SELECT payload_id,payload FROM curve_payloads"
            ).fetchone()
            payload = json.loads(zlib.decompress(blob))
            if failure == "attempt":
                payload["provenance"]["processing_attempt_id"] = "another-attempt"
            elif failure == "request":
                payload["provenance"]["processing_request_id"] = "another-request"
            else:
                payload["sample_id"] = "S2"
            connection.execute(
                "UPDATE curve_payloads SET payload=? WHERE payload_id=?",
                (zlib.compress(json.dumps(payload).encode()), pid),
            )
    with pytest.raises(InspectorError) as caught:
        generate_report(report, SelectionRequest(), output)
    assert caught.value.code == 4
    assert output.read_bytes() == before


def test_legitimate_candidate_without_observation_remains_available_as_metadata(
    tmp_path,
):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            (
                "not-acquired",
                "S2",
                json.dumps(
                    {
                        "candidate_id": "not-acquired",
                        "discovery_candidate_state": "excluded",
                    }
                ),
            ),
        )
    output = tmp_path / "unavailable.html"
    result = generate_report(
        report,
        SelectionRequest(strategy="query", candidate_ids=("not-acquired",)),
        output,
    )
    assert result.selected_count == 1
    item = _report_data(output)["items"][0]
    assert item["curves"] == []
    assert item["samples"][0]["evidence_status"] == "unavailable"
    assert item["samples"][0]["decision_refs"] == []


def test_candidate_presentation_ids_preserve_sample_identity(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        for sid in ("S1", "S2"):
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                ("shared-id", sid, json.dumps({"candidate_id": "shared-id"})),
            )
    output = tmp_path / "identities.html"
    generate_report(
        report, SelectionRequest(strategy="query", candidate_ids=("shared-id",)), output
    )
    items = _report_data(output)["items"]
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
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        blob = connection.execute("SELECT payload FROM curve_payloads").fetchone()[0]
        original = json.loads(zlib.decompress(blob))
        captures = [
            ("first", "initial_capture", "obs-earlier", "obs-earlier"),
            ("second", "reacquired_capture", "obs-01", "obs-01"),
            ("nopeak", "reacquired_capture", None, "C1"),
        ]
        for pid, kind, observation_id, record_id in captures:
            payload = {
                **original,
                "kind": kind,
                "peak_observation_content_id": observation_id,
            }
            connection.execute(
                "INSERT INTO curve_payloads VALUES (?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    "S1",
                    kind,
                    record_id,
                    "zlib-json-utf8-v1",
                    zlib.compress(json.dumps(payload).encode()),
                ),
            )
            capture = {
                "payload_id": pid,
                "sample_id": "S1",
                "candidate_id": "C1",
                "peak_observation_content_id": observation_id,
                "record_id": record_id,
                "kind": kind,
            }
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                ("capture_evidence", pid, json.dumps(capture)),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                ("C1", "S1", "capture_evidence", pid),
            )
    output = tmp_path / "stages.html"
    generate_report(report, SelectionRequest(), output)
    curves = _report_data(output)["items"][0]["curves"]
    assert len(curves) == 4
    assert {c["evidence_ref"] for c in curves} >= {"first", "second", "nopeak"}
    before = output.read_bytes()
    with sqlite3.connect(report) as connection:
        # The terminal obs still has its initial payload; losing a distinct
        # reacquisition of that same observation must still fail completion.
        connection.execute("DELETE FROM curve_payloads WHERE payload_id='second'")
    with pytest.raises(InspectorError) as caught:
        generate_report(report, SelectionRequest(), output)
    assert caught.value.code == 4
    assert output.read_bytes() == before


@pytest.mark.parametrize(
    ("capture_kind", "invocation", "expected_kind", "expected_label"),
    (
        (
            "recovery_observation",
            "untargeted-cid-nl-backfill-recovery:v1",
            "recovery_support",
            "方法補取支持",
        ),
        (
            "reacquired_capture",
            "untargeted-cid-nl-reacquire:v1",
            "reacquired_support",
            "重新擷取支持",
        ),
        (
            "initial_capture",
            "untargeted-cid-nl-real-data:v1",
            "discovery_support",
            "初始探索支持",
        ),
    ),
)
def test_sample_support_origin_uses_exact_method_capture_and_observation(
    tmp_path, capture_kind, invocation, expected_kind, expected_label
):
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        original = json.loads(
            zlib.decompress(
                connection.execute("SELECT payload FROM curve_payloads").fetchone()[0]
            )
        )
        recovery = {
            **original,
            "kind": capture_kind,
        }
        connection.execute(
            "INSERT INTO curve_payloads VALUES (?, ?, ?, ?, ?, ?)",
            (
                "recovery-curve",
                "S1",
                capture_kind,
                "obs-01",
                "zlib-json-utf8-v1",
                zlib.compress(json.dumps(recovery).encode()),
            ),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "capture_evidence",
                "recovery-curve",
                json.dumps(
                    {
                        "payload_id": "recovery-curve",
                        "sample_id": "S1",
                        "candidate_id": "C1",
                        "peak_observation_content_id": "obs-01",
                        "record_id": "obs-01",
                        "kind": capture_kind,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "method_assessments",
                "recovery-method",
                json.dumps(
                    {
                        "canonical_sample_identity_id": "S1",
                        "candidate_peak_content_ids": ["obs-01"],
                        "detector_invocation_id": invocation,
                        "method_result": "SUPPORT",
                    }
                ),
            ),
        )
        for kind, ref in (
            ("capture_evidence", "recovery-curve"),
            ("method_assessments", "recovery-method"),
        ):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)", ("C1", "S1", kind, ref)
            )

    output = tmp_path / "recovery-origin.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]

    assert item["details"]["review_summary"]["origin_kinds"] == [expected_kind]
    assert item["review_summary"] == item["details"]["review_summary"]
    assert item["samples"][0]["support_origin"] == {
        "kind": expected_kind,
        "label": expected_label,
        "method_refs": ["recovery-method"],
        "capture_refs": ["recovery-curve"],
        "observation_refs": ["obs-01"],
    }


def test_recovery_origin_survives_zero_terminal_final_cell_binding(tmp_path):
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "DELETE FROM links WHERE kind='terminal_subjects' "
            "AND record_id='terminal-01'"
        )
        final_group = json.loads(
            connection.execute(
                "SELECT payload FROM records WHERE kind='final_groups' "
                "AND record_id='G1'"
            ).fetchone()[0]
        )
        final_group["member_subjects"] = [
            {
                "member_subject_id": "member-01",
                "canonical_sample_identity_id": "S1",
                "subject_kind": "peak_hypothesis",
                "subject_id": "hypothesis-01",
                "physical_observation_ids": ["obs-01"],
            }
        ]
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='final_groups' AND record_id='G1'",
            (json.dumps(final_group),),
        )
        decision = json.loads(
            connection.execute(
                "SELECT payload FROM records WHERE kind='cell_decisions' "
                "AND record_id='cd-01'"
            ).fetchone()[0]
        )
        decision.update(
            group_hypothesis_id="G1",
            cell_observation_id="cell-observation-01",
        )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='cell_decisions' "
            "AND record_id='cd-01'",
            (json.dumps(decision),),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "cell_observations",
                "cell-observation-01",
                json.dumps(
                    {
                        "cell_observation_id": "cell-observation-01",
                        "group_hypothesis_id": "G1",
                        "canonical_sample_identity_id": "S1",
                        "predecessor_cells": [
                            {
                                "content": {
                                    "canonical_sample_identity_id": "S1",
                                    "group_hypothesis_id": "G1",
                                    "peak_observation_content_id": "obs-01",
                                    "method_result": "SUPPORT",
                                }
                            }
                        ],
                    }
                ),
            ),
        )
        original = json.loads(
            zlib.decompress(
                connection.execute("SELECT payload FROM curve_payloads").fetchone()[0]
            )
        )
        connection.execute(
            "INSERT INTO curve_payloads VALUES (?, ?, ?, ?, ?, ?)",
            (
                "recovery-curve",
                "S1",
                "recovery_observation",
                "obs-01",
                "zlib-json-utf8-v1",
                zlib.compress(
                    json.dumps({**original, "kind": "recovery_observation"}).encode()
                ),
            ),
        )
        for kind, ref, facts in (
            (
                "capture_evidence",
                "recovery-curve",
                {
                    "payload_id": "recovery-curve",
                    "sample_id": "S1",
                    "candidate_id": "C1",
                    "peak_observation_content_id": "obs-01",
                    "record_id": "obs-01",
                    "kind": "recovery_observation",
                },
            ),
            (
                "method_assessments",
                "recovery-method",
                {
                    "canonical_sample_identity_id": "S1",
                    "candidate_peak_content_ids": ["obs-01"],
                    "detector_invocation_id": "untargeted-cid-nl-backfill-recovery:v1",
                    "method_result": "SUPPORT",
                },
            ),
        ):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (kind, ref, json.dumps(facts)),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)", ("C1", "S1", kind, ref)
            )

    output = tmp_path / "zero-terminal-recovery.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]

    assert item["witnesses"][0]["observation_id"] is None
    assert item["review_summary"]["observed_sample_count"] == 1
    assert item["review_summary"]["origin_kinds"] == ["recovery_support"]
    assert item["samples"][0]["support_origin"] == {
        "kind": "recovery_support",
        "label": "方法補取支持",
        "method_refs": ["recovery-method"],
        "capture_refs": ["recovery-curve"],
        "observation_refs": ["obs-01"],
    }


def test_initial_edge_adds_one_exact_peer_without_expanding_selection(tmp_path):
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        original = json.loads(
            zlib.decompress(
                connection.execute("SELECT payload FROM curve_payloads").fetchone()[0]
            )
        )
        for candidate_id, group_id, row_id, observation_id in (
            ("C2", "G2", "row-02", "obs-02"),
            ("C3", "G3", "row-03", "obs-03"),
        ):
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                (
                    candidate_id,
                    "S1",
                    json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "canonical_sample_identity_id": "S1",
                            "precursor_mz": 501.0,
                        }
                    ),
                ),
            )
            for kind, ref, facts in (
                (
                    "rows",
                    row_id,
                    {"row_key_id": row_id, "group_hypothesis_id": group_id},
                ),
                (
                    "final_groups",
                    group_id,
                    {"group_hypothesis_id": group_id, "disposition": "provisional"},
                ),
                (
                    "terminal_subjects",
                    f"terminal-{candidate_id}",
                    {
                        "observations": [
                            {
                                "peak_observation_content_id": observation_id,
                                "rt_apex_min": 1.0,
                            }
                        ]
                    },
                ),
            ):
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?)",
                    (kind, ref, json.dumps(facts)),
                )
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (candidate_id, "S1", kind, ref),
                )
            curve = {
                **original,
                "candidate_id": candidate_id,
                "peak_observation_content_id": observation_id,
            }
            connection.execute(
                "INSERT INTO curve_payloads VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"curve-{candidate_id}",
                    "S1",
                    "initial_capture",
                    observation_id,
                    "zlib-json-utf8-v1",
                    zlib.compress(json.dumps(curve).encode()),
                ),
            )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "initial_edges",
                "edge-c1-c2",
                json.dumps(
                    {
                        "disposition": "excluded",
                        "initial_evidence": {"decision": "blocked_edge"},
                    }
                ),
            ),
        )
        for candidate_id in ("C1", "C2"):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (candidate_id, "S1", "initial_edges", "edge-c1-c2"),
            )

    output = tmp_path / "peer.html"
    result = generate_report(
        report,
        SelectionRequest(strategy="query", limit=1, candidate_ids=("C1",)),
        output,
    )
    data = _report_data(output)

    assert result.selected_count == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["review_summary"]["related_group_count"] == 1
    assert len(item["related_groups"]) == 1
    peer = item["related_groups"][0]
    assert peer["group_id"] == "G2"
    assert peer["edge_refs"] == ["edge-c1-c2"]
    assert len(peer["curves"]) == 1
    assert peer["curves"][0]["candidate_ids"] == ["C2"]


def test_internal_pair_support_preserves_edge_identity_and_disposition(tmp_path):
    report, _, _ = _create_mock_v2_db(
        tmp_path,
        samples=[
            {"sample_stem": "sample one", "canonical_sample_identity_id": "S1"},
            {"sample_stem": "sample two", "canonical_sample_identity_id": "S2"},
            {"sample_stem": "sample three", "canonical_sample_identity_id": "S3"},
        ],
    )
    with sqlite3.connect(report) as connection:
        for candidate_id, sample_id in (
            ("C1-duplicate", "S1"),
            ("C2", "S2"),
            ("C3", "S3"),
        ):
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                (
                    candidate_id,
                    sample_id,
                    json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "canonical_sample_identity_id": sample_id,
                            "precursor_mz": 500.0,
                        }
                    ),
                ),
            )
        for candidate_id, sample_id in (("C1-duplicate", "S1"), ("C2", "S2")):
            for kind, ref in (("rows", "row-01"), ("final_groups", "G1")):
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (candidate_id, sample_id, kind, ref),
                )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='final_groups' AND record_id='G1'",
            (
                json.dumps(
                    {
                        "group_hypothesis_id": "G1",
                        "disposition": "completed",
                        "member_subjects": [
                            {
                                "member_subject_id": "member-1",
                                "canonical_sample_identity_id": "S1",
                            },
                            {
                                "member_subject_id": "member-2",
                                "canonical_sample_identity_id": "S2",
                            },
                        ],
                    }
                ),
            ),
        )
        for kind, ref, payload in (
            (
                "rows",
                "row-02",
                {"row_key_id": "row-02", "group_hypothesis_id": "G2"},
            ),
            (
                "final_groups",
                "G2",
                {
                    "group_hypothesis_id": "G2",
                    "disposition": "completed",
                    "member_subjects": [
                        {
                            "member_subject_id": "member-3",
                            "canonical_sample_identity_id": "S3",
                        }
                    ],
                },
            ),
        ):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (kind, ref, json.dumps(payload)),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                ("C3", "S3", kind, ref),
            )
        edges = (
            (
                "edge-direct-three-links",
                "member-1",
                "member-2",
                "eligible",
                {
                    "decision": "strong_edge",
                    "reason": "strong_edge: score=90",
                    "score": 90,
                    "rt_raw_delta_sec": 1.25,
                    "rt_drift_corrected_delta_sec": 0.75,
                },
                (("C1", "S1"), ("C1-duplicate", "S1"), ("C2", "S2")),
            ),
            (
                "edge-review",
                "member-1",
                "member-2",
                "review_only",
                {
                    "decision": "weak_edge",
                    "reason": "weak_edge: score unavailable",
                },
                (("C1", "S1"), ("C2", "S2")),
            ),
            (
                "edge-external-excluded",
                "member-1",
                "member-3",
                "excluded",
                {
                    "decision": "blocked_edge",
                    "reason": "blocked: identity_conflict",
                    "score": 0,
                },
                (("C1", "S1"), ("C3", "S3")),
            ),
        )
        for ref, left_id, right_id, disposition, evidence, links in edges:
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "initial_edges",
                    ref,
                    json.dumps(
                        {
                            "left_member_subject_id": left_id,
                            "right_member_subject_id": right_id,
                            "disposition": disposition,
                            "initial_evidence": evidence,
                        }
                    ),
                ),
            )
            for candidate_id, sample_id in links:
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (candidate_id, sample_id, "initial_edges", ref),
                )

    output = tmp_path / "internal-pair-support.html"
    generate_report(
        report,
        SelectionRequest(strategy="query", limit=1, candidate_ids=("C1",)),
        output,
    )
    item = _report_data(output)["items"][0]

    assert item["internal_pair_support"] == [
        {
            "edge_ref": "edge-direct-three-links",
            "left_member_subject_id": "member-1",
            "right_member_subject_id": "member-2",
            "left_sample_label": "sample one",
            "right_sample_label": "sample two",
            "support_kind": "direct",
            "disposition": "eligible",
            "decision": "strong_edge",
            "reason": "strong_edge: score=90",
            "score": 90,
            "rt_raw_delta_sec": 1.25,
            "rt_drift_corrected_delta_sec": 0.75,
        },
        {
            "edge_ref": "edge-review",
            "left_member_subject_id": "member-1",
            "right_member_subject_id": "member-2",
            "left_sample_label": "sample one",
            "right_sample_label": "sample two",
            "support_kind": "review_only",
            "disposition": "review_only",
            "decision": "weak_edge",
            "reason": "weak_edge: score unavailable",
            "score": None,
            "rt_raw_delta_sec": None,
            "rt_drift_corrected_delta_sec": None,
        },
    ]
    assert item["review_summary"]["related_group_count"] == 1
    assert item["related_groups"][0]["edge_refs"] == ["edge-external-excluded"]


def test_initial_edge_peer_join_preserves_shared_candidate_multirow_identity(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            (
                "C2",
                "S1",
                json.dumps(
                    {
                        "candidate_id": "C2",
                        "canonical_sample_identity_id": "S1",
                        "precursor_mz": 501.0,
                    }
                ),
            ),
        )
        for candidate_id, group_id, row_id in (
            ("C2", "G2", "row-02"),
            ("C1", "G4", "row-04"),
        ):
            for kind, ref, facts in (
                (
                    "rows",
                    row_id,
                    {"row_key_id": row_id, "group_hypothesis_id": group_id},
                ),
                (
                    "final_groups",
                    group_id,
                    {"group_hypothesis_id": group_id, "disposition": "completed"},
                ),
            ):
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?)",
                    (kind, ref, json.dumps(facts)),
                )
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (candidate_id, "S1", kind, ref),
                )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "initial_edges",
                "edge-shared-multirow",
                json.dumps(
                    {
                        "disposition": "excluded",
                        "initial_evidence": {"decision": "split_edge"},
                    }
                ),
            ),
        )
        for candidate_id in ("C1", "C2"):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (candidate_id, "S1", "initial_edges", "edge-shared-multirow"),
            )

    output = tmp_path / "shared-multirow-peer.html"
    result = generate_report(
        report,
        SelectionRequest(strategy="query", limit=10, candidate_ids=("C1",)),
        output,
    )
    items = _report_data(output)["items"]

    assert result.selected_count == 2
    assert {item["details"]["source"]["group_hypothesis_id"] for item in items} == {
        "G1",
        "G4",
    }
    for item in items:
        assert item["review_summary"]["related_group_count"] == 1
        assert [peer["group_id"] for peer in item["related_groups"]] == ["G2"]
        assert item["related_groups"][0]["edge_refs"] == ["edge-shared-multirow"]


def test_curve_projects_declared_same_sample_rt_calibration_without_extrapolation(
    tmp_path,
):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        summary = json.loads(
            connection.execute(
                "SELECT payload FROM metadata WHERE key='summary'"
            ).fetchone()[0]
        )
        summary["known_istd_reference"] = {
            "calibration": {
                "schema_version": "known_istd_calibration_v1",
                "rt_unit": "minute",
                "interpolation": "piecewise_linear",
                "extrapolation": "unavailable",
                "samples": [
                    {
                        "sample_stem": "s1",
                        "knots": [[0.8, 1.6], [1.2, 2.4]],
                        "domain_rt_min": [0.8, 1.2],
                        "status": "available",
                    }
                ],
            }
        }
        connection.execute(
            "UPDATE metadata SET payload=? WHERE key='summary'",
            (json.dumps(summary),),
        )

    output = tmp_path / "aligned.html"
    generate_report(report, SelectionRequest(), output)
    curve = _report_data(output)["items"][0]["curves"][0]

    assert curve["raw_rts"] == [0.8, 0.9, 1.0, 1.1, 1.2]
    assert curve["aligned_rts"] == pytest.approx([1.6, 1.8, 2.0, 2.2, 2.4])
    assert curve["aligned_bound_left_rt"] == pytest.approx(1.8)
    assert curve["aligned_bound_right_rt"] == pytest.approx(2.2)
    assert curve["aligned_apex_rt"] == pytest.approx(2.0)

    with sqlite3.connect(report) as connection:
        summary["known_istd_reference"]["calibration"]["samples"][0].update(
            knots=[[0.9, 1.8], [1.1, 2.2]], domain_rt_min=[0.9, 1.1]
        )
        connection.execute(
            "UPDATE metadata SET payload=? WHERE key='summary'",
            (json.dumps(summary),),
        )
    outside = tmp_path / "outside.html"
    generate_report(report, SelectionRequest(), outside)
    outside_curve = _report_data(outside)["items"][0]["curves"][0]
    assert "aligned_rts" not in outside_curve


def test_declared_malformed_rt_calibration_is_rejected(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        summary = json.loads(
            connection.execute(
                "SELECT payload FROM metadata WHERE key='summary'"
            ).fetchone()[0]
        )
        summary["known_istd_reference"] = {
            "calibration": {
                "schema_version": "known_istd_calibration_v1",
                "rt_unit": "minute",
                "interpolation": "piecewise_linear",
                "extrapolation": "unavailable",
                "samples": [
                    {
                        "sample_stem": "s1",
                        "knots": [[0.8, 1.6], [0.7, 2.4]],
                        "domain_rt_min": [0.8, 0.7],
                        "status": "available",
                    }
                ],
            }
        }
        connection.execute(
            "UPDATE metadata SET payload=? WHERE key='summary'",
            (json.dumps(summary),),
        )

    with pytest.raises(InspectorError) as caught:
        generate_report(report, SelectionRequest(), tmp_path / "invalid.html")
    assert caught.value.code == 4


def test_readable_evidence_preserves_exact_record_curve_and_decision_bindings(
    tmp_path,
):
    report, _, _ = _create_mock_v2_db(tmp_path)
    output = tmp_path / "readable.html"
    generate_report(report, SelectionRequest(), output)

    item = _report_data(output)["items"][0]
    records = item["details"]["records"]
    assert next(r for r in records if r["ref"] == "cd-01")["facts"] == {
        "row_key_id": "row-01",
        "sample_id": "S1",
        "quantitative_value": 1000.0,
    }

    readable = {record["ref"]: record for record in item["details"]["readable_records"]}
    decision = readable["cd-01"]
    assert decision["sample_ids"] == ["S1"]
    assert decision["candidate_ids"] == ["C1"]
    assert decision["links"] == [{"ref": "row-01", "label": "查看結果列"}]
    assert {field["label"]: field["value"] for field in decision["fields"]}[
        "正式定量值"
    ] == "1,000"
    decision_refs = {ref["label"]: ref["value"] for ref in decision["technical_refs"]}
    assert (
        decision_refs.items()
        >= {
            "來源紀錄 ID": "cd-01",
            "樣本 ID": "S1",
            "結果列 ID": "row-01",
        }.items()
    )

    curve = item["curves"][0]
    curve_record = readable[curve["evidence_ref"]]
    assert curve_record["ref"] == "S1:initial_capture:C1"
    assert curve_record["sample_ids"] == ["S1"]
    assert curve_record["candidate_ids"] == ["C1"]
    curve_fields = {field["label"]: field["value"] for field in curve_record["fields"]}
    assert (
        curve_fields.items()
        >= {
            "積分範圍 (min)": "0.900–1.100",
            "積分峰頂 RT (min)": "1.000",
            "原始訊號峰頂 RT (min)": "1.000",
            "當次原始計數面積 (counts·s)": "2,500",
            "時間軸": "原始保留時間 (RT)",
        }.items()
    )
    curve_refs = {ref["label"]: ref["value"] for ref in curve_record["technical_refs"]}
    assert (
        curve_refs.items()
        >= {
            "曲線 evidence ref": "S1:initial_capture:C1",
            "觀察 ID": "obs-01",
            "處理 request ID": "req-01",
            "處理 attempt ID": "att-01",
        }.items()
    )
    assert curve["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]
    assert curve["integration"]["area_raw_counts_seconds"] == 2500.0
    assert curve["provenance"]["processing_request_id"] == "req-01"
    assert item["samples"][0]["decision_refs"] == ["cd-01"]
    assert item["samples"][0]["decision_reason"] == "來源未記錄定量理由。"


def test_fact_summary_and_readable_records_keep_null_zero_and_unknown_distinct(
    tmp_path,
):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='cell_decisions'",
            (
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "sample_id": "S1",
                        "quantitative_value": None,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            ("C2", "S2", json.dumps({"candidate_id": "C2", "precursor_mz": 500.0})),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "cell_decisions",
                "cd-02",
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "sample_id": "S2",
                        "quantitative_value": 0,
                    }
                ),
            ),
        )
        for kind, ref in (
            ("rows", "row-01"),
            ("final_groups", "G1"),
            ("cell_decisions", "cd-02"),
        ):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)", ("C2", "S2", kind, ref)
            )

    output = tmp_path / "null-zero.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}
    assert samples["S1"]["quantitative_value"] is None
    assert samples["S2"]["quantitative_value"] == 0
    assert samples["S1"]["decision_refs"] == ["cd-01"]
    assert samples["S2"]["decision_refs"] == ["cd-02"]
    assert samples["S1"]["decision_reason"] == "來源未記錄定量理由。"
    assert samples["S2"]["decision_reason"] == "來源未記錄定量理由。"
    assert "1 個空值" in item["reason_label"]
    assert "1 個零值" in item["reason_label"]
    assert "1 個未保存曲線" in item["reason_label"]
    assert not any(token in item["reason_label"] for token in ("成功", "一致"))

    readable = {record["ref"]: record for record in item["details"]["readable_records"]}
    assert readable["cd-01"]["candidate_ids"] == ["C1"]
    assert readable["cd-02"]["candidate_ids"] == ["C2"]
    curve_record = readable[item["curves"][0]["evidence_ref"]]
    assert curve_record["candidate_ids"] == ["C1"]
    assert {field["label"]: field["value"] for field in readable["cd-01"]["fields"]}[
        "正式定量值"
    ] == "空值（非零）"
    assert {field["label"]: field["value"] for field in readable["cd-02"]["fields"]}[
        "正式定量值"
    ] == "0"


def test_multiple_decisions_keep_all_exact_refs_without_a_single_value(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "cell_decisions",
                "cd-extra",
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "sample_id": "S1",
                        "quantitative_value": 2000.0,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            ("C1", "S1", "cell_decisions", "cd-extra"),
        )

    output = tmp_path / "multiple.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    sample = item["samples"][0]
    assert "quantitative_value" not in sample
    assert sample["decision_refs"] == ["cd-01", "cd-extra"]
    assert sample["decision_reason"] == "來源保留 2 筆定量決策，未記錄單一理由。"
    assert "有多筆定量決策，未形成單一結果" in item["reason_label"]


def test_structured_external_evidence_refs_remain_technical_without_fake_links(
    tmp_path,
):
    report, _, _ = _create_mock_v2_db(tmp_path)
    external_ref = {
        "content_identity": {
            "artifact_content_id": "xic.artifact-content.v1:content-01",
            "content_sha256": "content-sha-01",
        },
        "evidence_artifact_id": "xic.evidence-artifact.v1:external-01",
        "producer_processing_attempt_id": "xic.processing-attempt.v1:external-att",
        "producer_processing_request_id": "xic.processing-request.v1:external-req",
        "upstream_artifact_ids": [],
    }
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "method_assessments",
                "assessment-01",
                json.dumps(
                    {
                        "method_result": "HOLD",
                        "evidence_refs": [external_ref],
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            ("C1", "S1", "method_assessments", "assessment-01"),
        )

    output = tmp_path / "structured-external-ref.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    readable = {record["ref"]: record for record in item["details"]["readable_records"]}
    assessment = readable["assessment-01"]
    assert assessment["links"] == []
    technical_values = {ref["value"] for ref in assessment["technical_refs"]}
    assert technical_values >= {
        "xic.artifact-content.v1:content-01",
        "xic.evidence-artifact.v1:external-01",
        "xic.processing-attempt.v1:external-att",
        "xic.processing-request.v1:external-req",
        "content-sha-01",
    }


def test_decision_and_curve_link_only_exact_sample_method_and_edge_records(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            ("C2", "S2", json.dumps({"candidate_id": "C2", "precursor_mz": 500.0})),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "cell_decisions",
                "cd-02",
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "group_hypothesis_id": "G1",
                        "canonical_sample_identity_id": "S2",
                        "value": 700.0,
                        "variant": "numeric",
                    }
                ),
            ),
        )
        for kind, ref in (
            ("rows", "row-01"),
            ("final_groups", "G1"),
            ("cell_decisions", "cd-02"),
        ):
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)", ("C2", "S2", kind, ref)
            )
        for ref, sample_id, reason_code in (
            (
                "method-s1",
                "S1",
                "unique_support_within_declared_candidate_universe",
            ),
            ("method-s2", "S2", "another_recorded_reason"),
        ):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "method_assessments",
                    ref,
                    json.dumps(
                        {
                            "canonical_sample_identity_id": sample_id,
                            "method_result": "SUPPORT",
                            "reason_codes": [reason_code],
                            "technical_state": "evaluated",
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (
                    "C1" if sample_id == "S1" else "C2",
                    sample_id,
                    "method_assessments",
                    ref,
                ),
            )
        for ref, links in (
            ("edge-shared", (("C1", "S1"), ("C2", "S2"))),
            ("edge-s2-only", (("C2", "S2"),)),
        ):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "initial_edges",
                    ref,
                    json.dumps(
                        {
                            "disposition": "excluded",
                            "initial_evidence": {
                                "decision": "blocked_edge",
                                "failure_reason": "product_mz_out_of_tolerance",
                                "reason": "blocked: product_mz_out_of_tolerance",
                                "score": 0,
                            },
                        }
                    ),
                ),
            )
            for candidate_id, sample_id in links:
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (candidate_id, sample_id, "initial_edges", ref),
                )

    output = tmp_path / "related-records.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}
    readable = {record["ref"]: record for record in item["details"]["readable_records"]}

    s1_decision = readable[samples["S1"]["decision_refs"][0]]
    s1_link_refs = {link["ref"] for link in s1_decision["links"]}
    assert {"method-s1", "edge-shared"} <= s1_link_refs
    assert {"method-s2", "edge-s2-only"}.isdisjoint(s1_link_refs)
    s2_decision = readable[samples["S2"]["decision_refs"][0]]
    s2_link_refs = {link["ref"] for link in s2_decision["links"]}
    assert {"method-s2", "edge-shared", "edge-s2-only"} <= s2_link_refs
    assert "method-s1" not in s2_link_refs

    curve_ref = item["curves"][0]["evidence_ref"]
    curve_links = {link["ref"] for link in readable[curve_ref]["links"]}
    assert {"method-s1", "edge-shared"} <= curve_links
    assert {"method-s2", "edge-s2-only"}.isdisjoint(curve_links)

    assert "SUPPORT" in readable["method-s1"]["summary"]
    assert "在已宣告候選範圍內具有唯一支持" in readable["method-s1"]["summary"]
    assert "不代表正式定量採用" in readable["method-s1"]["summary"]
    assert "產品離子 m/z 超出容許範圍" in readable["edge-shared"]["summary"]
    assert "score 0" in readable["edge-shared"]["summary"]
    assert "不代表正式定量採用" in readable["edge-shared"]["summary"]


def test_excluded_candidate_exposes_exact_exclusion_without_curve(tmp_path):
    report, _, _ = _create_mock_v2_db(tmp_path)
    with sqlite3.connect(report) as connection:
        for candidate_id in ("C2", "C3"):
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                (
                    candidate_id,
                    "S2",
                    json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "canonical_sample_identity_id": "S2",
                            "precursor_mz": 510.0,
                            "discovery_candidate_state": (
                                "review_only_ambiguous_coisolation"
                            ),
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "exclusions",
                    f"exclusion-{candidate_id.lower()}",
                    json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "canonical_sample_identity_id": "S2",
                            "discovery_candidate_state": (
                                "review_only_ambiguous_coisolation"
                            ),
                            "reason_code": ("discovery_candidate_not_search_eligible"),
                            "assessment_artifact_ids": [],
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO links VALUES (?, ?, ?, ?)",
                (
                    candidate_id,
                    "S2",
                    "exclusions",
                    f"exclusion-{candidate_id.lower()}",
                ),
            )

    output = tmp_path / "excluded-candidate.html"
    generate_report(report, SelectionRequest(), output)
    items = _report_data(output)["items"]
    candidates = {
        item["witnesses"][0]["candidate_id"]: item
        for item in items
        if item["kind"] == "candidate"
    }

    for candidate_id, other_id in (("C2", "C3"), ("C3", "C2")):
        item = candidates[candidate_id]
        sample = item["samples"][0]
        expected_ref = f"exclusion-{candidate_id.lower()}"
        assert item["stratum"] == "excluded"
        assert item["curves"] == []
        assert sample["evidence_status"] == "unavailable"
        assert "quantitative_value" not in sample
        assert sample["decision_refs"] == [expected_ref]
        assert f"exclusion-{other_id.lower()}" not in sample["decision_refs"]
        readable = {
            record["ref"]: record for record in item["details"]["readable_records"]
        }
        exclusion = readable[expected_ref]
        assert "Discovery 候選未取得後續搜尋資格" in exclusion["summary"]
        assert "不是正式定量採用決策" in exclusion["summary"]
        fields = {field["label"]: field["value"] for field in exclusion["fields"]}
        assert fields["候選狀態"] == "review_only_ambiguous_coisolation"
        assert "discovery_candidate_not_search_eligible" in fields["排除理由"]

    quantitative_item = next(item for item in items if item["kind"] == "row")
    assert quantitative_item["samples"][0]["decision_refs"] == ["cd-01"]


def test_readable_numeric_formatting_keeps_scientific_payload_precision(tmp_path):
    import zlib

    report, _, _ = _create_mock_v2_db(tmp_path)
    precursor_mz = 307.1023254394531
    apex_rt = 17.2623
    area = 28578.925063705414
    with sqlite3.connect(report) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (
                "method_subjects",
                "format-01",
                json.dumps(
                    {
                        "precursor_mz": precursor_mz,
                        "rt_apex_min": apex_rt,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            ("C1", "S1", "method_subjects", "format-01"),
        )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='cell_decisions'",
            (
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "sample_id": "S1",
                        "quantitative_value": area,
                    }
                ),
            ),
        )
        payload_id, blob = connection.execute(
            "SELECT payload_id,payload FROM curve_payloads"
        ).fetchone()
        payload = json.loads(zlib.decompress(blob))
        payload["integration"]["apex_rt_min"] = apex_rt
        payload["integration"]["raw_apex_rt_min"] = apex_rt
        payload["integration"]["area_raw_counts_seconds"] = area
        connection.execute(
            "UPDATE curve_payloads SET payload=? WHERE payload_id=?",
            (zlib.compress(json.dumps(payload).encode()), payload_id),
        )

    output = tmp_path / "formatted-values.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    readable = {record["ref"]: record for record in item["details"]["readable_records"]}
    method_fields = {
        field["label"]: field["value"] for field in readable["format-01"]["fields"]
    }
    decision_fields = {
        field["label"]: field["value"] for field in readable["cd-01"]["fields"]
    }
    curve = item["curves"][0]
    curve_fields = {
        field["label"]: field["value"]
        for field in readable[curve["evidence_ref"]]["fields"]
    }
    assert method_fields["前驅離子 m/z"] == "307.1023"
    assert method_fields["峰頂 RT (min)"] == "17.262"
    assert decision_fields["正式定量值"] == "28,578.9251"
    assert curve_fields["積分峰頂 RT (min)"] == "17.262"
    assert curve_fields["原始訊號峰頂 RT (min)"] == "17.262"
    assert curve_fields["當次原始計數面積 (counts·s)"] == "28,578.9251"

    method_record = next(
        record for record in item["details"]["records"] if record["ref"] == "format-01"
    )
    assert method_record["facts"]["precursor_mz"] == precursor_mz
    assert method_record["facts"]["rt_apex_min"] == apex_rt
    assert item["details"]["cells"][0]["quantitative_value"] == area
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
    report, _, _ = _create_mock_v2_db(tmp_path)
    source_code = "trapezoid_counts_seconds_on_canonical_signal_v1"
    with sqlite3.connect(report) as connection:
        row = {
            "row_key_id": "row-01",
            "group_hypothesis_id": "G1",
            "route": {"destination": destination, "reason": state},
        }
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='rows'", (json.dumps(row),)
        )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='final_groups'",
            (json.dumps({"group_hypothesis_id": "G1", "disposition": state}),),
        )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='cell_decisions'",
            (
                json.dumps(
                    {
                        "row_key_id": "row-01",
                        "sample_id": "S1",
                        "quantitative_value": 1000.0,
                        "variant": "numeric",
                        "source": source_code,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            ("method_subjects", "method-01", json.dumps({"neutral_loss_da": 116.0474})),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?)",
            ("C1", "S1", "method_subjects", "method-01"),
        )
        if sample_count == 2:
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?)",
                ("C2", "S2", json.dumps({"candidate_id": "C2", "precursor_mz": 500.0})),
            )
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?)",
                (
                    "cell_decisions",
                    "cd-02",
                    json.dumps(
                        {
                            "row_key_id": "row-01",
                            "sample_id": "S2",
                            "quantitative_value": 700.0,
                            "variant": "numeric",
                            "source": source_code,
                        }
                    ),
                ),
            )
            for kind, rid in (
                ("rows", "row-01"),
                ("final_groups", "G1"),
                ("cell_decisions", "cd-02"),
            ):
                connection.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)", ("C2", "S2", kind, rid)
                )
    output = tmp_path / "labels.html"
    generate_report(report, SelectionRequest(), output)
    item = _report_data(output)["items"][0]
    assert len(item["samples"]) == sample_count
    assert ("已建立" if state == "established" else "暫定") in item["title"]
    assert ("定量矩陣" if destination == "matrix" else "封存紀錄") in item[
        "reason_label"
    ]
    assert "定量數值" in item["reason_label"]
    assert item["method_label"] == "NL 116.0474"
    assert all(
        s["status_label"] == "有定量值" and "梯形積分面積" in s["decision_label"]
        for s in item["samples"]
    )
    assert item["curves"][0]["stage_label"] == "首次擷取曲線"
    labels = item["title"] + item["reason_label"] + item["route_label"]
    assert not any(
        token in labels
        for token in ("row-01", "G1", "一致", "成功", "有效", "信心", "原始理由")
    )
    assert item["reason"] == state and item["route"] == row["route"]
    assert item["samples"][0]["decision_reason"] == (
        "來源記錄定量方式為當次採用訊號的梯形積分；未另記理由。"
    )
    assert item["curves"][0]["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]
