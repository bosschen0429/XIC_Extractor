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
    assert item["samples"][0]["decision_reason"] == source_code
    assert item["curves"][0]["raw_intensities"] == [10.0, 50.0, 1000.0, 40.0, 5.0]
