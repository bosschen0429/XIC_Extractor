"""Persisted explanations retain owner facts and query without acquisition."""

import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, replace

import pytest

from tests.alignment_explanation_fixtures import (
    curve_provenance,
    explanation_discovery_facts,
    explanation_summary,
    run_alignment_fixture,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment_explanation.errors import (
    CandidateNotFoundError,
    ExplanationError,
)
from xic_extractor.alignment_explanation.reader import (
    read_alignment_curves,
    read_alignment_explanation,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.immutable_sequence import ImmutableSequence


def test_missing_report_is_unavailable(tmp_path):
    from xic_extractor.alignment_explanation.errors import ExplanationUnavailableError
    from xic_extractor.alignment_explanation.reader import (
        read_alignment_explanation,
    )

    with pytest.raises(ExplanationUnavailableError, match="unavailable"):
        read_alignment_explanation(tmp_path / "missing.sqlite")


@pytest.mark.parametrize("full_audit", (False, True))
@pytest.mark.parametrize("second_measured_mz", (500.2022, None))
def test_candidate_measured_mz_roundtrip_without_center_fallback(
    tmp_path, monkeypatch, full_audit, second_measured_mz
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
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
        ms1_apex_mz_by_candidate=measured,
        full_audit=full_audit,
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
        full_audit=True,
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_alignment_explanation(report)
    assert summary["diagnostic_only"] is True
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    with matrix.open(encoding="utf-8-sig", newline="") as matrix_handle:
        assert summary["matrix_row_count"] == sum(
            1 for _ in csv.DictReader(matrix_handle, delimiter="\t")
        )
    assert summary["candidate_count"] == sum(map(len, candidates.values()))
    candidate_id = candidates["S1"][0].peak_id
    detail = read_alignment_explanation(report, candidate_id=candidate_id)
    assessment = next(
        item
        for item in completed.method_assessments
        if item.sample_key == detail["candidates"][0]["sample_key"]
    )
    retained = detail["method_assessments"][0]
    assert retained["reason_codes"] == list(assessment.reason_codes)
    assert retained["method_result"] == assessment.method_result
    assert retained["evaluation_receipt"] == json.loads(
        json.dumps(asdict(assessment.evaluation_receipt))
    )
    assert "artifact" not in retained
    assert all(item["candidate_id"] == candidate_id for item in detail["candidates"])
    if scenario == "all_hold":
        excluded = next(
            item
            for item in completed.application_result.excluded_candidates
            if item.candidate_id == candidate_id
        )
        assert detail["candidates"][0]["exclusion_reason"] == excluded.reason_code
        assert detail["candidates"][0]["terminal"] is None
        assert not any("destination" in group for group in detail["groups"])
        assert retained["method_result"] == "HOLD"
    else:
        terminal = detail["candidates"][0]["terminal"]
        assert terminal["peak"]["physical_peak_key"]
        source_groups = {
            tuple(member.subject_id for member in group.member_subjects): group
            for group in completed.initial_groups
        }
        for retained_group in detail["initial_groups"]:
            source_group = source_groups[
                tuple(
                    member["subject_id"] for member in retained_group["member_subjects"]
                )
            ]
            assert retained_group["inference_basis"] == json.loads(
                json.dumps(asdict(source_group.inference_basis))
            )
            assert retained_group["member_subjects"] == json.loads(
                json.dumps(
                    [
                        {
                            "sample_key": (member.sample_key),
                            "subject_kind": member.subject_kind,
                            "subject_id": member.subject_id,
                            "physical_peak_keys": [
                                peak.physical_peak_key for peak in member.physical_peaks
                            ],
                        }
                        for member in source_group.member_subjects
                    ]
                )
            )
            assert all(
                member["physical_peak_keys"]
                for member in retained_group["member_subjects"]
            )
        if scenario == "review_only":
            assert retained["method_result"] == "SUPPORT"
            row = next(group for group in detail["groups"] if "destination" in group)
            assert row["destination"] == "archive"
    # A fresh query process can neither acquire RAW nor import the scientific stack.
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
            full_audit=True,
            completed=completed,
            **explanation_discovery_facts(completed),
            peaks_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
    if scenario == "positive":
        missing_audit = replace(completed, method_assessments=(), initial_groups=())
        missing_audit_report = write_alignment_explanation(
            tmp_path / "missing_audit.sqlite",
            full_audit=True,
            completed=missing_audit,
            **explanation_discovery_facts(missing_audit),
            peaks_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
        assert read_alignment_explanation(missing_audit_report)[
            "audit_availability"
        ] == {
            "method_assessments": "unavailable",
            "initial_groups": "unavailable",
        }


def test_partial_write_never_publishes_complete_report(tmp_path):
    report = tmp_path / "partial.sqlite"

    with pytest.raises(OSError, match="interrupted write"):
        with ExplanationReportWriter(report) as writer:
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
        alignment_config=config,
    )
    original = write_alignment_explanation(
        tmp_path / "original.sqlite",
        completed=completed,
        **explanation_discovery_facts(completed),
        **kwargs,
        full_audit=True,
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
        children=application.children,
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
            full_audit=True,
            completed=duplicated,
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
            for table in (
                "method_subjects",
                "method_assessments",
                "exclusions",
                "groups",
                "initial_groups",
                "curves",
                "cells",
            )
        )
    assert heartbeat["records_written"] == stored
    for values in candidates.values():
        for candidate in values:
            assert read_alignment_explanation(
                report, candidate_id=candidate.peak_id
            ) == read_alignment_explanation(original, candidate_id=candidate.peak_id)


@dataclass(frozen=True)
class _ChildBatch:
    children: tuple


@pytest.mark.parametrize("scenario", ("positive", "review_only"))
def test_explanation_loads_each_file_backed_child_once(tmp_path, monkeypatch, scenario):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, scenario
    )
    application = completed.application_result
    kwargs = dict(
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    original = write_alignment_explanation(
        tmp_path / "memory.sqlite",
        completed=completed,
        **explanation_discovery_facts(completed),
        **kwargs,
        full_audit=True,
    )
    store = StreamedResultStore(
        tmp_path / "child-store",
        (
            application.processing_attempt_id,
            *application.canonical_samples,
        ),
    )
    store.append("one", _ChildBatch(tuple(application.children)))
    store.seal()
    loads = 0
    read_record = store._read_record

    def counted_read(*args):
        nonlocal loads
        loads += 1
        return read_record(*args)

    monkeypatch.setattr(store, "_read_record", counted_read)
    restored = FinalizedUntargetBatchApplicationResult(
        scope=application.scope,
        processing_attempt_id=application.processing_attempt_id,
        acquisition_sources=application.acquisition_sources,
        children=store.sequence("children"),
        excluded_candidates=application.excluded_candidates,
    )
    loads = 0
    report = write_alignment_explanation(
        tmp_path / "stored.sqlite",
        full_audit=True,
        completed=replace(completed, application_result=restored),
        **explanation_discovery_facts(replace(completed, application_result=restored)),
        **kwargs,
    )
    assert loads == len(application.children), (loads, len(application.children))
    assert read_alignment_explanation(report) == read_alignment_explanation(original)
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
            full_audit=True,
            completed=completed,
            **explanation_discovery_facts(completed),
            peaks_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
            progress_callback=interrupted,
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_incremental_report_deduplicates_and_aborts_conflicts(tmp_path):
    report = tmp_path / "stream.sqlite"
    summary = explanation_summary(
        {"samples": [{"sample_key": "s"}], "neutral_loss_by_tag": {"A": 116.0474}}
    )
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(summary["samples"])
        writer.add_method_subject({"method_subject_id": "M1"})
        candidate = dict(
            precursor_mz=500.0,
            neutral_loss_tag="A",
            peak_status="detected",
            method_subject_id="M1",
        )
        writer.add_candidate("a", "s", **candidate)
        with pytest.raises(ValueError, match="duplicate diagnostic candidate/sample"):
            writer.add_candidate("a", "s", **candidate)
        # The same fact emitted twice is stored once.
        writer.add_exclusion("a", "s", "held")
        writer.add_exclusion("a", "s", "held")
        writer.finalize(summary)
    detail = read_alignment_explanation(report, candidate_id="a")
    assert [
        (
            candidate["candidate_id"],
            candidate["sample_key"],
            candidate["method_subject_id"],
            candidate["peak_status"],
            candidate["exclusion_reason"],
        )
        for candidate in detail["candidates"]
    ] == [("a", "s", "M1", "detected", "held")]
    failed = tmp_path / "conflict.sqlite"
    with pytest.raises(ValueError, match="conflicting candidate exclusion"):
        with ExplanationReportWriter(failed) as writer:
            writer.add_exclusion("a", "s", "held")
            writer.add_exclusion("a", "s", "other")
    assert not failed.exists()
    assert not failed.with_name(failed.name + ".tmp").exists()


def _curve_payload():
    return {
        "schema_version": "curve_payload",
        "kind": "initial_capture",
        "sample_id": "S1",
        "candidate_id": None,
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
        "provenance": curve_provenance(),
    }


def test_related_set_links_preserve_union_of_repeated_records(tmp_path):
    report = tmp_path / "normalized.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples([{"sample_key": "S1", "sample_stem": "S1"}])
        # C1 and C2 end on the curve's peak; C3 shares its provenance subject.
        for candidate_id, subject in (("C1", "M1"), ("C2", "M2"), ("C3", "M3")):
            writer.add_candidate(
                candidate_id,
                "S1",
                precursor_mz=500.0,
                neutral_loss_tag="dR",
                method_subject_id=subject,
            )
        for candidate_id in ("C1", "C2"):
            writer.add_terminal(
                candidate_id,
                "S1",
                variant="observed",
                hypothesis_id=f"h-{candidate_id}",
                physical_peak_key="obs-1",
            )
        payload = _curve_payload()
        payload["provenance"] = {**payload["provenance"], "method_subject_id": "M3"}
        binding = dict(sample_id="S1", kind="initial_capture", record_id="obs-1")
        # The same record written repeatedly is stored once and relates the
        # union of its peak's and its subject's candidates.
        ids = {writer.add_curve_payload(**binding, payload=payload) for _ in range(3)}
        assert len(ids) == 1
        writer.finalize(explanation_summary({"neutral_loss_by_tag": {"dR": 116.0}}))

    evidence = read_review_evidence(report)
    assert {
        key for key, candidate in evidence.candidates.items() if candidate.curves
    } == {(candidate_id, "S1") for candidate_id in ("C1", "C2", "C3")}
    assert {candidate.curves for candidate in evidence.candidates.values()} == {("1",)}
    details = [
        read_alignment_explanation(report, candidate_id=candidate_id)[
            "capture_evidence"
        ]
        for candidate_id in ("C1", "C2", "C3")
    ]
    assert len(details[0]) == 1
    assert details == [details[0]] * 3


def test_report_rejects_terminal_without_a_candidate(tmp_path):
    report = tmp_path / "missing-candidate.sqlite"
    with pytest.raises(KeyError, match="missing"):
        with ExplanationReportWriter(report) as writer:
            writer.declare_samples([{"sample_key": "S1", "sample_stem": "S1"}])
            writer.add_terminal("missing", "S1", variant="observed")
            writer.finalize(explanation_summary({}))
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_indexed_query_preserves_many_groups_and_skips_unrelated_payloads(tmp_path):
    report = tmp_path / "indexed.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(
            [{"sample_key": s, "sample_stem": s} for s in ("s1", "s2")]
        )
        # A hypothesis sits in one initial group, so a and b reach a second
        # group through a same-method hypothesis (a2, b2) whose member also
        # holds their peak.
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
                variant="observed",
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
            writer.add_initial_group(
                disposition="completed",
                members=members,
                inference_basis={"basis": name},
                inference_decisions=[],
            )
        writer.add_final_group(
            "shared",
            feature_id=None,
            disposition="completed",
            members=[member("s1", "a")],
        )
        writer.finalize(explanation_summary({"neutral_loss_by_tag": {"dR": 116.0}}))
    with sqlite3.connect(report) as connection:
        assert (
            connection.execute("SELECT count(*) FROM initial_groups").fetchone()[0] == 3
        )
        # Only the last group, which relates b but not a, is unreadable.
        connection.execute(
            "UPDATE initial_groups SET inference_basis=x'00' "
            "WHERE id=(SELECT max(id) FROM initial_groups)"
        )
    assert read_alignment_explanation(report)["candidate_count"] == 4
    detail = read_alignment_explanation(report, candidate_id="a")
    assert sorted(
        group["inference_basis"]["basis"] for group in detail["initial_groups"]
    ) == ["second", "shared"]
    assert len(detail["groups"]) == 1
    with pytest.raises(ExplanationError, match="malformed"):
        read_alignment_explanation(report, candidate_id="b")


@pytest.mark.parametrize(
    "corruption",
    ("bytes", "schema_identity", "schema", "payload"),
)
@pytest.mark.parametrize("reader", (read_alignment_explanation, read_alignment_curves))
def test_invalid_report_never_returns_empty_success(tmp_path, corruption, reader):
    report = tmp_path / "bad.sqlite"
    if corruption == "bytes":
        report.write_bytes(b"not sqlite")
    else:
        with ExplanationReportWriter(report) as writer:
            writer.finalize(explanation_summary({}))
        with sqlite3.connect(report) as connection:
            if corruption == "schema_identity":
                row = connection.execute("SELECT payload FROM run").fetchone()
                summary = json.loads(row[0])
                summary["schema_version"] = "unsupported_explanation"
                connection.execute("UPDATE run SET payload=?", (json.dumps(summary),))
            elif corruption == "schema":
                connection.execute("DROP TABLE cells")
            else:
                connection.execute("UPDATE run SET payload='null'")
    message = (
        "重新執行 Alignment"
        if corruption in ("schema_identity", "payload")
        else "malformed|failed reading"
    )
    with pytest.raises(ExplanationError, match=message):
        reader(report)
