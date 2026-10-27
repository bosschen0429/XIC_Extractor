"""Persisted explanations retain owner facts and query without acquisition."""

import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, replace

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.result.application_result import (
    UntargetBatchApplicationResultRequest,
    finalize_alignment_result,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.diagnostics.alignment_explanation import (
    CandidateNotFoundError,
    ExplanationError,
    read_cid_nl_curves,
    read_cid_nl_explanation,
    read_explanation_metadata,
    write_explanation_report,
)
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.sample_metadata import SampleMetadata


def _summary_fixture(values, *, review_only=False):
    """Complete persisted metadata for small diagnostic writer fixtures."""
    summary = {
        "processing_request_id": "fixture",
        "scientific_input_schema_version": "fixture",
        "processing_algorithm_version": "fixture",
        "processing_contract_version": "fixture",
        "processing_attempt_id": "fixture",
        "samples": [],
        "acquisition_sources": [],
        "candidate_count": 0,
        "matrix_row_count": 0,
        "candidate_examples": [],
        "writer_row_count": 0,
        "matrix_path": "",
        "matrix_sha256": "",
        "alignment_config": {},
        "reason_counts": {},
        "record_counts": {},
        "retention": "review" if review_only else "full_audit",
        "selection_evidence_capability": "recorded_decisions",
        "curve_retention": "none",
        "curve_mz_ranges": [],
        "initial_edge_coverage": {},
        "audit_availability": {},
        "known_istd_reference": None,
        **values,
    }
    summary["samples"] = [
        {"sample_stem": sample["sample_key"], **sample} for sample in summary["samples"]
    ]
    return summary


def test_missing_report_is_unavailable(tmp_path):
    from xic_extractor.diagnostics.alignment_explanation import (
        ExplanationUnavailableError,
        read_cid_nl_explanation,
    )

    with pytest.raises(ExplanationUnavailableError, match="unavailable"):
        read_cid_nl_explanation(tmp_path / "missing.sqlite")


def _owner_run(tmp_path, monkeypatch, scenario):
    physical_records = {}
    preset = tmp_path / "method.toml"
    mode = "intersection" if scenario == "all_hold" else "union"
    preset.write_text(
        'name = "two losses"\ndescription = "Explanation fixture"\n'
        f'combine_mode = "{mode}"\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "A"\nvalue = 116.0474\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "B"\nvalue = 132.0423\n'
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\nseed_rt_gap_min = 0.2\n"
        'ms1_search_padding_min = 0.2\nresolver_mode = "local_minimum"\n',
        encoding="utf-8",
    )
    settings = apply_to_discovery(load_preset(str(preset)))
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in (1, 2))
    sources, candidates = {}, {}
    for path, loss in zip(paths, (116.0474, 132.0423), strict=True):
        path.write_bytes(path.stem.encode())
        center = 8.12 if scenario == "review_only" and path.stem == "S2" else 8.0
        source = _FakeRaw((center,), neutral_loss_das=(loss,))
        sources[path] = source
        candidates[path.stem] = _current_candidates(
            source,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:explanation",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    _install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    config = AlignmentConfig(
        anchor_min_seed_events=3 if scenario == "review_only" else 1,
        # Moderate support must also lie outside the preferred RT band;
        # aligned moderate owners are eligible through batch continuity.
        preferred_rt_sec=2.0 if scenario == "review_only" else 60.0,
    )
    runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "explanation"
        ),
        alignment_config=config,
    )
    completed = complete_dense_partition(batch, runtime, alignment_config=config)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    matrix = tmp_path / "matrix.tsv"
    write_alignment_matrix_tsv(
        matrix,
        writer.matrix,
        alignment_config=config,
        production_decisions=writer.production_decisions,
    )
    return completed, writer, candidates, matrix, config


@pytest.mark.parametrize("scenario", ("positive", "all_hold", "review_only"))
def test_owner_facts_survive_persisted_candidate_query(tmp_path, monkeypatch, scenario):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, scenario
    )
    report = write_cid_nl_explanation(
        tmp_path / "report.sqlite",
        full_audit=True,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_cid_nl_explanation(report)
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
    detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
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
    assert (
        retained["artifact"]["evidence_artifact_id"]
        == assessment.artifact.evidence_artifact_id
    )
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
        assert terminal["physical_observation_ids"]
        assert terminal["trace_group_id"] not in {
            g["group_hypothesis_id"] for g in records["initial_groups"]
        }
        assert "initial_edges" not in records
        source_groups = {
            group.group_hypothesis_id: group for group in completed.initial_groups
        }
        for retained_group in records["initial_groups"]:
            source_group = source_groups[retained_group["group_hypothesis_id"]]
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
                            "physical_observation_ids": (
                                member.physical_observation_ids
                            ),
                        }
                        for member in source_group.member_subjects
                    ]
                )
            )
            assert all(
                member["physical_observation_ids"]
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
        "for x in ('raw_reader','clr','numpy')):\n"
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
        read_cid_nl_explanation(report, candidate_id="unknown")
    with pytest.raises(FileExistsError):
        write_cid_nl_explanation(
            report,
            full_audit=True,
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
    if scenario == "positive":
        missing_audit = replace(
            completed, method_assessments=(), initial_groups=(), initial_edges=()
        )
        missing_audit_report = write_cid_nl_explanation(
            tmp_path / "missing_audit.sqlite",
            full_audit=True,
            completed=missing_audit,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
        assert read_cid_nl_explanation(missing_audit_report)["audit_availability"] == {
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
            summary=_summary_fixture({}),
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

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "all_hold"
    )
    kwargs = dict(
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    original = write_cid_nl_explanation(
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

    application = finalize_alignment_result(
        UntargetBatchApplicationResultRequest(
            scope=application.scope,
            processing_request=application.processing_request,
            processing_attempt=application.processing_attempt,
            children=application.children,
            excluded_candidates=ImmutableSequence((len(excluded),), load_exclusions),
        )
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

    with recorder.stage("alignment.write_cid_nl_explanation"):
        report = write_cid_nl_explanation(
            tmp_path / "duplicated.sqlite",
            full_audit=True,
            completed=duplicated,
            progress_callback=progress,
            **kwargs,
        )
    assert loads == 1
    assert read_cid_nl_explanation(report) == read_cid_nl_explanation(original)
    assert any(event["stage"] == "alignment.explanation.exclusions" for event in events)
    assert [event["records_written"] for event in events] == sorted(
        event["records_written"] for event in events
    )
    heartbeat = json.loads(live.read_text())["heartbeat"]
    assert heartbeat["stage"] == "alignment.explanation.finalizing"
    assert heartbeat["records_written"] == sum(
        read_cid_nl_explanation(report)["record_counts"].values()
    )
    for values in candidates.values():
        for candidate in values:
            assert read_cid_nl_explanation(
                report, candidate_id=candidate.candidate_id
            ) == read_cid_nl_explanation(original, candidate_id=candidate.candidate_id)


@dataclass(frozen=True)
class _ChildBatch:
    children: tuple


@pytest.mark.parametrize("scenario", ("positive", "review_only"))
def test_explanation_loads_each_file_backed_child_once(tmp_path, monkeypatch, scenario):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, scenario
    )
    application = completed.application_result
    kwargs = dict(
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    original = write_cid_nl_explanation(
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
    restored = finalize_alignment_result(
        UntargetBatchApplicationResultRequest(
            scope=application.scope,
            processing_request=application.processing_request,
            processing_attempt=application.processing_attempt,
            children=store.sequence("children"),
            excluded_candidates=application.excluded_candidates,
        )
    )
    loads = 0
    report = write_cid_nl_explanation(
        tmp_path / "stored.sqlite",
        full_audit=True,
        completed=replace(completed, application_result=restored),
        **kwargs,
    )
    assert loads == len(application.children), (loads, len(application.children))
    assert read_cid_nl_explanation(report) == read_cid_nl_explanation(original)
    for values in candidates.values():
        for candidate in values:
            assert read_cid_nl_explanation(
                report, candidate_id=candidate.candidate_id
            ) == read_cid_nl_explanation(original, candidate_id=candidate.candidate_id)


def test_explanation_progress_failure_does_not_publish_report(tmp_path, monkeypatch):
    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "all_hold"
    )
    report = tmp_path / "interrupted.sqlite"

    def interrupted(event):
        if event["stage"] == "alignment.explanation.method_assessments":
            raise OSError("progress unavailable")

    with pytest.raises(OSError, match="progress unavailable"):
        write_cid_nl_explanation(
            report,
            full_audit=True,
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
            progress_callback=interrupted,
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


@pytest.mark.parametrize("flushed", [False, True])
def test_incremental_report_deduplicates_and_aborts_conflicts(tmp_path, flushed):
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

    report = tmp_path / "stream.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.add_candidate("a", "s", {"candidate_id": "a", "availability": {}})
        writer.update_availability(("a", "s"), terminal="excluded")
        with pytest.raises(ValueError, match="duplicate diagnostic candidate/sample"):
            writer.add_candidate("a", "s", {"candidate_id": "a"})
        assert writer.add_record("facts", "one", {"value": 1}, (("a", "s"),))
        if flushed:
            writer._flush_records()
        assert not writer.add_record("facts", "one", {"value": 1}, (("a", "s"),))
        writer.finalize(_summary_fixture({}, review_only=writer.review_only))
    detail = read_cid_nl_explanation(report, candidate_id="a")
    assert detail["records"] == {"facts": [{"value": 1}]}
    assert detail["candidates"][0]["availability"] == {"terminal": "excluded"}
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
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

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
        writer.finalize(_summary_fixture({}, review_only=writer.review_only))

    expected = {
        (candidate_id, "S1", "facts", "one") for candidate_id in ("C1", "C2", "C3")
    }
    assert set(read_explanation_metadata(report).links) == expected
    for candidate_id in ("C1", "C2", "C3"):
        detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
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
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

    report = tmp_path / "missing-candidate.sqlite"
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with ExplanationReportWriter(report) as writer:
            writer.add_record("facts", "one", {"value": 1}, (("missing", "S1"),))
            writer.finalize(_summary_fixture({}))
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
        summary=_summary_fixture({}),
        candidates=rows(),
        records=Records(),
        links=(("199", "s", "facts", "199"),),
    )
    assert read_cid_nl_explanation(report, candidate_id="199")["candidates"] == [
        {"candidate_id": "199"}
    ]
    assert read_cid_nl_explanation(report, candidate_id="199")["records"] == {
        "facts": [{"value": 199}]
    }


def test_indexed_query_preserves_many_groups_and_skips_unrelated_payloads(tmp_path):
    report = write_explanation_report(
        tmp_path / "indexed.sqlite",
        summary=_summary_fixture({"candidate_count": 2}),
        candidates=(
            ("a", "s1", {"candidate_id": "a"}),
            ("b", "s2", {"candidate_id": "b"}),
        ),
        records={
            ("initial_groups", "shared"): {"group_hypothesis_id": "shared"},
            ("initial_groups", "second"): {"group_hypothesis_id": "second"},
            ("final_groups", "shared"): {"group_hypothesis_id": "shared"},
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
    assert read_cid_nl_explanation(report)["candidate_count"] == 2
    detail = read_cid_nl_explanation(report, candidate_id="a")
    assert len(detail["records"]["initial_groups"]) == 2
    assert len(detail["records"]["final_groups"]) == 1
    with pytest.raises(ExplanationError, match="malformed"):
        read_cid_nl_explanation(report, candidate_id="b")


@pytest.mark.parametrize(
    "corruption",
    ("bytes", "schema_identity", "schema", "payload"),
)
@pytest.mark.parametrize("reader", (read_cid_nl_explanation, read_cid_nl_curves))
def test_invalid_report_never_returns_empty_success(tmp_path, corruption, reader):
    report = tmp_path / "bad.sqlite"
    if corruption == "bytes":
        report.write_bytes(b"not sqlite")
    else:
        write_explanation_report(
            report, summary=_summary_fixture({}), candidates=(), records={}, links=()
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
