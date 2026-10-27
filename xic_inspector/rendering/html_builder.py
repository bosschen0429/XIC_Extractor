"""HTML report assembly and atomic file writing for XIC Inspector."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xic_inspector.rendering.template import HTML_TEMPLATE

MAX_HTML_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB hard cap


def serialize_safe_inline_json(data: Any) -> str:
    """Serialize data to JSON, strictly escaping </script> tags."""
    raw_json = json.dumps(data, ensure_ascii=False, indent=None)
    # Escape every '<', including mixed-case HTML end tags, using valid JSON.
    return (
        raw_json.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def build_report_html(
    *,
    title: str,
    evidence_capability: str,
    strategy: str,
    run_total: int,
    eligible_count: int,
    selected_count: int,
    source_db_name: str,
    payload_data: dict[str, Any],
) -> str:
    """Build single-file self-contained HTML report with embedded data."""
    data_json = serialize_safe_inline_json(payload_data)
    metadata_json = serialize_safe_inline_json(
        {
            "title": title,
            "evidence_capability": evidence_capability,
            "strategy": strategy,
            "run_total": run_total,
            "eligible_count": eligible_count,
            "selected_count": selected_count,
            "source_db_name": source_db_name,
        }
    )
    # A single replacement pass keeps user text from being interpreted as a
    # second template token. Payload and host metadata remain separate contracts.
    replacements = {
        "__REPORT_TITLE__": html.escape(title),
        "__EVIDENCE_CAPABILITY__": html.escape(evidence_capability),
        "__SOURCE_DB_NAME__": html.escape(source_db_name),
        "__INSPECTOR_DATA_JSON__": data_json,
        "__INSPECTOR_METADATA_JSON__": metadata_json,
    }
    return re.sub(
        "|".join(map(re.escape, replacements)),
        lambda match: replacements[match.group()],
        HTML_TEMPLATE,
    )


def write_report_atomically(
    html_content: str,
    output_path: Path,
    *,
    check_cancel: Callable[[], None] | None = None,
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

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, prefix=f"{output_path.name}.tmp.", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(encoded_bytes)
        if check_cancel is not None:
            check_cancel()
        os.replace(temp_path, output_path)
    except Exception:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

    return byte_size
