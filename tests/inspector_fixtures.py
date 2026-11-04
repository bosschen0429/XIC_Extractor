"""Persisted Inspector and review cases, independent of API test modules."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path

from tests.alignment_explanation_fixtures import curve_provenance, explanation_summary


def create_review_batch(tmp_path, sample_count=2):
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    sample_ids = tuple(f"S{i + 1}" for i in range(sample_count))
    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("recorded matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report, review_only=True) as writer:
        for rid, route, values in (
            ("a-complete", "matrix", tuple(100 * (i + 1) for i in range(sample_count))),
            ("b-missing", "matrix", (300,) + ("unavailable",) * (sample_count - 1)),
            ("z-priority", "matrix", (400,) + ("unresolved",) * (sample_count - 1)),
            ("archive", "archive", (500,) + ("unresolved",) * (sample_count - 1)),
        ):
            keys = []
            for sid, value in zip(sample_ids, values, strict=True):
                if isinstance(value, int):
                    cid = f"{rid}-{sid}"
                    keys.append((cid, sid))
                    writer.add_candidate(
                        cid,
                        sid,
                        {
                            "candidate_id": cid,
                            "sample_key": sid,
                            "precursor_mz": value,
                            "rt_apex_min": 1,
                        },
                    )
            writer.add_record(
                "rows",
                rid,
                {
                    "feature_id": rid,
                    "route": {"destination": route, "reason": "recorded"},
                },
                keys,
            )
            writer.add_record("final_groups", rid, {"feature_id": rid}, keys)
            writer.add_row_cells(
                rid,
                [
                    {
                        "sample_key": sid,
                        "variant": "numeric" if isinstance(value, int) else value,
                        "value": value if isinstance(value, int) else None,
                    }
                    for sid, value in zip(sample_ids, values, strict=True)
                ],
                {sid: i for i, sid in enumerate(sample_ids)},
            )
        writer.add_candidate(
            "orphan",
            "S1",
            {
                "candidate_id": "orphan",
                "sample_key": "S1",
                "precursor_mz": 600,
                "rt_apex_min": 1,
            },
        )
        writer.finalize(
            explanation_summary(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [
                        {"sample_key": s, "sample_stem": s} for s in sample_ids
                    ],
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def create_nearest_review_batch(tmp_path):
    """Saved matrix rows whose review order differs from numeric proximity."""
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    matrix = tmp_path / "nearest-matrix.tsv"
    matrix.write_text("saved near-search fixture", encoding="utf-8")
    report = tmp_path / "nearest-review.sqlite"
    rows = [(f"far-{i:02}", ((200.0 + i, 1.0),), False) for i in range(28)] + [
        ("near-a", ((123.3980, None),), False),
        ("near-b", ((123.4200, 5.0),), False),
        ("near-c", ((123.4567, 10.0),), True),
        ("missing-mz", ((None, 1.0),), False),
        ("multi-witness", ((350.0, 2.0), (123.4, 2.5)), False),
    ]
    with ExplanationReportWriter(report, review_only=True) as writer:
        for rid, witnesses, unresolved in rows:
            keys = []
            for index, (mz, rt) in enumerate(witnesses):
                cid = f"{rid}-{index}"
                keys.append((cid, "S1"))
                writer.add_candidate(
                    cid,
                    "S1",
                    {
                        "candidate_id": cid,
                        "sample_key": "S1",
                        "precursor_mz": mz,
                        "rt_apex_min": rt,
                    },
                )
            writer.add_record(
                "rows",
                rid,
                {
                    "feature_id": rid,
                    "route": {"destination": "matrix", "reason": "recorded"},
                },
                keys,
            )
            writer.add_record("final_groups", rid, {"feature_id": rid}, keys)
            writer.add_row_cells(
                rid,
                [
                    {
                        "sample_key": "S1",
                        "variant": "unresolved" if unresolved else "numeric",
                        "value": None if unresolved else 100.0,
                    }
                ],
                {"S1": 0},
            )
        writer.finalize(
            explanation_summary(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [{"sample_key": "S1", "sample_stem": "S1"}],
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def read_report_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', text, re.S
    )
    assert match is not None
    return json.loads(match.group(1))


def link_report_record(
    connection: sqlite3.Connection, row: tuple[str, str, str, str]
) -> None:
    """Insert one flat link through the normalized persisted representation."""
    candidate_id, sample_id, kind, record_id = row
    set_id = connection.execute(
        "SELECT COALESCE(MAX(set_id), 0) + 1 FROM link_members"
    ).fetchone()[0]
    connection.execute(
        "INSERT OR IGNORE INTO link_members SELECT ?,c.id FROM candidates c "
        "JOIN samples s ON s.id=c.sample WHERE c.candidate_id=? AND s.sample_key=?",
        (set_id, candidate_id, sample_id),
    )
    connection.execute(
        "INSERT OR IGNORE INTO record_links "
        "SELECT ?, id FROM records WHERE kind=? AND record_id=?",
        (set_id, kind, record_id),
    )


def create_inspector_report(
    directory: Path,
    *,
    attempt_id: str = "att-01",
    samples: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, str]:
    from xic_extractor.alignment_explanation.writer import ExplanationReportWriter
    from xic_extractor.peak_detection.integration_result import IntegrationResult

    db_path = directory / "alignment_explanation.sqlite"
    matrix_path = directory / "alignment_matrix.tsv"

    matrix_content = (
        b"cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\nrow-01\t500.0\t1.0\t1000\t0\n"
    )
    matrix_path.write_bytes(matrix_content)
    with matrix_path.open("rb") as handle:
        matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()

    if samples is None:
        samples = [
            {"sample_stem": "s1", "sample_key": "S1"},
            {"sample_stem": "s2", "sample_key": "S2"},
        ]

    sid = samples[0]["sample_key"]
    with ExplanationReportWriter(db_path) as writer:
        writer.add_candidate(
            "C1",
            sid,
            {
                "candidate_id": "C1",
                "sample_key": sid,
                "precursor_mz": 500.0,
            },
        )
        writer.add_record(
            "rows",
            "G1",
            {
                "feature_id": "G1",
                "reason": "Passed alignment",
            },
            [("C1", sid)],
        )
        writer.add_record(
            "final_groups",
            "G1",
            {"feature_id": "G1", "disposition": "completed"},
            [("C1", sid)],
        )
        writer.add_record(
            "cell_decisions",
            "cd-01",
            {"feature_id": "G1", "sample_id": sid, "quantitative_value": 1000.0},
            [("C1", sid)],
        )
        writer.add_record(
            "terminal_subjects",
            "terminal-01",
            {"observations": [{"physical_peak_key": "obs-01", "rt_apex_min": 1.0}]},
            [("C1", sid)],
        )
        # Add a curve payload for C1
        curve_envelope = {
            "schema_version": "curve_payload",
            "kind": "initial_capture",
            "sample_id": sid,
            "candidate_id": "C1",
            "physical_peak_key": "obs-01",
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
            "integration": asdict(
                IntegrationResult(
                    rt_left_min=0.9,
                    rt_apex_min=1.0,
                    rt_right_min=1.1,
                    raw_apex_rt_min=1.0,
                    rt_width_min=0.20000000000000007,
                    height_raw=1000.0,
                    height_smoothed=1000.0,
                    area_raw_counts_seconds=2500.0,
                )
            ),
            "provenance": {
                **curve_provenance(),
                "processing_attempt_id": attempt_id,
            },
        }
        writer.add_curve_payload(
            sample_id=sid,
            kind="initial_capture",
            record_id="obs-01",
            payload=curve_envelope,
        )
        writer.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": attempt_id,
                    "matrix_path": str(matrix_path),
                    "matrix_sha256": matrix_hash,
                    "samples": samples,
                },
                review_only=writer.review_only,
            )
        )

    return db_path, matrix_path, matrix_hash
