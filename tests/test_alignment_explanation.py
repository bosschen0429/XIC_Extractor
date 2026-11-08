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
    read_explanation_metadata,
)
from xic_extractor.alignment_explanation.writer import (
    write_explanation_report,
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
    completed, writer, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    first = candidates["S1"][0].candidate_id
    second = candidates["S2"][0].candidate_id
    measured = {first: 500.2010}
    if second_measured_mz is not None:
        measured[second] = second_measured_mz
    report = write_alignment_explanation(
        tmp_path / "measured.sqlite",
        completed=completed,
        candidates_by_sample=candidates,
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
        assert candidate["precursor_mz"] == candidates[stem][0].precursor_mz


def test_old_named_folder_requires_rerun_instead_of_fallback(tmp_path):
    from xic_inspector.api import resolve_source

    legacy = tmp_path / ("_".join(("cid", "nl", "explanation")) + ".sqlite")
    legacy.touch()
    with pytest.raises(FileNotFoundError, match="rerun Alignment"):
        resolve_source(tmp_path)


@pytest.mark.parametrize("scenario", ("positive", "all_hold", "review_only"))
def test_owner_facts_survive_persisted_candidate_query(tmp_path, monkeypatch, scenario):
    completed, writer, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, scenario
    )
    report = write_alignment_explanation(
        tmp_path / "report.sqlite",
        full_audit=True,
        completed=completed,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_alignment_explanation(report)
    assert summary["diagnostic_only"] is True
    assert summary["initial_edge_coverage"] == {
        "policy": "feature-group-membership",
        "unlisted_pairs": "not_evaluated",
        "rt_pruning": True,
        "retained_pair_record_count": len(completed.initial_edges),
        "compressed_group_count": len(completed.initial_groups),
        "compressed_member_count": sum(
            len(group.member_subjects) for group in completed.initial_groups
        ),
    }
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    with matrix.open(encoding="utf-8-sig", newline="") as matrix_handle:
        assert summary["matrix_row_count"] == sum(
            1 for _ in csv.DictReader(matrix_handle, delimiter="\t")
        )
    assert summary["candidate_count"] == sum(map(len, candidates.values()))
    candidate_id = candidates["S1"][0].candidate_id
    detail = read_alignment_explanation(report, candidate_id=candidate_id)
    records = detail["records"]
    assessment = next(
        item
        for item in completed.method_assessments
        if item.content.sample_key == detail["candidates"][0]["sample_key"]
    )
    retained = records["method_assessments"][0]
    assert retained["reason_codes"] == list(assessment.content.reason_codes)
    assert retained["method_result"] == assessment.content.method_result
    assert retained["evaluation_receipt"] == json.loads(
        json.dumps(asdict(assessment.content.evaluation_receipt))
    )
    assert "artifact" not in retained
    assert all(item["candidate_id"] == candidate_id for item in detail["candidates"])
    if scenario == "all_hold":
        excluded = next(
            item
            for item in completed.application_result.excluded_candidates
            if item.candidate_id == candidate_id
        )
        assert records["exclusions"][0]["reason_code"] == excluded.reason_code
        assert "terminal_subjects" not in records
        assert "rows" not in records
        assert retained["method_result"] == "HOLD"
    else:
        terminal = records["terminal_subjects"][0]
        assert terminal["physical_peak_keys"]
        assert "initial_edges" not in records
        source_groups = {
            tuple(member.subject_id for member in group.member_subjects): group
            for group in completed.initial_groups
        }
        for retained_group in records["initial_groups"]:
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
            assert records["rows"][0]["route"]["destination"] == "archive"
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
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
    if scenario == "positive":
        missing_audit = replace(
            completed, method_assessments=(), initial_groups=(), initial_edges=()
        )
        missing_audit_report = write_alignment_explanation(
            tmp_path / "missing_audit.sqlite",
            full_audit=True,
            completed=missing_audit,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
        assert read_alignment_explanation(missing_audit_report)[
            "audit_availability"
        ] == {
            "method_assessments": "unavailable",
            "initial_groups": "unavailable",
            "initial_edges": "unavailable",
        }


def test_partial_write_never_publishes_complete_report(tmp_path):
    report = tmp_path / "partial.sqlite"

    def interrupted():
        yield "a", "s", {"candidate_id": "a"}
        raise OSError("interrupted write")

    with pytest.raises(OSError, match="interrupted write"):
        write_explanation_report(
            report,
            summary=explanation_summary({}),
            candidates=interrupted(),
            records={},
            links=(),
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


@dataclass(frozen=True)
class _AssessmentBatch:
    method_assessments: tuple


def test_exclusions_load_once_with_restored_duplicate_assessments(
    tmp_path, monkeypatch
):
    from xic_extractor.execution.timing import TimingRecorder

    completed, writer, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "all_hold"
    )
    kwargs = dict(
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    original = write_alignment_explanation(
        tmp_path / "original.sqlite",
        completed=completed,
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
        processing_request=application.processing_request,
        processing_attempt=application.processing_attempt,
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
    assert heartbeat["records_written"] == sum(
        read_alignment_explanation(report)["record_counts"].values()
    )
    for values in candidates.values():
        for candidate in values:
            assert read_alignment_explanation(
                report, candidate_id=candidate.candidate_id
            ) == read_alignment_explanation(
                original, candidate_id=candidate.candidate_id
            )


@dataclass(frozen=True)
class _ChildBatch:
    children: tuple


@pytest.mark.parametrize("scenario", ("positive", "review_only"))
def test_explanation_loads_each_file_backed_child_once(tmp_path, monkeypatch, scenario):
    completed, writer, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, scenario
    )
    application = completed.application_result
    kwargs = dict(
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    original = write_alignment_explanation(
        tmp_path / "memory.sqlite",
        completed=completed,
        **kwargs,
        full_audit=True,
    )
    store = StreamedResultStore(
        tmp_path / "child-store",
        (
            application.processing_request,
            application.processing_attempt,
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
        processing_request=application.processing_request,
        processing_attempt=application.processing_attempt,
        children=store.sequence("children"),
        excluded_candidates=application.excluded_candidates,
    )
    loads = 0
    report = write_alignment_explanation(
        tmp_path / "stored.sqlite",
        full_audit=True,
        completed=replace(completed, application_result=restored),
        **kwargs,
    )
    assert loads == len(application.children), (loads, len(application.children))
    assert read_alignment_explanation(report) == read_alignment_explanation(original)
    for values in candidates.values():
        for candidate in values:
            assert read_alignment_explanation(
                report, candidate_id=candidate.candidate_id
            ) == read_alignment_explanation(
                original, candidate_id=candidate.candidate_id
            )


def test_explanation_progress_failure_does_not_publish_report(tmp_path, monkeypatch):
    completed, writer, candidates, matrix, config = run_alignment_fixture(
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
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
            progress_callback=interrupted,
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


@pytest.mark.parametrize("flushed", [False, True])
def test_incremental_report_deduplicates_and_aborts_conflicts(tmp_path, flushed):
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    report = tmp_path / "stream.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.add_candidate("a", "s", {"candidate_id": "a"})
        with pytest.raises(ValueError, match="duplicate diagnostic candidate/sample"):
            writer.add_candidate("a", "s", {"candidate_id": "a"})
        assert writer.add_record("facts", "one", {"value": 1}, (("a", "s"),))
        if flushed:
            writer._flush_records()
        assert not writer.add_record("facts", "one", {"value": 1}, (("a", "s"),))
        writer.finalize(
            explanation_summary(
                {"samples": [{"sample_key": "s"}]}, review_only=writer.review_only
            )
        )
    detail = read_alignment_explanation(report, candidate_id="a")
    assert detail["records"] == {"facts": [{"value": 1}]}
    failed = tmp_path / "conflict.sqlite"
    with pytest.raises(ValueError, match="conflicting diagnostic owner identity"):
        with ExplanationReportWriter(failed) as writer:
            writer.add_record("facts", "one", {"value": 1}, ())
            if flushed:
                writer._flush_records()
            writer.add_record("facts", "one", {"value": 2}, ())
    assert not failed.exists()
    assert not failed.with_name(failed.name + ".tmp").exists()


def test_related_set_links_preserve_union_through_record_foreign_keys(tmp_path):
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    report = tmp_path / "normalized.sqlite"
    with ExplanationReportWriter(report) as writer:
        for candidate_id in ("C1", "C2", "C3"):
            writer.add_candidate(
                candidate_id,
                "S1",
                {"candidate_id": candidate_id, "sample_id": "S1"},
            )
        writer.add_record(
            "facts",
            "one",
            {"value": 1},
            (("C1", "S1"), ("C2", "S1")),
        )
        # Same record, reordered members and an overlapping extension must
        # produce one exact logical union after view expansion.
        writer.add_record(
            "facts",
            "one",
            {"value": 1},
            (("C2", "S1"), ("C1", "S1")),
        )
        writer.add_record(
            "facts",
            "one",
            {"value": 1},
            (("C2", "S1"), ("C3", "S1")),
        )
        writer.finalize(
            explanation_summary(
                {"samples": [{"sample_key": "S1"}]}, review_only=writer.review_only
            )
        )

    expected = {
        (candidate_id, "S1", "facts", "one") for candidate_id in ("C1", "C2", "C3")
    }
    assert set(read_explanation_metadata(report).links) == expected
    for candidate_id in ("C1", "C2", "C3"):
        detail = read_alignment_explanation(report, candidate_id=candidate_id)
        assert detail["records"] == {"facts": [{"value": 1}]}

    with sqlite3.connect(report) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
        assert connection.execute(
            "SELECT DISTINCT typeof(record_rowid) FROM record_links"
        ).fetchall() == [("integer",)]
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM records")


def test_report_rejects_candidate_links_without_a_candidate(tmp_path):
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )

    report = tmp_path / "missing-candidate.sqlite"
    with pytest.raises(KeyError, match="missing"):
        with ExplanationReportWriter(report) as writer:
            writer.add_record("facts", "one", {"value": 1}, (("missing", "S1"),))
            writer.finalize(explanation_summary({}))
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_report_consumes_candidate_payloads_without_retaining_rows(tmp_path):
    import weakref

    class Payload(dict):
        pass

    previous = None

    def rows():
        nonlocal previous
        for index in range(200):
            if previous is not None:
                assert previous() is None, "writer retained a consumed producer row"
            payload = Payload(candidate_id=str(index))
            previous = weakref.ref(payload)
            yield str(index), "s", payload
            del payload

    class Records(dict):
        def items(self):
            nonlocal previous
            previous = None
            for index in range(200):
                if previous is not None:
                    assert previous() is None, "writer retained a consumed record"
                payload = Payload(value=index)
                previous = weakref.ref(payload)
                yield ("facts", str(index)), payload
                del payload

    report = write_explanation_report(
        tmp_path / "bounded.sqlite",
        summary=explanation_summary({"samples": [{"sample_key": "s"}]}),
        candidates=rows(),
        records=Records(),
        links=(("199", "s", "facts", "199"),),
    )
    candidate = read_alignment_explanation(report, candidate_id="199")["candidates"][0]
    assert (candidate["candidate_id"], candidate["sample_key"]) == ("199", "s")
    assert read_alignment_explanation(report, candidate_id="199")["records"] == {
        "facts": [{"value": 199}]
    }


def test_indexed_query_preserves_many_groups_and_skips_unrelated_payloads(tmp_path):
    report = write_explanation_report(
        tmp_path / "indexed.sqlite",
        summary=explanation_summary(
            {"candidate_count": 2, "samples": [{"sample_key": s} for s in ("s1", "s2")]}
        ),
        candidates=(
            ("a", "s1", {"candidate_id": "a"}),
            ("b", "s2", {"candidate_id": "b"}),
        ),
        records={
            ("initial_groups", "shared"): {"feature_id": "shared"},
            ("initial_groups", "second"): {"feature_id": "second"},
            ("final_groups", "shared"): {"feature_id": "shared"},
            ("exclusions", "unrelated"): {"reason_code": "unrelated"},
        },
        links=(
            ("a", "s1", "initial_groups", "shared"),
            ("a", "s1", "initial_groups", "second"),
            ("a", "s1", "final_groups", "shared"),
            ("b", "s2", "initial_groups", "shared"),
            ("b", "s2", "exclusions", "unrelated"),
        ),
    )
    with sqlite3.connect(report) as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 4
        connection.execute(
            "UPDATE candidates SET payload='invalid' WHERE candidate_id='b'"
        )
        connection.execute(
            "UPDATE records SET payload='invalid' WHERE kind='exclusions'"
        )
    assert read_alignment_explanation(report)["candidate_count"] == 2
    detail = read_alignment_explanation(report, candidate_id="a")
    assert len(detail["records"]["initial_groups"]) == 2
    assert len(detail["records"]["final_groups"]) == 1
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
        write_explanation_report(
            report, summary=explanation_summary({}), candidates=(), records={}, links=()
        )
        with sqlite3.connect(report) as connection:
            if corruption == "schema_identity":
                row = connection.execute("SELECT payload FROM metadata").fetchone()
                summary = json.loads(row[0])
                summary["schema_version"] = "unsupported_explanation"
                connection.execute(
                    "UPDATE metadata SET payload=?", (json.dumps(summary),)
                )
            elif corruption == "schema":
                connection.execute("DROP TABLE records")
            else:
                connection.execute("UPDATE metadata SET payload='null'")
    message = (
        "重新執行 Alignment"
        if corruption == "schema_identity"
        else "malformed|failed reading"
    )
    with pytest.raises(ExplanationError, match=message):
        reader(report)
