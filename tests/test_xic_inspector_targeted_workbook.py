"""Saved Targeted facts remain attributable and read-only through Inspector."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from openpyxl import Workbook, load_workbook

from tests.test_xic_workbench import _client
from xic_inspector.api import (
    InspectorError,
    SelectionRequest,
    prepare_report,
    resolve_source,
)
from xic_workbench.workspace import ReviewWorkspace


@pytest.fixture
def workbook(tmp_path):
    path = tmp_path / "targeted.xlsx"
    book = Workbook()
    targets = book.active
    targets.title = "Targets"
    targets.append(["Label", "Role", "ISTD Pair", "m/z", "RT min", "RT max"])
    targets.append(["Analyte", "Analyte", "ISTD", 258.1085, 8, 10])
    targets.append(["ISTD", "ISTD", None, 261.127, 8, 10])
    results = book.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Group",
            "Target",
            "RT",
            "Area",
            "PeakStart",
            "PeakEnd",
            "Review State",
            "Reason",
            "Counted Detection",
        ]
    )
    results.append(
        [
            "Sample A",
            "A",
            "Analyte",
            "ND",
            "ND",
            "ND",
            "ND",
            "review_required",
            "No positive peak",
            "FALSE",
        ]
    )
    results.append([None, None, "ISTD", 8.8, 0, 8.7, 8.9, "recorded", None, "FALSE"])
    results.append(
        [
            "Sample B",
            None,
            "Analyte",
            9.1,
            123.456,
            9.0,
            9.2,
            "review_required",
            '<script>alert("unsafe")</script>',
            "TRUE",
        ]
    )
    results.append([None, None, "ISTD", 9.0, None, 8.9, 9.1, "recorded", None, "FALSE"])
    for cell_range in ("A2:A3", "B2:B3", "A4:A5", "B4:B5"):
        results.merge_cells(cell_range)
    queue = book.create_sheet("Review Queue")
    queue.append(["Sample", "Target", "Status", "Why"])
    queue.append(["Sample A", "Analyte", "Review", "Peak not found"])
    queue.append(["Sample B", "Analyte", "Check", "MS2 trigger missing"])
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    metadata.append(["signal_processing_method", "gaussian"])
    book.save(path)
    book.close()
    return path


def _payload(html):
    return json.loads(
        re.search(
            rb'<script id="inspector-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )[1]
    )


def test_targeted_http_review_preserves_values_scope_and_source(workbook):
    before = hashlib.sha256(workbook.read_bytes()).hexdigest()
    files = set(workbook.parent.iterdir())
    with _client(ReviewWorkspace()) as request:
        status, page, _ = request("/api/open", {"source": str(workbook.parent)})
        assert status == 200
        assert page["source_kind"] == "targeted_workbook"
        assert [item["title"] for item in page["items"]] == ["Analyte", "ISTD"]
        assert all(not item["observed_sample_ids"] for item in page["items"])
        _, detail, _ = request("/api/detail", {"item_id": page["items"][0]["id"]})
        status, html, _ = request(f"/reports/{detail['view_id']}.html?embed=1")
        assert status == 200
        records = _payload(html)["items"][0]["details"]["records"]
        assert [(r["SampleName"], r["Area"], r["Group"]) for r in records] == [
            ("Sample A", "ND", "A"),
            ("Sample B", 123.456, None),
        ]
        assert records[1]["PeakStart"] == 9.0
        assert records[1]["Reason"] == '<script>alert("unsafe")</script>'
        assert b'<script>alert("unsafe")</script>' not in html
        assert ["signal_processing_method", "gaussian"] in _payload(html)["summary"][
            "method_context"
        ]
        _, scoped, _ = request(
            "/api/query",
            {"sample_id": "Sample B", "target_id": "Analyte", "attention_only": True},
        )
        assert (scoped["run_total"], scoped["matched_count"]) == (2, 1)
        assert scoped["samples"] == [{"id": "Sample B", "label": "Sample B"}]
        assert len(scoped["all_samples"]) == 2
        _, detail, _ = request("/api/detail", {"item_id": scoped["items"][0]["id"]})
        _, html, _ = request(f"/reports/{detail['view_id']}.html")
        details = _payload(html)["items"][0]["details"]
        assert [r["SampleName"] for r in details["records"]] == ["Sample B"]
        assert details["review_queue"] == [
            {
                "Sample": "Sample B",
                "Target": "Analyte",
                "Status": "Check",
                "Why": "MS2 trigger missing",
            }
        ]
        _, zero, _ = request("/api/query", {"target_id": "ISTD"})
        _, detail, _ = request("/api/detail", {"item_id": zero["items"][0]["id"]})
        _, html, _ = request(f"/reports/{detail['view_id']}.html")
        assert [
            r["Area"] for r in _payload(html)["items"][0]["details"]["records"]
        ] == [0, None]
        assert (
            request("/api/query", {"attention_only": True, "target_id": "ISTD"})[1][
                "matched_count"
            ]
            == 0
        )
        assert (
            request("/api/query", {"rt_min": 9.05, "rt_max": 9.15})[1]["matched_count"]
            == 1
        )
        assert (
            request("/api/query", {"limit": 1, "offset": 1})[1]["items"][0]["title"]
            == "ISTD"
        )
        assert request("/api/query", {"sample_id": "absent"})[1]["matched_count"] == 0
        invalid = workbook.parent / "invalid.xlsx"
        invalid.write_text("not a workbook")
        assert request("/api/open", {"source": str(invalid)})[0] == 400
        assert request("/api/query", {"target_id": "Analyte"})[1]["matched_count"] == 1
        invalid.unlink()
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == before
    assert set(workbook.parent.iterdir()) == files


@pytest.mark.parametrize(
    "damage", ["sheet", "header", "unmerged_sample", "schema", "queue"]
)
def test_malformed_targeted_workbook_is_actionable(workbook, damage):
    book = load_workbook(workbook)
    if damage == "sheet":
        del book["Targets"]
    elif damage == "header":
        book["XIC Results"]["E1"] = "unrecognized area"
    elif damage == "unmerged_sample":
        book["XIC Results"].unmerge_cells("A2:A3")
    elif damage == "schema":
        book["Run Metadata"]["B2"] = "unsupported"
    else:
        book["Review Queue"]["A2"] = "unknown sample"
    book.save(workbook)
    book.close()
    with pytest.raises(InspectorError, match="Targeted XLSX"):
        prepare_report(workbook, SelectionRequest(strategy="sample"))


def test_workbook_binding_and_ambiguous_directory(workbook):
    report = prepare_report(workbook, SelectionRequest(strategy="attention"))
    assert [item["title"] for item in report.payload["items"]] == ["Analyte"]
    other = workbook.with_name("another.xlsx")
    other.write_bytes(workbook.read_bytes())
    with pytest.raises(ValueError, match="多份 XLSX"):
        resolve_source(workbook.parent)
    book = load_workbook(workbook)
    book["XIC Results"]["E4"] = 999
    book.save(workbook)
    book.close()
    with pytest.raises(InspectorError, match="expected_source"):
        prepare_report(
            workbook, SelectionRequest(strategy="sample"), expected_source=report.source
        )


def test_review_filter_scopes_sample_records_not_just_targets(workbook):
    book = load_workbook(workbook)
    book["Review Queue"].delete_rows(3)
    book.save(workbook)
    book.close()
    report = prepare_report(
        workbook, SelectionRequest(strategy="query", attention_only=True)
    )
    records = report.payload["items"][0]["details"]["records"]
    assert [(row["SampleName"], row["Area"]) for row in records] == [("Sample A", "ND")]
    # Sample B's RT cannot match after the saved-review filter excluded its row.
    report = prepare_report(
        workbook, SelectionRequest(strategy="query", attention_only=True, rt_min=9)
    )
    assert report.matched_count == 0
