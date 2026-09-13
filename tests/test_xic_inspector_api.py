"""Tests for xic_inspector.api and cli: contracts, error codes, and cancellation."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from xic_extractor.diagnostics.cid_nl_explanation import (
    ExplanationReportWriter,
)
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)
from xic_inspector.cli import main as cli_main


def _create_mock_v2_db(
    directory: Path,
    *,
    request_id: str = "req-01",
    attempt_id: str = "att-01",
    samples: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, str]:
    db_path = directory / "cid_nl_explanation.sqlite"
    matrix_path = directory / "alignment_matrix.tsv"

    matrix_content = (
        b"cluster_id\tprecursor_mz\trt_apex_min\tS1\tS2\n"
        b"row-01\t500.0\t1.0\t1000\t0\n"
    )
    matrix_path.write_bytes(matrix_content)
    with matrix_path.open("rb") as handle:
        matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()

    if samples is None:
        samples = [
            {"sample_stem": "s1", "canonical_sample_identity_id": "S1"},
            {"sample_stem": "s2", "canonical_sample_identity_id": "S2"},
        ]

    with ExplanationReportWriter(db_path) as writer:
        writer.add_candidate(
            "C1",
            "S1",
            {
                "candidate_id": "C1",
                "canonical_sample_identity_id": "S1",
                "precursor_mz": 500.0,
            },
        )
        writer.add_record(
            "rows",
            "row-01",
            {
                "row_key_id": "row-01",
                "group_hypothesis_id": "G1",
                "reason": "Passed alignment",
            },
            [("C1", "S1")],
        )
        writer.add_record(
            "final_groups",
            "G1",
            {"group_hypothesis_id": "G1", "disposition": "completed"},
            [("C1", "S1")],
        )
        writer.add_record(
            "cell_decisions",
            "cd-01",
            {"row_key_id": "row-01", "sample_id": "S1", "quantitative_value": 1000.0},
            [("C1", "S1")],
        )
        # Add a curve payload for C1
        curve_envelope = {
            "schema_version": "curve_payload_v1",
            "kind": "initial_capture",
            "sample_id": "S1",
            "candidate_id": "C1",
            "peak_observation_content_id": "obs-01",
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
            "integration": {
                "apex_rt_min": 1.0,
                "raw_apex_rt_min": 1.0,
                "rt_left_min": 0.9,
                "rt_right_min": 1.1,
                "height_raw": 1000.0,
                "area_raw_counts_seconds": 2500.0,
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
        writer.finalize(
            {
                "processing_request_id": request_id,
                "processing_attempt_id": attempt_id,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "samples": samples,
            }
        )

    return db_path, matrix_path, matrix_hash


def test_generate_report_sample_strategy_success() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, matrix_hash = _create_mock_v2_db(tmp)
        out_html = tmp / "report.html"

        req = SelectionRequest(strategy="sample", limit=10, seed=0)
        progresses = []

        res = generate_report(
            report=db_path,
            selection=req,
            output=out_html,
            on_progress=lambda p: progresses.append(p.stage),
        )

        assert out_html.is_file()
        assert res.output_path == out_html.resolve()
        assert res.evidence_capability == "retained_curves_v1"
        assert res.run_total >= 1
        assert res.selected_count >= 1
        assert res.sample_ids == ("S1", "S2")
        assert "metadata" in progresses
        assert "selection" in progresses
        assert "curves" in progresses
        assert "render" in progresses
        assert "write" in progresses

        content = out_html.read_text(encoding="utf-8")
        assert "XIC Inspector" in content
        assert "retained_curves_v1" in content


def test_error_code_2_invalid_request() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = _create_mock_v2_db(tmp)
        out_html = tmp / "report.html"

        # Invalid limit
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(limit=0), out_html)
        assert exc_info.value.code == 2

        # Invalid offset
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(offset=-1), out_html)
        assert exc_info.value.code == 2

        # Non-query parameters passed to sample
        with pytest.raises(InspectorError) as exc_info:
            generate_report(
                db_path,
                SelectionRequest(strategy="sample", candidate_ids=("C1",)),
                out_html,
            )
        assert exc_info.value.code == 2

        # Query strategy with zero filters
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(strategy="query"), out_html)
        assert exc_info.value.code == 2

        # Empty/whitespace identifier
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(sample_ids=("  ",)), out_html)
        assert exc_info.value.code == 2


def test_error_code_3_source_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        missing_db = tmp / "missing.sqlite"
        out_html = tmp / "report.html"
        with pytest.raises(InspectorError) as exc_info:
            generate_report(missing_db, SelectionRequest(), out_html)
        assert exc_info.value.code == 3


def test_error_code_4_source_invalid_mismatched_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, _ = _create_mock_v2_db(tmp)
        out_html = tmp / "report.html"

        # Tamper with matrix file
        matrix_path.write_text("tampered content", encoding="utf-8")

        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), out_html)
        assert exc_info.value.code == 4


def test_error_code_6_output_aliasing_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, matrix_path, _ = _create_mock_v2_db(tmp)

        # Output aliases source DB
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), db_path)
        assert exc_info.value.code == 6

        # Output aliases matrix
        with pytest.raises(InspectorError) as exc_info:
            generate_report(db_path, SelectionRequest(), matrix_path)
        assert exc_info.value.code == 6


def test_cancellation_preserves_old_html_and_code_130() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = _create_mock_v2_db(tmp)
        out_html = tmp / "existing.html"
        out_html.write_text("Old unmodified HTML content", encoding="utf-8")

        with pytest.raises(InspectorCancelled) as exc_info:
            generate_report(
                db_path,
                SelectionRequest(),
                out_html,
                is_cancelled=lambda: True,
            )
        assert exc_info.value.code == 130
        # Check old HTML is untouched
        assert out_html.read_text(encoding="utf-8") == "Old unmodified HTML content"
        # Check no temporary files remained
        assert len(list(tmp.glob("*.tmp.*"))) == 0


def test_normalized_sample_scope_semantics() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = _create_mock_v2_db(tmp, samples=[
            {"sample_stem": "b_sample", "canonical_sample_identity_id": "S_B"},
            {"sample_stem": "a_sample", "canonical_sample_identity_id": "S_A"},
        ])
        out_html = tmp / "report.html"

        # 1. No sample filter -> All universe samples
        res1 = generate_report(
            db_path, SelectionRequest(strategy="sample"), out_html
        )
        assert res1.sample_ids == ("S_A", "S_B")

        # 2. Specific valid sample -> Subsets and sorted
        res2 = generate_report(
            db_path,
            SelectionRequest(strategy="sample", sample_ids=("S_B",)),
            out_html,
        )
        assert res2.sample_ids == ("S_B",)

        # 3. Unknown-only sample scope -> Empty tuple
        res3 = generate_report(
            db_path,
            SelectionRequest(strategy="sample", sample_ids=("UNKNOWN_S",)),
            out_html,
        )
        assert res3.sample_ids == ()
        assert res3.eligible_count == 0
        assert res3.selected_count == 0


def test_cli_execution() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path, _, _ = _create_mock_v2_db(tmp)
        out_html = tmp / "cli_report.html"

        # CLI success
        code = cli_main([
            "--report", str(db_path),
            "--output", str(out_html),
            "--strategy", "sample",
            "--limit", "5",
        ])
        assert code == 0
        assert out_html.is_file()

        # CLI invalid request -> exit code 2
        bad_code = cli_main([
            "--report", str(db_path),
            "--output", str(out_html),
            "--limit", "999",
        ])
        assert bad_code == 2
