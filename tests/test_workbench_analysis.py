"""The HTML entry saves editable inputs and retains analysis lifecycle results."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from tests.test_xic_inspector_api import _create_mock_db
from tests.test_xic_workbench import _client
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_extractor.raw_reader import RawSampleHeader
from xic_inspector.api import SelectionRequest, prepare_report
from xic_workbench.analysis_workspace import AnalysisWorkspace
from xic_workbench.jobs import AnalysisJobs
from xic_workbench.workspace import ReviewWorkspace, WorkspaceBusy


def _terminal(jobs):
    deadline = time.monotonic() + 5
    while jobs.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not jobs.busy
    return jobs.state()


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_next_job_keeps_running_clock_after_previous_job_finishes(
    tmp_path, monkeypatch, outcome
):
    jobs = AnalysisJobs(tmp_path)
    terminal_visible = threading.Event()
    finish_previous = threading.Event()
    next_running = threading.Event()
    finish_next = threading.Event()
    clock = [100.0]
    monkeypatch.setattr("xic_workbench.jobs.time.time", lambda: clock[0])

    class PauseAfterTerminal:
        """Let a request arrive as soon as the previous terminal state is visible."""

        def __init__(self):
            self.lock = threading.RLock()
            self.paused = False

        def __enter__(self):
            self.lock.acquire()
            return self

        def __exit__(self, *args):
            pause = (
                threading.current_thread().name.startswith("xic-analysis")
                and not self.paused
                and jobs._current["status"] == outcome
            )
            if pause:
                self.paused = True
            self.lock.release()
            if pause:
                terminal_visible.set()
                assert finish_previous.wait(5)

    def run(kind, values):
        if values["name"] == "next":
            next_running.set()
            assert finish_next.wait(5)
        elif outcome == "failed":
            raise ValueError("first failed")
        elif outcome == "cancelled":
            raise InterruptedError("first cancelled")
        return values

    jobs._lock = PauseAfterTerminal()
    monkeypatch.setattr(jobs, "_run", run)
    try:
        jobs.start("samples", {"name": "first"})
        assert terminal_visible.wait(5)
        clock[0] = 200.0
        next_job = jobs.start("samples", {"name": "next"})["job"]
        finish_previous.set()
        assert next_running.wait(5)
        clock[0] = 207.0
        state = jobs.state()["job"]
        assert state["job_id"] == next_job["job_id"]
        assert state["status"] == "running"
        assert "finished_at" not in state
        assert state["elapsed_seconds"] == 7.0
        clock[0] = 211.0
        assert jobs.state()["job"]["elapsed_seconds"] == 11.0
    finally:
        finish_previous.set()
        finish_next.set()
        jobs.close()


def test_analysis_failure_and_cancel_retain_previous_published_results(
    tmp_path, monkeypatch
):
    from xic_workbench import targeted

    entered = threading.Event()
    proceed = threading.Event()
    mode = ["succeed"]

    class Run:
        def __init__(self, config_dir, *, progress, should_cancel):
            self.stage = "讀取指定 RAW"
            self.output_dir = tmp_path / "output"
            self.available_outputs = ()
            self.progress = progress
            self.should_cancel = should_cancel

        def execute(self):
            if mode[0] == "succeed":
                output = self.output_dir / "results.xlsx"
                output.parent.mkdir()
                output.write_bytes(b"published-result")
                self.available_outputs = (("結果", output),)
                return {
                    "published_paths": [str(output)],
                    "targets": [
                        {"label": "A", "median_area": 10},
                        {"label": "B", "median_area": 29},
                    ],
                }
            self.progress(1, 2, "second.raw")
            entered.set()
            assert proceed.wait(3)
            if self.should_cancel():
                raise InterruptedError("使用者停止；已發布結果保留")
            raise ValueError("second.raw 無法讀取")

    monkeypatch.setattr(targeted, "TargetedRun", Run)
    monkeypatch.setattr("xic_workbench.config_io.write_settings", lambda *a, **kw: None)
    monkeypatch.setattr("xic_workbench.config_io.write_targets", lambda *a, **kw: None)
    jobs = AnalysisJobs(tmp_path)
    try:
        jobs.start("targeted", {"settings": {}, "targets": []})
        success = _terminal(jobs)["results"]["targeted"]
        mode[0] = "fail"
        running = jobs.start("targeted", {"settings": {}, "targets": []})
        assert entered.wait(3)
        with pytest.raises(WorkspaceBusy):
            jobs.start("samples", {})
        assert jobs.state()["job"]["message"] == "second.raw"
        proceed.set()
        state = _terminal(jobs)
        assert state["job"]["status"] == "failed"
        assert state["results"]["targeted"] == success
        entered.clear()
        proceed.clear()
        running = jobs.start("targeted", {"settings": {}, "targets": []})
        assert entered.wait(3)
        assert jobs.cancel(running["job"]["job_id"])["job"]["status"] == "cancelling"
        proceed.set()
        state = _terminal(jobs)
        assert state["job"]["status"] == "cancelled"
        assert state["results"]["targeted"] == success
        assert Path(success["published_paths"][0]).read_bytes() == b"published-result"
    finally:
        proceed.set()
        jobs.close()


def test_http_analysis_configuration_roundtrip_without_qt(tmp_path):
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    with _client(workspace) as request:
        status, config, _ = request("/api/config")
        assert status == 200
        assert config["targeted_form"]["settings"]
        settings = config["settings"] | {
            "data_dir": "C:/new batch",
        }
        targets = [
            config["targets"][0]
            | {"label": "precise", "mz": "501.1234567890123", "extra": "keep-me"}
        ]
        assert (
            request("/api/targeted/save", {"settings": settings, "targets": targets})[0]
            == 200
        )
        _, saved, _ = request("/api/config")
        assert saved["settings"]["data_dir"] == "C:/new batch"
        assert saved["targets"] == targets
        assert (
            request("/api/targets/import", {"path": str(tmp_path / "missing.csv")})[0]
            == 400
        )
        assert request("/api/config")[1]["targets"] == targets
        assert (
            request("/api/preferences", {"theme": "dark", "view": "targeted"})[0] == 200
        )
    persisted = json.loads((tmp_path / "config" / "workbench.json").read_text())
    assert persisted == {"theme": "dark", "view": "targeted"}


def test_artifact_urls_serve_only_registered_published_files(tmp_path):
    analysis = AnalysisWorkspace(tmp_path / "config", {})
    published = tmp_path / "published.html"
    published.write_text("<h1>Published report</h1>")
    analysis.jobs._results["targeted"] = {"published_paths": [str(published)]}
    try:
        artifact = analysis.state()["results"]["targeted"]["artifacts"][0]
        assert analysis.artifact_path(artifact["url"].split("/")[-1]) == published
        with pytest.raises(ValueError):
            analysis.artifact_path("unregistered")
    finally:
        analysis.close()


@pytest.mark.parametrize("mode", ["full", "align_only"])
def test_untargeted_job_loads_saved_sequence_metadata_when_samples_are_absent(
    tmp_path, monkeypatch, mode
):
    from xic_workbench import untargeted

    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw = raw_dir / "Sample-A.raw"
    raw.write_bytes(b"")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    monkeypatch.setattr(
        "xic_extractor.raw_reader.read_raw_sample_header",
        lambda path, dll_dir: RawSampleHeader(
            raw_path=str(path),
            raw_stem=path.stem,
            sample_name="Header Alpha",
            sample_id="A",
            row_number=4,
            sample_type="Unknown",
            creation_date=None,
            vial="A:04",
            comment="",
        ),
    )
    sequence = tmp_path / "sample_metadata.tsv"
    sequence.write_text(
        "schema_version\tsample_name\traw_stem\tinjection_order\t"
        "sample_role\tbatch_id\tprep_batch_id\tmatrix_type\tgroup\t"
        "excluded\texclusion_reason\n"
        "sample_metadata\tAlpha\tSample-A\t\tstudy_sample\tB1\tP1\t"
        "plasma\tcase\tfalse\t\n",
        encoding="utf-8",
    )
    captured = {}

    class Run:
        stage = "done"
        output_dir = tmp_path / "output"
        available_outputs = ()

        def __init__(self, request, *, progress, should_cancel):
            captured["request"] = request

        def execute(self):
            return {"published_paths": []}

    monkeypatch.setattr(untargeted, "UntargetedRun", Run)
    jobs = AnalysisJobs(tmp_path / "config")
    try:
        jobs.start(
            "untargeted",
            {
                "mode": mode,
                "preset": "dna_dr",
                "raw_dir": str(raw_dir),
                "raw_file": "",
                "dll_dir": str(dll_dir),
                "output_dir": str(tmp_path / "output"),
                "discovery_batch_index": str(tmp_path / "batch.csv"),
                "targets_csv": "",
                "sequence_source": str(sequence),
                "custom_roles": {"Sample-A": "pooled_qc"},
                "samples": None,
                "overrides": {},
            },
        )
        state = _terminal(jobs)

        assert state["job"]["status"] == "succeeded"
        metadata = captured["request"].sample_metadata
        assert metadata is not None
        assert [(row.sample_name, row.sample_role) for row in metadata] == [
            ("Alpha", "pooled_qc")
        ]
        assert metadata[0].injection_order == 4
    finally:
        jobs.close()


def test_untargeted_job_uses_saved_global_execution_profile_when_omitted(
    tmp_path, monkeypatch
):
    from xic_workbench import untargeted
    from xic_workbench.config_io import write_settings

    config_dir = tmp_path / "config"
    write_settings({"parallel_workers": "3"}, config_dir=config_dir)
    captured = {}

    class Run:
        stage = "done"
        output_dir = tmp_path / "output"
        available_outputs = ()

        def __init__(self, request, *, progress, should_cancel):
            captured["request"] = request

        def execute(self):
            return {"published_paths": []}

    monkeypatch.setattr(untargeted, "UntargetedRun", Run)
    jobs = AnalysisJobs(config_dir)
    try:
        jobs.start(
            "untargeted",
            {
                "mode": "discovery_only",
                "preset": "dna_dr",
                "raw_dir": "",
                "raw_file": str(tmp_path / "one.raw"),
                "dll_dir": str(tmp_path / "dll"),
                "output_dir": str(tmp_path / "output"),
                "discovery_batch_index": "",
                "targets_csv": "",
                "sequence_source": "",
                "custom_roles": {},
                "samples": None,
                "overrides": {},
            },
        )
        state = _terminal(jobs)

        assert state["job"]["status"] == "succeeded"
        assert captured["request"].execution_profile.parallel_workers == 3
    finally:
        jobs.close()


def test_untargeted_job_completes_explicit_sample_order_from_raw_headers(
    tmp_path, monkeypatch
):
    from xic_workbench import untargeted

    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw_a = raw_dir / "Sample-A.raw"
    raw_b = raw_dir / "Sample-B.raw"
    raw_a.write_bytes(b"")
    raw_b.write_bytes(b"")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    header_reads = []

    def read_header(path, dll_dir):
        header_reads.append(path)
        return RawSampleHeader(
            raw_path=str(path),
            raw_stem=path.stem,
            sample_name=f"Header {path.stem}",
            sample_id=path.stem,
            row_number={"Sample-A": 2, "Sample-B": 1}[path.stem],
            sample_type="Unknown",
            creation_date=None,
            vial="",
            comment="",
        )

    monkeypatch.setattr("xic_extractor.raw_reader.read_raw_sample_header", read_header)
    captured = {}

    class Run:
        stage = "done"
        output_dir = tmp_path / "output"
        available_outputs = ()

        def __init__(self, request, *, progress, should_cancel):
            captured["request"] = request

        def execute(self):
            return {"published_paths": []}

    monkeypatch.setattr(untargeted, "UntargetedRun", Run)
    samples = [
        {
            "sample_name": "Operator Alpha",
            "raw_stem": "Sample-A",
            "injection_order": None,
            "sample_role": "qc",
            "batch_id": "Operator batch",
            "prep_batch_id": "Prep A",
            "matrix_type": "plasma",
            "group": "case",
            "excluded": False,
            "exclusion_reason": "",
        },
        {
            "sample_name": "Operator Beta",
            "raw_stem": "Sample-B",
            "injection_order": 9,
            "sample_role": "blank",
            "batch_id": "Operator batch",
            "prep_batch_id": "Prep B",
            "matrix_type": "plasma",
            "group": "control",
            "excluded": True,
            "exclusion_reason": "operator",
        },
    ]
    jobs = AnalysisJobs(tmp_path / "config")
    try:
        jobs.start(
            "untargeted",
            {
                "mode": "full",
                "preset": "dna_dr",
                "raw_dir": str(raw_dir),
                "raw_file": "",
                "dll_dir": str(dll_dir),
                "output_dir": str(tmp_path / "output"),
                "discovery_batch_index": "",
                "targets_csv": "",
                "sequence_source": "",
                "custom_roles": {},
                "samples": samples,
                "overrides": {},
            },
        )
        state = _terminal(jobs)

        assert state["job"]["status"] == "succeeded"
        metadata = captured["request"].sample_metadata
        assert metadata is not None
        assert [row.injection_order for row in metadata] == [2, 9]
        assert [row.sample_name for row in metadata] == [
            "Operator Alpha",
            "Operator Beta",
        ]
        assert metadata[1].excluded is True
        assert header_reads == [raw_a, raw_b]
    finally:
        jobs.close()


def test_untargeted_job_leaves_default_metadata_to_alignment_owner(
    tmp_path, monkeypatch
):
    from xic_workbench import untargeted

    captured = {}

    class Run:
        stage = "done"
        output_dir = tmp_path / "output"
        available_outputs = ()

        def __init__(self, request, *, progress, should_cancel):
            captured["request"] = request

        def execute(self):
            return {"published_paths": []}

    monkeypatch.setattr(untargeted, "UntargetedRun", Run)
    monkeypatch.setattr(
        "xic_workbench.samples.derive_sample_metadata_from_raw_files",
        lambda *_args, **_kwargs: pytest.fail(
            "adapter must leave default metadata alone"
        ),
    )
    jobs = AnalysisJobs(tmp_path / "config")
    try:
        jobs.start(
            "untargeted",
            {
                "mode": "align_only",
                "preset": "dna_dr",
                "raw_dir": str(tmp_path / "raws"),
                "raw_file": "",
                "dll_dir": str(tmp_path / "dll"),
                "output_dir": str(tmp_path / "output"),
                "discovery_batch_index": str(tmp_path / "batch.csv"),
                "targets_csv": "",
                "sequence_source": "",
                "custom_roles": {},
                "samples": None,
                "overrides": {},
            },
        )
        state = _terminal(jobs)

        assert state["job"]["status"] == "succeeded"
        assert captured["request"].sample_metadata is None
    finally:
        jobs.close()


def test_inspector_job_rejects_source_not_bound_to_published_untargeted_result(
    tmp_path,
):
    current_dir = tmp_path / "current"
    stale_dir = tmp_path / "stale"
    current_dir.mkdir()
    stale_dir.mkdir()
    current_report, current_matrix, current_hash = _create_mock_db(current_dir)
    stale_report, _, _ = _create_mock_db(
        stale_dir, request_id="req-stale", attempt_id="att-stale"
    )
    expected_source = prepare_report(
        current_report, SelectionRequest(strategy="sample", limit=10)
    ).source
    assert isinstance(expected_source, EvidenceLocator)
    current_output = tmp_path / "current-inspector.html"
    stale_output = tmp_path / "stale-inspector.html"
    jobs = AnalysisJobs(tmp_path / "config")
    assert expected_source.matrix_path == current_matrix.resolve()
    assert expected_source.matrix_sha256 == current_hash
    jobs._results["untargeted"] = {"evidence": expected_source}
    try:
        jobs.start(
            "inspector",
            {
                "source": str(current_report),
                "output": str(current_output),
                "selection": {"strategy": "sample", "limit": 10},
            },
        )
        succeeded = _terminal(jobs)
        assert succeeded["job"]["status"] == "succeeded"
        published = succeeded["results"]["inspector"]

        jobs.start(
            "inspector",
            {
                "source": str(stale_report),
                "output": str(stale_output),
                "selection": {"strategy": "sample", "limit": 10},
            },
        )
        state = _terminal(jobs)

        assert state["job"]["status"] == "failed"
        assert "expected_source" in state["job"]["error"]
        assert state["results"]["inspector"] == published
        assert current_output.is_file()
        assert not stale_output.exists()
    finally:
        jobs.close()




def test_http_targeted_bootstrap_explains_active_source(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text(
        "key,value,description\nsignal_processing_window_points,21,saved\n",
        encoding="utf-8",
    )
    workspace = ReviewWorkspace(config_dir=config_dir)
    with _client(workspace) as request:
        status, config, _ = request("/api/config")
        assert status == 200
        assert config["settings"]["signal_processing_window_points"] == "21"
        summary = config["targeted_method"]
        assert summary["warnings"]
        assert config["settings_source"] == "settings.csv"


def test_targeted_bootstrap_combines_saved_settings_and_launch_values(
    tmp_path,
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text(
        "key,value,description\nsignal_processing_window_points,21,saved\n",
        encoding="utf-8",
    )
    workspace = AnalysisWorkspace(
        config_dir, {"raw_dir": "C:/launch-batch", "dll_dir": "C:/preferred-dll"}
    )
    try:
        config = workspace.bootstrap()
        assert config["settings"]["data_dir"] == "C:/launch-batch"
        assert config["settings"]["dll_dir"] == "C:/preferred-dll"
        assert config["settings"]["signal_processing_window_points"] == "21"
        assert config["untargeted"]["dll_dir"] == "C:/preferred-dll"
        assert config["targeted_method"]["warnings"]
    finally:
        workspace.jobs.close()



def test_http_method_reset_preserves_batch_resources_and_execution(tmp_path):
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    with _client(workspace) as request:
        status, config, _ = request("/api/config")
        assert status == 200
        local = {
            "data_dir": "different-batch",
            "dll_dir": "different-dll",
            "injection_order_source": "order.csv",
            "targeted_ms1_shape_identity_support_tsv": "support.tsv",
            "parallel_workers": "1",
        }
        settings = {**config["settings"], **local, "ms2_precursor_tol_da": "1.2"}
        status, summary, _ = request("/api/targeted/method", {"settings": settings})
        assert status == 200
        assert summary["custom_keys"] == ["ms2_precursor_tol_da"]
        assert summary["resource_keys"] == ["targeted_ms1_shape_identity_support_tsv"]
        status, reset, _ = request("/api/targeted/method/reset", {"settings": settings})
        assert status == 200
        assert reset["settings"]["ms2_precursor_tol_da"] == "0.8"
        assert {key: reset["settings"][key] for key in local} == local
        assert reset["summary"]["custom_keys"] == []
        # Draft operations do not persist settings or alter the target input.
        assert not (tmp_path / "config" / "settings.csv").exists()
        _, unchanged, _ = request("/api/config")
        assert unchanged["targets"] == config["targets"]
        assert unchanged["settings"] == config["settings"]
