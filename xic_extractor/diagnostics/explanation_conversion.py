"""Reduce an existing completed full report without acquisition or alignment."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationReportWriter,
    _object,
    _read_completed_summary,
)
from xic_extractor.diagnostics.explanation_review import (
    REVIEW_SCHEMA_VERSION,
    review_record,
)


def compact_explanation(
    source: Path,
    destination: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Copy useful facts into review storage, preserving the source artifact."""
    started = perf_counter()
    if source.resolve() == destination.resolve():
        raise ExplanationError("Compact output must differ from the source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        summary = _read_completed_summary(connection)
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
            raise ExplanationError("Source is already a compact review")
        samples = {
            sample["sample_key"]: index
            for index, sample in enumerate(summary["samples"])
        }
        matrix = Path(summary["matrix_path"])
        if not matrix.is_file():
            matrix = source.parent / matrix.name
        if not matrix.is_file():
            raise ExplanationError("The bound Matrix must be present beside the source")
        with matrix.open("rb") as handle:
            if (
                hashlib.file_digest(handle, "sha256").hexdigest()
                != summary["matrix_sha256"]
            ):
                raise ExplanationError("The bound Matrix hash differs from the report")
        with ExplanationReportWriter(destination, review_only=True) as writer:
            if progress:
                progress("Copying candidate and group review facts")
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            ):
                writer.add_candidate(cid, sid, _object(payload))
            excluded = "('cell_observations','cell_decisions','capture_evidence')"
            links = iter(
                connection.execute(
                    "SELECT kind, record_id, candidate_id, sample_id FROM links "
                    f"WHERE kind NOT IN {excluded} ORDER BY kind, record_id"
                )
            )
            linked = next(links, None)
            for kind, rid, payload in connection.execute(
                "SELECT kind, record_id, payload FROM records "
                f"WHERE kind NOT IN {excluded} ORDER BY kind, record_id"
            ):
                related = []
                while linked is not None and linked[:2] < (kind, rid):
                    linked = next(links, None)
                while linked is not None and linked[:2] == (kind, rid):
                    related.append((linked[2], linked[3]))
                    linked = next(links, None)
                writer.add_record(
                    kind, rid, review_record(kind, _object(payload)), related
                )
            if progress:
                progress("Packing cell values and reasons by row")
            capture_stages = {
                (sid, oid): stage
                for sid, oid, stage in connection.execute(
                    "SELECT json_extract(payload,'$.sample_id'), "
                    "json_extract(payload,'$.peak_observation_content_id'), "
                    "json_extract(payload,'$.kind') "
                    "FROM records WHERE kind='capture_evidence'"
                )
                if oid is not None
            }
            rows = connection.execute(
                "SELECT d.payload, json_extract(d.payload, '$.observation') "
                "FROM records d "
                "WHERE d.kind='cell_decisions' "
                "ORDER BY json_extract(d.payload,'$.row_key_id'), d.record_id"
            )
            current_row = None
            current_group = ""
            cells = []
            cell_count = 0
            for decision_payload, observation_payload in rows:
                decision = _object(decision_payload)
                observation = _object(observation_payload)
                row_id = decision["row_key_id"]
                if current_row is not None and row_id != current_row:
                    writer.add_row_cells(current_row, current_group, cells, samples)
                    cells = []
                current_row = row_id
                current_group = decision["group_hypothesis_id"]
                effective = (
                    [observation["backfill_cell"]]
                    if observation.get("backfill_cell") is not None
                    else observation.get("predecessor_cells", [])
                )
                contents = [cell["content"] for cell in effective]
                sid = decision["sample_key"]
                observation_ids = sorted(
                    {
                        content["peak_observation_content_id"]
                        for content in contents
                        if content.get("peak_observation_content_id")
                    }
                )
                stages = {
                    capture_stages.get(
                        (sid, content.get("peak_observation_content_id"))
                    )
                    for content in contents
                    if content.get("method_result") == "SUPPORT"
                }
                origin = (
                    "backfill_support"
                    if "backfill_observation" in stages or "method_backfill" in stages
                    else "reacquired_support"
                    if "reacquired_capture" in stages
                    else "discovery_support"
                    if "initial_capture" in stages
                    else "unavailable"
                )
                cells.append(
                    {
                        **decision,
                        "evidence_states": observation.get("evidence_states", []),
                        "reason_codes": sorted(
                            {
                                reason
                                for content in contents
                                for reason in content.get("reason_codes", [])
                            }
                        ),
                        "support_origin": origin,
                        "observation_ids": observation_ids,
                    }
                )
                cell_count += 1
                if progress and cell_count % 100000 == 0:
                    progress(f"Packed {cell_count} cells")
            if current_row is not None:
                writer.add_row_cells(current_row, current_group, cells, samples)
            summary.update(
                matrix_path=str(matrix.resolve()),
                retention="review",
                selection_evidence_capability="recorded_decisions",
                curve_retention="none",
                curve_mz_ranges=[],
                record_counts=dict(writer.record_counts),
            )
            writer.finalize(summary)
        return {
            "source_bytes": source.stat().st_size,
            "review_bytes": destination.stat().st_size,
            "candidate_count": summary["candidate_count"],
            "cell_count": cell_count,
            "elapsed_seconds": round(perf_counter() - started, 3),
            "output": str(destination.resolve()),
        }
    finally:
        connection.close()
