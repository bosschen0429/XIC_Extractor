"""Tests for XIC Inspector HTML generation, safety escaping, and 50 MiB hard cap."""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.xic_inspector_html_fixtures import (
    _evaluate_report_functions,
    build_origin_curve_payload,
)
from xic_inspector.rendering.html_builder import (
    MAX_HTML_SIZE_BYTES,
    build_report_html,
    serialize_safe_inline_json,
    write_report_atomically,
)


@pytest.fixture
def origin_curve_payload() -> dict:
    return build_origin_curve_payload()


def test_support_origin_styles_fixture_curves_in_both_views(
    origin_curve_payload: dict,
) -> None:
    report = build_report_html(
        title="Origin curves",
        evidence_capability="retained_curves",
        strategy="sample",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="fixture.sqlite",
        payload_data=origin_curve_payload,
    )
    rendered = _evaluate_report_functions(
        report,
        origin_curve_payload,
        "return JSON.stringify({plot:plot(items[0],'raw'),"
        "legend:legend(items[0]),lanes:relationshipLanes(items[0],relationshipModel(items[0]))});",
    )

    chart = ET.fromstring(rendered["plot"])
    plotted = {
        line.attrib["data-curve-ref"]: line
        for line in chart.findall("{http://www.w3.org/2000/svg}polyline")
    }
    assert len(plotted) == 3
    assert plotted["curve-2"].attrib["stroke"] == "var(--subtle)"
    assert plotted["curve-2"].attrib["stroke-width"] == "1"
    for i, (origin, dash) in enumerate(
        zip(
            ("discovery_support", "backfill_support", "unavailable"),
            ("", "6 4", ""),
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
    title = '<img src=x onerror="alert(2)">\\__REQUEST_ID__\n'
    report = build_report_html(
        title=title,
        evidence_capability="metadata_only",
        strategy="query",
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
    markup = _evaluate_report_functions(
        report, payload, "return JSON.stringify(root.innerHTML);"
    )
    assert "&lt;img" in markup
    assert "\\__REQUEST_ID__\n" in markup


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
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="generic.sqlite",
        payload_data=payload,
    )

    markup = _evaluate_report_functions(
        report, payload, "return JSON.stringify(root.innerHTML);"
    )
    assert "Generic report" in markup
    assert 'data-source-name="generic.sqlite"' in markup
    assert 'for="xic-inspector-1-search"' in markup
    for required_id in (
        "active-filters",
        "record-list",
        "workspace-content",
        "zoom-dialog",
        "report-info-dialog",
    ):
        assert f'data-inspector-id="{required_id}"' in markup
        assert f'id="xic-inspector-1-{required_id}"' in markup
    assert "對齊時間圖不可用" in report
    assert "counts·s" in report
    assert "https://" not in report


def test_report_offers_sample_lanes_without_a_pair_graph() -> None:
    payload = {
        "items": [
            {
                "id": "group-1",
                "samples": [],
                "details": {"readable_records": [{"ref": "record-1"}]},
            }
        ],
        "samples": [],
    }
    report = build_report_html(
        title="Sample lanes report",
        evidence_capability="metadata_only",
        strategy="sample",
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
    assert "data-lane-capture" in report
    for label in ("relationship-network", "data-relation-node", "data-relation-pair"):
        assert label not in report


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
        title="Relationships",
        evidence_capability="retained_curves",
        strategy="sample",
        run_total=1,
        eligible_count=1,
        selected_count=1,
        source_db_name="source.json",
        payload_data=payload,
    )
    return _evaluate_report_functions(report, payload, expression)


def test_sample_relationships_show_saved_curves_without_pair_claims(
    origin_curve_payload,
):
    rendered = _relationship_render(
        origin_curve_payload,
        "const panel=sampleRelationshipPanel(items[0]);"
        "const plotted=plot(items[0],'raw');items[0].curves=[];"
        "return JSON.stringify({panel,plot:plotted,"
        "empty:sampleRelationshipPanel(items[0])});",
    )
    assert rendered["panel"].count("data-lane-member=") == 3
    assert rendered["panel"].count('class="lane-raw"') == 3
    assert "配對" not in rendered["panel"]
    assert "來源未提供此樣本的精確支持曲線" not in rendered["panel"]
    assert rendered["plot"].count("data-curve-ref=") == 3
    assert rendered["empty"] == ""


def test_unrecorded_origins_and_missing_alignment_keep_curves_readable(
    origin_curve_payload,
):
    for row in origin_curve_payload["items"][0]["samples"]:
        row["support_origin"] = {"kind": "unavailable"}
    rendered = _relationship_render(
        origin_curve_payload,
        "return JSON.stringify({plot:plot(items[0],'raw'),"
        "aligned:plotCard(items[0],'aligned','平滑訊號'),"
        'field:fieldValue(\'{"gate":"accepted","ratio":0.5}\')});',
    )
    chart = ET.fromstring(rendered["plot"])
    dashes = [
        line.attrib["stroke-dasharray"]
        for line in chart.findall("{http://www.w3.org/2000/svg}polyline")
    ]
    # Old numeric cells with no recorded origin must not masquerade as detect;
    # the explicitly empty sample still uses its thin solid line.
    assert dashes == ["1 5", "1 5", ""]
    assert 'id="plot-aligned"' in rendered["aligned"]
    assert "對齊時間圖不可用" in rendered["aligned"]
    assert "<svg" not in rendered["aligned"]
    assert "<dt>gate</dt><dd>accepted</dd>" in rendered["field"]
    assert "{" not in rendered["field"]


def test_member_lanes_keep_exact_capture_scope(origin_curve_payload):
    item = origin_curve_payload["items"][0]
    item["relationship_members"] = [
        {
            "subject_id": f"M{i}",
            "sample_id": f"S{i}",
            "sample_label": f"Sample {i}",
        }
        for i in range(3)
    ]
    item["curves"].append({**item["curves"][0], "evidence_ref": "unbound-curve"})
    rendered = _relationship_render(
        origin_curve_payload,
        "const model=relationshipModel(items[0]);"
        "return JSON.stringify({lanes:relationshipLanes(items[0],model),"
        "refs:model.nodes.find(n=>n.id==='M0').curves.map(c=>c.evidence_ref)});",
    )
    for member in ("M0", "M1", "M2"):
        assert f'data-lane-member="{member}"' in rendered["lanes"]
    assert rendered["refs"] == ["curve-0"]
