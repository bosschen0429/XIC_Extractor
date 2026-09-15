"""Tests for XIC Inspector HTML generation, safety escaping, and 50 MiB hard cap."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from xic_inspector.rendering.html_builder import (
    MAX_HTML_SIZE_BYTES,
    build_report_html,
    serialize_safe_inline_json,
    write_report_atomically,
)


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
    assert "來源未提供目前所選曲線與訊號的對齊座標" in report
    assert "Array.isArray(item.internal_pair_support)" in report
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
        "樣本配對支持",
        "relationship-network",
        "依進樣先後，由上而下",
        "data-relation-node",
        "data-relation-pair",
        "data-lane-capture",
        "直接支持",
        "僅供檢閱",
        "這不表示族群內每一對都一致",
        "原始 RT 差",
        "校正 RT 差",
        "分數",
        "查看配對紀錄",
        "不是最終分組決策的完整追蹤",
    ):
        assert label in report
    assert "初始候選比較紀錄" not in report
    assert "相關族群比較" not in report
    assert "已分開保存的相關族群" not in report


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
            evidence_capability="retained_curves_v1",
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
        assert "retained_curves_v1" in content
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
