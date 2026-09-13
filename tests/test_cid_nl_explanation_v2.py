"""Tests for SQLite v2 schema, curve payloads, and v1 backward compatibility."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from xic_extractor.diagnostics.cid_nl_explanation import (
    CidNlEvidenceLocator,
    ExplanationError,
    ExplanationReportWriter,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)


def test_cid_nl_evidence_locator_immutability() -> None:
    locator = CidNlEvidenceLocator(
        report_path=Path("out/cid_nl_explanation.sqlite"),
        matrix_path=Path("out/alignment_matrix.tsv"),
        matrix_sha256="abcd" * 16,
        processing_request_id="req-01",
        processing_attempt_id="att-01",
        schema_version=2,
    )
    assert locator.schema_version == 2
    assert locator.processing_request_id == "req-01"
    with pytest.raises(AttributeError):
        locator.schema_version = 3  # type: ignore


def test_v2_curve_payload_write_and_read() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_v2.sqlite"
        with ExplanationReportWriter(db_path) as writer:
            writer.add_candidate(
                "C1", "S1", {"candidate_id": "C1", "canonical_sample_identity_id": "S1"}
            )
            curve_envelope = {
                "schema_version": "curve_payload_v1",
                "kind": "initial_capture",
                "sample_id": "S1",
                "candidate_id": "C1",
                "peak_observation_content_id": "obs-01",
                "trace_coordinate": {
                    "mz": 450.1234,
                    "rt_min": 1.0,
                    "rt_max": 2.0,
                    "ppm_tol": 10.0,
                },
                "curves": {
                    "rt": [1.0, 1.2, 1.5, 1.8, 2.0],
                    "raw": [0.0, 100.0, 5000.0, 200.0, 0.0],
                    "baseline": None,
                    "smoothed": None,
                    "positive": None,
                },
                "integration": {
                    "apex_rt_min": 1.5,
                    "raw_apex_rt_min": 1.5,
                    "rt_left_min": 1.1,
                    "rt_right_min": 1.9,
                    "height_raw": 5000.0,
                    "area_raw_counts_seconds": 12000.0,
                    "area_profile_selected": None,
                    "integration_method": "raw_trapezoid",
                    "boundary_source": "candidate_interval",
                },
                "provenance": {"profile_id": None, "trace_content_id": None},
            }
            writer.add_curve_payload(
                payload_id="S1:initial_capture:C1",
                sample_id="S1",
                kind="initial_capture",
                record_id="C1",
                payload=curve_envelope,
            )
            final_path = writer.finalize(
                {
                    "processing_request_id": "req-1",
                    "processing_attempt_id": "att-1",
                    "samples": [
                        {"sample_stem": "s1", "canonical_sample_identity_id": "S1"}
                    ],
                }
            )

        summary = read_cid_nl_explanation(final_path)
        assert summary["schema_version"] == 2
        assert summary["plot_evidence_capability"] == "retained_curves_v1"

        curves = read_cid_nl_curves(final_path, record_ids=["C1"])
        assert len(curves) == 1
        assert curves[0]["candidate_id"] == "C1"
        assert curves[0]["curves"]["raw"] == [0.0, 100.0, 5000.0, 200.0, 0.0]


def test_v2_rejects_undeclared_curve_kind_and_keys() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_reject.sqlite"
        with ExplanationReportWriter(db_path) as writer:
            # Undeclared kind
            with pytest.raises(ExplanationError, match="undeclared curve kind"):
                writer.add_curve_payload(
                    payload_id="p1",
                    sample_id="S1",
                    kind="fraudulent_curve_kind",
                    record_id="C1",
                    payload={"kind": "fraudulent_curve_kind"},
                )

            # Undeclared key
            with pytest.raises(ExplanationError, match="undeclared curve payload keys"):
                writer.add_curve_payload(
                    payload_id="p1",
                    sample_id="S1",
                    kind="initial_capture",
                    record_id="C1",
                    payload={
                        "schema_version": "curve_payload_v1",
                        "kind": "initial_capture",
                        "sample_id": "S1",
                        "extra_fake_field": 123,
                    },
                )


def test_v1_backward_compatibility() -> None:
    """A v1 file is recognized with capability='metadata_only'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        v1_path = Path(tmpdir) / "v1_legacy.sqlite"
        conn = sqlite3.connect(v1_path)
        summary_json = (
            '{"schema_version": 1, "diagnostic_only": true, '
            '"completion": "post_matrix_publication", "samples": []}'
        )
        conn.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, "
            "payload TEXT NOT NULL, PRIMARY KEY(candidate_id, sample_id));"
            "CREATE TABLE records (kind TEXT, record_id TEXT, "
            "payload TEXT NOT NULL, PRIMARY KEY(kind, record_id));"
            "CREATE TABLE links (candidate_id TEXT, sample_id TEXT, kind TEXT, "
            "record_id TEXT, PRIMARY KEY(candidate_id, sample_id, kind, record_id));"
            f"INSERT INTO metadata VALUES ('summary', '{summary_json}');"
            "PRAGMA user_version = 1;"
        )
        conn.commit()
        conn.close()

        summary = read_cid_nl_explanation(v1_path)
        assert summary["schema_version"] == 1
        assert summary["plot_evidence_capability"] == "metadata_only"
