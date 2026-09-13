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
