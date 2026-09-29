"""Tests for XIC Inspector HTML generation, safety escaping, and 50 MiB hard cap."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from xic_inspector.rendering.html_builder import (
    MAX_HTML_SIZE_BYTES,
    build_report_html,
    serialize_safe_inline_json,
    write_report_atomically,
)


@pytest.fixture
def origin_curve_payload() -> dict:
    kinds = ["discovery_support", "reacquired_support", "backfill_support"]
    labels = ["初始探索支持", "重新擷取支持", "Backfill（回填）支持"]
    samples = [{"id": f"S{i}", "label": f"Sample {i}"} for i in range(3)]
    rows = [
        {
            "sample_id": f"S{i}",
            "support_origin": {
                "kind": kind,
                "label": labels[i],
                "capture_refs": [f"curve-{i}"],
            },
        }
        for i, kind in enumerate(kinds)
    ]
    curves = [
        {
            "sample_id": f"S{i}",
            "evidence_ref": f"curve-{i}",
            "raw_rts": [1.0, 1.1, 1.2],
            "raw_intensities": [0.0, 10.0 + i, 0.0],
            "processed_curves": [
                {"kind": "smoothed", "intensities": [0.0, 8.0 + i, 0.0]}
            ],
        }
        for i in range(3)
    ]
    return {
        "samples": samples,
        "items": [{"id": "group", "kind": "group", "samples": rows, "curves": curves}],
    }


def test_support_origin_styles_fixture_curves_in_both_views(
    origin_curve_payload: dict,
) -> None:
    report = build_report_html(
        title="Origin curves",
        evidence_capability="retained_curves",
        strategy="sample",
        request_id="origin",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="fixture.sqlite",
        payload_data=origin_curve_payload,
    )
    script = re.search(r"<script>(.*?)</script>", report, re.S)
    assert script is not None
    # Exercise the report's actual JavaScript with its embedded fixture. Stop before
    # DOM event registration; rendering functions need only the report data.
    prefix = script[1].split("  $('record-list').addEventListener", 1)[0]
    assert prefix != script[1]
    data = json.dumps(origin_curve_payload, ensure_ascii=False)
    document = (
        "var document={getElementById:id=>id==='inspector-data'"
        f"?{{textContent:{json.dumps(data)}}}:{{dataset:{{strategy:'sample'}}}}}};"
    )
    js = (
        document
        + "console.log("
        + prefix
        + "return JSON.stringify({plot:plot(items[0],'raw'),"
        "legend:legend(items[0]),lanes:relationshipLanes(items[0],relationshipModel(items[0]))});})());"
    )
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the embedded JavaScript"
    result = subprocess.run(
        [node],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rendered = json.loads(result.stdout)

    chart = ET.fromstring(rendered["plot"])
    plotted = {
        line.attrib["data-curve-ref"]: line
        for line in chart.findall("{http://www.w3.org/2000/svg}polyline")
    }
    assert len(plotted) == 3
    for i, (origin, dash) in enumerate(
        zip(
            ("discovery_support", "reacquired_support", "backfill_support"),
            ("", "8 4", "2 4"),
            strict=True,
        )
    ):
        line = plotted[f"curve-{i}"]
        assert line.attrib["data-support-origin"] == origin
        assert line.attrib["stroke-dasharray"] == dash
        assert len(line.attrib["points"].split()) == 3
        assert f"Sample {i} · " in rendered["legend"]
        assert f'data-support-origin="{origin}"' in rendered["lanes"]
        assert f'stroke-dasharray="{dash}"' in rendered["lanes"]


def test_safe_inline_json_escaping() -> None:
    """Verify that strings such as </script> are escaped to prevent breakout."""
    hostile = {"reason": "Test </ScRiPt><script>alert('xss')</script> end & < >"}
    serialized = serialize_safe_inline_json(hostile)
    assert "</script>" not in serialized
    assert "<" not in serialized
    assert json.loads(serialized) == hostile


def test_hostile_fields_stay_data_in_portable_report() -> None:
    payload = {"items": [{"id": "</sCrIpT><script>alert(1)</script>"}], "samples": []}
    report = build_report_html(
        title='<img src=x onerror="alert(2)">',
        evidence_capability="metadata_only",
        strategy="query",
        request_id="r",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="source.sqlite",
        payload_data=payload,
    )
    inline = re.search(
        r'<script id="inspector-data" type="application/json">(.*?)</script>',
        report,
        re.S,
    )
    assert inline is not None
    assert json.loads(inline[1]) == payload
    assert "<img src=x" not in report
    assert len(re.findall(r"<script(?:\s|>)", report)) == 2
    assert not re.search(r"<(?:script|img|link)\b[^>]*(?:src|href)=", report)


def test_report_exposes_offline_review_workbench_for_generic_payload() -> None:
    """Protect the stable review entry points without requiring method data."""
    payload = {
        "items": [
            {
                "id": "item-1",
                "title": "Recorded result",
                "samples": [],
                "details": {"records": []},
            }
        ],
        "samples": [],
    }
    report = build_report_html(
        title="Generic report",
        evidence_capability="metadata_only",
        strategy="sample",
        request_id="generic",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="generic.sqlite",
        payload_data=payload,
    )

    for required_id in (
        "active-filters",
        "record-list",
        "workspace-content",
        "zoom-dialog",
        "report-info-dialog",
    ):
        assert f'id="{required_id}"' in report
    assert "本報告未提供可讀來源紀錄" in report
    assert "對齊時間圖不可用" in report
    assert "counts·s" in report
    assert "https://" not in report


def test_report_exposes_pair_support_distinctions_and_candidate_record_scope() -> None:
    payload = {
        "items": [
            {
                "id": "group-1",
                "samples": [],
                "internal_pair_support": [
                    {
                        "edge_ref": "edge-direct",
                        "left_member_subject_id": "member-a",
                        "right_member_subject_id": "member-b",
                        "left_sample_label": "Sample A",
                        "right_sample_label": "Sample B",
                        "support_kind": "direct",
                        "reason": "eligible edge",
                    },
                    {
                        "edge_ref": "edge-review",
                        "left_member_subject_id": "member-a",
                        "right_member_subject_id": "member-c",
                        "left_sample_label": "Sample A",
                        "right_sample_label": "Sample C",
                        "support_kind": "review_only",
                        "reason": "retained for review",
                    },
                ],
                "details": {"readable_records": [{"ref": "edge-direct"}]},
            }
        ],
        "samples": [],
    }
    report = build_report_html(
        title="Pair support report",
        evidence_capability="metadata_only",
        strategy="sample",
        request_id="pair-support",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="generic.sqlite",
        payload_data=payload,
    )

    inline = re.search(
        r'<script id="inspector-data" type="application/json">(.*?)</script>',
        report,
        re.S,
    )
    assert inline is not None
    assert json.loads(inline[1]) == payload
    for label in (
        "relationship-network",
        "data-relation-node",
        "data-relation-pair",
        "data-lane-capture",
    ):
        assert label in report


def test_record_scope_does_not_become_a_candidate_adoption_claim() -> None:
    """Candidate links scope a record; they do not identify an adopted candidate."""
    payload = {
        "items": [
            {
                "id": "item-1",
                "samples": [
                    {
                        "sample_id": "sample-1",
                        "decision_refs": ["decision-1"],
                    }
                ],
                "witnesses": [
                    {"candidate_id": "candidate-1", "sample_id": "sample-1"},
                    {"candidate_id": "candidate-2", "sample_id": "sample-1"},
                ],
                "details": {
                    "readable_records": [
                        {
                            "ref": "decision-1",
                            "title": "Decision record",
                            "sample_ids": ["sample-1"],
                            "candidate_ids": ["candidate-1", "candidate-2"],
                        }
                    ]
                },
            }
        ],
        "samples": [{"id": "sample-1", "label": "S01"}],
    }
    report = build_report_html(
        title="Scoped reference",
        evidence_capability="metadata_only",
        strategy="sample",
        request_id="scoped",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="generic.sqlite",
        payload_data=payload,
    )

    assert "當次採用" not in report
    assert "目前查看" in report


def test_build_and_write_report_atomically() -> None:
    """Verify atomic write of self-contained HTML report."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "sub" / "report.html"
        html = build_report_html(
            title="測試報告",
            evidence_capability="retained_curves",
            strategy="sample",
            request_id="req-123",
            run_total=100,
            eligible_count=50,
            selected_count=10,
            source_db_name="test.sqlite",
            payload_data={"items": [], "samples": []},
        )
        size = write_report_atomically(html, out)
        assert out.exists()
        assert size > 0
        content = out.read_text(encoding="utf-8")
        assert "測試報告" in content
        assert "retained_curves" in content
        assert "req-123" in content
        # Check no temporary files left
        tmp_files = list(out.parent.glob("*.tmp.*"))
        assert len(tmp_files) == 0


def test_write_report_rejects_over_budget_50mib() -> None:
    """Verify that reports exceeding 50 MiB are rejected and cleaned up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "large.html"
        # Synthetic large content > 50 MiB
        oversized = "A" * (MAX_HTML_SIZE_BYTES + 1024)
        with pytest.raises(ValueError, match="exceeds 50 MiB limit"):
            write_report_atomically(oversized, out)
        assert not out.exists()


def test_cancellation_at_commit_keeps_existing_report(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    out.write_text("previous", encoding="utf-8")

    def cancel() -> None:
        assert list(tmp_path.glob("report.html.tmp.*"))
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        write_report_atomically("new", out, check_cancel=cancel)
    assert out.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob("report.html.tmp.*")) == []


def _relationship_render(payload, expression):
    report = build_report_html(
        title="Relationships", evidence_capability="retained_curves",
        strategy="sample", request_id="relationships", run_total=1,
        eligible_count=1, selected_count=1, source_db_name="source.json",
        payload_data=payload,
    )
    script = re.search(r"<script>(.*?)</script>", report, re.S)
    assert script is not None
    prefix = script[1].split("  $('record-list').addEventListener", 1)[0]
    assert prefix != script[1]
    data = json.dumps(payload, ensure_ascii=False)
    document = (
        "var document={getElementById:id=>id==='inspector-data'"
        f"?{{textContent:{json.dumps(data)}}}:{{dataset:{{strategy:'sample'}}}}}};"
    )
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the embedded JavaScript"
    result = subprocess.run(
        [node], input=document + "console.log(" + prefix + expression + "})());",
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("availability", "explicit_empty", "expected"),
    [
        (None, False, "來源未提供樣本關係資料"),
        (None, True, "來源未提供樣本關係資料"),
        ("unavailable", False, "來源未提供樣本關係資料"),
        ("not_retained", True, "來源未保存樣本配對圖"),
        ("recorded", True, "本群組沒有保存的配對紀錄"),
    ],
)
def test_missing_relationships_do_not_become_zero_support(
    origin_curve_payload, availability, explicit_empty, expected,
):
    item = origin_curve_payload["items"][0]
    if availability is not None:
        item["relationship_availability"] = availability
    if explicit_empty:
        item["internal_pair_support"] = []
    for row in item["samples"]:
        row["support_origin"] = {"kind": "unavailable"}
    rendered = _relationship_render(
        origin_curve_payload,
        "return JSON.stringify({panel:internalPairSupportPanel(items[0]),"
        "plot:plot(items[0],'raw')});",
    )
    assert expected in rendered["panel"]
    assert "不能據此判定沒有配對支持" in rendered["panel"]
    assert "0 對直接支持" not in rendered["panel"]
    assert "relationship-network" not in rendered["panel"]
    assert "來源未提供此樣本的精確支持曲線" not in rendered["panel"]
    assert rendered["plot"].count("data-curve-ref=") == 3


def test_recorded_relationships_keep_pair_and_exact_capture_scope(origin_curve_payload):
    item = origin_curve_payload["items"][0]
    item["relationship_availability"] = "recorded"
    item["relationship_members"] = [
        {"member_subject_id": f"M{i}", "sample_id": f"S{i}",
         "sample_label": f"Sample {i}"} for i in range(3)
    ]
    item["internal_pair_support"] = [{
        "edge_ref": "edge-01", "left_member_subject_id": "M0",
        "right_member_subject_id": "M1", "support_kind": "direct",
    }]
    item["curves"].append({**item["curves"][0], "evidence_ref": "unbound-curve"})
    rendered = _relationship_render(
        origin_curve_payload,
        "const model=relationshipModel(items[0]);"
        "const overview=internalPairSupportPanel(items[0]);rel.pair='edge-01';"
        "return JSON.stringify({overview,lanes:relationshipLanes(items[0],model),"
        "refs:model.byId.get('M0').curves.map(c=>c.evidence_ref)});",
    )
    assert "1 對直接支持，0 對僅供檢閱" in rendered["overview"]
    assert 'data-relation-pair="edge-01"' in rendered["overview"]
    assert 'data-lane-member="M0"' in rendered["lanes"]
    assert 'data-lane-member="M1"' in rendered["lanes"]
    assert 'data-lane-member="M2"' not in rendered["lanes"]
    assert rendered["refs"] == ["curve-0"]


def test_unretained_pair_graph_keeps_saved_support_curves(origin_curve_payload):
    origin_curve_payload["items"][0]["relationship_availability"] = "not_retained"
    panel = _relationship_render(
        origin_curve_payload,
        "return JSON.stringify(internalPairSupportPanel(items[0]));",
    )
    assert "來源未保存樣本配對圖" in panel
    assert "0 對直接支持" not in panel
    assert panel.count('data-lane-member=') == 3
    assert panel.count('class="lane-raw"') == 3
