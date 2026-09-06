"""Persisted explanations retain owner facts and query without acquisition."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _peak_config,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.diagnostics.cid_nl_explanation import (
    CandidateNotFoundError,
    ExplanationError,
    read_cid_nl_explanation,
    write_explanation_report,
)
from xic_extractor.diagnostics.cid_nl_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
)
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.sample_metadata import SampleMetadata


def test_missing_report_is_unavailable(tmp_path):
    from xic_extractor.diagnostics.cid_nl_explanation import (
        ExplanationUnavailableError,
        read_cid_nl_explanation,
    )

    with pytest.raises(ExplanationUnavailableError, match="unavailable"):
        read_cid_nl_explanation(tmp_path / "missing.sqlite")


def _owner_run(tmp_path, monkeypatch, scenario):
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
        source = _FakeRaw((8.0,), neutral_loss_das=(loss,))
        sources[path] = source
        candidates[path.stem] = _current_candidates(
            source, raw_file=path, settings=settings, peak_config=peak_config
        )
    batch = build_untargeted_cid_nl_production_batch(
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
    )
    _install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "explanation"
        ),
    )
    config = AlignmentConfig(
        anchor_min_seed_events=3 if scenario == "review_only" else 1
    )
    completed = compose_untargeted_cid_nl_production_result(
        batch, runtime, alignment_config=config
    )
    writer = project_completed_untarget_batch_writer_inputs(completed)
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
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_cid_nl_explanation(report)
    assert summary["diagnostic_only"] is True
    assert summary["matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    assert summary["matrix_row_count"] == (1 if scenario == "positive" else 0)
    assert summary["candidate_count"] == sum(map(len, candidates.values()))
    candidate_id = candidates["S1"][0].candidate_id
    detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
    records = detail["records"]
    assessment = next(
        item
        for item in completed.method_assessments
        if item.content.canonical_sample_identity_id
        == detail["candidates"][0]["canonical_sample_identity_id"]
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
        assert records["initial_edges"][0]["initial_evidence"] == json.loads(
            json.dumps(asdict(completed.initial_edges[0].initial_evidence))
        )
        assert (
            records["initial_edges"][0]["disposition"]
            == completed.initial_edges[0].disposition
        )
        if scenario == "review_only":
            assert retained["method_result"] == "SUPPORT"
            assert (
                records["initial_edges"][0]["initial_evidence"]["seed_support_level"]
                == "moderate"
            )
            assert records["initial_edges"][0]["disposition"] == "review_only"
            assert records["rows"][0]["route"]["destination"] == "archive"
        else:
            assert records["rows"][0]["route"]["destination"] == "matrix"
            assert records["cell_decisions"][0]["value"] > 0
            assert records["cell_observations"][0]["predecessor_cells"]
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
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
    if scenario == "positive":
        legacy = replace(
            completed, method_assessments=(), initial_groups=(), initial_edges=()
        )
        legacy_report = write_cid_nl_explanation(
            tmp_path / "legacy.sqlite",
            completed=legacy,
            writer_inputs=writer,
            candidates_by_sample=candidates,
            matrix_path=matrix,
            alignment_config=config,
        )
        assert read_cid_nl_explanation(legacy_report)["audit_availability"] == {
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
            report, summary={}, candidates=interrupted(), records={}, links=()
        )
    assert not report.exists()
    assert not report.with_name(report.name + ".tmp").exists()


def test_indexed_query_preserves_many_groups_and_skips_unrelated_payloads(tmp_path):
    report = write_explanation_report(
        tmp_path / "indexed.sqlite",
        summary={"candidate_count": 2},
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


@pytest.mark.parametrize("corruption", ("bytes", "version", "schema", "payload"))
def test_invalid_report_never_returns_empty_success(tmp_path, corruption):
    report = tmp_path / "bad.sqlite"
    if corruption == "bytes":
        report.write_bytes(b"not sqlite")
    else:
        write_explanation_report(
            report, summary={}, candidates=(), records={}, links=()
        )
        with sqlite3.connect(report) as connection:
            if corruption == "version":
                connection.execute("PRAGMA user_version=42")
            elif corruption == "schema":
                connection.execute("DROP TABLE records")
            else:
                connection.execute("UPDATE metadata SET payload='null'")
    with pytest.raises(ExplanationError, match="malformed|unsupported"):
        read_cid_nl_explanation(report)
