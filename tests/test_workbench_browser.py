"""Browser contracts for the shipped HTML entry; no RAW/DLL analysis is run."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.inspector_fixtures import create_inspector_report, link_report_record
from tests.raw_header_fakes import install_fake_raw_headers
from tests.workbench_browser_fixtures import DEADLINE_SCALE as DEADLINE_SCALE
from tests.workbench_browser_fixtures import browser as browser
from tests.workbench_browser_fixtures import browser_errors as browser_errors
from tests.workbench_browser_fixtures import config_dir as config_dir
from tests.workbench_browser_fixtures import workbench as workbench
from tests.workbench_browser_fixtures import workbench_defaults as workbench_defaults
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_workbench.config_io import read_settings, write_settings
from xic_workbench.samples import (
    read_batch_samples,
    sample_annotations_path,
    save_batch_samples,
)
from xic_workbench.workspace import ReviewWorkspace

pytestmark = pytest.mark.browser_e2e
pytest_plugins = ("tests.browser_diagnostics",)

# Background jobs include server work and polling before UI content is ready.
# Keep content assertions at the expect deadline after this boundary.
BACKGROUND_JOB_TIMEOUT_MS = 30_000 * DEADLINE_SCALE
# A test thread holding a server request until the browser reaches a state.
HANDSHAKE_TIMEOUT_S = 10 * DEADLINE_SCALE


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_help_opens_task_guide_without_changing_the_analysis(workbench, theme):
    page = workbench
    if theme == "dark":
        page.get_by_role("button", name="切換深色模式").click()
    original_raw = page.locator("#t-data_dir").input_value()
    page.get_by_role("button", name="使用說明與快捷鍵").click()
    expect(page.get_by_role("heading", name="使用說明與快捷鍵")).to_be_visible()
    with page.expect_popup() as opened:
        page.get_by_role("link", name="開啟使用說明").click()
    guide = opened.value
    try:
        expect(
            guide.get_by_role("heading", name="XIC Extractor 使用說明")
        ).to_be_visible()
        guide.get_by_role("link", name="Targeted 操作指南", exact=True).click()
        expect(
            guide.get_by_role(
                "heading", name=re.compile("從目標清單.*走到可使用的批次結果")
            )
        ).to_be_visible()
        guide.get_by_role("link", name="使用說明首頁", exact=True).first.click()
        expect(
            guide.get_by_role("heading", name="XIC Extractor 使用說明")
        ).to_be_visible()
        assert page.locator("#t-data_dir").input_value() == original_raw
    finally:
        guide.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("workbench_defaults", [{"dll_dir": "missing-launch-reader"}])
def test_reader_setup_acceptance_install_and_reload(
    workbench, config_dir, tmp_path, monkeypatch, theme
):
    from xic_extractor import raw_reader, thermo_reader_install

    installed = tmp_path / "RawFileReader-8.0.42"

    def install(destination=None, *, accept_license, fetch=None):
        assert accept_license is True
        installed.mkdir()
        for name in raw_reader.EXPECTED_THERMO_DLLS:
            (installed / name).write_bytes(b"test assembly")
        return installed

    monkeypatch.setattr(thermo_reader_install, "install_thermo_reader", install)
    monkeypatch.setattr(raw_reader, "dotnet_runtime_errors", lambda: ["Missing .NET"])
    page = workbench
    page.reload()
    if page.locator("html").get_attribute("data-theme") != theme:
        page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    notice = page.locator("#reader-setup")
    expect(notice).to_be_visible()
    expect(page.locator("#reader-setup-dotnet")).to_be_visible()
    expect(page.locator("#reader-setup-command")).to_have_text(
        raw_reader.DOTNET_INSTALL_COMMAND
    )
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.locator("#reader-setup-copy").click()
    assert (
        page.evaluate("navigator.clipboard.readText()")
        == raw_reader.DOTNET_INSTALL_COMMAND
    )
    button = page.locator("#reader-setup-install")
    expect(button).to_be_disabled()
    page.locator("#reader-setup-accept").check()
    expect(button).to_be_enabled()
    notice.screenshot(path=str(tmp_path / f"reader-setup-{theme}.png"))
    button.click()
    expect(page.locator("#reader-setup-dll")).to_be_hidden()
    # Installing the DLLs must not pretend to install the missing runtime.
    expect(page.locator("#reader-setup-dotnet")).to_be_visible()
    assert read_settings(config_dir=config_dir)["dll_dir"] == str(installed)
    assert page.locator("#t-dll_dir").input_value() == str(installed)
    assert page.locator("#u-dll_dir").input_value() == str(installed)
    monkeypatch.setattr(raw_reader, "dotnet_runtime_errors", lambda: [])
    page.reload()
    expect(notice).to_be_hidden()
    assert page.locator("#t-dll_dir").input_value() == str(installed)


def test_dll_readiness_follows_validation_not_just_a_nonempty_path(workbench, tmp_path):
    from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS

    page = workbench
    item = page.locator("#target-checklist li").filter(has_text="RAW 讀取環境")
    expect(item).not_to_have_class("ok")
    valid = tmp_path / "valid-reader"
    valid.mkdir()
    for name in EXPECTED_THERMO_DLLS:
        (valid / name).write_bytes(b"test assembly")
    page.locator("#t-dll_dir").evaluate(
        "(node, value) => {node.value = value; "
        "node.dispatchEvent(new Event('input', {bubbles: true}));}",
        str(valid),
    )
    expect(item).to_have_class("ok")
    expect(page.locator("#reader-setup-dll")).to_be_hidden()
    page.locator("#t-dll_dir").evaluate(
        "(node, value) => {node.value = value; "
        "node.dispatchEvent(new Event('input', {bubbles: true}));}",
        str(tmp_path / "not-installed"),
    )
    expect(item).not_to_have_class("ok")
    expect(item).to_have_class("todo")
    expect(page.locator("#reader-setup-dll")).to_be_visible()


@pytest.mark.parametrize("workbench_defaults", [{"dll_dir": "missing-launch-reader"}])
def test_existing_reader_can_be_selected_without_downloading_or_accepting_again(
    workbench, config_dir, tmp_path
):
    from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS

    installed = tmp_path / "managed-reader"
    installed.mkdir()
    for name in EXPECTED_THERMO_DLLS:
        (installed / name).write_bytes(b"test assembly")
    page = workbench
    page.reload()
    expect(page.locator("#reader-setup-install")).to_be_hidden()
    page.get_by_role("button", name="使用已安裝版本", exact=True).click()
    expect(page.locator("#t-dll_dir")).to_have_value(str(installed))
    assert read_settings(config_dir=config_dir)["dll_dir"] == str(installed)
    page.reload()
    expect(page.locator("#t-dll_dir")).to_have_value(str(installed))
    expect(page.locator("#reader-setup-dll")).to_be_hidden()


def test_real_browser_reload_and_multiple_tabs_retire_only_after_last_close(
    browser, config_dir, browser_errors
):
    from tests.workbench_lifecycle_fixtures import _launch, _request

    with _launch(config_dir) as (process, url):
        context = browser.new_context()
        try:
            first = context.new_page()
            first.on("pageerror", lambda error: browser_errors.append(str(error)))
            first.goto(url)
            expect(first.locator("#t-data_dir")).to_be_visible()
            first.reload()
            expect(first.locator("#t-data_dir")).to_be_visible()
            second = context.new_page()
            second.on("pageerror", lambda error: browser_errors.append(str(error)))
            second.goto(url)
            expect(second.locator("#t-data_dir")).to_be_visible()
            first.close()
            assert process.poll() is None
            assert _request(url, "/api/review")[0] == 200
            second.close()
            assert process.wait(timeout=30) == 0
        finally:
            context.close()


def test_analysis_poll_waits_for_previous_response(workbench):
    page = workbench
    pending = []
    page.route("**/api/analysis", lambda route: pending.append(route))
    page.clock.install()
    try:
        with page.expect_request("**/api/analysis"):
            page.reload()
            expect(page.locator("body")).not_to_have_class("booting")
        # Several polling intervals elapse while the first response is held.
        # A slow local server must not accumulate overlapping status requests.
        page.clock.run_for(3000)
        assert len(pending) == 1
        with page.expect_response("**/api/analysis") as response:
            pending.pop().continue_()
        response.value.finished()
        with page.expect_request("**/api/analysis"):
            page.clock.run_for(1500)
    finally:
        for route in pending:
            route.continue_()
        page.unroute("**/api/analysis")


@pytest.mark.parametrize("transition", ["start", "cancel"])
def test_delayed_analysis_poll_cannot_undo_job_action(workbench, fake_runs, transition):
    page = workbench
    pending = []
    page.evaluate("window.heldAnalysisPolls = 0")

    def hold_snapshot(route):
        pending.append(route)
        page.evaluate("window.heldAnalysisPolls += 1")

    page.clock.install()
    if transition == "cancel":
        _start(page, "targeted")
        expect(page.locator("#analysis-stop")).to_be_enabled()
    page.route("**/api/analysis", hold_snapshot)
    try:
        with page.expect_request("**/api/analysis"):
            page.clock.run_for(1500)
        page.wait_for_function("window.heldAnalysisPolls === 1")
        assert len(pending) == 1
        old_route = pending.pop()
        old_response = old_route.fetch()
        if transition == "start":
            assert old_response.json()["job"] is None
            _start(page, "targeted")
            expect(
                page.locator("button[form=targeted-form][type=submit]")
            ).to_be_disabled()
        else:
            assert old_response.json()["job"]["status"] == "running"
            page.locator("#analysis-stop").click()
            page.locator("#confirm-accept").click()
            expect(page.locator("#analysis-stop")).to_be_disabled()
        with page.expect_response("**/api/analysis") as delivered:
            old_route.fulfill(response=old_response)
        delivered.value.finished()
        # The next poll proves the old callback has finished. Hold this response
        # too, so a fresh snapshot cannot hide the regression under test.
        with page.expect_request("**/api/analysis"):
            page.clock.run_for(1500)
        page.wait_for_function("window.heldAnalysisPolls === 2")
        expect(page.locator("#analysis-job")).to_be_visible()
        expect(page.locator("button[form=targeted-form][type=submit]")).to_be_disabled()
        if transition == "cancel":
            expect(page.locator("#analysis-stop")).to_be_disabled()
    finally:
        for route in pending:
            with page.expect_response("**/api/analysis") as released:
                route.continue_()
            released.value.finished()
        page.unroute("**/api/analysis", hold_snapshot)


@dataclass
class FakeRuns:
    """Gate completion on an event so polling/progress assertions cannot race."""

    outcome: str = "success"
    release: threading.Event = field(default_factory=threading.Event)
    output_dirs: list = field(default_factory=list)
    sample_annotations: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    targeted_workbook: Path | None = None
    istd_warnings: list = field(default_factory=list)
    result: dict = field(default_factory=dict)


@pytest.fixture
def fake_runs(monkeypatch, tmp_path):
    control = FakeRuns()

    class FakeRun:
        def __init__(
            self,
            _inputs,
            *,
            progress,
            should_cancel,
            output_dir=None,
            sample_annotations=None,
        ):
            control.output_dirs.append(output_dir)
            control.sample_annotations.append(sample_annotations)
            control.requests.append(_inputs)
            self.progress = progress
            self.should_cancel = should_cancel
            self.stage = "Browser fake analysis"
            self.output_dir = tmp_path / "results"
            self.available_outputs = ()
            self.sample_summary = None

        def execute(self):
            self.progress(1, 2, "Browser fake progress")
            while not control.release.wait(0.02):
                if self.should_cancel():
                    raise InterruptedError("Browser fake cancelled")
            if self.should_cancel():
                raise InterruptedError("Browser fake cancelled")
            if control.outcome == "failure":
                raise RuntimeError("Browser fake failure")
            self.output_dir.mkdir(exist_ok=True)
            artifact = self.output_dir / "browser-result.tsv"
            artifact.write_text("label\tvalue\nBrowser target\t1\n", encoding="utf-8")
            self.progress(2, 2, "Browser fake complete")
            return {
                "output_dir": str(self.output_dir),
                **(
                    {"excel_path": str(control.targeted_workbook)}
                    if control.targeted_workbook
                    else {}
                ),
                "published_paths": [str(artifact)]
                + (
                    [str(control.targeted_workbook)]
                    if control.targeted_workbook
                    else []
                ),
                "total_files": 1,
                "istd_warnings": control.istd_warnings,
                "targets": [{"label": "Browser target"}],
                "sample_count": 1,
                "candidate_counts": {"total": 1},
                **control.result,
            }

    monkeypatch.setattr("xic_workbench.targeted.TargetedRun", FakeRun)
    monkeypatch.setattr("xic_workbench.untargeted.UntargetedRun", FakeRun)
    yield control
    control.release.set()


def _drop_file(page, selector, name, content):
    if selector == "#istd-path-field":
        _expand_section(page, "#istd-list-tools")
    transfer = page.evaluate_handle(
        """({name, content}) => {
        const transfer = new DataTransfer();
        const bytes = Uint8Array.from(atob(content), c => c.charCodeAt(0));
        transfer.items.add(new File([bytes], name));
        return transfer;
    }""",
        {"name": name, "content": base64.b64encode(content).decode()},
    )
    page.locator(selector).dispatch_event("drop", {"dataTransfer": transfer})


def _view(page, name):
    with page.expect_response(
        lambda response: response.url.endswith("/api/preferences")
    ):
        page.locator(f".topbar [data-view='{name}']").click()
    expect(page.locator(f"#workspace-{name}")).to_be_visible()


def _expand_section(page, selector):
    section = page.locator(selector)
    if section.get_attribute("open") is None:
        section.locator(":scope > summary").click()


def _configure_curve_sources(page, raw_dir, dll_dir):
    _expand_section(page, "#curve-settings")
    page.locator("#raw-dir").fill(str(raw_dir))
    page.locator("#dll-dir").fill(str(dll_dir))


def _set_reader_path(page, value):
    # Machine setup is a precondition here, not the user-facing analysis journey.
    page.locator("#t-dll_dir").evaluate(
        "(node, value) => {node.value = value; "
        "node.dispatchEvent(new Event('input', {bubbles: true}));}",
        value,
    )


def _open_results(page):
    page.locator("#open-result").click()
    expect(page.locator("#analysis-result")).to_be_visible()


def _start(page, kind):
    page.locator(f"button[form={kind}-form][type=submit]").click()


def _wait_for_analysis_status(page, status):
    expect(page.locator("#analysis-job-title")).to_contain_text(
        status, timeout=BACKGROUND_JOB_TIMEOUT_MS
    )


def _wait_for_curve_report(page, *, inline=False):
    review = page.locator("#inline-curve-review" if inline else "#xic-review")
    # The view becomes visible only after acquisition and report mounting finish.
    expect(review).to_be_visible(timeout=BACKGROUND_JOB_TIMEOUT_MS)
    return review


def test_browse_fills_the_field_from_the_native_chooser(
    workbench, monkeypatch, tmp_path
):
    from xic_workbench import server

    page = workbench
    choices = [str(tmp_path), ""]
    requests = []

    def choose(kind, current=""):
        requests.append((kind, current))
        return choices.pop(0)

    monkeypatch.setattr(server, "choose_path", choose)
    field = page.locator("#t-data_dir")
    browse = page.locator("[data-browse='t-data_dir']")
    initial = field.input_value()
    browse.click()
    expect(field).to_have_value(str(tmp_path))
    # Cancelling the chooser keeps the chosen location.
    browse.click()
    expect(browse).not_to_have_attribute("aria-busy", "true")
    expect(field).to_have_value(str(tmp_path))
    # The chooser starts from whatever the field holds.
    assert requests == [("folder", initial), ("folder", str(tmp_path))]


@pytest.mark.parametrize("kind", ["folder", "report"])
def test_result_chooser_opens_batch_folders_and_report_files(
    workbench, monkeypatch, tmp_path, kind
):
    from shutil import copyfile

    from tests.inspector_fixtures import create_review_batch
    from xic_extractor.alignment_explanation.storage import (
        EXPLANATION_ARTIFACT_NAME,
    )
    from xic_workbench import server

    original, _ = create_review_batch(tmp_path)
    batch = tmp_path / "completed-batch"
    alignment = batch / "alignment"
    alignment.mkdir(parents=True)
    report = alignment / EXPLANATION_ARTIFACT_NAME
    copyfile(original, report)
    chosen = str(batch if kind == "folder" else report)
    choices, calls = [chosen, ""], []

    def choose(requested_kind, current=""):
        calls.append((requested_kind, current))
        return choices.pop(0)

    monkeypatch.setattr(server, "choose_path", choose)
    page = workbench
    _open_results(page)
    field = page.locator("#source")
    field.fill("previous-selection")
    name = "選擇結果資料夾" if kind == "folder" else "選擇結果檔案"
    browse = page.get_by_role("button", name=name, exact=True)
    browse.click()
    expect(field).to_have_value(chosen)
    browse.click()
    expect(browse).not_to_have_attribute("aria-busy", "true")
    expect(field).to_have_value(chosen)
    assert calls == [(kind, "previous-selection"), (kind, chosen)]
    with page.expect_response(
        lambda response: response.url.endswith("/api/open")
    ) as opened:
        page.locator("#open-form button[type=submit]").click()
    assert opened.value.json()["source"] == str(report)
    expect(page.locator("#batch-title")).to_have_text("Untargeted 結果")


def test_navigation_and_theme_survive_reload(workbench):
    page = workbench
    for view in ("untargeted", "targeted"):
        _view(page, view)
        for other in {"targeted", "untargeted"} - {view}:
            expect(page.locator(f"#workspace-{other}")).to_be_hidden()
    _view(page, "untargeted")
    initial = page.locator("html").get_attribute("data-theme")
    page.locator("#theme-toggle").click()
    theme = "light" if initial == "dark" else "dark"
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("#workspace-untargeted")).to_be_visible()


def test_untargeted_only_submits_complete_analysis(workbench, config_dir):
    page = workbench
    draft = config_dir / "discovery_gui.json"
    draft.write_text(
        json.dumps(
            {
                "mode": "align_only",
                "alignment_enabled": False,
                "backfill_enabled": False,
                "discovery_batch_index": "missing.csv",
            }
        )
    )
    page.reload()
    expect(page.locator("#workspace-targeted")).to_be_visible()
    _view(page, "untargeted")
    for selector in (
        "input[name=u-mode]",
        "#u-backfill-enabled",
        "#u-discovery_batch_index",
        "#u-raw_file",
    ):
        expect(page.locator(selector)).to_have_count(0)
    with page.expect_request("**/api/untargeted/save") as sent:
        page.locator("#untargeted-save").click()
    retired = {
        "mode",
        "backfill_enabled",
        "alignment_enabled",
        "raw_file",
        "discovery_batch_index",
        "execution_profile",
    }
    assert not retired & sent.value.post_data_json.keys()
    expect(page.locator("#toasts")).to_contain_text("已儲存")
    assert not retired & json.loads(draft.read_text()).keys()


def test_sequence_warnings_are_visible_in_sample_loading(workbench, monkeypatch):
    page = workbench
    summary = {
        "raw_count": 1,
        "matched_count": 1,
        "unmatched_sequence_names": ["missing"],
        "conflicts": [
            {"kind": "duplicate_order", "detail": "7", "sequence_names": ["A", "B"]},
            {
                "kind": "name_mismatch",
                "detail": "BC2312 / BC2303",
                "sequence_names": ["A"],
            },
        ],
        "unreadable_raw": ["blank.raw"],
    }
    monkeypatch.setattr(
        "xic_workbench.samples.load_samples",
        lambda *args, **kwargs: {"samples": [], "summary": summary},
    )
    _view(page, "untargeted")
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-conflicts li")).to_have_text(
        [
            "序號 7 出現 2 次：A、B",
            "A：檔名是 BC2312，描述寫 BC2303",
            "讀不到檔頭，已排除（不分析）：blank.raw",
            "未配對的上機資訊：missing",
        ]
    )
    expect(page.locator("#samples-summary")).to_contain_text("1 筆上機資訊未配對")
    expect(page.locator("#samples-summary")).to_contain_text("以 RAW 為準")
    page.reload()
    expect(page.locator("body")).not_to_have_class("booting")
    expect(page.locator("#samples-conflicts li")).to_have_count(4)
    expect(page.locator("#samples-summary")).to_contain_text("以 RAW 為準")
    summary["conflicts"] = []
    summary["unreadable_raw"] = []
    summary["unmatched_sequence_names"] = []
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-conflicts")).to_be_hidden()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_word_import_preview_finishes_or_stops_without_losing_confirmed_samples(
    workbench, config_dir, tmp_path, monkeypatch, outcome
):
    from tests.sample_metadata_sequence_parsing_fixtures import _create_synthetic_docx
    from xic_extractor import sample_metadata
    from xic_workbench.samples import save_batch_samples

    page = workbench
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    for name in ("NormalBC001_DNA", "QC_Pool_1"):
        (raw_dir / f"{name}.raw").write_bytes(b"not an acquired RAW")
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)
    save_batch_samples(
        config_dir,
        raw_dir,
        [
            asdict(
                SampleMetadata("NormalBC001_DNA", "NormalBC001_DNA", group="Confirmed")
            ),
            asdict(SampleMetadata("QC_Pool_1", "QC_Pool_1", sample_role="pooled_qc")),
        ],
    )
    saved_path = sample_annotations_path(config_dir, raw_dir)
    original = saved_path.read_bytes()
    entered = threading.Event()
    release = threading.Event()
    parse = sample_metadata._sequence_entries

    def parse_document(path):
        entered.set()
        assert release.wait(HANDSHAKE_TIMEOUT_S)
        if outcome == "failure":
            raise ValueError("無法解析 Word 進樣表，請檢查文件")
        return parse(path)

    monkeypatch.setattr(sample_metadata, "_sequence_entries", parse_document)
    monkeypatch.setattr(
        "xic_workbench.samples.read_analysis_set",
        lambda *a, **k: pytest.fail("preview opened RAW"),
    )
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("2 個樣本")
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-injection_order_source").fill(str(sequence))
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-import").click()
    try:
        assert entered.wait(HANDSHAKE_TIMEOUT_S)
        expect(page.locator("#analysis-job-message")).to_contain_text("解析上機資訊")
        expect(page.locator("#analysis-stop")).to_be_enabled()
        if outcome == "cancel":
            page.locator("#analysis-stop").click()
            # The shared stop confirmation is an explicit user action.
            with page.expect_response("**/api/analysis/cancel"):
                page.get_by_role("button", name="停止工作", exact=True).last.click()
    finally:
        release.set()
    if outcome == "success":
        expect(page.locator("#analysis-job-title")).to_contain_text("已完成")
        expect(page.locator("#t-samples-summary")).to_contain_text("配對 2 個")
        expect(page.locator("#t-samples-conflicts")).not_to_contain_text(
            "尚未讀取 RAW 檔頭"
        )
        assert load_sample_metadata(saved_path)[0].group == "Confirmed"
    else:
        expect(page.locator("#analysis-job-title")).to_contain_text(
            "已停止" if outcome == "cancel" else "執行失敗"
        )
        assert saved_path.read_bytes() == original
    expect(page.locator("#t-samples-import")).to_be_enabled()
    expect(page.locator("#t-injection_order_source")).to_have_value(str(sequence))


def test_targeted_reports_sequence_warnings_before_analysis_and_after_reload(
    workbench,
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from tests.analysis_set_fixtures import header
    from xic_extractor.raw_reader_errors import RawReaderError

    page = workbench
    raw = tmp_path / "raw"
    raw.mkdir()
    (tmp_path / "dll").mkdir()
    for name in ("A.raw", "B.raw", "Blank_bad.raw"):
        (raw / name).touch()
    sequence = tmp_path / "sequence.csv"
    sequence.write_text("File Name,Sample Name,Order\nA,A,7\nB,B,7\n", encoding="utf-8")

    def read_header(path, dll):
        if path.stem == "Blank_bad":
            raise RawReaderError("unreadable test header")
        return (
            header(str(path), 0 if path.stem == "A" else 11),
            "study_sample",
            path.stem,
        )

    monkeypatch.setattr("xic_extractor.raw_sequence._analysis_header", read_header)
    release = threading.Event()

    class Service:
        def execute_application(self, request):
            assert release.wait(BACKGROUND_JOB_TIMEOUT_MS / 1000)
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _: Service(),
    )
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-injection_order_source").fill(str(sequence))
    page.locator('button[form="targeted-form"]').click()
    try:
        expect(page.locator("#target-samples-conflicts")).to_contain_text(
            "序號 7 出現 2 次"
        )
        expect(page.locator("#target-samples-conflicts")).to_contain_text(
            "Blank_bad.raw"
        )
        page.reload()
        expect(page.locator("body")).not_to_have_class("booting")
        expect(page.locator("#target-samples-conflicts")).to_contain_text(
            "序號 7 出現 2 次"
        )
    finally:
        release.set()
    expect(page.locator("#target-result")).to_be_visible()
    page.reload()
    expect(page.locator("#target-samples-conflicts")).to_contain_text("Blank_bad.raw")


@pytest.mark.parametrize("tag", ["R", ""])
def test_targeted_exposes_intent_only_and_editor_round_trips(
    workbench, config_dir, tag
):
    page = workbench
    expect(page.locator("#target-method-state")).not_to_be_visible()
    page.locator("summary", has_text="處理策略與設定來源").click()
    expect(page.locator("#target-method-state")).to_have_text("共用 Targeted 策略")
    expect(page.locator("#targeted-form")).not_to_contain_text("版本")
    page.locator("summary", has_text="處理策略與設定來源").click()
    for cell in page.locator("#target-preview th, #target-preview td").all():
        expect(cell).to_have_css("text-align", "center")
    expect(page.locator("#t-parallel_workers")).to_have_count(0)
    page.locator("#target-edit").click()
    expect(page.locator("#target-editor")).to_be_visible()
    editor_cells = page.locator(
        "#target-editor table th, #target-editor table td, "
        "#target-editor table input, #target-editor table select"
    )
    assert editor_cells.count() > 0
    for cell in editor_cells.all():
        expect(cell).to_have_css("text-align", "center")
    page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True).fill(
        "2.75"
    )
    page.get_by_role("combobox", name="辨認方法（第 1 列）", exact=True).select_option(
        tag
    )
    page.locator("#target-editor-save").click()
    expect(page.locator("#target-editor")).not_to_be_visible()
    page.reload()
    page.locator("#target-edit").click()
    expect(
        page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True)
    ).to_have_value("2.75")
    expect(
        page.get_by_role("combobox", name="辨認方法（第 1 列）", exact=True)
    ).to_have_value(tag)
    assert read_settings(config_dir=config_dir)["parallel_workers"] == ""
    expect(page.locator("#t-parallel_workers")).to_have_count(0)


@pytest.mark.parametrize("theme,mode", [("light", "all"), ("dark", "any")])
def test_targeted_identification_method_editor_and_row_binding(
    workbench, config_dir, tmp_path, theme, mode
):
    page = workbench
    if theme == "dark":
        with page.expect_response("**/api/preferences"):
            page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    page.locator("#target-identification-edit").click()
    # A new method starts empty; nothing is pre-selected on the user's behalf.
    expect(page.locator(".method-condition")).to_have_count(0)
    expect(page.locator("#target-identification-delete")).to_be_disabled()
    page.locator("#target-identification-name").fill("Custom pair")
    page.locator("#target-identification-combine").select_option(mode)
    page.locator("#target-identification-ppm").fill("12")
    page.locator("#target-condition-add-cid").click()
    page.locator("#target-condition-add-delta").click()
    # The quantitation ion is fixed at zero and has no editable offset.
    expect(page.get_by_label("Δ m/z（條件 2，第 1 個離子）", exact=True)).to_have_count(
        0
    )
    partner = page.get_by_label("Δ m/z（條件 2，第 2 個離子）", exact=True)
    expect(partner).to_have_value("")
    page.locator("#target-identification-apply").click()
    expect(page.locator("#target-identification-error")).to_contain_text("Δ m/z")
    partner.fill("0")
    page.locator("#target-identification-apply").click()
    expect(page.locator("#target-identification-error")).to_contain_text("不可重複")
    partner.fill("-2.002")
    page.locator("#target-identification-apply").click()
    expect(page.locator("#target-identification-error")).to_be_empty()
    expect(page.locator("#target-identification-select")).to_have_value("Custom pair")
    page.locator("#target-identification-editor").evaluate(
        "dialog => Promise.all(dialog.getAnimations({subtree: true})"
        ".map(animation => animation.finished))"
    )
    fields = [
        page.locator(f"#{name}").bounding_box()
        for name in [
            "target-identification-name",
            "target-identification-combine",
            "target-identification-ppm",
        ]
    ]
    assert max(box["y"] for box in fields) - min(box["y"] for box in fields) <= 1
    assert {round(box["height"]) for box in fields} == {38}
    name = page.get_by_label(
        "離子名稱（條件 2，第 2 個離子）", exact=True
    ).bounding_box()
    offset = partner.bounding_box()
    assert abs(name["y"] - offset["y"]) <= 1
    assert name["height"] == offset["height"] == 38
    page.locator("#target-identification-editor").screenshot(
        path=str(tmp_path / f"identification-{theme}.png")
    )
    page.get_by_role("button", name="關閉辨認方法", exact=True).click()
    expect(page.locator("#target-identification-summary")).to_have_text("Custom pair")
    rows = [
        page.locator(selector).bounding_box()
        for selector in ["#target-dropzone .drop-target", "#target-method-select"]
    ]
    assert abs(rows[0]["x"] - rows[1]["x"]) <= 1
    assert {round(box["height"]) for box in rows} == {38}
    page.locator(".card").filter(
        has=page.locator("#target-identification-summary")
    ).screenshot(path=str(tmp_path / f"target-card-{theme}.png"))

    page.locator("#target-edit").click()
    page.locator("#target-bulk-method").select_option("Custom pair")
    page.locator("#target-bulk-apply").click()
    expect(
        page.get_by_role("combobox", name="辨認方法（第 1 列）", exact=True)
    ).to_have_value("Custom pair")
    # A new row starts with the previous row's method.
    page.locator("#target-add").click()
    page.get_by_role("textbox", name="名稱（第 2 列）", exact=True).fill("Second")
    page.get_by_role("textbox", name="m/z（第 2 列）", exact=True).fill("600")
    page.get_by_role("textbox", name="預期 RT（min）（第 2 列）", exact=True).fill("2")
    expect(
        page.get_by_role("combobox", name="辨認方法（第 2 列）", exact=True)
    ).to_have_value("Custom pair")
    page.get_by_role("combobox", name="辨認方法（第 1 列）", exact=True).select_option(
        ""
    )
    page.locator("#target-editor-save").click()
    expect(page.locator("#target-editor")).not_to_be_visible()
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("#target-preview")).to_contain_text("Custom pair")
    expect(page.locator("#target-preview")).to_contain_text("MS1 only")
    state = json.loads((config_dir / "workspace.json").read_text(encoding="utf-8"))
    assert "default_method" not in state
    assert state["targets"][0]["method"] == ""
    assert {row["method"] for row in state["targets"][1:]} == {"Custom pair"}
    saved = state["identification_methods"]["Custom pair"]
    assert saved["combine_mode"] == mode
    assert saved["mz_tolerance_ppm"] == 12
    assert saved["conditions"][1]["positions"][1]["offset_mz"] == -2.002

    # A method that targets still use cannot be deleted.
    page.locator("#target-identification-edit").click()
    page.locator("#target-identification-delete").click()
    expect(page.locator("#target-identification-error")).to_contain_text("個目標使用")
    expect(page.locator("#target-identification-select")).to_have_value("Custom pair")


def test_imported_quantitation_role_survives_temporary_duplicate_zero(
    workbench, config_dir, tmp_path
):
    from xic_extractor.configuration.targeted_method_files import (
        create_targeted_method,
        save_targeted_method,
    )
    from xic_workbench.config_io import read_targets

    definition = {
        "conditions": [
            {
                "kind": "delta_mass",
                "positions": [
                    {"label": "light", "offset_mz": -2},
                    {"label": "quant", "offset_mz": 0},
                ],
            }
        ]
    }
    portable = tmp_path / "imported.xlsx"
    save_targeted_method(
        create_targeted_method(
            {},
            [
                dict(row, method="Imported")
                for row in read_targets(config_dir=config_dir)
            ],
            identification_methods={"Imported": definition},
        ),
        portable,
    )
    page = workbench
    page.locator("#target-import-file").set_input_files(portable)
    expect(page.locator("#target-import-preview")).to_be_visible()
    page.locator("#target-import-confirm").click()
    page.locator("#target-identification-edit").click()
    partner = page.get_by_label("Δ m/z（條件 1，第 1 個離子）", exact=True)
    quant = page.get_by_label("Δ m/z（條件 1，第 2 個離子）", exact=True)
    expect(partner).to_have_value("-2")
    expect(quant).to_have_count(0)
    partner.fill("0")
    page.get_by_role("button", name="新增離子（條件 1）", exact=True).click()
    # The imported zero position keeps the quantitation role during the edit.
    expect(quant).to_have_count(0)
    partner.fill("-2")
    page.get_by_label("Δ m/z（條件 1，第 3 個離子）", exact=True).fill("2")
    page.locator("#target-identification-apply").click()
    expect(page.locator("#target-identification-error")).to_be_empty()
    page.get_by_role("button", name="關閉辨認方法", exact=True).click()
    page.locator("#target-save").click()
    expect(
        page.locator("#toasts .toast.ok").filter(has_text="Targeted 設定與目標已儲存")
    ).to_be_visible()
    page.reload()
    page.locator("#target-identification-edit").click()
    expect(partner).to_have_value("-2")
    expect(quant).to_have_count(0)
    expect(page.get_by_label("Δ m/z（條件 1，第 3 個離子）", exact=True)).to_have_value(
        "2"
    )


def test_add_ion_after_deleting_middle_position_keeps_names_unique(
    workbench, config_dir
):
    page = workbench
    page.locator("#target-identification-edit").click()
    page.locator("#target-identification-name").fill("Reordered pair")
    page.locator("#target-condition-add-delta").click()
    add = page.get_by_role("button", name="新增離子（條件 1）", exact=True)
    add.click()
    add.click()
    page.get_by_role(
        "button", name="移除離子（條件 1，第 3 個離子）", exact=True
    ).click()
    add.click()
    for position, offset in [(2, "1"), (3, "2"), (4, "3")]:
        page.get_by_label(f"Δ m/z（條件 1，第 {position} 個離子）", exact=True).fill(
            offset
        )
    page.locator("#target-identification-apply").click()
    expect(page.locator("#target-identification-error")).to_be_empty()
    page.get_by_role("button", name="關閉辨認方法", exact=True).click()
    page.locator("#target-save").click()
    expect(
        page.locator("#toasts .toast.ok").filter(has_text="Targeted 設定與目標已儲存")
    ).to_be_visible()
    state = json.loads((config_dir / "workspace.json").read_text(encoding="utf-8"))
    positions = state["identification_methods"]["Reordered pair"]["conditions"][0][
        "positions"
    ]
    assert len({p["label"] for p in positions}) == len(positions) == 4
    assert len({p["offset_mz"] for p in positions}) == 4


def test_untargeted_method_apply_is_draft_until_saved(workbench, config_dir, tmp_path):
    from xic_extractor.presets import load_preset

    page = workbench
    _view(page, "untargeted")
    current = config_dir / "current_discovery_method.toml"
    page.locator("#method-edit").click()
    page.locator("#method-name").fill("Saved method")
    page.locator("#method-apply").click()
    expect(page.locator("#method-editor")).not_to_be_visible()
    assert not current.exists(), "Applying a draft must not save it"
    page.locator("#untargeted-save").click()
    expect(page.locator("#untargeted-dirty")).to_be_empty()
    assert load_preset(current).name == "Saved method"
    saved_bytes = current.read_bytes()

    page.locator("#method-edit").click()
    page.locator("#method-name").fill("Unsaved method")
    reusable = tmp_path / "reusable.toml"
    page.locator("#method-save-as").click()
    page.locator("#path-dialog-value").fill(str(reusable))
    with page.expect_response("**/api/method/save"):
        page.locator("#path-dialog").get_by_role("button", name="確定").click()
    assert load_preset(reusable).name == "Unsaved method"
    assert current.read_bytes() == saved_bytes
    expect(page.locator("#u-preset option:checked")).to_have_text("Saved method")
    page.locator("#method-apply").click()
    expect(page.locator("#method-editor")).not_to_be_visible()
    expect(page.locator("#u-preset option:checked")).to_have_text("Unsaved method")
    assert current.read_bytes() == saved_bytes
    page.reload()
    expect(page.locator("#u-preset option:checked")).to_have_text("Saved method")
    expect(page.locator("#untargeted-dirty")).to_be_empty()


def test_switching_method_discards_the_unsaved_applied_choice(workbench):
    page = workbench
    _view(page, "untargeted")
    page.locator("#method-edit").click()
    page.locator("#method-name").fill("Discarded draft")
    page.locator("#method-apply").click()
    expect(page.locator("#method-editor")).not_to_be_visible()
    with page.expect_response("**/api/method"):
        page.locator("#u-preset").select_option("rna_ribose")
    expect(page.locator("#u-preset")).to_have_value("rna_ribose")
    expect(page.locator("#u-preset option", has_text="Discarded draft")).to_have_count(
        0
    )
    page.locator("#untargeted-save").click()
    expect(page.locator("#untargeted-dirty")).to_be_empty()
    page.reload()
    expect(page.locator("#u-preset")).to_have_value("rna_ribose")
    expect(page.locator("#method-preview")).to_contain_text("RNA_ribose")


def test_untargeted_method_edit_keeps_hidden_values_and_start_saves_draft(
    workbench, fake_runs, config_dir, tmp_path
):
    from xic_extractor.presets import apply_to_discovery, load_preset
    from xic_workbench.methods import method_details, save_method

    imported = tmp_path / "imported.toml"
    draft = method_details("dna_dr")["draft"]
    draft["description"] = "Imported recipe note"
    draft["overrides"]["ms2_precursor_tol_da"] = 0.37
    draft["overrides"]["rt_min"] = 1.5
    save_method(draft, imported)
    original = imported.read_bytes()
    page = workbench
    _view(page, "untargeted")
    _expand_section(page, "#method-file-tools")
    page.locator("#method-import").click()
    page.locator("#path-dialog-value").fill(str(imported))
    page.locator("#path-dialog").get_by_role("button", name="確定").click()
    expect(page.locator("#u-preset")).to_have_value(str(imported))
    expect(page.get_by_text("Imported recipe note", exact=True)).to_have_count(0)
    page.get_by_text("調整本次方法參數", exact=True).click()
    page.locator("#tune-nl_tolerance_ppm").fill("17")
    page.locator("#u-rt-crop").uncheck()
    page.locator("#method-edit").click()
    expect(page.get_by_role("textbox", name="說明", exact=True)).to_have_count(0)
    page.locator("#method-name").fill("Edited method")
    page.locator("#method-apply").click()
    expect(page.locator("#method-editor")).not_to_be_visible()
    expect(page.get_by_text("Imported recipe note", exact=True)).to_have_count(0)
    expect(page.locator("#tune-nl_tolerance_ppm")).to_have_value("17")
    expect(page.locator("#u-rt-crop")).not_to_be_checked()

    page.locator("#u-raw_dir").fill(str(tmp_path / "next-batch"))
    fake_runs.release.set()
    _start(page, "untargeted")
    _wait_for_analysis_status(page, "已完成")
    request = fake_runs.requests[-1]
    settings = apply_to_discovery(request.preset_snapshot)
    assert settings.ms2_precursor_tol_da == 0.37
    assert settings.nl_tolerance_ppm == 17
    assert settings.rt_min is None
    assert imported.read_bytes() == original
    current = config_dir / "current_discovery_method.toml"
    assert load_preset(current).name == "Edited method"
    assert load_preset(current).description == "Imported recipe note"
    page.reload()
    expect(page.locator("#u-preset option:checked")).to_have_text("Edited method")
    expect(page.locator("#tune-nl_tolerance_ppm")).to_have_value("17")
    expect(page.locator("#u-rt-crop")).not_to_be_checked()


@pytest.mark.parametrize("kind", ["targeted", "untargeted"])
def test_new_batch_preserves_method_but_uses_the_new_folders_samples(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch, kind
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    previous, following = tmp_path / "previous", tmp_path / "following"
    for folder, group, excluded in (
        (previous, "Previous group", True),
        (following, "Following group", False),
    ):
        _placeholder_batch(folder, ("S1",))
        save_batch_samples(
            config_dir,
            folder,
            [
                asdict(
                    SampleMetadata(
                        "S1",
                        "S1",
                        group=group,
                        excluded=excluded,
                        exclusion_reason="previous exclusion" if excluded else "",
                    )
                )
            ],
        )
    _view(page, kind)
    raw, sequence, output, prefix, new, save = (
        (
            "t-data_dir",
            "t-injection_order_source",
            "t-output_dir",
            "t-",
            "target-new",
            "target-save",
        )
        if kind == "targeted"
        else (
            "u-raw_dir",
            "sequence-source",
            "u-output_dir",
            "",
            "untargeted-new",
            "untargeted-save",
        )
    )
    targets = page.locator("#target-preview").text_content()
    if kind == "untargeted":
        with page.expect_response("**/api/method"):
            page.locator("#u-preset").select_option("rna_ribose")
        page.get_by_text("調整本次方法參數", exact=True).click()
        page.locator("#tune-nl_tolerance_ppm").fill("17")
    istd = page.locator("#u-targets_csv").input_value()
    page.locator(f"#{output}").fill(str(tmp_path / "deliveries"))
    page.locator(f"#{raw}").fill(str(previous))
    _expand_section(page, f"#{prefix}sample-tools")
    page.locator(f"#{sequence}").fill(
        str(sample_annotations_path(config_dir, previous))
    )
    _expand_section(page, f"#{prefix}sample-tools")
    page.locator(f"#{prefix}samples-refresh").click()
    expect(page.locator(f"#{prefix}samples-count")).to_have_text("1 個樣本")
    expect(page.locator(f"#{prefix}role-summary")).to_contain_text("排除 1")
    page.locator(f"#{new}").click()
    expect(page.locator(f"#{raw}")).to_have_value("")
    expect(page.locator(f"#{sequence}")).to_have_value("")
    expect(page.locator(f"#{prefix}samples-count")).to_have_text("尚未讀取樣本")
    page.locator(f"#{save}").click()
    expect(
        page.locator(f"#{'target' if kind == 'targeted' else kind}-dirty")
    ).to_be_empty()
    page.reload()
    expect(page.locator(f"#{raw}")).to_have_value("")
    expect(page.locator(f"#{sequence}")).to_have_value("")
    expect(page.locator(f"#{output}")).to_have_value(str(tmp_path / "deliveries"))
    if kind == "untargeted":
        expect(page.locator("#u-preset")).to_have_value("rna_ribose")
        expect(page.locator("#tune-nl_tolerance_ppm")).to_have_value("17")
        expect(page.locator("#u-targets_csv")).to_have_value(istd)
    else:
        expect(page.locator("#target-preview")).to_have_text(targets)
    page.locator(f"#{raw}").fill(str(following))
    page.locator(f"#{raw}").press("Tab")
    # Wait for the automatic filename preview before opening its tools; the
    # explicit read then loads the folder's confirmed groups and exclusions.
    expect(page.locator(f"#{prefix}samples-count")).to_have_text("1 個樣本")
    _expand_section(page, f"#{prefix}sample-tools")
    page.locator(f"#{prefix}samples-refresh").click()
    expect(page.locator(f"#{prefix}samples-source")).to_contain_text(
        "沿用已確認的角色與排除"
    )
    _wait_for_analysis_status(page, "已完成")
    fake_runs.release.set()
    # A completed sample read remains visible until the analysis start reply.
    # Hold that handoff so this test cannot accidentally observe the old job.
    entered, release_start = threading.Event(), threading.Event()
    start_analysis = ReviewWorkspace.start_analysis

    def held_start(workspace, job_kind, values):
        if job_kind == kind:
            entered.set()
            assert release_start.wait(HANDSHAKE_TIMEOUT_S)
        return start_analysis(workspace, job_kind, values)

    monkeypatch.setattr(ReviewWorkspace, "start_analysis", held_start)
    try:
        _start(page, kind)
        assert entered.wait(HANDSHAKE_TIMEOUT_S)
        expect(page.locator("#analysis-job-title")).to_have_text(
            "讀取樣本資訊 · 已完成"
        )
        assert not fake_runs.requests
        with page.expect_response("**/api/analysis/start") as started:
            release_start.set()
        assert started.value.status == 202
        assert started.value.json()["job"]["kind"] == kind
    finally:
        release_start.set()
    _wait_for_analysis_status(page, f"{kind.capitalize()} 分析 · 已完成")
    if kind == "untargeted":
        samples = fake_runs.requests[-1].sample_metadata
        assert [(s.raw_stem, s.group, s.excluded) for s in samples] == [
            ("S1", "Following group", False)
        ]
    else:
        assert fake_runs.sample_annotations[-1] == sample_annotations_path(
            config_dir, following
        )
    previous_rows = load_sample_metadata(sample_annotations_path(config_dir, previous))
    assert previous_rows[0].excluded is True


def test_target_list_save_and_load_preserves_batch(workbench):
    page = workbench
    original_batch = page.locator("#t-data_dir").input_value()
    page.get_by_role("button", name="另存清單", exact=True).click()
    page.get_by_label("清單名稱（不含 .xlsx）", exact=True).fill("Browser saved list")
    page.locator("#path-dialog").get_by_role("button", name="確定").click()
    expect(page.locator("#target-method-select option:checked")).to_have_text(
        "Browser saved list"
    )
    page.locator("#target-edit").click()
    page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True).fill("4")
    page.locator("#target-editor-save").click()
    page.get_by_role("button", name="載入清單", exact=True).click()
    expect(
        page.locator("#toasts .toast.ok").filter(has_text="已載入目標清單")
    ).to_be_visible()
    expect(page.locator("#t-data_dir")).to_have_value(original_batch)
    page.locator("#target-edit").click()
    expect(
        page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True)
    ).to_have_value("1.5")


def test_template_batch_folder_reads_as_unselected_and_still_saves(
    workbench, config_dir, tmp_path
):
    page = workbench
    write_settings(
        {**CANONICAL_SETTINGS_DEFAULTS, "dll_dir": str(tmp_path / "dll")},
        config_dir=config_dir,
    )
    page.reload()
    # The shipped example path is not a user choice; the field must look empty.
    expect(page.locator("#t-data_dir")).to_have_value("")
    expect(page.locator("#target-checklist")).to_contain_text("尚未選擇資料夾")
    page.locator("#target-save").click()
    expect(page.locator("#toasts .toast.ok")).to_contain_text("已儲存")
    assert (
        read_settings(config_dir=config_dir)["data_dir"]
        == CANONICAL_SETTINGS_DEFAULTS["data_dir"]
    )


def test_analysis_pages_share_path_rows_and_targeted_output_is_kept(
    fake_runs, workbench, config_dir, tmp_path
):
    page = workbench
    labels = page.locator("#targeted-data .path-line:visible > label")
    expect(labels).to_have_text(["RAW 資料夾", "結果存放位置"])
    # 上機資訊 belongs to the sample step both flows share.
    for step in ("targeted", "untargeted"):
        expect(
            page.locator(f'[data-sample-step="{step}"] .import-line > label')
        ).to_have_text("上機資訊")
    expect(page.locator("#t-output_dir")).to_have_value(
        str(config_dir.parent / "output")
    )
    # A location that does not exist yet is created by the run, not an error.
    chosen = tmp_path / "chosen-output"
    page.locator("#t-output_dir").fill(str(chosen))
    page.locator("#t-output_dir").press("Tab")
    expect(page.locator("#t-output_dir")).to_have_attribute("aria-invalid", "false")
    _start(page, "targeted")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")
    # Each run writes its own new folder inside the location; none is reused.
    _start(page, "targeted")
    for _ in range(100):
        if len(fake_runs.output_dirs) == 2:
            break
        page.wait_for_timeout(50)
    first, second = fake_runs.output_dirs
    assert first.parent == second.parent == chosen
    assert first != second
    assert re.fullmatch(r"\d{8}_\d{6}_targeted(_.+)?", first.name)
    page.reload()
    expect(page.locator("#t-output_dir")).to_have_value(str(chosen))
    _view(page, "untargeted")
    expect(page.locator("#untargeted-paths .path-line:visible > label")).to_have_text(
        ["RAW 資料夾", "結果存放位置"]
    )


def test_empty_targeted_run_explains_where_to_add_a_target(workbench):
    page = workbench
    page.locator("#target-edit").click()
    page.get_by_role("button", name="刪除第 1 列", exact=True).click()
    page.locator("#target-editor").get_by_role(
        "button", name="完成", exact=True
    ).click()
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.url.endswith("/api/analysis/start")
            else None
        ),
    )
    _start(page, "targeted")
    error = page.locator("#toasts .toast.error")
    expect(error).to_have_text("目標清單是空的：按「檢視與編輯」新增，或匯入目標清單。")
    # The empty list is caught before any run request reaches the workspace.
    assert requests == []


def test_readiness_lists_analysis_inputs_without_blocking_submit(workbench):
    page = workbench
    _view(page, "untargeted")
    checklist = page.locator("#untargeted-checklist")
    expect(checklist).to_contain_text("RAW 資料夾")
    expect(checklist).to_contain_text("RAW 讀取環境")
    expect(checklist).to_contain_text("結果存放位置")
    expect(page.locator("button[form=untargeted-form][type=submit]")).to_be_enabled()


@pytest.fixture
def analysis_feedback(workbench):
    """Serve a published result without running another application job."""
    page = workbench
    state = {
        "job": {
            "job_id": "published",
            "kind": "targeted",
            "status": "succeeded",
            "elapsed_seconds": 1,
            "completed": 1,
            "total": 1,
            "cancellable": True,
        },
        "results": {
            "targeted": {
                "total_files": 1,
                "targets": [{"label": "Saved target"}],
                "artifacts": [
                    {
                        "name": "saved.tsv",
                        "path": "/saved.tsv",
                        "url": "/api/artifacts/saved",
                    }
                ],
                "istd_warnings": [{"label": "ISTD", "detected": 0, "total": 1}],
            }
        },
    }

    def bootstrap(route):
        response = route.fetch()
        config = response.json()
        config["analysis"] = state
        route.fulfill(response=response, json=config)

    page.route("**/api/config", bootstrap)
    page.route("**/api/analysis", lambda route: route.fulfill(json=state))
    page.clock.install()
    page.reload()
    expect(page.locator("body")).not_to_have_class("booting")
    expect(page.locator("#target-result")).to_be_visible()
    return page, state


@pytest.mark.parametrize("kind", ["targeted", "untargeted"])
def test_analysis_submit_progress_and_download(fake_runs, workbench, tmp_path, kind):
    page = workbench
    if kind == "untargeted":
        _view(page, kind)
        page.locator("#u-raw_dir").fill(str(tmp_path / "raw"))
        page.locator("#u-output_dir").fill(str(tmp_path / "results"))
    _start(page, kind)
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    expect(page.locator("#analysis-progress")).to_have_attribute("value", "1")
    expect(page.locator(f"button[form={kind}-form][type=submit]")).to_be_disabled()
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")
    result = page.locator(f"#{'target' if kind == 'targeted' else kind}-result")
    expect(result).to_be_visible()
    result.locator(".artifact-details > summary").click()
    link = result.get_by_role("link", name="browser-result.tsv")
    expect(link).to_be_visible()
    with page.expect_download() as download:
        link.click()
    assert (
        Path(download.value.path())
        .read_text(encoding="utf-8")
        .endswith("Browser target\t1\n")
    )


def test_analysis_feedback_retains_results_and_requires_stop_confirmation(
    analysis_feedback,
):
    page, state = analysis_feedback
    result = page.locator("#target-result")
    previous = result.inner_text()
    state["job"].update(job_id="second", status="running", completed=1, total=2)
    with page.expect_response("**/api/analysis"):
        page.clock.run_for(1000)
    expect(page.locator("#analysis-job-title")).to_contain_text("執行中")
    expect(page.locator("#analysis-progress")).to_have_attribute("value", "1")
    state["job"].update(status="failed", error="Example failure")
    with page.expect_response("**/api/analysis"):
        page.clock.run_for(1000)
    expect(page.locator("#analysis-job-title")).to_contain_text("執行失敗")
    expect(page.locator("#analysis-job-message")).to_contain_text("Example failure")
    expect(result).to_have_text(previous, use_inner_text=True)
    state["job"].update(job_id="third", status="running", error=None)
    with page.expect_response("**/api/analysis"):
        page.clock.run_for(1000)
    expect(page.locator("#analysis-stop")).to_be_visible()
    cancelled = []

    def cancel(route):
        cancelled.append(route.request.post_data_json)
        state["job"].update(status="cancelled")
        route.fulfill(json=state)

    page.route("**/api/analysis/cancel", cancel)
    page.locator("#analysis-stop").click()
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.locator("#confirm-dialog button[value=cancel]").click()
    expect(page.locator("#analysis-job-title")).to_contain_text("執行中")
    assert not cancelled
    page.locator("#analysis-stop").click()
    page.locator("#confirm-accept").click()
    expect(page.locator("#analysis-job-title")).to_contain_text("已停止")
    assert cancelled == [{"job_id": "third"}]
    expect(result).to_have_text(previous, use_inner_text=True)
    expect(result.locator('a[href="/api/artifacts/saved"]')).to_have_count(1)


def test_untargeted_result_shows_one_matrix_and_groups_technical_files(
    analysis_feedback,
):
    page, state = analysis_feedback
    folder = "C:/runs/validation"
    matrix = f"{folder}/alignment_matrix.tsv"
    paths = [matrix, f"{folder}/sample_metadata.tsv"] + [
        f"{folder}/{sample}/{name}"
        for sample in ("A", "B")
        for name in ("discovery_candidates.csv", "discovery_review.csv")
    ]
    state["job"].update(kind="untargeted", job_id="untargeted-result")
    state["results"] = {
        "untargeted": {
            "output_dir": folder,
            "matrix_tsv": matrix,
            "sample_count": 2,
            "overview": {"matrix_count": 4, "complete_count": 3, "unresolved_count": 1},
            "evidence": {"path": f"{folder}/alignment_explanation.sqlite"},
            "alignment_outputs": {"review_tsv": f"{folder}/alignment_review.tsv"},
            "artifacts": [
                {
                    "name": path.rsplit("/", 1)[-1],
                    "path": path,
                    "url": f"/artifacts/{i}",
                }
                for i, path in enumerate(paths)
            ],
        }
    }
    page.reload()
    _view(page, "untargeted")
    card = page.locator("#untargeted-result")
    expect(card.locator("a:visible")).to_have_count(1)
    expect(
        card.get_by_role("link", name="下載 Alignment 矩陣", exact=True)
    ).to_be_visible()
    expect(card.get_by_role("button", name="檢閱結果", exact=True)).to_have_class(
        "primary"
    )
    expect(card.get_by_role("button", name="整理矩陣", exact=True)).to_be_visible()
    expect(
        card.get_by_role("button", name="自訂 Inspector 匯出", exact=True)
    ).to_be_hidden()
    files = card.locator(".artifact-details")
    expect(files).not_to_have_attribute("open", "")
    files.locator("summary").click()
    for sample in ("A", "B"):
        expect(files.get_by_role("heading", name=sample, exact=True)).to_be_visible()
    expect(
        files.get_by_role("link", name="discovery_candidates.csv", exact=True)
    ).to_have_count(2)
    expect(
        card.get_by_role("button", name="自訂 Inspector 匯出", exact=True)
    ).to_be_visible()


def test_istd_warning_distinguishes_confirmation_from_signal(analysis_feedback):
    page, _ = analysis_feedback
    warning = page.locator(".result-warning")
    expect(warning).to_contain_text("ISTD ISTD：0 / 1 個樣本通過確認")
    expect(warning).to_contain_text("未通過確認不代表沒有訊號")
    expect(warning).not_to_contain_text("個樣本偵測到")


@pytest.mark.parametrize("kind", ["targeted", "untargeted"])
def test_setup_orders_primary_choices_and_reveals_auxiliary_tools(workbench, kind):
    page = workbench
    _view(page, kind)
    form = page.locator(f"#{kind}-form")
    expect(form.locator(".step-no")).to_have_text(["1", "2", "3"])
    expect(form.locator("h2")).to_have_text(
        [
            "資料與輸出",
            "目標清單" if kind == "targeted" else "分析方法",
            "樣本角色",
        ]
    )
    prefix = "t-" if kind == "targeted" else ""
    expect(page.locator(f"#{prefix}samples-count")).to_be_visible()
    expect(page.locator(f"#{prefix}samples-refresh")).to_be_hidden()
    _expand_section(page, f"#{prefix}sample-tools")
    expect(page.locator(f"#{prefix}samples-refresh")).to_be_visible()
    expect(page.locator(f"#{prefix}samples-import")).to_be_visible()
    if kind == "untargeted":
        expect(page.locator("#istd-summary")).to_be_visible()
        expect(page.locator("#istd-preview")).to_be_hidden()
        _expand_section(page, "#istd-list-tools")
        expect(page.locator("#istd-preview")).to_be_visible()


def test_saved_result_needs_no_curve_source_until_requested(workbench, tmp_path):
    from tests.inspector_fixtures import create_review_batch

    page = workbench
    report, matrix = create_review_batch(tmp_path)
    original = matrix.read_bytes()
    _open_results(page)
    expect(page.locator("#source")).to_be_visible()
    expect(page.locator("#raw-dir")).not_to_be_visible()
    expect(page.locator("#dll-dir")).not_to_be_visible()
    page.locator("#source").fill(str(report))
    page.locator("#open-form button[type=submit]").click()
    expect(page.locator("#result-download")).to_be_visible()
    page.get_by_role("button", name="瀏覽矩陣群組").click()
    expect(page.locator("#recorded-review")).to_be_visible()
    expect(page.locator("#raw-dir")).not_to_be_visible()
    page.locator(".acquisition-panel > summary").click()
    expect(page.locator("#raw-dir")).to_be_visible()
    expect(page.locator("#dll-dir")).to_be_visible()
    assert matrix.read_bytes() == original


def test_result_reload_has_one_initialization_path(workbench, tmp_path):
    from tests.inspector_fixtures import create_review_batch

    page = workbench
    report, _ = create_review_batch(tmp_path)
    _open_results(page)
    page.locator("#source").fill(str(report))
    page.locator("#open-form button[type=submit]").click()
    expect(page.locator("#batch-title")).to_have_text("Untargeted 結果")
    sessions = []
    page.on(
        "request",
        lambda request: (
            sessions.append(request.url)
            if request.url.endswith("/api/session")
            else None
        ),
    )
    page.reload()
    expect(page.locator("body")).not_to_have_class(re.compile(r".*\bbooting\b.*"))
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#batch-title")).to_have_text("Untargeted 結果")
    assert len(sessions) == 1


def test_nearest_mz_search_ranks_before_paging_and_keeps_optional_rt(
    workbench, tmp_path
):
    from tests.inspector_fixtures import create_nearest_review_batch

    page = workbench
    report, matrix = create_nearest_review_batch(tmp_path)
    before = [path.read_bytes() for path in (report, matrix)]
    curve_requests = []
    page.on(
        "request",
        lambda request: (
            curve_requests.append(request.url)
            if request.url.endswith("/api/curves")
            else None
        ),
    )
    _open_results(page)
    page.locator("#source").fill(str(report))
    expect(page.locator("#raw-dir")).to_have_value("")
    page.locator("#open-form button[type=submit]").click()
    page.get_by_role("button", name="瀏覽矩陣群組").click()
    expect(page.locator("#untargeted-search-mode")).to_have_value("nearest")
    expect(page.locator("#rt-min")).to_be_visible()
    expect(page.locator("#rt-min")).to_have_value("")
    page.locator("#nearest-mz").fill("123.4000")
    with page.expect_response("**/api/query") as query:
        page.locator("#query-form button[type=submit]").click()
    titles = page.locator("#item-list .item-top strong")
    expect(titles).to_have_count(25)
    expect(titles.nth(0)).to_have_text("m/z 123.3980")
    expect(titles.nth(1)).to_have_text("m/z 123.4200")
    expect(titles.nth(2)).to_have_text("m/z 123.4567")
    # The workspace tests own distance/ranking correctness; the UI must display
    # the returned distance rather than recompute it from the formatted title.
    distance = query.value.json()["items"][0]["mz_distance"] / 123.4 * 1e6
    expect(page.locator("#item-list .item-distance").first).to_have_text(
        f"Δ {distance:.1f} ppm" if distance < 10 else f"Δ {round(distance):,} ppm"
    )
    expect(page.locator("#nearest-results-note")).to_contain_text("1 組缺少有效 m/z")
    expect(page.locator("#list-summary")).to_contain_text("32")
    page.locator("#next").click()
    expect(titles).to_have_count(7)
    expect(titles.last).to_have_text("m/z 350.0000")
    page.locator("#previous").click()
    expect(titles.nth(0)).to_have_text("m/z 123.3980")

    # Optional RT narrows eligibility without changing distance order and
    # remains applied when pagination requests the next candidate slice.
    page.locator("#rt-min").fill("0.5")
    page.locator("#query-form button[type=submit]").click()
    expect(titles.nth(0)).to_have_text("m/z 123.4200")
    page.locator("#next").click()
    expect(titles).to_have_count(6)
    expect(titles.last).to_have_text("m/z 350.0000")
    page.locator("#reset-query").click()
    expect(page.locator("#nearest-mz")).to_have_value("")
    expect(page.locator("#rt-min")).to_have_value("")
    expect(page.locator("#nearest-results-note")).to_be_hidden()
    expect(page.locator("#list-summary")).to_contain_text("33")
    expect(titles).to_have_count(25)
    assert not curve_requests
    assert [path.read_bytes() for path in (report, matrix)] == before


def test_nearest_mode_ignores_exact_filters_and_rejects_invalid_input(
    workbench, tmp_path
):
    from tests.inspector_fixtures import create_nearest_review_batch

    page = workbench
    report, _ = create_nearest_review_batch(tmp_path)
    _open_results(page)
    page.locator("#source").fill(str(report))
    expect(page.locator("#raw-dir")).to_have_value("")
    page.locator("#open-form button[type=submit]").click()
    page.get_by_role("button", name="瀏覽矩陣群組").click()
    page.locator("#untargeted-search-mode").select_option("exact")
    page.locator("#mz-min").fill("123.4")
    page.locator("#mz-max").fill("123.4")
    page.locator("#group-filter > summary").click()
    page.locator("#group-id").fill("multi-witness")
    page.locator("#query-form button[type=submit]").click()
    expect(page.locator("#item-list button")).to_have_count(1)
    # Exact matching still uses any eligible witness, not the displayed m/z.
    expect(page.locator("#item-list .item-top strong")).to_have_text("m/z 350.0000")
    expect(page.locator("#nearest-results-note")).to_be_hidden()
    page.locator("#untargeted-search-mode").select_option("nearest")
    expect(page.locator("#mz-min")).to_be_disabled()
    expect(page.locator("#group-id")).to_be_disabled()
    page.locator("#nearest-mz").fill("123.4")
    with page.expect_request("**/api/query") as request:
        page.locator("#query-form button[type=submit]").click()
    assert request.value.post_data_json == {
        "nearest_mz": 123.4,
        "review_scope": "matrix",
        "offset": 0,
        "limit": 25,
    }
    expect(page.locator("#item-list button")).to_have_count(25)
    expect(page.locator("#item-list .item-top strong").first).to_have_text(
        "m/z 123.3980"
    )
    for invalid in ("abc", "Infinity", "NaN", "0", "-1"):
        page.locator("#nearest-mz").fill(invalid)
        expect(page.locator("#nearest-mz-error")).to_contain_text("大於 0 的有限 m/z")
        assert not page.locator("#query-form").evaluate("form => form.checkValidity()")
        page.locator("#query-form button[type=submit]").click()
        expect(page.locator("#list-summary")).to_contain_text("123.4")
    page.locator("#nearest-mz").fill("123.4000")
    expect(page.locator("#nearest-mz-error")).to_be_hidden()
    page.locator("#query-form button[type=submit]").click()
    expect(page.locator("#item-list .item-top strong").first).to_have_text(
        "m/z 123.3980"
    )


def test_review_search_and_saved_results_keep_one_scroll_and_visible_evidence(
    workbench, tmp_path
):
    page = workbench
    db, _, _ = create_inspector_report(tmp_path)
    # Keep the existing curve-bearing fixture, extending only review row scope
    # beyond the UI's 25-item page. No scientific values are asserted here.
    groups = {"row-01": "G1"}
    with sqlite3.connect(db) as connection:
        for index in range(2, 27):
            row, group = f"row-{index:02}", f"G{index:02}"
            groups[row] = group
            for kind, identity, payload in (
                ("rows", row, {"feature_id": group}),
                (
                    "final_groups",
                    group,
                    {"feature_id": group, "disposition": "completed"},
                ),
            ):
                connection.execute(
                    "INSERT INTO records (kind, record_id, payload) VALUES (?, ?, ?)",
                    (kind, identity, json.dumps(payload)),
                )
                link_report_record(connection, ("C1", "S1", kind, identity))
    _open_results(page)
    page.locator("#source").fill(str(tmp_path))
    page.locator("#open-form button[type=submit]").click()
    expect(page.locator("#item-list button")).to_have_count(25)
    page.locator("#next").click()
    expect(page.locator("#item-list button")).to_have_count(1)
    off_page_id = page.locator("#item-list button").get_attribute("data-item-id")
    off_page_group = groups[json.loads(off_page_id)[-1]]
    page.locator("#previous").click()
    expect(page.locator("#item-list button")).to_have_count(25)
    assert off_page_id not in page.locator("#item-list button").evaluate_all(
        "buttons => buttons.map(button => button.dataset.itemId)"
    )
    page.locator("#group-filter > summary").click()
    page.locator("#group-id").fill(off_page_group)
    page.locator("#query-form button[type=submit]").click()
    expect(page.locator("#item-list button")).to_have_count(1)
    expect(page.locator("#item-list button")).to_have_attribute(
        "data-item-id", off_page_id
    )
    page.locator("#reset-query").click()
    expect(page.locator("#item-list button")).to_have_count(25)
    page.locator("#group-id").fill("G1")
    page.locator("#query-form button[type=submit]").click()
    expect(page.locator("#item-list button")).to_have_count(1)
    # Query also selects an item. Let that action finish before selecting again,
    # otherwise two detail responses can replace the view during a scroll.
    expect(page.locator("#query-form button[type=submit]")).to_be_enabled()
    with page.expect_response(
        lambda response: response.url.endswith("/api/detail")
    ) as detail:
        page.locator("#item-list button").first.click()
    expect(page.locator("#recorded-review")).to_have_attribute(
        "data-view-id", detail.value.json()["view_id"]
    )
    review = page.locator("#recorded-review")
    expect(review.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(review.locator("[data-inspector-id='svg-raw']")).to_have_count(0)
    # Mounted content participates in the host page's normal flow.
    expect(page.locator("#review iframe, #xic-dialog iframe")).to_have_count(0)
    assert review.evaluate(
        "container => container.scrollHeight <= container.clientHeight + 2"
    )
    review.get_by_role("button", name="查看依據").first.click()
    expect(review.locator("[data-inspector-id='evidence-panel']")).to_be_visible()
    review.locator("[data-inspector-id='close-evidence']").click()
    expect(review.locator("[data-inspector-id='evidence-panel']")).to_have_count(0)


@pytest.mark.parametrize("targeted", [True, False])
def test_review_download_survives_reload_and_keeps_original_product(
    workbench, tmp_path, targeted
):
    from tests.inspector_fixtures import create_review_batch
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    if targeted:
        source = product = write_rare_detection_workbook(tmp_path / "分析結果.xlsx")
    else:
        source, product = create_review_batch(tmp_path)
    page = workbench
    _open_results(page)
    page.locator("#source").fill(str(source))
    page.locator("#open-form button[type=submit]").click()
    link = page.get_by_role(
        "link", name="下載結果 Excel" if targeted else "下載 Alignment 矩陣", exact=True
    )
    expect(link).to_be_visible()
    page.reload()
    expect(link).to_be_visible()
    with page.expect_download() as pending:
        link.click()
    downloaded = pending.value
    assert downloaded.suggested_filename == product.name
    assert Path(downloaded.path()).read_bytes() == product.read_bytes()


def test_untargeted_review_starts_with_results_and_explains_missing_curves(
    workbench, tmp_path
):
    from tests.inspector_fixtures import create_review_batch

    report, _ = create_review_batch(tmp_path)
    page = workbench
    _open_results(page)
    page.locator("#source").fill(str(report))
    expect(page.locator("#raw-dir")).to_have_value("")
    page.locator("#open-form button[type=submit]").click()
    overview = page.locator("#targeted-overview")
    # The overview leads with the Matrix; unresolved groups are context only.
    expect(overview.get_by_role("heading", name="3 組進入矩陣")).to_be_visible()
    expect(overview.get_by_role("button", name="瀏覽矩陣群組")).to_have_class("primary")
    expect(page.locator("#review")).to_be_hidden()
    overview.get_by_role("button", name="1 部分樣本多峰留空").click()
    expect(page.locator("#item-list button")).to_have_count(1)
    expect(page.locator("#inline-curve-status")).to_contain_text("尚未讀取 RAW 曲線")
    saved = page.locator("#recorded-review")
    expect(saved.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(saved.locator(".sample-table tbody tr").first).to_contain_text("4.000e+2")
    expect(saved.locator("[data-inspector-id='svg-raw']")).to_have_count(0)
    page.locator("#back-to-overview").click()
    expect(overview).to_be_visible()
    overview.get_by_role("button", name="瀏覽矩陣群組").click()
    expect(page.locator("#item-list button")).to_have_count(3)
    page.locator("#back-to-overview").click()
    overview.locator("summary").click()
    overview.get_by_role("button", name="1 組未採用").click()
    expect(page.locator("#list-summary strong")).to_have_text("未採用群組")
    expect(page.locator("#list-summary")).to_contain_text("1 組")
    expect(
        page.locator("#recorded-review").locator(".sample-table tbody tr").first
    ).to_contain_text("5.000e+2")


@pytest.mark.parametrize("sample_count", (1, 6))
def test_untargeted_curves_open_beside_saved_report_and_reuse_loaded_view(
    workbench, tmp_path, monkeypatch, sample_count
):
    from dataclasses import replace

    from tests.xic_inspector_curve_bundle_fixtures import _fixture_bundle
    from xic_inspector.sources.curve_bundle import CurveBundle, write_curve_bundle
    from xic_workbench import acquisition

    calls = []
    detail_entered, release_detail = threading.Event(), threading.Event()
    detail = ReviewWorkspace.detail

    def held_detail(workspace, item_id):
        result = detail(workspace, item_id)
        if sample_count == 6 and result["item"]["group_id"] == "a-complete":
            detail_entered.set()
            assert release_detail.wait(HANDSHAKE_TIMEOUT_S)
        return result

    monkeypatch.setattr(ReviewWorkspace, "detail", held_detail)

    def acquire(report, output, *, sample_ids, **kwargs):
        calls.append(sample_ids)
        transition = _fixture_bundle().transitions[0]
        traces = tuple(
            replace(transition.traces[0], trace_id=sid, sample_id=sid, sample_label=sid)
            for sid in sample_ids
        )
        write_curve_bundle(CurveBundle((replace(transition, traces=traces),)), output)
        return {"curve_count": len(traces)}

    monkeypatch.setattr(acquisition, "export_explanation_curves", acquire)
    page = workbench
    from tests.inspector_fixtures import create_review_batch

    db = (
        create_inspector_report(tmp_path, samples=[{"sample_key": "S1"}])[0]
        if sample_count == 1
        else create_review_batch(tmp_path, sample_count=sample_count)[0]
    )
    _open_results(page)
    page.locator("#source").fill(str(db))
    page.locator("#open-form button[type=submit]").click()
    selected_view = None
    if sample_count > 1:
        page.get_by_role("button", name="瀏覽矩陣群組").click()
        _configure_curve_sources(page, tmp_path, tmp_path)
        page.locator("#load-curves").click()
        _wait_for_curve_report(page, inline=True)
        expect(
            page.locator("#inline-curve-review").locator(
                "[data-inspector-id='svg-raw'] polyline"
            )
        ).to_have_count(sample_count)
        previous_view = page.locator("#recorded-review").get_attribute("data-view-id")
        try:
            with page.expect_response("**/api/detail") as selected:
                page.locator("#item-list button").filter(has_text="6 / 6").click()
                assert detail_entered.wait(HANDSHAKE_TIMEOUT_S)
                expect(page.locator("#recorded-review")).to_have_attribute(
                    "data-view-id", previous_view
                )
                release_detail.set()
            selected_view = selected.value.json()["view_id"]
        finally:
            release_detail.set()
    else:
        _configure_curve_sources(page, tmp_path, tmp_path)
        page.locator("#load-curves").click()
    page.locator(".acquisition-panel > summary").click()
    expect(page.locator(".acquisition-panel > summary")).to_be_visible()
    expect(page.locator("#sample-list")).to_be_hidden()
    page.locator(".acquisition-panel > summary").click()
    saved = page.locator("#recorded-review")
    # A nonempty ID can still belong to the previous selection. Bind this
    # stability assertion to the selected detail response, not an old mount.
    expect(saved).to_have_attribute("data-view-id", selected_view or re.compile(r".+"))
    recorded = saved.get_attribute("data-view-id")
    review = _wait_for_curve_report(page, inline=True)
    expect(review.locator("[data-inspector-id='svg-raw'] polyline")).to_have_count(
        sample_count
    )
    expect(page.locator("#xic-dialog")).to_be_hidden()
    expect(saved.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(saved.locator("[data-inspector-id='svg-raw']")).to_have_count(0)
    assert (
        page.locator("#recorded-review").evaluate(
            "container => container.dataset.viewId"
        )
        == recorded
    )
    page.locator("#load-curves").click()
    expect(review.locator("[data-inspector-id='svg-raw'] polyline")).to_have_count(
        sample_count
    )
    assert set(calls[-1]) == {f"S{i + 1}" for i in range(sample_count)}
    assert len(calls) == (1 if sample_count == 1 else 2)
    review.locator("button[data-zoom]").click()
    expect(review.locator("[data-inspector-id='zoom-dialog']")).to_be_visible()
    page.wait_for_function("""() => {
        const dialog = document.querySelector(
            "#inline-curve-review [data-inspector-id='zoom-dialog']");
        const d = dialog.getBoundingClientRect();
        return d.top >= 0 && d.bottom <= innerHeight + 2;
    }""")
    review.locator("[data-inspector-id='zoom-close']").click()
    expect(review.locator("[data-inspector-id='zoom-dialog']")).to_be_hidden()


def test_group_position_curves_are_labelled_and_leave_saved_empty_cell_unchanged(
    workbench, config_dir, tmp_path, monkeypatch
):
    from tests.explanation_review_fixtures import (
        _group_with_missing_apex_review,
        _install_distinct_curve_raws,
    )
    from xic_workbench.samples import save_batch_samples

    report, matrix = _group_with_missing_apex_review(tmp_path)
    save_batch_samples(
        config_dir,
        tmp_path,
        [
            asdict(SampleMetadata(sid, sid, sample_role=role, group="Stored"))
            for sid, role in (
                ("S1", "study_sample"),
                ("S2", "study_sample"),
                ("S3", "qc"),
            )
        ],
    )
    originals = [path.read_bytes() for path in (report, matrix)]
    opened = []
    _install_distinct_curve_raws(monkeypatch, opened)
    page = workbench
    _open_results(page)
    page.locator("#source").fill(str(report))
    page.locator("#open-form button[type=submit]").click()
    page.get_by_role("button", name="瀏覽矩陣群組").click()
    _configure_curve_sources(page, tmp_path, tmp_path)
    page.locator("#load-curves").click()
    review = _wait_for_curve_report(page, inline=True)
    curves = review.locator("[data-inspector-id='svg-raw'] polyline")
    expect(curves).to_have_count(3)
    expect(review.locator('polyline[data-sample-id="S3"]')).to_have_attribute(
        "stroke-dasharray", "6 4"
    )
    review.get_by_role("button", name="聚焦 S3", exact=True).hover()
    expect(review.get_by_role("tooltip")).to_contain_text("依群組位置擷取")
    # Curves group by confirmed role, without another RAW read.
    review.get_by_role("checkbox", name="顯示 研究樣本 整組", exact=True).uncheck()
    expect(curves).to_have_count(1)
    expect(curves).to_have_attribute("data-sample-id", "S3")
    review.get_by_role("checkbox", name="顯示 QC 整組", exact=True).uncheck()
    expect(curves).to_have_count(0)
    review.get_by_role("checkbox", name="顯示 研究樣本 整組", exact=True).check()
    expect(curves).to_have_count(2)
    review.get_by_role("checkbox", name="顯示 QC 整組", exact=True).check()
    expect(curves).to_have_count(3)
    saved = page.locator("#recorded-review .sample-table tbody tr")
    expect(saved.filter(has_text="S1")).to_contain_text("1.000e+2")
    expect(saved.filter(has_text="S2")).to_contain_text("2.000e+2")
    expect(saved.filter(has_text="S3")).to_contain_text("空值")
    assert [name for name, _ in opened] == ["S1.raw", "S2.raw", "S3.raw"]
    assert [path.read_bytes() for path in (report, matrix)] == originals
    # A loaded view keeps the confirmed display context it was prepared with.
    view_id = review.get_attribute("data-view-id")
    save_batch_samples(
        config_dir,
        tmp_path,
        [
            asdict(SampleMetadata(sid, sid, sample_role="blank"))
            for sid in ("S1", "S2", "S3")
        ],
    )
    captured = page.evaluate(
        "id=>fetch(`/api/views/${id}`,{headers:{'X-XIC-Client':'workbench'}})"
        ".then(response=>response.json())",
        view_id,
    )
    assert captured["sample_groups"] == {"S1": "研究樣本", "S2": "研究樣本", "S3": "QC"}
    exported = page.evaluate(
        """async id=>{
        const response=await fetch(`/reports/${id}.html`,{
            headers:{'X-XIC-Client':'workbench'}});
        const html=new DOMParser().parseFromString(await response.text(),'text/html');
        return JSON.parse(html.querySelector('#inspector-data').textContent);
    }""",
        view_id,
    )
    assert "sample_groups" not in exported
    assert exported["items"] == captured["items"]
    assert exported["samples"] == captured["samples"]


def test_targeted_nd_row_opens_its_paired_curves_without_changing_results(
    workbench, tmp_path, monkeypatch
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S1": 13000})
    path = write_rare_detection_workbook(tmp_path / "nd.xlsx")
    original = path.read_bytes()
    _open_results(page)
    page.locator("#source").fill(str(path))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    _configure_curve_sources(page, raw_dir, tmp_path)
    saved = page.locator("#recorded-review")
    expect(saved.locator(".tgt-table tbody tr").first).to_contain_text("ND")
    saved.get_by_role("button", name="看 S1 的 Analyte XIC", exact=True).click()
    review = _wait_for_curve_report(page)
    expect(review.locator(".tx-sample-name")).to_have_text("S1")
    expect(review.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "filter_pending", [False, True], ids=["completed-reading", "pending-reading"]
)
def test_curve_history_restores_snapshot_after_pending_load(
    workbench,
    tmp_path,
    monkeypatch,
    filter_pending,
):
    from contextlib import contextmanager

    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )
    from xic_workbench import targeted_curves

    page = workbench
    raw_dir = tmp_path / "review-raw"
    reads = install_targeted_raws(
        monkeypatch, raw_dir, {"S3": 1000, "S4": 300}, pair_scale=1000
    )
    release = threading.Event()
    open_raw = targeted_curves.open_raw

    @contextmanager
    def delayed_raw(*args, **kwargs):
        assert release.wait(HANDSHAKE_TIMEOUT_S)
        with open_raw(*args, **kwargs) as raw:
            yield raw

    monkeypatch.setattr(targeted_curves, "open_raw", delayed_raw)
    for sample in ("S3", "S4"):
        batch = raw_dir / sample
        batch.mkdir()
        (raw_dir / f"{sample}.raw").rename(batch / f"{sample}.raw")
    workbook = write_rare_detection_workbook(tmp_path / "paired.xlsx")
    original = workbook.read_bytes()
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    _configure_curve_sources(page, raw_dir, tmp_path)
    recorded = page.locator("#recorded-review")
    row_button = recorded.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True)
    expect(row_button).to_be_visible()
    row_button.scroll_into_view_if_needed()
    reading_scroll = page.evaluate("window.scrollY")
    recorded_view = page.locator("#recorded-review").evaluate(
        "container => container.dataset.viewId"
    )
    curve_posts = []
    page.on(
        "request",
        lambda request: (
            curve_posts.append(request)
            if request.url.endswith("/api/curves") and request.method == "POST"
            else None
        ),
    )
    row_button.click()
    dialog = page.locator("#xic-dialog")
    expect(dialog).to_be_visible()
    page.go_back()
    expect(dialog).to_be_hidden()
    page.wait_for_function("y => Math.abs(window.scrollY - y) < 2", arg=reading_scroll)
    release.set()
    expect(page.locator("#status")).to_contain_text(
        "診斷曲線已載入", timeout=BACKGROUND_JOB_TIMEOUT_MS
    )
    # Closing during payload loading must not mount the response into a hidden
    # drawer; Forward can request that same completed curve snapshot again.
    view_id = page.locator("#xic-download").get_attribute("href").split("/")[-1]
    view_id = view_id.split(".")[0]
    view_url = f"**/api/views/{view_id}"
    delayed_payload = []

    def delay_first_payload(route):
        if not delayed_payload:
            delayed_payload.append(route)
        else:
            route.continue_()

    if filter_pending:
        # This history entry was captured while acquisition was still pending.
        # Replacing live detail must not lose its eventual view ID.
        page.locator("#target-id").select_option("ISTD")
        expect(page.locator("#item-title")).to_contain_text("ISTD")
        expect(page.locator("#target-id")).to_be_enabled()
    page.route(view_url, delay_first_payload)
    with page.expect_request(view_url):
        page.go_forward()
    expect(dialog).to_be_visible()
    review = page.locator("#xic-review")
    expect(review.locator(".xic-inspector")).to_have_count(0)
    page.go_back()
    expect(dialog).to_be_hidden()
    with page.expect_response(lambda response: response.url.endswith(view_id)):
        delayed_payload[0].fulfill(response=delayed_payload[0].fetch())
    page.evaluate("""() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
    expect(review.locator(".xic-inspector")).to_have_count(0)
    expect(page.locator("#status")).not_to_have_class(re.compile(r"\bworking\b"))
    page.go_forward()
    expect(dialog).to_be_visible()
    expect(page.locator("#group-curve-settings")).to_be_hidden()
    expect(page.locator("#load-curves")).to_be_hidden()
    expect(page.locator("#xic-sample")).to_have_value("S3")
    _wait_for_curve_report(page)
    curves = review.locator(".tx-chart polyline")
    expect(review.locator('.tx-chart circle[data-kind="raw"]')).not_to_have_count(0)
    expect(review.get_by_text("原始 scan 點", exact=True)).to_be_visible()
    expect(curves).to_have_count(2)
    expect(review.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    expect(review.locator(".tx-provenance")).to_contain_text("尚未比對是否為分析原檔")
    # The result table never navigates away; each ion has its own y scale.
    assert (
        page.locator("#recorded-review").evaluate(
            "container => container.dataset.viewId"
        )
        == recorded_view
    )
    expect(review.locator(".tx-chart")).to_have_count(2)
    expect(review).to_have_attribute("data-view-id", view_id)
    expect(page.locator("#target-id")).to_have_value("Analyte")
    expect(review.locator(".tx-sample-name")).to_have_text("S3")
    expect(review.locator(".tx-chart-meta").nth(0)).to_contain_text("面積 5.55e+1")
    expect(review.locator(".tx-chart-meta").nth(1)).to_contain_text("面積 1.00e+3")
    assert len(reads) == 2
    assert workbook.read_bytes() == original


@pytest.fixture
def cached_paired_curves(workbench, tmp_path, monkeypatch):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "cached-raw"
    reads = install_targeted_raws(
        monkeypatch, raw_dir, {"S3": 1000, "S4": 300}, pair_scale=1000
    )
    workbook = write_rare_detection_workbook(tmp_path / "cached-paired.xlsx")
    original = workbook.read_bytes()
    posts = []
    page.on(
        "request",
        lambda request: (
            posts.append(request)
            if request.url.endswith("/api/curves") and request.method == "POST"
            else None
        ),
    )
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    _configure_curve_sources(page, raw_dir, tmp_path)
    page.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True).click()
    review = _wait_for_curve_report(page)
    expect(review.locator(".tx-sample-name")).to_have_text("S3")
    views = {"S3": review.get_attribute("data-view-id")}
    page.locator("#xic-next").click()
    _wait_for_curve_report(page)
    expect(review.locator(".tx-sample-name")).to_have_text("S4")
    views["S4"] = review.get_attribute("data-view-id")
    assert len(reads) == 4
    assert len(posts) == 2
    yield page, views, reads, posts
    assert workbook.read_bytes() == original


def test_cached_curve_history_restores_focus_range_and_drawer(cached_paired_curves):
    page, views, reads, posts = cached_paired_curves
    page.locator("#xic-sample").select_option("S3")
    review = _wait_for_curve_report(page)
    expect(review).to_have_attribute("data-view-id", views["S3"])
    recorded_view = page.locator("#recorded-review").get_attribute("data-view-id")
    opener = page.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True)
    review.get_by_label("顯示 RT 起點", exact=True).fill("8.95")
    review.get_by_label("顯示 RT 終點", exact=True).fill("9.05")
    review.get_by_role("button", name="套用範圍", exact=True).click()
    dialog = page.locator("#xic-dialog")
    for close in (
        page.go_back,
        lambda: page.keyboard.press("Escape"),
        lambda: page.locator("#xic-close").click(),
    ):
        close()
        expect(dialog).to_be_hidden()
        expect(opener).to_be_focused()
        page.go_forward()
        expect(dialog).to_be_visible()
        expect(review.get_by_label("顯示 RT 起點", exact=True)).to_have_value("8.95")
        expect(review.get_by_label("顯示 RT 終點", exact=True)).to_have_value("9.05")
    page.go_back()
    page.locator("#target-id").select_option("ISTD")
    expect(page.locator("#item-title")).to_contain_text("ISTD")
    expect(page.locator("#target-id")).to_be_enabled()
    page.go_forward()
    expect(review).to_have_attribute("data-view-id", views["S3"])
    expect(page.locator("#target-id")).to_have_value("Analyte")
    expect(review.locator(".tx-sample-name")).to_have_text("S3")
    expect(page.locator("#recorded-review")).to_have_attribute(
        "data-view-id", recorded_view
    )
    page.set_viewport_size({"width": 420, "height": 760})
    bounds = dialog.bounding_box()
    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 421
    assert len(reads) == 4 and len(posts) == 2


def test_missing_curve_recovers_to_cached_sample_feedback(cached_paired_curves):
    page, views, reads, posts = cached_paired_curves
    page.locator("#xic-next").click()  # S5 has no RAW.
    expect(page.locator("#xic-status")).to_contain_text(
        "找不到", timeout=BACKGROUND_JOB_TIMEOUT_MS
    )
    expect(page.locator("#status")).to_contain_text("S5")
    expect(page.locator("#status")).to_have_class(re.compile(r"\berror\b"))
    expect(page.locator("#xic-review")).to_be_hidden()
    expect(page.locator("#xic-retry")).to_be_visible()
    for sample in ("S4", "S3"):
        page.locator("#xic-previous").click()
        review = page.locator("#xic-review")
        expect(review).to_be_visible()
        expect(review).to_have_attribute("data-view-id", views[sample])
        expect(review.locator(".tx-sample-name")).to_have_text(sample)
        expect(page.locator("#xic-retry")).to_be_hidden()
        for status in ("#xic-status", "#status"):
            expect(page.locator(status)).to_contain_text("診斷曲線已載入")
            expect(page.locator(status)).to_contain_text(sample)
        expect(page.locator("#status")).not_to_have_class(re.compile(r"\berror\b"))
    page.locator("#xic-close").click()
    expect(page.locator("#status")).to_contain_text("S3")
    assert len(reads) == 4 and len(posts) == 3


def test_cached_curve_does_not_clear_a_current_query_error(
    cached_paired_curves, browser_errors
):
    page, views, reads, posts = cached_paired_curves
    page.locator("#xic-close").click()
    failure = "Browser query failure remains actionable"
    page.route(
        "**/api/query",
        lambda route: route.fulfill(status=500, json={"error": failure}),
    )
    expected_http_error = (
        "Failed to load resource: the server responded with a status of "
        "500 (Internal Server Error)"
    )
    with page.expect_console_message(
        lambda message: message.type == "error" and message.text == expected_http_error
    ):
        page.locator("#target-attention").check()
    browser_errors.remove(expected_http_error)
    expect(page.locator("#status")).to_have_text(failure)
    page.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True).click()
    review = page.locator("#xic-review")
    expect(review.locator(".tx-sample-name")).to_have_text("S3")
    expect(review).to_have_attribute("data-view-id", views["S3"])
    expect(page.locator("#xic-status")).to_contain_text("診斷曲線已載入")
    expect(page.locator("#xic-status")).to_contain_text("S3")
    expect(page.locator("#xic-retry")).to_be_hidden()
    expect(page.locator("#status")).to_have_text(failure)
    expect(page.locator("#status")).to_have_class(re.compile(r"\berror\b"))
    page.locator("#xic-close").click()
    expect(page.locator("#status")).to_have_text(failure)
    assert len(reads) == 4
    assert len(posts) == 2


@pytest.mark.parametrize("old_status", [200, 500], ids=["success", "failure"])
def test_delayed_cached_curve_cannot_replace_current_sample_feedback(
    cached_paired_curves, browser_errors, old_status
):
    page, views, reads, posts = cached_paired_curves
    pending = []
    old_url = f"**/api/views/{views['S3']}"
    page.route(old_url, lambda route: pending.append(route))
    try:
        with page.expect_request(old_url):
            page.locator("#xic-sample").select_option("S3")
        expect(page.locator("#xic-status")).to_contain_text("正在")
        expect(page.locator("#xic-loading")).to_be_visible()
        expect(page.locator("#xic-retry")).to_be_hidden()
        page.locator("#xic-sample").select_option("S4")
        review = page.locator("#xic-review")
        expect(review.locator(".tx-sample-name")).to_have_text("S4")
        expect(review).to_have_attribute("data-view-id", views["S4"])
        expect(page.locator("#status")).to_contain_text("診斷曲線已載入")
        expect(page.locator("#status")).to_contain_text("S4")
        expect(page.locator("#xic-status")).to_contain_text("S4")
        current_global = page.locator("#status").text_content()
        current_local = page.locator("#xic-status").text_content()
        old = pending.pop()
        if old_status == 500:
            expected_http_error = (
                "Failed to load resource: the server responded with a status of "
                "500 (Internal Server Error)"
            )
            with page.expect_console_message(
                lambda message: (
                    message.type == "error" and message.text == expected_http_error
                )
            ):
                with page.expect_response(old_url):
                    old.fulfill(status=500, json={"error": "Obsolete S3 view failure"})
            browser_errors.remove(expected_http_error)
        else:
            with page.expect_response(old_url):
                old.fulfill(response=old.fetch())
        # Flush the released payload's consumer before checking visible authority.
        page.evaluate("""() => new Promise(resolve =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
        expect(page.locator("#xic-sample")).to_have_value("S4")
        expect(review.locator(".tx-sample-name")).to_have_text("S4")
        expect(review).to_have_attribute("data-view-id", views["S4"])
        expect(page.locator("#status")).to_have_text(current_global)
        expect(page.locator("#status")).not_to_have_class(re.compile(r"\berror\b"))
        expect(page.locator("#xic-status")).to_have_text(current_local)
        expect(page.locator("#xic-retry")).to_be_hidden()
        expect(page.locator("#xic-loading")).to_be_hidden()
        assert len(reads) == 4
        assert len(posts) == 2
    finally:
        for route in pending:
            route.continue_()
        page.unroute(old_url)


def test_targeted_overview_opens_and_leaves_single_target_review(workbench, tmp_path):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    overview = page.locator("#targeted-overview")
    expect(overview.locator("h2")).to_have_text("先看 1 筆結果")
    overview.locator("td button", has_text="Analyte").click()
    expect(page.locator("#item-list")).to_be_hidden()
    expect(page.locator("#target-id")).to_be_visible()
    expect(page.locator("#recorded-review .tgt-table")).to_be_visible()
    expect(page.locator("#review")).to_have_class("review-grid single-target")
    page.locator("#target-id").select_option("")
    expect(page.locator("#item-list")).to_be_visible()
    expect(page.locator("#review")).to_have_class("review-grid")


def test_saved_method_and_delta_evidence_is_visible_and_readonly(workbench, tmp_path):
    from openpyxl import Workbook

    from tests.targeted_delta_mass_fixtures import pattern
    from xic_extractor.configuration.identification_method_table import (
        METHOD_TABLE_HEADERS,
        identification_method_rows,
    )
    from xic_extractor.configuration.targeted_identification import (
        parse_identification_methods,
    )

    book = Workbook()
    targets = book.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "Method",
        ]
    )
    targets.append(["Custom target", "Analyte", 302, 2, 2, "my pair"])
    results = book.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Target",
            "RT",
            "Area",
            "PeakStart",
            "PeakEnd",
            "Reason",
            "Review",
            "Review Reason",
            "Counted Detection",
        ]
    )
    results.append(
        [
            "Sample A",
            "Custom target",
            2,
            123.5,
            1.9,
            2.1,
            "Delta Mass 配對符合",
            "",
            "",
            "TRUE",
        ]
    )
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    definitions = book.create_sheet("Identification Methods")
    definitions.append(list(METHOD_TABLE_HEADERS))
    definition = pattern()
    definition["combine_mode"] = "any"
    definition["conditions"].insert(0, {"kind": "cid_nl", "tag": "dR"})
    for row in identification_method_rows(
        parse_identification_methods({"my pair": definition})
    ):
        definitions.append([row.get(header) for header in METHOD_TABLE_HEADERS])
    answers = book.create_sheet("Method Evidence")
    answers.append(["SampleName", "Target", "Method", "Condition Label", "Status"])
    answers.append(
        ["Sample A", "Custom target", "my pair", "CID-NL · dR", "not_acquired"]
    )
    answers.append(["Sample A", "Custom target", "my pair", "Delta Mass", "matched"])
    evidence = book.create_sheet("Delta Mass Evidence")
    evidence.append(
        [
            "SampleName",
            "Target",
            "Method",
            "Pattern Status",
            "Position",
            "Expected m/z",
            "Shape r",
        ]
    )
    evidence.append(
        ["Sample A", "Custom target", "my pair", "pass", "heavy", 302, 0.987]
    )
    path = tmp_path / "delta-mass.xlsx"
    book.save(path)
    book.close()
    original = path.read_bytes()
    page = workbench
    _open_results(page)
    page.locator("#source").fill(str(path))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Custom target").click()
    expect(page.locator(".tgt-review")).to_contain_text("沒有 MS2")
    expect(page.locator(".tgt-review")).to_contain_text("符合任一條件（OR）")
    page.locator("summary", has_text="Delta Mass 配對明細").click()
    panel = page.locator("details").filter(
        has=page.locator("summary", has_text="Delta Mass 配對明細")
    )
    expect(panel).to_contain_text("Sample A")
    expect(panel).to_contain_text("heavy")
    expect(panel).to_contain_text("0.987")
    expect(page.get_by_role("cell", name="沒有 MS2", exact=True)).to_be_visible()
    page.locator(".tgt-review").screenshot(path=str(tmp_path / "method-evidence.png"))
    assert path.read_bytes() == original


def test_review_back_forward_preserves_target_and_filters(workbench, tmp_path):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    page.locator("#target-attention").check()
    expect(
        page.locator("#recorded-review").locator(".tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    page.go_back()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#review")).to_be_visible()
    expect(page.locator("#target-id")).to_have_value("Analyte")
    expect(page.locator("#target-attention")).to_be_checked()
    expect(
        page.locator("#recorded-review").locator(".tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    page.locator("#back-to-overview").click()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    _view(page, "targeted")
    page.go_back()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#target-attention")).to_be_checked()
    page.go_forward()
    expect(page.locator("#workspace-targeted")).to_be_visible()


@pytest.mark.parametrize("query_pending", [False, True], ids=["completed", "pending"])
def test_review_history_restores_server_query_before_loading_xic(
    workbench, tmp_path, monkeypatch, query_pending
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    queried, release = threading.Event(), threading.Event()
    if query_pending:
        query = ReviewWorkspace.query

        def hold_query_response(workspace, values):
            result = query(workspace, values)
            if values.get("target_id") == "ISTD":
                queried.set()
                assert release.wait(HANDSHAKE_TIMEOUT_S)
            return result

        monkeypatch.setattr(ReviewWorkspace, "query", hold_query_response)
    raw_dir = tmp_path / "review-raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S3": 1000}, pair_scale=1000)
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    _configure_curve_sources(page, raw_dir, tmp_path)
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    analyte_xic = page.locator("#recorded-review").get_by_role(
        "button", name="看 S3 的 Analyte XIC", exact=True
    )
    expect(analyte_xic).to_be_visible()
    _view(page, "untargeted")
    _open_results(page)
    page.locator("#target-id").select_option("ISTD")
    try:
        if query_pending:
            assert queried.wait(HANDSHAKE_TIMEOUT_S)
        else:
            expect(page.locator("#item-title")).to_contain_text("ISTD")
            expect(
                page.locator("#recorded-review").get_by_role(
                    "button", name="看 S3 的 ISTD XIC", exact=True
                )
            ).to_be_visible()
        page.go_back()
        expect(page.locator("#workspace-untargeted")).to_be_visible()
        page.go_back()
    finally:
        release.set()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    expect(page.locator("#load-curves")).to_be_enabled()
    with page.expect_response("**/api/curves") as response:
        analyte_xic.click()
    assert response.value.status == 202, response.value.json()
    review = _wait_for_curve_report(page)
    expect(review.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])


def test_review_overview_button_skips_workspace_tab_history(workbench, tmp_path):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    _view(page, "targeted")
    _open_results(page)
    page.locator("#back-to-overview").click()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#item-title")).to_contain_text("Analyte")


def test_targeted_row_xic_without_raw_points_at_the_missing_folder(workbench, tmp_path):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    page.set_viewport_size({"width": 1280, "height": 480})
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    row_button = page.locator("#recorded-review").get_by_role(
        "button", name="看 S5 的 Analyte XIC", exact=True
    )
    row_button.scroll_into_view_if_needed()
    expect(page.locator("#raw-dir")).not_to_be_in_viewport()
    row_button.click()
    # The request stops before RAW work and the reason is where the user looks.
    expect(page.locator("#xic-dialog")).to_be_hidden()
    expect(page.locator("#raw-dir")).to_be_focused()
    expect(page.locator("#raw-dir")).to_be_in_viewport()
    expect(page.locator("#status")).to_contain_text("請先選擇 RAW 資料夾")
    expect(page.locator("#curve-source-status")).to_contain_text("請先選擇 RAW 資料夾")
    expect(page.locator("#curve-source-status")).to_be_in_viewport()


def test_read_first_opens_on_minority_rows_and_clearing_lists_all(workbench, tmp_path):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview .overview-actions .primary").click()
    review = page.locator("#recorded-review")
    rows = review.locator(".tgt-table tbody tr:not([hidden])")
    # Reading first starts on the minority row; every sample is one click away.
    expect(rows).to_have_count(1)
    expect(review.locator(".tgt-filter button")).to_have_text(
        ["全部（5）", "需先看（1）"]
    )
    # A filter change applies at once; no separate search press is needed.
    page.locator("#target-attention").uncheck()
    expect(rows).to_have_count(5)


@pytest.mark.parametrize(
    "changed_input", [None, "raw_dir", "sequence_source", "dll_dir", "saved_role"]
)
def test_sample_restore_is_bound_to_current_inputs(
    workbench, fake_runs, tmp_path, monkeypatch, changed_input
):
    page = workbench
    old = tmp_path / "batch_a"
    old.mkdir()
    row = asdict(SampleMetadata("S1", "S1", sample_role="blank", injection_order=1))
    monkeypatch.setattr(
        "xic_workbench.samples.load_samples",
        lambda *args, **kwargs: {
            "samples": [row],
            "summary": {
                "raw_count": 1,
                "matched_count": 0,
                "conflicts": [],
                "unreadable_raw": ["old_blank.raw"],
            },
        },
    )
    _view(page, "untargeted")
    page.locator("#u-raw_dir").fill(str(old))
    page.locator("#u-raw_dir").press("Tab")
    with page.expect_response(lambda r: r.url.endswith("/api/untargeted/save")):
        page.locator("#untargeted-save").click()
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-count")).to_have_text("1 個樣本")
    # Restoration must not depend on the most recent job being a samples job.
    _view(page, "targeted")
    fake_runs.release.set()
    _start(page, "targeted")
    _wait_for_analysis_status(page, "已完成")
    _view(page, "untargeted")
    if changed_input == "saved_role":
        page.locator("#samples-edit").click()
        with page.expect_response(lambda r: r.url.endswith("/api/samples/save")):
            page.get_by_label("S1 角色", exact=True).select_option("study_sample")
        page.keyboard.press("Escape")
        with page.expect_response(lambda r: r.url.endswith("/api/untargeted/save")):
            page.locator("#untargeted-save").click()
    elif changed_input:
        selector = (
            "#sequence-source"
            if changed_input == "sequence_source"
            else f"#u-{changed_input}"
        )
        if changed_input == "dll_dir":
            _set_reader_path(page, str(tmp_path / "new_source"))
        else:
            page.locator(selector).fill(str(tmp_path / "new_source"))
            page.locator(selector).press("Tab")
        with page.expect_response(lambda r: r.url.endswith("/api/untargeted/save")):
            page.locator("#untargeted-save").click()
    page.reload()
    expect(page.locator("body")).not_to_have_class("booting")
    with page.expect_request(lambda r: r.url.endswith("/api/untargeted/save")) as sent:
        page.locator("#untargeted-save").click()
    payload = sent.value.post_data_json
    if changed_input and changed_input != "saved_role":
        assert payload["samples"] is None
        expect(page.locator("#samples-conflicts")).to_be_hidden()
        assert "custom_roles" not in payload
    else:
        expected = (
            {**row, "sample_role": "study_sample"}
            if changed_input == "saved_role"
            else row
        )
        assert payload["samples"] == [expected]
        expect(page.locator("#samples-conflicts")).to_contain_text("old_blank.raw")


def test_downloaded_current_method_edits_and_saves_without_changing_batch(
    workbench, config_dir, tmp_path
):
    from xic_extractor.configuration.targeted_method_files import load_targeted_method

    page = workbench
    batch = page.locator("#t-data_dir").input_value()
    dll = page.locator("#t-dll_dir").input_value()
    with page.expect_download() as download:
        page.get_by_role("link", name="下載目標清單範例 XLSX", exact=True).click()
    path = tmp_path / "downloaded-method.xlsx"
    download.value.save_as(path)
    original = path.read_bytes()
    assert dict(load_targeted_method(path).settings) == {"strategy": "cid-nl"}
    with page.expect_file_chooser() as chooser:
        page.locator("#target-import").click()
    chooser.value.set_files(path)
    expect(page.locator("#target-import-summary")).to_contain_text(
        "方法檔（Method + Targets）"
    )
    expect(page.locator("#target-preview")).to_contain_text("Browser target")
    page.locator("#target-import-confirm").click()
    expect(page.locator("#target-preview")).to_contain_text("5-hmdC")
    expect(page.locator("#t-data_dir")).to_have_value(batch)
    expect(page.locator("#t-dll_dir")).to_have_value(dll)
    page.locator("#target-edit").click()
    page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True).fill(
        "9.25"
    )
    page.locator("#target-editor-save").click()
    page.locator("#target-method-save").click()
    page.locator("#path-dialog-value").fill("Edited method")
    page.locator("#path-dialog").get_by_role("button", name="確定").click()
    expect(page.locator("#target-method-select option:checked")).to_have_text(
        "Edited method"
    )
    saved = load_targeted_method(config_dir / "methods" / "Edited method.xlsx")
    assert dict(saved.settings) == {"strategy": "cid-nl"}
    assert saved.target_rows[0]["expected_rt_min"] == "9.25"
    assert saved.target_rows[0]["method"] == "dR"
    assert path.read_bytes() == original
    assert not (config_dir / "settings.csv").exists()
    assert not (config_dir / "targets.csv").exists()
    assert (config_dir / "workspace.json").is_file()
    page.screenshot(path=str(tmp_path / "xlsx-method-workspace.png"), full_page=True)


def test_drop_target_table_preview_cancel_and_confirm(workbench, tmp_path):

    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration.targeted_method_files import save_target_table

    page = workbench
    # Format decoding belongs to the upload owner tests. This case exercises
    # the browser's drop/preview/cancel/confirm wiring with one real workbook.
    path = tmp_path / "renamed.xlsx"
    save_target_table(targeted_rows(), path)
    batch = page.locator("#t-data_dir").input_value()
    for confirm in [False, True]:
        transfer = page.evaluate_handle(
            """({name, content}) => {
            const bytes = Uint8Array.from(atob(content), c => c.charCodeAt(0));
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], name));
            return transfer;
        }""",
            {
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode(),
            },
        )
        page.locator("#target-dropzone").dispatch_event(
            "drop", {"dataTransfer": transfer}
        )
        expect(page.locator("#target-import-summary")).to_contain_text(
            "目標表 · 2 個目標（ISTD 1 個）"
        )
        expect(page.locator("#target-import-rows tr").nth(1)).to_contain_text(
            "standard"
        )
        expect(page.locator("#target-preview")).to_contain_text("Browser target")
        if confirm:
            page.locator("#target-import-confirm").click()
        else:
            page.locator("#target-import-preview").get_by_role(
                "button", name="取消", exact=True
            ).click()
            expect(page.locator("#target-preview")).to_contain_text("Browser target")
    expect(page.locator("#target-preview")).to_contain_text("analyte")
    expect(page.locator("#target-preview")).not_to_contain_text("Browser target")
    expect(page.locator("#t-data_dir")).to_have_value(batch)
    page.locator("#target-import-file").set_input_files(
        {"name": "bad.xls", "mimeType": "application/octet-stream", "buffer": b"bad"}
    )
    expect(page.locator("#target-import-status")).to_contain_text("不支援 .xls")
    expect(page.locator("#target-preview")).to_contain_text("analyte")


def test_analysis_review_uses_run_paths_even_after_batch_edit(
    workbench, fake_runs, tmp_path, monkeypatch
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "run-raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S1": 13000})
    fake_runs.targeted_workbook = write_rare_detection_workbook(tmp_path / "run.xlsx")
    fake_runs.release.set()
    page.locator("#t-data_dir").fill(str(raw_dir))
    _set_reader_path(page, str(tmp_path))
    page.get_by_role("button", name="開始 Targeted 分析", exact=True).click()
    _wait_for_analysis_status(page, "已完成")
    expect(page.locator("#analysis-job-message")).to_contain_text("工作已完成")
    # The finished run opens its result; the next batch is edited on setup.
    expect(page.locator("#analysis-result")).to_be_visible()
    page.locator("#result-back").click()
    page.locator("#t-data_dir").fill(str(tmp_path / "next-batch"))
    _set_reader_path(page, str(tmp_path / "next-dll"))
    page.get_by_role("button", name="檢閱結果", exact=True).click()
    with page.expect_download() as pending:
        page.get_by_role("link", name="下載結果 Excel", exact=True).click()
    assert (
        Path(pending.value.path()).read_bytes()
        == fake_runs.targeted_workbook.read_bytes()
    )
    expect(page.locator("#raw-dir")).to_have_value(str(raw_dir))
    expect(page.locator("#dll-dir")).to_have_value(str(tmp_path))
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    page.locator("#recorded-review").get_by_role(
        "button", name="看 S1 的 Analyte XIC", exact=True
    ).click()
    review = _wait_for_curve_report(page)
    expect(review.locator('.tx-chart polyline[data-kind="smoothed"]')).to_have_count(2)
    expect(page.locator("#status")).not_to_contain_text("請先選擇 RAW 資料夾")


@pytest.mark.parametrize("extension,drop", [("xlsx", True), ("tsv", False)])
def test_untargeted_istd_shared_upload_and_persistence(
    workbench, config_dir, tmp_path, extension, drop
):
    import csv

    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration.targeted_method_files import save_target_table
    from xic_extractor.configuration.targets import read_istd_targets

    page = workbench
    path = tmp_path / f"shared.{extension}"
    if extension == "xlsx":
        save_target_table(targeted_rows(), path)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(dict.fromkeys(k for r in targeted_rows() for k in r)),
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerows(targeted_rows())
    original_targets = page.locator("#target-preview").inner_text()
    page.get_by_role("button", name="Untargeted", exact=True).click()
    _expand_section(page, "#istd-list-tools")
    expect(page.get_by_role("link", name="下載共用清單範例 XLSX")).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("link", name="下載共用清單範例 XLSX").click()
    download.value.save_as(tmp_path / "example.xlsx")
    assert read_istd_targets(tmp_path / "example.xlsx").targets
    _expand_section(page, "#istd-list-tools")
    if drop:
        transfer = page.evaluate_handle(
            """({name, content}) => {
            const transfer = new DataTransfer();
            const bytes = Uint8Array.from(atob(content), c => c.charCodeAt(0));
            transfer.items.add(new File([bytes], name));
            return transfer;
        }""",
            {
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode(),
            },
        )
        page.locator("#istd-path-field").dispatch_event(
            "drop", {"dataTransfer": transfer}
        )
    else:
        _drop_file(page, "#istd-path-field", path.name, path.read_bytes())
    expect(page.locator("#istd-import-status")).to_contain_text(
        f"已匯入 shared.{extension}"
    )
    expect(page.locator("#istd-summary")).to_have_text(
        "1 個適用內標；0 個不適用此方法；略過 1 個一般目標。"
    )
    expect(page.locator("#istd-preview")).to_contain_text("standard")
    expect(page.locator("#istd-preview")).not_to_contain_text("analyte")
    retained = page.locator("#u-targets_csv").input_value()
    _drop_file(page, "#istd-path-field", "bad.xls", b"bad")
    expect(page.locator("#istd-import-status")).to_contain_text("不支援 .xls")
    expect(page.locator("#u-targets_csv")).to_have_value(retained)
    expect(page.locator("#istd-preview")).to_contain_text("standard")
    page.locator("#untargeted-save").click()
    expect(page.locator("#untargeted-dirty")).to_be_empty()
    assert (
        json.loads((config_dir / "discovery_gui.json").read_text(encoding="utf-8"))[
            "targets_csv"
        ]
        == retained
    )
    path.unlink()
    page.reload()
    page.get_by_role("button", name="Untargeted", exact=True).click()
    expect(page.locator("#u-targets_csv")).to_have_value(retained)
    expect(page.locator("#istd-summary")).to_have_text(
        "1 個適用內標；0 個不適用此方法；略過 1 個一般目標。"
    )
    _expand_section(page, "#istd-list-tools")
    page.locator("#u-targets_csv").fill("")
    page.locator("#u-targets_csv").press("Tab")
    expect(page.locator("#istd-summary")).to_have_text("本次不使用內標清單。")
    expect(page.locator("#istd-preview")).to_be_empty()
    page.get_by_role("button", name="Targeted", exact=True).click()
    expect(page.locator("#target-preview")).to_have_text(
        original_targets, use_inner_text=True
    )


def test_untargeted_istd_applicability_tracks_method_not_nd(workbench, tmp_path):
    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration.targeted_method_files import save_target_table

    page = workbench
    targets = targeted_rows()
    targets.append({**targets[0], "label": "RNA standard", "method": "R"})
    path = tmp_path / "method-standards.xlsx"
    save_target_table(targets, path)
    _view(page, "untargeted")
    _drop_file(page, "#istd-path-field", path.name, path.read_bytes())
    expect(page.locator("#istd-import-status")).to_contain_text("已匯入")
    rna = page.locator("#istd-preview tr").filter(has_text="RNA standard")
    expect(page.locator("#istd-summary")).to_have_text(
        "1 個適用內標；1 個不適用此方法；略過 1 個一般目標。"
    )
    expect(rna).to_contain_text("不適用此方法（略過）")
    page.locator("#u-preset").select_option("rna_ribose")
    expect(rna.locator("td").last).to_have_text("適用")
    page.locator("#u-preset").select_option("rna_mer")
    expect(page.locator("#istd-summary")).to_contain_text("0 個適用內標")
    expect(page.locator("#istd-summary")).to_contain_text("本次不使用內標參考")
    expect(rna.locator("td").last).to_have_text("不適用此方法（略過）")
    expect(page.locator("#istd-preview")).not_to_contain_text("ND")
    page.locator("#u-preset").select_option("dna_dr")
    expect(page.locator("#istd-summary")).to_contain_text("1 個適用內標")


def test_delayed_review_payload_cannot_replace_newer_sample_selection(
    workbench, tmp_path
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "delayed-view.xlsx")
    delayed = []

    def delay_first_view(route):
        if not delayed:
            delayed.append(route)
        else:
            route.continue_()

    page.route("**/api/views/*", delay_first_view)
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    # Payload loading must not prevent a newer query from becoming authoritative.
    expect(page.locator("#target-sample")).to_be_enabled()
    page.wait_for_function(
        "() => !document.querySelector('#recorded-review').children.length"
    )
    assert delayed
    page.locator("#target-sample").select_option("S1")
    saved = page.locator("#recorded-review")
    rows = saved.locator(".tgt-table tbody tr:not([hidden])")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("S1")
    current_view = saved.get_attribute("data-view-id")
    old_view = delayed[0].request.url.rsplit("/", 1)[-1]
    assert current_view and old_view != current_view
    with page.expect_response(lambda response: response.url.endswith(old_view)):
        delayed[0].fulfill(response=delayed[0].fetch())
    # Let the response consumer run before checking its obsolete data never mounts.
    page.evaluate("""() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
    expect(saved).to_have_attribute("data-view-id", current_view)
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("S1")


def test_downloaded_curve_report_keeps_snapshot_and_interacts_without_network(
    workbench, review_page, tmp_path, monkeypatch
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "offline-raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S3": 1000, "S4": 300})
    workbook = write_rare_detection_workbook(tmp_path / "offline-saved.xlsx")
    original = workbook.read_bytes()
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    _configure_curve_sources(page, raw_dir, tmp_path)
    page.locator("#recorded-review").get_by_role(
        "button", name="看 S3 的 Analyte XIC", exact=True
    ).click()
    mounted = _wait_for_curve_report(page)
    expect(mounted.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    expect(mounted.locator(".tx-chart-meta").first).to_contain_text("面積 5.55e+1")
    with page.expect_download() as pending:
        page.locator("#xic-download").click()
    downloaded = tmp_path / "downloaded-s3.html"
    pending.value.save_as(downloaded)
    page.locator("#xic-next").click()
    _wait_for_curve_report(page)
    expect(mounted.locator(".tx-sample-name")).to_have_text("S4")
    expect(mounted.locator(".tx-chart-meta").first).to_contain_text("面積 ND")

    offline = review_page
    network_requests = []
    offline.on(
        "request",
        lambda request: (
            network_requests.append(request.url)
            if request.url.startswith(("http://", "https://"))
            else None
        ),
    )
    offline.context.route(re.compile(r"^https?://"), lambda route: route.abort())
    offline.context.set_offline(True)
    offline.goto(downloaded.as_uri())
    report = offline.locator("#inspector-root")
    expect(report.locator(".tx-sample-name")).to_have_text("S3")
    expect(report.get_by_label("樣本", exact=True)).to_have_value("S3")
    expect(report.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    # The fixture has no saved bounds: preserve unavailable RT and saved Areas,
    # rather than inventing either from the newly read RAW intensities.
    expect(report.locator(".tx-chart-meta").nth(0)).to_contain_text(
        "保存 RT — · 面積 5.55e+1"
    )
    expect(report.locator(".tx-chart-meta").nth(1)).to_contain_text(
        "保存 RT — · 面積 1.00e+3"
    )
    lines = report.locator('.tx-chart polyline[data-kind="smoothed"]')
    expect(lines).to_have_count(2)
    initial_points = lines.first.get_attribute("points")
    report.get_by_label("顯示 RT 起點", exact=True).fill("8.95")
    report.get_by_label("顯示 RT 終點", exact=True).fill("9.05")
    report.get_by_role("button", name="套用範圍", exact=True).click()
    assert lines.first.get_attribute("points") != initial_points
    report.get_by_role("button", name="完整 RT", exact=True).click()
    expect(report.get_by_label("顯示 RT 起點", exact=True)).to_have_value("0")
    expect(report.get_by_label("顯示 RT 終點", exact=True)).to_have_value("12")
    assert not network_requests
    assert workbook.read_bytes() == original


@pytest.fixture
def role_review(workbench, tmp_path):
    from openpyxl import load_workbook

    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "role-groups.xlsx")
    book = load_workbook(workbook)
    targets, results = book["Targets"], book["XIC Results"]
    targets.delete_rows(2, targets.max_row - 1)
    for row in (
        ["Alpha", "Analyte", "Gamma", 258.1085, 8, 10],
        ["Beta", "Analyte", "Gamma", 300, 8, 10],
        ["Gamma", "ISTD", None, 261.127, 8, 10],
    ):
        targets.append(row)
    records = list(results.values)[1:]
    results.delete_rows(2, results.max_row - 1)
    for analyte, standard in zip(records[::2], records[1::2], strict=True):
        # Neutral names ensure groups come from Targets.Role, not label inference.
        results.append([analyte[0], "Alpha", *analyte[2:]])
        results.append([standard[0], "Beta", *standard[2:]])
        results.append([standard[0], "Gamma", *standard[2:]])
    book.save(workbook)
    book.close()
    original = workbook.read_bytes()
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Alpha").click()
    yield page
    assert workbook.read_bytes() == original


def test_targeted_role_change_preserves_other_filter_inputs(role_review):
    page = role_review
    role = page.locator("#target-role")
    expect(role).to_be_visible()
    expect(role.locator("option")).to_have_text(
        ["全部類別", "Analyte（分析物）", "ISTD（內標）"]
    )
    names = page.locator("#target-id option")
    titles = page.locator("#item-list .item-top strong")
    role.select_option("Analyte")
    expect(page.locator("#target-id")).to_have_value("")
    expect(names).to_have_text(["全部目標", "Alpha", "Beta"])
    expect(titles).to_have_text(["Alpha", "Beta"])
    page.locator("#target-sample").select_option("S3")
    page.locator("#review-range-filters summary").click()
    for field_id, value in (
        ("mz-min", "250"),
        ("mz-max", "400"),
        ("rt-min", "8"),
        ("rt-max", "10"),
    ):
        page.locator(f"#{field_id}").fill(value)
    page.locator("#target-id").select_option("Alpha")
    page.locator("#target-attention").check()
    expect(titles).to_have_text(["Alpha"])
    expect(names).to_have_text(["全部目標", "Alpha", "Beta"])
    with page.expect_request("**/api/query") as grouped:
        role.select_option("ISTD")
    expected_filters = {
        "target_role": "ISTD",
        "target_id": "",
        "sample_id": "S3",
        "attention_only": True,
        "mz_min": 250,
        "mz_max": 400,
        "rt_min": 8,
        "rt_max": 10,
    }
    assert {
        key: grouped.value.post_data_json[key] for key in expected_filters
    } == expected_filters
    expect(page.locator("#target-id")).to_have_value("")
    expect(names).to_have_text(["全部目標", "Gamma"])
    empty = page.locator("#selection-empty")
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    expect(empty).to_contain_text("ISTD")
    expect(page.locator("#target-sample")).to_have_value("S3")
    expect(page.locator("#target-attention")).to_be_checked()
    page.locator("#target-attention").uncheck()
    expect(empty).to_be_hidden()
    expect(titles).to_have_text(["Gamma"])
    expect(page.locator("#item-title")).to_contain_text("Gamma")
    expect(
        page.locator("#recorded-review .tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    page.locator("#target-id").select_option("Gamma")
    role.select_option("Analyte")
    expect(page.locator("#target-id")).to_have_value("")
    expect(page.locator("#target-sample")).to_have_value("S3")
    expect(titles).to_have_text(["Alpha", "Beta"])


def test_targeted_role_history_and_reset_restore_available_targets(role_review):
    page = role_review
    role = page.locator("#target-role")
    names = page.locator("#target-id option")
    titles = page.locator("#item-list .item-top strong")
    role.select_option("Analyte")
    expect(names).to_have_text(["全部目標", "Alpha", "Beta"])
    page.locator("#target-id").select_option("Beta")
    expect(page.locator("#item-title")).to_contain_text("Beta")
    _view(page, "untargeted")
    _open_results(page)
    role.select_option("ISTD")
    expect(titles).to_have_text(["Gamma"])
    page.go_back()
    expect(page.locator("#workspace-untargeted")).to_be_visible()
    page.go_back()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(role).to_have_value("Analyte")
    expect(names).to_have_text(["全部目標", "Alpha", "Beta"])
    expect(page.locator("#target-id")).to_have_value("Beta")
    expect(page.locator("#item-title")).to_contain_text("Beta")
    page.go_forward()
    expect(page.locator("#workspace-untargeted")).to_be_visible()
    page.go_forward()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(role).to_have_value("ISTD")
    expect(names).to_have_text(["全部目標", "Gamma"])
    expect(page.locator("#target-id")).to_have_value("")
    expect(titles).to_have_text(["Gamma"])
    page.locator("#reset-query").click()
    expect(role).to_have_value("")
    expect(names).to_have_text(["全部目標", "Alpha", "Beta", "Gamma"])
    expect(titles).to_have_text(["Alpha", "Beta", "Gamma"])
    expect(page.locator("#target-sample")).to_have_value("")
    expect(page.locator("#target-attention")).not_to_be_checked()
    role.select_option("ISTD")
    expect(names).to_have_text(["全部目標", "Gamma"])
    page.locator("#back-to-overview").click()
    page.locator("#targeted-overview td button", has_text="Alpha").click()
    expect(role).to_have_value("")
    expect(names).to_have_text(["全部目標", "Alpha", "Beta", "Gamma"])
    expect(page.locator("#target-id")).to_have_value("Alpha")
    expect(page.locator("#item-title")).to_contain_text("Alpha")


def test_targeted_zero_results_explain_applied_filters_and_restore_one_condition(
    workbench,
    tmp_path,
    browser_errors,
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "zero.xlsx")
    original = workbook.read_bytes()
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    page.locator("#target-attention").check()
    page.locator("#target-sample").select_option("S3")
    page.locator("#target-id").select_option("ISTD")
    empty = page.locator("#selection-empty")
    expect(empty).to_be_visible()
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    for condition in ("目標：ISTD", "樣本：S3", "優先檢閱"):
        expect(empty).to_contain_text(condition)
    expect(page.locator("#page-label")).to_have_text("0 筆")
    expect(page.locator("#selection")).to_be_hidden()
    _view(page, "untargeted")
    page.go_back()
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    expect(page.locator("#target-id")).to_have_value("ISTD")
    expect(page.locator("#target-attention")).to_be_checked()

    pending = []
    page.route("**/api/query", lambda route: pending.append(route))
    with page.expect_request("**/api/query"):
        page.locator("#target-id").select_option("Analyte")
    expect(empty.locator("h2")).to_have_text("正在查詢結果…")
    pending.pop().fulfill(status=500, json={"error": "查詢測試失敗"})
    expect(empty.locator("h2")).to_have_text("查詢未完成")
    expect(empty).to_contain_text("查詢測試失敗")
    expect(page.locator("#status")).to_contain_text("查詢測試失敗")
    browser_errors.remove(
        "Failed to load resource: the server responded with a status of "
        "500 (Internal Server Error)"
    )
    page.unroute("**/api/query")
    page.locator("#target-id").select_option("ISTD")
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    page.locator("#target-attention").uncheck()
    expect(empty).to_be_hidden()
    expect(page.locator("#item-title")).to_contain_text("ISTD")
    expect(page.locator("#target-id")).to_have_value("ISTD")
    expect(page.locator("#target-sample")).to_have_value("S3")
    expect(
        page.locator("#recorded-review .tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    assert workbook.read_bytes() == original


@pytest.mark.parametrize("mode", ["nearest", "exact"])
def test_untargeted_zero_results_keep_search_mode_and_remaining_bounds(
    workbench,
    tmp_path,
    mode,
):
    from tests.inspector_fixtures import create_nearest_review_batch

    page = workbench
    report, matrix = create_nearest_review_batch(tmp_path)
    original = [path.read_bytes() for path in (report, matrix)]
    _open_results(page)
    page.locator("#source").fill(str(report))
    expect(page.locator("#raw-dir")).to_have_value("")
    page.locator("#open-form button[type=submit]").click()
    page.get_by_role("button", name="瀏覽矩陣群組").click()
    page.locator("#untargeted-search-mode").select_option(mode)
    if mode == "nearest":
        page.locator("#nearest-mz").fill("123.4")
    else:
        page.locator("#mz-min").fill("123.4")
        page.locator("#mz-max").fill("123.4")
    page.locator("#rt-min").fill("20")
    page.locator("#query-form button[type=submit]").click()
    empty = page.locator("#selection-empty")
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    for condition in ("矩陣群組", "123.4", "RT ≥ 20"):
        expect(empty).to_contain_text(condition)
    expect(page.locator("#page-label")).to_have_text("0 筆")
    if mode == "exact":
        page.locator("#review-range-filters summary").click()
    page.locator("#rt-min").fill("")
    page.locator("#query-form button[type=submit]").click()
    expect(empty).to_be_hidden()
    expect(page.locator("#untargeted-search-mode")).to_have_value(mode)
    titles = page.locator("#item-list .item-top strong")
    if mode == "nearest":
        expect(page.locator("#nearest-mz")).to_have_value("123.4")
        expect(titles).to_have_count(25)
        expect(titles.nth(0)).to_have_text("m/z 123.3980")
        expect(titles.nth(1)).to_have_text("m/z 123.4200")
        expect(page.locator("#list-summary")).to_contain_text("32")
    else:
        expect(page.locator("#mz-min")).to_have_value("123.4")
        expect(page.locator("#mz-max")).to_have_value("123.4")
        expect(titles).to_have_text(["m/z 350.0000"])
    assert [path.read_bytes() for path in (report, matrix)] == original


def test_zero_result_history_discards_pending_query_feedback(
    workbench, tmp_path, browser_errors
):
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "zero-history.xlsx")
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    page.locator("#target-attention").check()
    page.locator("#target-id").select_option("ISTD")
    empty = page.locator("#selection-empty")
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    pending = []
    page.route("**/api/query", lambda route: pending.append(route))
    with page.expect_request("**/api/query"):
        page.locator("#target-id").select_option("Analyte")
    expect(empty.locator("h2")).to_have_text("正在查詢結果…")
    _view(page, "untargeted")
    page.go_back()
    expected_http_error = (
        "Failed to load resource: the server responded with a status of "
        "500 (Internal Server Error)"
    )
    with page.expect_console_message(
        lambda message: message.text == expected_http_error
    ):
        pending.pop().fulfill(status=500, json={"error": "Discarded query failure"})
    browser_errors.remove(expected_http_error)
    expect(empty.locator("h2")).to_have_text("沒有符合的結果")
    expect(empty).to_contain_text("目標：ISTD")
    expect(empty).to_contain_text("優先檢閱")
    expect(page.locator("#status")).not_to_contain_text("Discarded query failure")


def _placeholder_batch(raw_dir: Path, stems: tuple[str, ...]) -> None:
    raw_dir.mkdir(exist_ok=True)
    for minute, stem in enumerate(stems):
        path = raw_dir / f"{stem}.raw"
        path.write_bytes(b"")
        os.utime(path, (1_767_000_000 + minute * 60,) * 2)


def test_sample_save_preserves_editor_selection_and_focus(
    workbench, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    batch = tmp_path / "raw"
    _placeholder_batch(batch, ("Tumor_A", "Normal_A"))
    rows = read_batch_samples(config_dir, batch, dll_dir=tmp_path)["samples"]
    save_batch_samples(config_dir, batch, rows)
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("2 個樣本")
    page.locator("#t-samples-edit").click()
    pending = []
    page.route("**/api/samples/save", lambda route: pending.append(route))
    page.get_by_label("Tumor_A 角色", exact=True).select_option("pooled_qc")
    selected = page.get_by_label("選取 Normal_A", exact=True)
    selected.check()
    other_role = page.get_by_label("Normal_A 角色", exact=True)
    other_role.focus()
    assert len(pending) == 1
    with page.expect_response(lambda r: r.url.endswith("/api/samples/save")):
        pending.pop().continue_()
    expect(page.locator("#t-samples-source")).to_contain_text("沿用已確認的角色與排除")
    expect(selected).to_be_checked()
    expect(other_role).to_be_focused()
    page.unroute("**/api/samples/save")


def test_an_open_sample_editor_follows_another_tabs_job(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    batch = tmp_path / "raw"
    _placeholder_batch(batch, ("Tumor_A", "Normal_A"))
    rows = read_batch_samples(config_dir, batch, dll_dir=tmp_path)["samples"]
    save_batch_samples(config_dir, batch, rows)
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("2 個樣本")
    page.locator("#t-samples-edit").click()
    role = page.get_by_label("Tumor_A 角色", exact=True)
    excluded = page.get_by_label("Normal_A 排除", exact=True)
    other = page.context.new_page()
    try:
        other.goto(page.url)
        expect(other.locator("body")).not_to_have_class("booting")
        _start(other, "targeted")
        expect(role).to_be_disabled()
        expect(excluded).to_be_disabled()
        expect(page.locator("#sample-apply-role")).to_be_disabled()
        fake_runs.release.set()
        expect(role).to_be_enabled()
        expect(excluded).to_be_enabled()
        expect(role).to_have_value("study_sample")
        expect(excluded).not_to_be_checked()
    finally:
        fake_runs.release.set()
        other.close()


def test_sample_edits_save_in_order_and_persist_the_latest_role(
    workbench, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    batch = tmp_path / "raw"
    _placeholder_batch(batch, ("Tumor_A", "Normal_A"))
    rows = read_batch_samples(config_dir, batch, dll_dir=tmp_path)["samples"]
    save_batch_samples(config_dir, batch, rows)
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("2 個樣本")
    page.locator("#t-samples-edit").click()
    pending = []

    def hold_save(route):
        pending.append(route)
        page.evaluate("window.heldSampleSaves = (window.heldSampleSaves || 0) + 1")

    page.route("**/api/samples/save", hold_save)
    try:
        role = page.get_by_label("Tumor_A 角色", exact=True)
        with page.expect_request("**/api/samples/save"):
            role.select_option("qc")
        # A completed status round-trip lets the second change's fetch reach
        # the route if it was dispatched while the first write is still held.
        with page.expect_response("**/api/analysis") as observed:
            role.select_option("blank")
        observed.value.finished()
        assert len(pending) == 1, "Overlapping saves can publish an older edit last"
        with page.expect_request("**/api/samples/save"):
            pending.pop(0).continue_()
        page.wait_for_function("window.heldSampleSaves === 2")
        with page.expect_response("**/api/samples/save") as saved:
            pending.pop(0).continue_()
        saved.value.finished()
        persisted = read_batch_samples(config_dir, batch, dll_dir=tmp_path)["samples"]
        assert {row["raw_stem"]: row["sample_role"] for row in persisted} == {
            "Tumor_A": "blank",
            "Normal_A": "study_sample",
        }
        expect(role).to_have_value("blank")
    finally:
        for route in pending:
            route.continue_()
        page.unroute("**/api/samples/save")


def test_both_flows_share_roles_and_exclusions_without_statistical_groups(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    batch = tmp_path / "raw"
    _placeholder_batch(batch, ("TumorBC1_DNA", "TumorBC2_DNA", "NormalBC1_DNA", "QC_1"))

    # Targeted reads, edits and confirms the folder's sample metadata.
    rows = read_batch_samples(config_dir, batch, dll_dir=tmp_path)["samples"]
    save_batch_samples(config_dir, batch, rows)
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("4 個樣本")
    checklist = page.locator("#target-checklist")
    expect(checklist).not_to_contain_text("組別")
    expect(page.locator("#t-role-summary")).to_have_text("研究樣本 3QC 1")
    expect(page.locator("#t-role-summary")).not_to_contain_text("Other")
    expect(page.locator('[data-part="group-cards"]')).to_have_count(0)
    expect(
        page.locator('[data-part="samples-table"], [data-part="samples-carry"]')
    ).to_have_count(0)
    page.locator("#t-samples-edit").click()
    editor = page.locator("#sample-editor")
    expect(editor.get_by_role("columnheader", name="組別", exact=True)).to_have_count(0)
    expect(page.locator("#sample-bulk-group")).to_have_count(0)
    with page.expect_response(lambda r: r.url.endswith("/api/samples/save")):
        editor.get_by_label("NormalBC1_DNA 角色", exact=True).select_option("blank")
    expect(
        editor.get_by_role("columnheader", name="排除原因", exact=True)
    ).to_have_count(0)
    expect(page.locator("#sample-bulk-reason")).to_have_count(0)
    # Selecting samples does not exclude them; the exclusion checkbox saves directly.
    page.locator("#sample-select-all").check()
    expect(editor.locator("#sample-rows input[data-sample]:checked")).to_have_count(4)
    expect(editor.get_by_label("TumorBC2_DNA 排除", exact=True)).not_to_be_checked()
    page.locator("#sample-select-all").uncheck()
    with page.expect_response(lambda r: r.url.endswith("/api/samples/save")):
        editor.get_by_label("TumorBC2_DNA 排除", exact=True).check()
    page.keyboard.press("Escape")
    expect(page.locator("#t-role-summary")).to_contain_text("Blank 1")
    expect(checklist).to_contain_text("排除的樣本1 個：TumorBC2_DNA")
    expect(checklist).not_to_contain_text("未標組別的研究樣本")
    expect(page.locator("#t-role-summary")).to_contain_text("排除 1")
    saved = {
        row.raw_stem: row
        for row in load_sample_metadata(sample_annotations_path(config_dir, batch))
    }
    assert (saved["NormalBC1_DNA"].sample_role, saved["TumorBC2_DNA"].excluded) == (
        "blank",
        True,
    )

    # Untargeted on the same folder reads the same confirmed metadata, even
    # with a different 上機資訊 path entered.
    _view(page, "untargeted")
    page.locator("#u-raw_dir").fill(str(batch))
    page.locator("#u-raw_dir").press("Tab")
    _expand_section(page, "#sample-tools")
    page.locator("#sequence-source").fill(str(tmp_path / "unrelated.docx"))
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-count")).to_have_text("4 個樣本")
    expect(page.locator("#samples-source")).to_contain_text("沿用已確認的角色與排除")
    page.locator("#samples-edit").click()
    expect(editor.get_by_label("TumorBC2_DNA 排除", exact=True)).to_be_checked()
    expect(editor.get_by_label("NormalBC1_DNA 角色", exact=True)).to_have_value("blank")
    page.keyboard.press("Escape")
    expect(page.locator("#untargeted-checklist")).to_contain_text(
        "排除的樣本1 個：TumorBC2_DNA"
    )

    # The Targeted run reads the confirmed file for this run only.
    _view(page, "targeted")
    _start(page, "targeted")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    assert fake_runs.sample_annotations[-1] == sample_annotations_path(
        config_dir, batch
    )
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")
    assert read_settings(config_dir=config_dir)["injection_order_source"] == ""


def _grouped_workbook(path: Path, groups: dict[str, str]) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    targets = book.active
    targets.title = "Targets"
    targets.append(["Label", "Role", "ISTD Pair", "m/z", "RT min", "RT max"])
    targets.append(["Analyte", "Analyte", None, 258.1085, 8, 10])
    results = book.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Group",
            "Target",
            "RT",
            "Area",
            "PeakStart",
            "PeakEnd",
            "Review",
            "Review Reason",
            "Reason",
            "Counted Detection",
        ]
    )
    for sample, group in groups.items():
        results.append(
            [sample, group, "Analyte", 9.0, 10.0, 8.9, 9.1, None, None, None, "TRUE"]
        )
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    book.save(path)
    book.close()
    return path


def test_legacy_groups_stay_in_inspector_but_not_analysis_setup(
    workbench, config_dir, tmp_path, monkeypatch
):
    from xic_workbench.samples import save_batch_samples

    page = workbench
    install_fake_raw_headers(monkeypatch)
    groups = {
        "TumorBC101_DNA": "Tumor",
        "NormalBC101_DNA": "Normal",
        "TumorBC102_DNA": "Tumor",
    }
    batch = tmp_path / "raw"
    _placeholder_batch(batch, tuple(groups))
    save_batch_samples(
        config_dir,
        batch,
        [
            asdict(SampleMetadata(stem, stem, sample_role="study_sample", group=group))
            for stem, group in groups.items()
        ],
    )
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("3 個樣本")

    def colour(locator):
        return locator.evaluate("node => getComputedStyle(node).backgroundColor")

    expect(page.locator("#t-role-summary .group-chip")).to_have_count(0)
    expect(page.locator("#t-role-summary")).to_have_text("研究樣本 3")
    page.locator("#t-samples-edit").click()
    expect(
        page.locator('#sample-editor input[list="sample-group-options"]')
    ).to_have_count(0)
    page.keyboard.press("Escape")
    assert {
        row.raw_stem: row.group
        for row in load_sample_metadata(sample_annotations_path(config_dir, batch))
    } == groups
    workbook = _grouped_workbook(tmp_path / "saved.xlsx", groups)
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    review = page.locator("#recorded-review")
    palettes = []
    for change_theme in (False, True):
        if change_theme:
            initial = page.locator("html").get_attribute("data-theme")
            page.locator("#theme-toggle").click()
            expect(page.locator("html")).to_have_attribute(
                "data-theme", "light" if initial == "dark" else "dark"
            )
        palette = {}
        for sample, group in groups.items():
            cell = review.locator("tr", has_text=sample)
            expected = colour(cell.locator(".sample-dot"))
            expect(cell.locator(".sample-group")).to_have_text(group)
            expect(cell.locator(".sample-dot")).to_be_visible()
            expect(cell.locator(".sample-dot")).to_have_css(
                "background-color", expected
            )
            assert expected != "rgba(0, 0, 0, 0)"
            palette[group] = expected
        assert palette["Tumor"] != palette["Normal"]
        palettes.append(palette)
    assert palettes[0] != palettes[1]


def test_review_marks_an_exclusion_for_the_runs_folder(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    run_folder = tmp_path / "raw"
    stems = ("TumorBC101_DNA", "TumorBC102_DNA", "NormalBC101_DNA")
    _placeholder_batch(run_folder, stems)
    # The run reads its 上機資訊 directly; the folder is never confirmed.
    sequence = tmp_path / "run-sequence.csv"
    sequence.write_text(
        "File Name,Sample Type,Group\n"
        "TumorBC101_DNA,Unknown,Tumor\n"
        "NormalBC101_DNA,Unknown,Normal\n",
        encoding="utf-8",
    )
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-injection_order_source").fill(str(sequence))
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("3 個樣本")
    fake_runs.targeted_workbook = _grouped_workbook(
        tmp_path / "run.xlsx", dict.fromkeys(stems, "")
    )
    fake_runs.release.set()
    _start(page, "targeted")
    _wait_for_analysis_status(page, "已完成")

    # The next batch is another folder, saved as the current settings; the
    # exclusion still goes to the run's folder with the run's annotations.
    page.locator("#result-back").click()
    page.locator("#t-data_dir").fill(str(tmp_path / "next-batch"))
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-injection_order_source").fill("")
    with page.expect_response(lambda r: r.url.endswith("/api/targeted/save")):
        page.locator("#target-save").click()
    page.get_by_role("button", name="檢閱結果", exact=True).click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    review = page.locator("#recorded-review")
    # Pointing the editable RAW field elsewhere, e.g. to load curves, does
    # not move the exclusion away from the run's folder.
    _expand_section(page, "#curve-settings")
    page.locator("#raw-dir").fill(str(tmp_path / "curves-copy"))
    review.get_by_role("button", name="標記排除 TumorBC102_DNA", exact=True).click()
    dialog = page.locator("#exclude-dialog")
    expect(dialog).to_contain_text("重新執行後才會套用")
    expect(page.locator("#exclude-reason")).to_have_count(0)
    with page.expect_response(lambda r: r.url.endswith("/api/samples/exclude")):
        dialog.get_by_role("button", name="標記排除", exact=True).click()
    expect(page.locator("#toasts")).to_contain_text(
        "已標記排除 TumorBC102_DNA。目前結果不變；重新執行後才會套用。"
    )
    saved = {
        row.raw_stem: row
        for row in load_sample_metadata(sample_annotations_path(config_dir, run_folder))
    }
    assert (
        saved["TumorBC102_DNA"].excluded,
        saved["TumorBC102_DNA"].exclusion_reason,
    ) == (
        True,
        "",
    )
    assert (saved["TumorBC101_DNA"].group, saved["NormalBC101_DNA"].group) == (
        "Tumor",
        "Normal",
    )

    assert not sample_annotations_path(config_dir, tmp_path / "next-batch").exists()
    assert not sample_annotations_path(config_dir, tmp_path / "curves-copy").exists()

    # The sample step on that folder shows it, and its export carries it.
    _view(page, "targeted")
    page.locator("#t-data_dir").fill(str(run_folder))
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-refresh").click()
    expect(page.locator("#t-samples-count")).to_have_text("3 個樣本")
    expect(page.locator("#t-role-summary")).to_contain_text("排除 1")
    expect(page.locator("#target-checklist")).to_contain_text(
        "排除的樣本1 個：TumorBC102_DNA"
    )
    exported = tmp_path / "exported.tsv"
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-export").click()
    page.locator("#path-dialog-value").fill(str(exported))
    page.locator("#path-dialog").get_by_role("button", name="確定").click()
    expect(page.locator("#toasts")).to_contain_text("樣本資料已匯出")
    exported_rows = {row.raw_stem: row for row in load_sample_metadata(exported)}
    assert exported_rows["TumorBC102_DNA"].excluded is True

    # With that folder loaded, a later exclusion updates the step at once.
    _open_results(page)
    review.get_by_role("button", name="標記排除 NormalBC101_DNA", exact=True).click()
    with page.expect_response(lambda r: r.url.endswith("/api/samples/exclude")):
        dialog.get_by_role("button", name="標記排除", exact=True).click()
    _view(page, "targeted")
    expect(page.locator("#t-role-summary")).to_contain_text("排除 2")


def test_a_result_opened_without_its_run_offers_no_exclusion(workbench, tmp_path):
    page = workbench
    workbook = _grouped_workbook(tmp_path / "manual.xlsx", {"S1": "", "S2": ""})
    _open_results(page)
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form").evaluate("form => form.requestSubmit()")
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    review = page.locator("#recorded-review")
    expect(review.locator(".tgt-sample")).to_have_count(2)
    # No run says which RAW folder owns these samples.
    expect(review.locator(".tgt-exclude")).to_have_count(0)


def test_an_import_reaches_the_other_flow_and_its_run(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch
):
    page = workbench
    install_fake_raw_headers(monkeypatch)
    batch = tmp_path / "raw"
    _placeholder_batch(batch, ("S1", "S2"))
    _view(page, "untargeted")
    page.locator("#u-raw_dir").fill(str(batch))
    page.locator("#u-raw_dir").press("Tab")
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-count")).to_have_text("2 個樣本")

    # Targeted imports 上機資訊 that excludes S1 on the same folder.
    listed = tmp_path / "listed.tsv"
    listed.write_text(
        "schema_version\tsample_name\traw_stem\tinjection_order\tsample_role\t"
        "batch_id\tprep_batch_id\tmatrix_type\tgroup\texcluded\texclusion_reason\n"
        "sample_metadata\tS1\tS1\t\tstudy_sample\t\t\t\tNew\ttrue\tclot\n"
        "sample_metadata\tS2\tS2\t\tstudy_sample\t\t\t\tNew\tfalse\t\n",
        encoding="utf-8",
    )
    _view(page, "targeted")
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-injection_order_source").fill(str(listed))
    _expand_section(page, "#t-sample-tools")
    page.locator("#t-samples-import").click()
    expect(page.locator("#t-role-summary")).to_contain_text("排除 1")

    _view(page, "untargeted")
    expect(page.locator("#samples-count")).to_have_text("2 個樣本")
    expect(page.locator("#role-summary")).to_contain_text("排除 1")
    expect(page.locator("#role-summary")).to_contain_text("研究樣本 1")
    _start(page, "untargeted")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    rows = {row.raw_stem: row for row in fake_runs.requests[-1].sample_metadata}
    assert (rows["S1"].excluded, rows["S1"].group) == (True, "New")
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")


def _open_curation_folder(page, tmp_path, fake_runs):
    from tests.matrix_curation_fixtures import alignment_output, sample_rows
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
    from xic_extractor.tabular_io import write_tsv

    output = alignment_output(tmp_path / "results")
    write_tsv(output / "sample_metadata.tsv", sample_rows(), SAMPLE_METADATA_COLUMNS)
    fake_runs.result = {
        "matrix_tsv": str(output / "alignment_matrix.tsv"),
        "alignment_outputs": {"review_tsv": str(output / "alignment_review.tsv")},
    }
    _view(page, "untargeted")
    page.locator("#u-output_dir").fill(str(output))
    _start(page, "untargeted")
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")
    page.locator("#untargeted-result").get_by_role("button", name="整理矩陣").click()
    threshold = page.locator("#curation-finite_fraction-min_finite_fraction")
    expect(threshold).to_be_enabled()
    threshold.fill("0.6")
    expect(page.locator("#curation-export")).to_be_enabled()
    return output


def test_editing_curation_source_invalidates_steps_before_blur(
    workbench, tmp_path, fake_runs
):
    from tests.matrix_curation_fixtures import alignment_output

    page = workbench
    _open_curation_folder(page, tmp_path, fake_runs)
    another = alignment_output(tmp_path / "another-output")
    page.locator("#curation-input-tools > summary").click()
    pending = []
    page.route("**/api/matrix-curation/source", lambda route: pending.append(route))
    try:
        source = page.locator("#curation-source_dir")
        source.fill(str(another))
        expect(source).to_be_focused()
        expect(page.locator("#curation-export")).to_be_disabled()
        expect(page.locator("#curation-steps [data-step]")).to_have_count(0)
    finally:
        for route in pending:
            route.continue_()
        page.unroute("**/api/matrix-curation/source")


def test_curation_does_not_export_changed_settings_while_preferences_are_saving(
    workbench, tmp_path, fake_runs
):
    page = workbench
    output = _open_curation_folder(page, tmp_path, fake_runs)
    pending = []

    def hold_preferences(route):
        pending.append(route)
        page.evaluate("window.curationPreferencesHeld = true")

    page.route("**/api/preferences", hold_preferences)
    try:
        page.locator("#curation-export").click()
        page.wait_for_function("window.curationPreferencesHeld === true")
        page.locator("#curation-finite_fraction-min_finite_fraction").fill("0.8")
        with page.expect_response("**/api/preferences") as saved:
            pending.pop().continue_()
        saved.value.finished()
        expect(page.locator("#toasts")).to_contain_text("設定已變更")
        assert not list(output.glob("matrix_curation_*"))
    finally:
        for route in pending:
            route.continue_()
        page.unroute("**/api/preferences")


def test_matrix_curation_from_a_run_and_from_an_existing_folder(
    workbench, fake_runs, config_dir, tmp_path, monkeypatch
):
    from tests.matrix_curation_fixtures import (
        alignment_output,
        sample_rows,
    )
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
    from xic_extractor.tabular_io import read_tsv_with_header, write_tsv

    page = workbench
    raw_dir = tmp_path / "run-raw"
    run_output = alignment_output(tmp_path / "results", raw_dir=raw_dir)
    write_tsv(
        run_output / "sample_metadata.tsv", sample_rows(), SAMPLE_METADATA_COLUMNS
    )
    fake_runs.result = {
        "matrix_tsv": str(run_output / "alignment_matrix.tsv"),
        "alignment_outputs": {"review_tsv": str(run_output / "alignment_review.tsv")},
    }
    _view(page, "untargeted")
    page.locator("#u-raw_dir").fill(str(tmp_path / "raw"))
    page.locator("#u-output_dir").fill(str(run_output))
    _start(page, "untargeted")
    fake_runs.release.set()
    _wait_for_analysis_status(page, "已完成")

    # After a run only the threshold is needed: folder and samples come along.
    page.locator("#untargeted-result").get_by_role("button", name="整理矩陣").click()
    expect(page.locator("#result-curation")).to_be_visible()
    expect(page.locator("#curation-source_dir")).to_have_value(str(run_output))
    expect(page.locator("#curation-input-tools")).not_to_have_attribute("open", "")
    expect(page.locator("#curation-sample_metadata")).to_be_hidden()
    expect(page.locator("#curation-source")).to_contain_text(
        "已沿用這次分析保存的樣本資訊"
    )
    expect(page.locator("#curation-steps .curation-step").first).to_contain_text(
        "同化合物收斂"
    )
    step = page.locator("#curation-step-finite_fraction")
    expect(step).to_be_enabled()
    expect(step).to_be_checked()
    # This folder has no per-cell explanation: the step says why it cannot run.
    expect(page.locator("#curation-step-same_compound")).to_be_disabled()
    expect(page.locator(".curation-step[data-step=same_compound]")).to_contain_text(
        "alignment_explanation.sqlite"
    )
    expect(page.locator("#curation-source")).to_contain_text("內標清單")
    from xic_workbench import matrix_curation

    preview = matrix_curation.preview_curation
    released = threading.Event()
    entered = threading.Event()

    def slow_preview(*args, **kwargs):
        entered.set()
        assert released.wait(HANDSHAKE_TIMEOUT_S)
        return preview(*args, **kwargs)

    monkeypatch.setattr(matrix_curation, "preview_curation", slow_preview)
    # The preview follows the threshold without a separate refresh action.
    page.locator("#curation-finite_fraction-min_finite_fraction").fill("0.6")
    try:
        assert entered.wait(HANDSHAKE_TIMEOUT_S)
        expect(page.locator("#curation-preview")).to_contain_text("正在計算")
        # A pending preview must not hold the server's mutable-workspace lock.
        with page.expect_response("**/api/preferences"):
            page.locator("#theme-toggle").click()
        expect(
            page.locator("#curation-finite_fraction-min_finite_fraction")
        ).to_be_enabled()
    finally:
        released.set()
    expect(page.locator("#curation-preview")).to_contain_text("保留 2 列、移除 2 列")
    expect(page.locator("#curation-preview")).to_contain_text("至少 2/3 個研究樣本有值")
    expect(page.locator("#curation-preview")).to_contain_text("本次沒有 ISTD 保護")
    page.locator("button[form=curation-form][type=submit]").click()
    _wait_for_analysis_status(page, "已完成")
    # Curation runs from the result page, so its job feedback is shown there.
    expect(page.locator("#analysis-result #analysis-job")).to_be_visible()
    result = page.locator("#curation-result")
    expect(result).to_contain_text("2保留 feature")
    expect(result).to_contain_text("4原始 feature")
    expect(result.get_by_role("link", name="下載整理後矩陣")).to_be_visible()
    (curated,) = run_output.glob("matrix_curation_*")
    _, kept = read_tsv_with_header(curated / "analysis_matrix.tsv")
    assert [row["Mz"] for row in kept] == ["101.0000", "103.0000"]

    # An existing folder without sample data names the reason; choosing a
    # sample file makes the step available, and the last threshold returns.
    page.reload()
    page.locator("#untargeted-result").get_by_role("button", name="整理矩陣").click()
    expect(page.locator("#result-curation")).to_be_visible()
    page.locator("#curation-input-tools > summary").click()
    existing = alignment_output(tmp_path / "existing")
    page.locator("#curation-source_dir").fill(str(existing))
    page.locator("#curation-source_dir").press("Tab")
    expect(step).to_be_disabled()
    expect(page.locator("#curation-steps")).to_contain_text("找不到本次分析的樣本資訊")
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    page.locator("#curation-sample_metadata").fill(str(samples))
    page.locator("#curation-sample_metadata").press("Tab")
    expect(step).to_be_enabled()
    expect(page.locator("#curation-finite_fraction-min_finite_fraction")).to_have_value(
        "0.6"
    )
    expect(page.locator("#curation-preview")).to_contain_text("保留 2 列、移除 2 列")
    expect(page.locator("#curation-preview")).to_contain_text("至少 2/3 個研究樣本有值")
    expect(page.locator("#curation-preview")).to_contain_text("本次沒有 ISTD 保護")
    page.locator("button[form=curation-form][type=submit]").click()
    _wait_for_analysis_status(page, "已完成")
    expect(result).to_contain_text(str(existing))
    assert len(list(existing.glob("matrix_curation_*"))) == 1


@pytest.mark.parametrize(
    "kind,field", [("targeted", "t-data_dir"), ("untargeted", "u-raw_dir")]
)
def test_path_errors_and_automatic_sample_summary(workbench, tmp_path, kind, field):
    page = workbench
    _view(page, kind)
    path = page.locator(f"#{field}")
    path.fill(str(tmp_path / "missing"))
    path.dispatch_event("change")
    # Users read what is wrong, not the operating system's exception text.
    expect(page.locator(f"#{field}-problem")).to_have_text("找不到這個資料夾")
    checklist = "#target-checklist" if kind == "targeted" else "#untargeted-checklist"
    expect(page.locator(checklist)).to_contain_text("找不到這個資料夾")
    expect(page.locator(checklist)).not_to_contain_text("Error")
    expect(path).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#t-parallel_workers")).to_have_count(0)
    expect(page.locator("#t-dll_dir")).not_to_be_visible()
    expect(page.locator("#u-dll_dir")).not_to_be_visible()
    raw = tmp_path / "sample-input"
    raw.mkdir()
    (raw / "sample.raw").touch()
    path.fill(str(raw))
    path.dispatch_event("change")
    prefix = "t-" if kind == "targeted" else ""
    expect(page.locator(f"#{prefix}samples-summary")).to_contain_text(
        "RAW 1", timeout=BACKGROUND_JOB_TIMEOUT_MS
    )
    expect(path).to_have_attribute("aria-invalid", "false")


@pytest.mark.parametrize("kind", ["targeted", "untargeted"])
@pytest.mark.parametrize("fresh", [True, False])
def test_results_stay_in_analysis_page_without_raw(
    workbench, fake_runs, tmp_path, kind, fresh
):
    from tests.inspector_fixtures import create_review_batch
    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    page = workbench
    if kind == "targeted":
        source = artifact = write_rare_detection_workbook(tmp_path / "saved.xlsx")
        fake_runs.targeted_workbook = source
    else:
        source, artifact = create_review_batch(tmp_path)
        fake_runs.result = {
            "evidence": {"report_path": str(source)},
            "matrix_tsv": str(artifact),
        }
    expect(page.locator(".topbar [data-view]")).to_have_count(2)
    _view(page, kind)
    if fresh:
        fake_runs.release.set()
        _start(page, kind)
        _wait_for_analysis_status(page, "已完成")
    else:
        page.locator("#open-result").click()
        page.locator("#source").fill(str(source))
        page.locator("#open-form button[type=submit]").click()
    # The result replaces the setup form instead of hanging under it.
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#targeted-overview")).to_be_visible()
    expect(page.locator("#analysis-root")).to_be_hidden()
    expect(page.locator("#batch-title")).to_be_focused()
    expect(page.locator("#targeted-overview")).to_be_in_viewport()
    expect(page.locator("#source-settings")).not_to_have_attribute("open", "")
    # The start shortcut belongs to setup; on a result it starts nothing.
    started = len(fake_runs.requests)
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(500)
    assert len(fake_runs.requests) == started
    if kind == "untargeted":
        expect(page.locator("#targeted-overview")).not_to_contain_text("HIGH")
        expect(page.locator("#result-curation")).to_be_visible()
        expect(page.locator("#result-curation")).not_to_have_attribute("open", "")
    else:
        expect(page.locator("#result-curation")).not_to_be_visible()
    before = page.locator("#targeted-overview").text_content()
    # Editing the next analysis happens on its setup; returning shows the
    # same saved result.
    page.locator("#result-back").click()
    expect(page.locator(f"#workspace-{kind}")).to_be_visible()
    expect(page.locator("#analysis-result")).to_be_hidden()
    field = "#t-output_dir" if kind == "targeted" else "#u-output_dir"
    page.locator(field).fill(str(tmp_path / "next-analysis"))
    page.go_back()
    expect(page.locator("#analysis-result")).to_be_visible()
    expect(page.locator("#targeted-overview")).to_have_text(before)
    with page.expect_download() as pending:
        page.locator("#result-download").click()
    assert Path(pending.value.path()).read_bytes() == artifact.read_bytes()


def test_sample_refresh_keeps_the_latest_action_when_sequence_blurs(
    workbench, config_dir, tmp_path
):
    page = workbench
    batch = tmp_path / "confirmed-batch"
    _placeholder_batch(batch, ("A", "B", "C", "D"))
    save_batch_samples(
        config_dir,
        batch,
        [
            {
                "sample_name": name,
                "raw_stem": name,
                "sample_role": "study_sample",
                "group": name,
                "excluded": False,
                "exclusion_reason": "",
            }
            for name in ("A", "B", "C", "D")
        ],
    )
    _view(page, "untargeted")
    page.locator("#u-raw_dir").fill(str(batch))
    page.locator("#u-raw_dir").press("Tab")
    # Force the previously intermittent order by an observable completion,
    # not a sleep: the automatic preview has populated the summary first.
    expect(page.locator("#samples-count")).to_have_text("4 個樣本")
    _expand_section(page, "#sample-tools")
    page.locator("#sequence-source").fill(str(tmp_path / "unrelated.docx"))
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-count")).to_have_text("4 個樣本")
    expect(page.locator("#samples-source")).to_contain_text("沿用已確認的角色與排除")


def test_late_automatic_sample_preview_cannot_replace_newer_manual_read(
    workbench, config_dir, tmp_path
):
    page = workbench
    batch = tmp_path / "confirmed-batch"
    _placeholder_batch(batch, ("A", "B"))

    def save_role(role):
        save_batch_samples(
            config_dir,
            batch,
            [
                {
                    "sample_name": name,
                    "raw_stem": name,
                    "sample_role": role,
                    "excluded": False,
                    "exclusion_reason": "",
                }
                for name in ("A", "B")
            ],
        )

    save_role("qc")
    pending = []

    def hold_preview(route):
        response = route.fetch()
        assert {row["sample_role"] for row in response.json()["samples"]} == {"qc"}
        pending.append((route, response))
        page.evaluate("window.previewHeld = true")

    page.route("**/api/samples/preview", hold_preview)
    _view(page, "untargeted")
    with page.expect_request("**/api/samples/preview"):
        page.locator("#u-raw_dir").fill(str(batch))
        page.locator("#u-raw_dir").press("Tab")
    page.wait_for_function("window.previewHeld === true")
    save_role("study_sample")
    _expand_section(page, "#sample-tools")
    page.locator("#samples-refresh").click()
    expect(page.locator("#role-summary")).to_contain_text("研究樣本 2")
    with page.expect_response("**/api/samples/preview") as released:
        route, response = pending.pop()
        route.fulfill(response=response)
    released.value.finished()
    # Observe the render after the released response, not an earlier DOM snapshot.
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#role-summary")).to_contain_text("研究樣本 2")
    expect(page.locator("#role-summary")).not_to_contain_text("Older")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_path_error_leaves_a_usable_input(workbench, tmp_path, theme):
    page = workbench
    page.set_viewport_size({"width": 760, "height": 950})
    if theme == "dark":
        page.locator("#theme-toggle").click()
    path = page.locator("#t-data_dir")
    path.fill(str(tmp_path / ("missing-folder-" * 12)))
    path.press("Tab")
    expect(page.locator("#t-data_dir-problem")).to_have_text("找不到這個資料夾")
    assert (
        path.bounding_box()["width"]
        > page.locator('[data-browse="t-data_dir"]').bounding_box()["width"]
    )
    overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
        .filter(node => node.getBoundingClientRect().right > innerWidth)
        .map(node => ({tag: node.tagName, id: node.id, cls: node.className,
                       right: node.getBoundingClientRect().right})).slice(0, 12)""")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        overflow
    )


def test_folder_picker_automatically_lists_samples(workbench, tmp_path, monkeypatch):
    batch = tmp_path / "picked"
    _placeholder_batch(batch, ("A", "B"))
    monkeypatch.setattr("xic_workbench.server.choose_path", lambda *args: str(batch))
    workbench.locator('[data-browse="t-data_dir"]').click()
    expect(workbench.locator("#t-samples-count")).to_have_text("2 個樣本")


@pytest.mark.parametrize("initial_request", ("method", "istd"))
def test_startup_controls_wait_for_initial_data(workbench, initial_request):
    page = workbench
    pending = []
    endpoint = f"**/api/{initial_request}"
    page.route(endpoint, lambda route: pending.append(route))
    try:
        with page.expect_request(endpoint):
            page.reload()
        # Controls must stay inert until all initial data and observers are ready.
        theme = page.locator("#theme-toggle")
        theme.focus()
        expect(theme).not_to_be_focused()
    finally:
        for route in pending:
            route.continue_()
        page.unroute(endpoint)
    with page.expect_response("**/api/preferences"):
        page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
