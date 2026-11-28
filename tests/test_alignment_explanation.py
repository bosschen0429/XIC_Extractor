"""Persisted explanations retain owner facts and query without acquisition."""

import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, replace

import pytest

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    explanation_summary,
    run_alignment_fixture,
    write_alignment_explanation,
)
from tests.untargeted_partition_fixtures import children_of
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment_explanation.errors import (
    CandidateNotFoundError,
    ExplanationError,
)
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
    read_review_curves,
    read_review_evidence,
    read_summary_with_examples,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.immutable_sequence import ImmutableSequence


def test_missing_report_is_unavailable(tmp_path):
    from xic_extractor.alignment_explanation.errors import ExplanationUnavailableError
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )

    with pytest.raises(ExplanationUnavailableError, match="找不到這份結果的檢閱資料"):
        read_alignment_explanation(tmp_path / "missing.sqlite")


@pytest.mark.parametrize("second_measured_mz", (500.2022, None))
def test_candidate_measured_mz_roundtrip_without_center_fallback(
    tmp_path, monkeypatch, second_measured_mz
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    first = candidates["S1"][0].peak_id
    second = candidates["S2"][0].peak_id
    measured = {first: 500.2010}
    if second_measured_mz is not None:
        measured[second] = second_measured_mz
    report = write_alignment_explanation(
        tmp_path / "measured.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        ms1_apex_mz_by_candidate=measured,
    )
    for cid, stem, expected in (
        (first, "S1", 500.2010),
        (second, "S2", second_measured_mz),
    ):
        candidate = read_alignment_explanation(report, candidate_id=cid)["candidates"][
            0
        ]
        assert candidate["sample_stem"] == stem
        assert candidate["ms1_apex_mz"] == expected
        assert (
            candidate["precursor_mz"]
            == explanation_discovery_facts(completed)["precursor_mz_by_candidate"][cid]
        )


def test_old_named_folder_requires_rerun_instead_of_fallback(tmp_path):
    from xic_inspector.api import resolve_source

    legacy = tmp_path / ("_".join(("cid", "nl", "explanation")) + ".sqlite")
    legacy.touch()
    with pytest.raises(FileNotFoundError, match="rerun Alignment"):
        resolve_source(tmp_path)


@pytest.mark.parametrize("scenario", ("positive", "all_hold", "review_only"))
def test_owner_facts_survive_persisted_candidate_query(tmp_path, monkeypatch, scenario):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, scenario
    )
    report = write_alignment_explanation(
        tmp_path / "report.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    summary = read_alignment_explanation(report)
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    with matrix.open(encoding="utf-8-sig", newline="") as matrix_handle:
        assert summary["matrix_row_count"] == sum(
            1 for _ in csv.DictReader(matrix_handle, delimiter="\t")
        )
    assert summary["candidate_count"] == sum(map(len, candidates.values()))
    candidate_id = candidates["S1"][0].peak_id
    detail = read_alignment_explanation(report, candidate_id=candidate_id)
    assert all(item["candidate_id"] == candidate_id for item in detail["candidates"])
    if scenario == "all_hold":
        excluded = next(
            item
            for item in completed.application_result.excluded_candidates
            if item.candidate_id == candidate_id
        )
        assert detail["candidates"][0]["exclusion_reason"] == excluded.reason_code
        assert detail["candidates"][0]["terminal_peak"] is None
        assert not any("destination" in group for group in detail["groups"])
    else:
        assert detail["candidates"][0]["terminal_peak"]["physical_peak_key"]
        assert all(
            member["physical_peak_keys"]
            for group in detail["groups"]
            for member in group["member_subjects"]
        )
        if scenario == "review_only":
            row = next(group for group in detail["groups"] if "destination" in group)
            assert row["destination"] == "archive"
    script = (
        "import sys,runpy\n"
        "def guard(event,args):\n"
        " if event=='import' and any(x in args[0] "
        "for x in ('raw_reader','clr','numpy','alignment_explanation.writer',"
        "'alignment_explanation_projection')):\n"
        "  raise RuntimeError('forbidden scientific import')\n"
        " if event=='open' and str(args[0]).lower().endswith(('.raw','.synthetic')):\n"
        "  raise RuntimeError('forbidden acquisition')\n"
        "sys.addaudithook(guard)\n"
        "runpy.run_module('tools.diagnostics.explain_cid_nl_run',run_name='__main__')\n"
    )
    before = report.read_bytes()
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            "--report",
            str(report),
            "--candidate-id",
            candidate_id,
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == detail
    assert report.read_bytes() == before
    with pytest.raises(CandidateNotFoundError, match="not found"):
        read_alignment_explanation(report, candidate_id="unknown")
    with pytest.raises(FileExistsError):
        write_alignment_explanation(
            report,
            completed=completed,
            children=children_of(completed.application_result),
            **explanation_discovery_facts(completed),
            peaks_by_sample=candidates,
            matrix_path=matrix,
        )


def test_actual_observation_apex_is_retained_in_query_metadata(tmp_path, monkeypatch):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    expected = {}
    for child in children_of(completed.application_result):
        for terminal in child.scientific_core.terminal_subjects:
            hypothesis = getattr(terminal, "hypothesis", None)
            if hypothesis is not None:
                expected[(terminal.candidate_id, terminal.sample_key)] = [
                    {
                        "physical_peak_key": (ref.physical_peak_key),
                        "rt_apex_min": ref.integration.rt_apex_min,
                        "mz": ref.mz,
                        "ppm": ref.generation_provenance.query_ppm_tol,
                    }
                    for ref in (hypothesis.peak,)
                ]
    assert expected
    report = write_alignment_explanation(
        tmp_path / "metadata.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    completed.close()
    actual = {
        key: candidate.terminal["observations"]
        for key, candidate in read_review_evidence(report).candidates.items()
        if candidate.terminal is not None
    }
    assert actual == expected


def test_a_fully_detected_row_reviews_its_same_mz_peaks(tmp_path, monkeypatch):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    report = write_alignment_explanation(
        tmp_path / "report.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )

    curves = read_review_curves(report, feature_id="F000001")

    assert all(location.physical_peak_key for location in curves.locations)
    assert (curves.row_mz, curves.row_ppm) == (500.0, 10.0)
    assert {peak.sample_key for peak in curves.same_mz_peaks} == {
        location.sample_key for location in curves.locations
    }


def test_partial_write_never_publishes_complete_report(tmp_path):
    report = tmp_path / "partial.sqlite"

    with pytest.raises(OSError, match="interrupted write"):
        with ExplanationReportWriter(report) as writer:
            writer.declare_samples([{"sample_key": "s", "sample_stem": "s"}])
            writer.add_candidate(
                "a",
                "s",
                precursor_mz=None,
                neutral_loss_tag="dR",
                method_subject_id="m",
            )
            raise OSError("interrupted write")
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


@dataclass(frozen=True)
class _AssessmentBatch:
    method_assessments: tuple


def test_exclusions_load_once_with_restored_duplicate_assessments(
    tmp_path, monkeypatch
):
    from xic_extractor.execution.timing import TimingRecorder

    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "all_hold"
    )
    kwargs = dict(
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    original = write_alignment_explanation(
        tmp_path / "original.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        **kwargs,
    )
    store = StreamedResultStore(tmp_path / "assessment-store", ())
    store.append("one", _AssessmentBatch(completed.method_assessments))
    restored = store.read(0).method_assessments
    assert restored[0] is not completed.method_assessments[0]
    assert restored[0] == completed.method_assessments[0]
    application = completed.application_result
    excluded = tuple(application.excluded_candidates)
    loads = 0

    def load_exclusions(_index):
        nonlocal loads
        loads += 1
        return excluded

    application = FinalizedUntargetBatchApplicationResult(
        scope=application.scope,
        processing_attempt_id=application.processing_attempt_id,
        acquisition_sources=application.acquisition_sources,
        child_outputs=application.child_outputs,
        excluded_candidates=ImmutableSequence((len(excluded),), load_exclusions),
    )
    duplicated = replace(
        completed,
        application_result=application,
        method_assessments=(*completed.method_assessments, *restored),
    )
    loads = 0
    events = []
    live = tmp_path / "writer-live.json"
    recorder = TimingRecorder("alignment", live_output_path=live)

    def progress(event):
        events.append(event)
        recorder.heartbeat_event(event)

    with recorder.stage("alignment.write_alignment_explanation"):
        report = write_alignment_explanation(
            tmp_path / "duplicated.sqlite",
            completed=duplicated,
            children=children_of(duplicated.application_result),
            **explanation_discovery_facts(duplicated),
            progress_callback=progress,
            **kwargs,
        )
    assert loads == 1
    assert read_alignment_explanation(report) == read_alignment_explanation(original)
    assert any(event["stage"] == "alignment.explanation.exclusions" for event in events)
    assert [event["records_written"] for event in events] == sorted(
        event["records_written"] for event in events
    )
    heartbeat = json.loads(live.read_text())["heartbeat"]
    assert heartbeat["stage"] == "alignment.explanation.finalizing"
    with sqlite3.connect(report) as connection:
        stored = sum(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("groups", "cells")
        )
    assert heartbeat["records_written"] == stored
    for values in candidates.values():
        for candidate in values:
            assert read_alignment_explanation(
                report, candidate_id=candidate.peak_id
            ) == read_alignment_explanation(original, candidate_id=candidate.peak_id)


def test_explanation_progress_failure_does_not_publish_report(tmp_path, monkeypatch):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "all_hold"
    )
    report = tmp_path / "interrupted.sqlite"

    def interrupted(event):
        if event["stage"] == "alignment.explanation.method_assessments":
            raise OSError("progress unavailable")

    with pytest.raises(OSError, match="progress unavailable"):
        write_alignment_explanation(
            report,
            completed=completed,
            children=children_of(completed.application_result),
            **explanation_discovery_facts(completed),
            peaks_by_sample=candidates,
            matrix_path=matrix,
            progress_callback=interrupted,
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_exact_candidate_loads_only_its_evidence_across_samples(tmp_path, monkeypatch):
    reports = []
    samples = [{"sample_key": key, "sample_stem": key} for key in ("A", "B")]
    for unrelated in (0, 20):
        path = tmp_path / f"report-{unrelated}.sqlite"
        with ExplanationReportWriter(path) as writer:
            writer.declare_samples(samples)
            for idx, (cid, sample) in enumerate(
                [
                    ("same", "A"),
                    ("same", "B"),
                    *((f"other-{i}", "A") for i in range(unrelated)),
                ]
            ):
                subject = f"M{idx}"
                writer.add_candidate(
                    cid,
                    sample,
                    precursor_mz=400.0 + idx,
                    neutral_loss_tag="A",
                    method_subject_id=subject,
                )
                writer.add_terminal(
                    cid,
                    sample,
                    hypothesis_id=f"h{idx}",
                    physical_peak_key=f"p{idx}",
                )
            writer.finalize(
                explanation_summary(
                    {"samples": samples, "neutral_loss_by_tag": {"A": 116.0}}
                )
            )
        reports.append(path)

    read_alignment_explanation(reports[0])
    connect = sqlite3.connect
    fetched = []

    def counted_connect(*args, **kwargs):
        db = connect(*args, **kwargs)

        def count_row(cursor, row):
            fetched.append(1)
            return row

        db.row_factory = count_row
        return db

    monkeypatch.setattr(sqlite3, "connect", counted_connect)
    counts = []
    page_counts = []
    for report in reports:
        fetched.clear()
        result = read_alignment_explanation(report, candidate_id="same")
        counts.append(len(fetched))
        assert [
            (row["sample_key"], row["precursor_mz"]) for row in result["candidates"]
        ] == [("A", 400), ("B", 401)]
        summary = read_alignment_explanation(report)
        fetched.clear()
        page = read_review_evidence(
            report, candidate_keys=[("same", "B")], summary=summary
        )
        assert list(page.candidates) == [("same", "B")]
        assert page.candidates["same", "B"].fields["precursor_mz"] == 401
        page_counts.append(len(fetched))
    assert counts[0] == counts[1]
    assert page_counts[0] == page_counts[1]
    fetched.clear()
    plain = read_alignment_explanation(reports[-1])
    summary_rows = len(fetched)
    fetched.clear()
    with_examples, examples = read_summary_with_examples(reports[-1])
    assert with_examples == plain
    assert examples == [
        {"candidate_id": f"other-{i}", "sample_stem": "A"} for i in (0, 1, 10, 11, 12)
    ]
    assert len(fetched) == summary_rows + 5


def test_incremental_report_deduplicates_and_aborts_conflicts(tmp_path):
    report = tmp_path / "stream.sqlite"
    summary = explanation_summary(
        {"samples": [{"sample_key": "s"}], "neutral_loss_by_tag": {"A": 116.0474}}
    )
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(summary["samples"])
        candidate = dict(
            precursor_mz=500.0,
            neutral_loss_tag="A",
            method_subject_id="M1",
        )
        writer.add_candidate("a", "s", **candidate)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            writer.add_candidate("a", "s", **candidate)
        writer.add_exclusion("a", "s", "held")
        writer.add_exclusion("a", "s", "held")
        writer.finalize(summary)
    detail = read_alignment_explanation(report, candidate_id="a")
    assert [
        (
            candidate["candidate_id"],
            candidate["sample_key"],
            candidate["neutral_loss_da"],
            candidate["exclusion_reason"],
        )
        for candidate in detail["candidates"]
    ] == [("a", "s", 116.0474, "held")]
    failed = tmp_path / "conflict.sqlite"
    with pytest.raises(ValueError, match="conflicting candidate exclusion"):
        with ExplanationReportWriter(failed) as writer:
            writer.declare_samples(summary["samples"])
            writer.add_candidate("a", "s", **candidate)
            writer.add_exclusion("a", "s", "held")
            writer.add_exclusion("a", "s", "other")
    assert not failed.exists()
    assert not failed.with_name(failed.name + ".tmp").exists()


def test_report_rejects_terminal_without_a_candidate(tmp_path):
    report = tmp_path / "missing-candidate.sqlite"
    with pytest.raises(ExplanationError, match="undeclared candidate: missing"):
        with ExplanationReportWriter(report) as writer:
            writer.declare_samples([{"sample_key": "S1", "sample_stem": "S1"}])
            writer.add_terminal("missing", "S1")
            writer.finalize(explanation_summary({}))
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_indexed_query_preserves_many_groups_and_skips_unrelated_payloads(tmp_path):
    report = tmp_path / "indexed.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(
            [{"sample_key": s, "sample_stem": s} for s in ("s1", "s2")]
        )
        for cid, sid, subject in (
            ("a", "s1", "Ma"),
            ("a2", "s1", "Ma"),
            ("b", "s2", "Mb"),
            ("b2", "s2", "Mb"),
        ):
            writer.add_candidate(
                cid,
                sid,
                precursor_mz=500.0,
                neutral_loss_tag="dR",
                method_subject_id=subject,
            )
            writer.add_terminal(
                cid,
                sid,
                hypothesis_id=f"h-{cid}",
                physical_peak_key=f"p-{cid}",
            )

        def member(sid, cid, *peaks):
            return {
                "sample_key": sid,
                "subject_id": f"h-{cid}",
                "subject_kind": "peak_hypothesis",
                "physical_peak_keys": [f"p-{cid}", *peaks],
            }

        for name, members in (
            ("shared", [member("s1", "a"), member("s2", "b")]),
            ("second", [member("s1", "a2", "p-a")]),
            ("unrelated", [member("s2", "b2", "p-b")]),
        ):
            writer.add_final_group(
                name, feature_id=name, disposition="completed", members=members
            )
            writer.add_cells(
                name,
                [
                    {"sample_key": member["sample_key"], "variant": "numeric"}
                    for member in members
                ],
            )
        writer.finalize(explanation_summary({"neutral_loss_by_tag": {"dR": 116.0}}))
    with sqlite3.connect(report) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE cells SET reason_codes='not json' WHERE group_id="
            "(SELECT id FROM groups WHERE feature_id='unrelated')"
        )
    assert read_alignment_explanation(report)["candidate_count"] == 4
    detail = read_alignment_explanation(report, candidate_id="a")
    assert sorted(group["feature_id"] for group in detail["groups"]) == [
        "second",
        "shared",
    ]
    with pytest.raises(ExplanationError, match="malformed"):
        read_alignment_explanation(report, candidate_id="b")


@pytest.mark.parametrize(
    "corruption",
    ("bytes", "schema_identity", "schema", "payload"),
)
def test_invalid_report_never_returns_empty_success(tmp_path, corruption):
    report = tmp_path / "bad.sqlite"
    if corruption == "bytes":
        report.write_bytes(b"not sqlite")
    else:
        with ExplanationReportWriter(report) as writer:
            writer.finalize(explanation_summary({}))
        with sqlite3.connect(report) as connection:
            if corruption == "schema_identity":
                connection.execute("DROP TRIGGER peaks_fixed")
            elif corruption == "schema":
                connection.execute("DROP TABLE cell_curves")
            else:
                connection.execute("DELETE FROM run")
    message = (
        "重新執行 Alignment"
        if corruption in ("schema_identity", "schema")
        else "malformed"
    )
    with pytest.raises(ExplanationError, match=message):
        read_alignment_explanation(report)
