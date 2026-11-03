"""Tests for the complete SQLite schema and bound curve payloads."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zlib
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.test_alignment_explanation import _summary_fixture
from xic_extractor.alignment_explanation.reader import (
    read_alignment_curves,
    read_alignment_explanation,
)
from xic_extractor.alignment_explanation.storage import (
    ExplanationError,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult


def test_curve_payload_write_and_read() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with ExplanationReportWriter(db_path) as writer:
            writer.add_candidate("C1", "S1", {"candidate_id": "C1", "sample_key": "S1"})
            curve_envelope = {
                "schema_version": "curve_payload",
                "kind": "initial_capture",
                "sample_id": "S1",
                "candidate_id": "C1",
                "physical_peak_key": "obs-01",
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
                "integration": asdict(
                    IntegrationResult(
                        rt_left_min=1.1,
                        rt_apex_min=1.5,
                        rt_right_min=1.9,
                        raw_apex_rt_min=1.5,
                        rt_width_min=0.7999999999999998,
                        height_raw=5000.0,
                        height_smoothed=5000.0,
                        area_raw_counts_seconds=12000.0,
                    )
                ),
                "provenance": _curve_provenance(),
            }
            writer.add_curve_payload(
                sample_id="S1",
                kind="initial_capture",
                record_id="C1",
                payload=curve_envelope,
            )
            final_path = writer.finalize(
                _summary_fixture(
                    {
                        "processing_attempt_id": "att-1",
                        "samples": [{"sample_stem": "s1", "sample_key": "S1"}],
                    },
                    review_only=writer.review_only,
                )
            )

        summary = read_alignment_explanation(final_path)
        assert summary["schema_version"] == "alignment_explanation"
        assert summary["plot_evidence_capability"] == "retained_curves"

        curves = read_alignment_curves(final_path, record_ids=["C1"])
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
                    sample_id="S1",
                    kind="fraudulent_curve_kind",
                    record_id="C1",
                    payload={"kind": "fraudulent_curve_kind"},
                )

            # Undeclared key
            with pytest.raises(ExplanationError, match="undeclared curve payload keys"):
                writer.add_curve_payload(
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
    from tests.test_alignment_explanation import _owner_run
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    expected = {}
    for child in completed.application_result.children:
        for terminal in child.scientific_core.terminal_subjects:
            hypothesis = getattr(terminal, "hypothesis", None)
            if hypothesis is not None:
                expected[(terminal.candidate_id, terminal.sample_key)] = [
                    {
                        "physical_peak_key": (ref.content.physical_peak_key),
                        "rt_apex_min": ref.content.integration.rt_apex_min,
                    }
                    for ref in hypothesis.peak_observation_refs
                ]
    assert expected
    report = write_alignment_explanation(
        tmp_path / "metadata.sqlite",
        full_audit=True,
        completed=completed,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    completed.close()
    with sqlite3.connect(report) as connection:
        actual = {
            (record["candidate_id"], record["sample_key"]): record["observations"]
            for (raw,) in connection.execute(
                "SELECT payload FROM records WHERE kind='terminal_subjects'"
            )
            if (record := json.loads(raw)).get("hypothesis_id")
        }
    assert actual == expected


def _curve_provenance():
    return {
        "profile_version": None,
        "acquisition_source_id": None,
        "processing_attempt_id": None,
        "selected_trace_indices": None,
        "resolver_mode": None,
        "proposal_sources": None,
        "method_subject_id": None,
        "profile": None,
        "physical_state": None,
        "physical_reason": None,
    }


def _bound_curve_payload():
    return {
        "schema_version": "curve_payload",
        "kind": "initial_capture",
        "sample_id": "S1",
        "candidate_id": "C1",
        "physical_peak_key": "obs-1",
        "trace_coordinate": {"mz": 500.0, "rt_min": 1.0, "rt_max": 2.0, "ppm_tol": 7.0},
        "curves": {
            "rt": [1.0, 1.5, 2.0],
            "raw": [0.0, 9.0, 0.0],
            "baseline": None,
            "smoothed": None,
            "positive": None,
        },
        "integration": None,
        "provenance": _curve_provenance(),
    }


def test_curve_rowid_preserves_exact_payload_across_reopen(
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
    expected_payload_id = 1

    with ExplanationReportWriter(tmp_path / "automatic-id.sqlite") as writer:
        payload_id = writer.add_curve_payload(
            sample_id="S1",
            kind="initial_capture",
            record_id="obs-1",
            payload=payload,
        )
        report = writer.finalize(
            _summary_fixture({"samples": []}, review_only=writer.review_only)
        )

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
    assert read_alignment_curves(report, record_ids=["obs-1"]) == [
        {**payload, "payload_id": expected_payload_id}
    ]


def test_shared_curve_is_reused_and_distinct_data_gets_its_own_row(tmp_path):
    payload = _bound_curve_payload()
    with ExplanationReportWriter(tmp_path / "dedup.sqlite") as writer:
        binding = dict(sample_id="S1", kind="initial_capture", record_id="obs-1")
        writer.add_curve_payload(**binding, payload=payload)
        writer._flush_curve_payloads()
        writer.add_curve_payload(**binding, payload=deepcopy(payload))
        changed = deepcopy(payload)
        changed["curves"]["raw"][1] = 8.0
        distinct = writer.add_curve_payload(**binding, payload=changed)
        assert distinct == 2
        report = writer.finalize(
            _summary_fixture({"samples": []}, review_only=writer.review_only)
        )
    with sqlite3.connect(report) as connection:
        assert (
            connection.execute("SELECT count(*) FROM curve_payloads").fetchone()[0] == 2
        )
    assert read_alignment_curves(report, record_ids=["obs-1"])[0] == {
        **payload,
        "payload_id": 1,
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
            sample_id="S1",
            kind="initial_capture",
            record_id="obs-1",
            payload=payload,
        )
        report = writer.finalize(
            _summary_fixture({"samples": []}, review_only=writer.review_only)
        )
    if corruption == "sample":
        payload["sample_id"] = "S2"
    elif corruption == "record":
        payload["physical_peak_key"] = "other-observation"
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
        read_alignment_curves(report, sample_ids=["S1"], record_ids=["obs-1"])


def test_empty_acquisition_preserves_unavailable_without_inventing_peak(tmp_path):
    payload = _bound_curve_payload()
    payload["physical_peak_key"] = None
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
            sample_id="S1",
            kind="initial_capture",
            record_id=payload["candidate_id"],
            payload=payload,
        )
        invented = {**payload, "physical_peak_key": "obs-1"}
        with pytest.raises(ExplanationError, match="empty acquisition"):
            writer.add_curve_payload(
                sample_id="S1",
                kind="initial_capture",
                record_id="obs-1",
                payload=invented,
            )
        report = writer.finalize(
            _summary_fixture({"samples": []}, review_only=writer.review_only)
        )
    assert read_alignment_curves(report) == [{**payload, "payload_id": 1}]


@pytest.mark.parametrize("section", ["provenance", "integration", "signal_quality"])
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_curve_reader_requires_complete_nested_fields(tmp_path, section, change):
    from dataclasses import asdict

    from xic_extractor.peak_detection.integration_result import IntegrationResult
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    payload = _bound_curve_payload()
    payload["integration"] = asdict(
        IntegrationResult(
            rt_left_min=1.0,
            rt_apex_min=1.5,
            rt_right_min=2.0,
            raw_apex_rt_min=1.5,
            rt_width_min=1.0,
            height_raw=9.0,
            height_smoothed=9.0,
            area_raw_counts_seconds=270.0,
            signal_quality=SignalQuality(
                status="available",
                noise_sigma=1.0,
                signal_to_noise=9.0,
                low_signal_to_noise=True,
            ),
        )
    )
    with ExplanationReportWriter(tmp_path / "nested.sqlite") as writer:
        writer.add_curve_payload(
            sample_id="S1", kind="initial_capture", record_id="obs-1", payload=payload
        )
        report = writer.finalize(_summary_fixture({"samples": []}, review_only=False))
    nested = (
        payload["integration"]["signal_quality"]
        if section == "signal_quality"
        else payload[section]
    )
    if change == "missing":
        nested.pop(next(iter(nested)))
    else:
        nested["unexpected"] = None
    with sqlite3.connect(report) as connection:
        connection.execute(
            "UPDATE curve_payloads SET payload=?",
            (zlib.compress(json.dumps(payload).encode()),),
        )
    with pytest.raises(ExplanationError, match="rerun"):
        read_alignment_curves(report, record_ids=["obs-1"])
