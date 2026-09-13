"""HTML report assembly and atomic file writing for XIC Inspector."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from xic_inspector.rendering.template import HTML_TEMPLATE

MAX_HTML_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB hard cap


def serialize_safe_inline_json(data: Any) -> str:
    """Serialize data to JSON, strictly escaping </script> tags."""
    raw_json = json.dumps(data, ensure_ascii=False, indent=None)
    # Prevent breaking </script> tags in inline HTML
    escaped = raw_json.replace("</script>", r"\u003c/script\>")
    return escaped.replace("</Script>", r"\u003c/Script\>")


def build_report_html(
    *,
    title: str,
    evidence_capability: str,
    strategy: str,
    request_id: str,
    run_total: int,
    eligible_count: int,
    selected_count: int,
    source_db_name: str,
    payload_data: dict[str, Any],
) -> str:
    """Build single-file self-contained HTML report with embedded data."""
    cap_badge = "ok" if evidence_capability == "retained_curves_v1" else "null"
    data_json = serialize_safe_inline_json(payload_data)

    content = HTML_TEMPLATE
    content = content.replace("__REPORT_TITLE__", html.escape(title))
    content = content.replace("__CAPABILITY_BADGE__", cap_badge)
    content = content.replace(
        "__EVIDENCE_CAPABILITY__", html.escape(evidence_capability)
    )
    content = content.replace("__STRATEGY__", html.escape(strategy))
    content = content.replace("__REQUEST_ID__", html.escape(request_id))
    content = content.replace("__RUN_TOTAL__", str(run_total))
    content = content.replace("__ELIGIBLE_COUNT__", str(eligible_count))
    content = content.replace("__SELECTED_COUNT__", str(selected_count))
    content = content.replace("__SOURCE_DB_NAME__", html.escape(source_db_name))
    content = content.replace("__ITEM_COUNT__", str(selected_count))
    content = content.replace("__INSPECTOR_DATA_JSON__", data_json)

    return content


def write_report_atomically(
    html_content: str,
    output_path: Path,
) -> int:
    """Write HTML report atomically and verify against 50 MiB limit.

    Returns:
        The total byte size of the written file.

    Raises:
        ValueError: If size exceeds 50 MiB limit or write fails.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    encoded_bytes = html_content.encode("utf-8")
    byte_size = len(encoded_bytes)

    if byte_size > MAX_HTML_SIZE_BYTES:
        raise ValueError(
            f"Report size {byte_size} bytes exceeds 50 MiB limit "
            f"({MAX_HTML_SIZE_BYTES} bytes)"
        )

    temp_path = output_path.with_name(f"{output_path.name}.tmp.{os.getpid()}")
    try:
        temp_path.write_bytes(encoded_bytes)
        os.replace(temp_path, output_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

    return byte_size
