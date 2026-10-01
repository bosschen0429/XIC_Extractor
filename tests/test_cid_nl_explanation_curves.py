"""Tests for the complete SQLite schema and bound curve payloads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zlib
from copy import deepcopy
from pathlib import Path

import pytest

from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationReportWriter,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
from xic_extractor.evidence_artifacts import EvidenceLocator


def test_cid_nl_evidence_locator_immutability() -> None:
    locator = EvidenceLocator(
        report_path=Path("out/cid_nl_explanation.sqlite"),
        matrix_path=Path("out/alignment_matrix.tsv"),
        matrix_sha256="abcd" * 16,
        processing_request_id="req-01",
        processing_attempt_id="att-01",
        schema_version="alignment_explanation",
    )
    assert locator.schema_version == "alignment_explanation"
    assert locator.processing_request_id == "req-01"
    with pytest.raises(AttributeError):
        locator.schema_version = "unknown"  # type: ignore


def test_curve_payload_write_and_read() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with ExplanationReportWriter(db_path) as writer:
            writer.add_candidate(
                "C1", "S1", {"candidate_id": "C1", "canonical_sample_identity_id": "S1"}
            )
            curve_envelope = {
                "schema_version": "curve_payload",
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
        assert summary["schema_version"] == "alignment_explanation"
        assert summary["plot_evidence_capability"] == "retained_curves"

        curves = read_cid_nl_curves(final_path, record_ids=["C1"])
        assert len(curves) == 1
        assert curves[0]["candidate_id"] == "C1"
        assert curves[0]["curves"]["raw"] == [0.0, 100.0, 5000.0, 200.0, 0.0]


def test_rejects_undeclared_curve_kind_and_keys() -> None:
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
                        "schema_version": "curve_payload",
                        "kind": "initial_capture",
                        "sample_id": "S1",
                        "extra_fake_field": 123,
                    },
                )


def test_actual_observation_apex_is_retained_in_query_metadata(tmp_path, monkeypatch):
    from tests.test_cid_nl_explanation import _owner_run
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_cid_nl_explanation,
    )

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    expected = {}
    for child in completed.application_result.children:
        for terminal in child.scientific_core.terminal_subjects:
            hypothesis = getattr(terminal, "hypothesis", None)
            if hypothesis is not None:
                expected[
                    (terminal.candidate_id, terminal.canonical_sample_identity_id)
                ] = [
                    {
                        "peak_observation_content_id": (
                            ref.content.peak_observation_content_id
                        ),
                        "rt_apex_min": ref.content.integration.rt_apex_min,
                    }
                    for ref in hypothesis.peak_observation_refs
                ]
    assert expected
    report = write_cid_nl_explanation(
        tmp_path / "metadata.sqlite",
        full_audit=True,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()
    with sqlite3.connect(report) as connection:
        actual = {
            (record["candidate_id"], record["canonical_sample_identity_id"]): record[
                "observations"
            ]
            for (raw,) in connection.execute(
                "SELECT payload FROM records WHERE kind='terminal_subjects'"
            )
            if (record := json.loads(raw)).get("hypothesis_id")
        }
    assert actual == expected


def _bound_curve_payload():
    return {
        "schema_version": "curve_payload",
        "kind": "initial_capture",
        "sample_id": "S1",
        "candidate_id": "C1",
        "peak_observation_content_id": "obs-1",
        "trace_coordinate": {"mz": 500.0, "rt_min": 1.0, "rt_max": 2.0, "ppm_tol": 7.0},
        "curves": {
            "rt": [1.0, 1.5, 2.0],
            "raw": [0.0, 9.0, 0.0],
            "baseline": None,
            "smoothed": None,
            "positive": None,
        },
        "integration": None,
        "provenance": {"profile_id": None, "trace_content_id": None},
    }


def test_automatic_curve_id_uses_legacy_canonical_envelope_and_persists_exact_payload(
    tmp_path,
):
    payload = _bound_curve_payload()
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected_payload_id = "xic.inspector-curve:" + hashlib.sha256(canonical).hexdigest()

    with ExplanationReportWriter(tmp_path / "automatic-id.sqlite") as writer:
        payload_id = writer.add_curve_payload(
            payload_id=None,
            sample_id="S1",
            kind="initial_capture",
            record_id="obs-1",
            payload=payload,
        )
        report = writer.finalize({"samples": []})

    assert payload_id == expected_payload_id
    with sqlite3.connect(report) as connection:
        row = connection.execute(
            "SELECT payload_id, sample_id, kind, record_id, codec, payload "
            "FROM curve_payloads"
        ).fetchone()
    assert row[:5] == (
        expected_payload_id,
        "S1",
        "initial_capture",
        "obs-1",
        "zlib-json-utf8",
    )
    assert zlib.decompress(row[5]) == canonical
    assert read_cid_nl_curves(report, record_ids=["obs-1"]) == [
        {**payload, "payload_id": expected_payload_id}
    ]


def test_shared_curve_is_reused_and_conflicting_same_identity_rejected(tmp_path):
    payload = _bound_curve_payload()
    with ExplanationReportWriter(tmp_path / "dedup.sqlite") as writer:
        binding = dict(
            payload_id="p1", sample_id="S1", kind="initial_capture", record_id="obs-1"
        )
        writer.add_curve_payload(**binding, payload=payload)
        writer._flush_curve_payloads()
        writer.add_curve_payload(**binding, payload=deepcopy(payload))
        changed = deepcopy(payload)
        changed["curves"]["raw"][1] = 8.0
        with pytest.raises(ExplanationError, match="conflict"):
            writer.add_curve_payload(**binding, payload=changed)
        report = writer.finalize({"samples": []})
    with sqlite3.connect(report) as connection:
        assert (
            connection.execute("SELECT count(*) FROM curve_payloads").fetchone()[0] == 1
        )
    assert read_cid_nl_curves(report, record_ids=["obs-1"])[0] == {
        **payload,
        "payload_id": "p1",
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "sample",
        "record",
        "nested_field",
        "length",
        "nonfinite",
        "provenance_type",
        "profile_field",
    ],
)
def test_selected_reader_rejects_corrupt_payload_binding_and_shape(
    tmp_path, corruption
):
    payload = _bound_curve_payload()
    with ExplanationReportWriter(tmp_path / "corrupt.sqlite") as writer:
        writer.add_curve_payload(
            payload_id="p1",
            sample_id="S1",
            kind="initial_capture",
            record_id="obs-1",
            payload=payload,
        )
        report = writer.finalize({"samples": []})
    if corruption == "sample":
        payload["sample_id"] = "S2"
    elif corruption == "record":
        payload["peak_observation_content_id"] = "other-observation"
    elif corruption == "nested_field":
        payload["curves"]["invented_smoothed"] = [0.0, 1.0, 0.0]
    elif corruption == "length":
        payload["curves"]["raw"].pop()
    elif corruption == "provenance_type":
        payload["provenance"] = "malformed"
    elif corruption == "profile_field":
        payload["provenance"]["profile"] = {"invented_profile": True}
    else:
        payload["curves"]["raw"][1] = float("nan")
    with sqlite3.connect(report) as connection:
        connection.execute(
            "UPDATE curve_payloads SET payload=?",
            (zlib.compress(json.dumps(payload).encode()),),
        )
    with pytest.raises(ExplanationError):
        read_cid_nl_curves(report, sample_ids=["S1"], record_ids=["obs-1"])


def test_empty_acquisition_preserves_unavailable_without_inventing_peak(tmp_path):
    payload = _bound_curve_payload()
    payload["peak_observation_content_id"] = None
    payload["integration"] = None
    payload["curves"] = {
        "rt": [],
        "raw": [],
        "baseline": None,
        "smoothed": None,
        "positive": None,
    }
    payload["provenance"]["physical_state"] = "unavailable"
    with ExplanationReportWriter(tmp_path / "empty.sqlite") as writer:
        writer.add_curve_payload(
            payload_id="empty",
            sample_id="S1",
            kind="initial_capture",
            record_id=payload["candidate_id"],
            payload=payload,
        )
        invented = {**payload, "peak_observation_content_id": "obs-1"}
        with pytest.raises(ExplanationError, match="empty acquisition"):
            writer.add_curve_payload(
                payload_id="invalid",
                sample_id="S1",
                kind="initial_capture",
                record_id="obs-1",
                payload=invented,
            )
        report = writer.finalize({"samples": []})
    assert read_cid_nl_curves(report) == [{**payload, "payload_id": "empty"}]
