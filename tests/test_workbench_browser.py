"""Browser contracts for the shipped HTML entry; no RAW/DLL analysis is run."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_xic_inspector_api import _create_mock_db, _insert_link
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_workbench.config_io import read_settings, write_settings, write_targets
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace

pytestmark = pytest.mark.browser_e2e


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        # CI leaves this unset and uses the browser pinned by uv.lock.
        channel = os.environ.get("XIC_PLAYWRIGHT_CHANNEL")
        browser = playwright.chromium.launch(
            **({"channel": channel} if channel else {})
        )
        yield browser
        browser.close()


@pytest.fixture
def config_dir(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    write_settings(
        {
            **CANONICAL_SETTINGS_DEFAULTS,
            "data_dir": str(tmp_path / "raw"),
            "dll_dir": str(tmp_path / "dll"),
        },
        config_dir=config,
    )
    write_targets(
        [
            {
                "label": "Browser target",
                "mz": "500",
                "expected_rt_min": "1.5",
                "cid_nl_tag": "dR",
                "role": "istd",
                "istd_pair": "",
            }
        ],
        config_dir=config,
    )
    return config


@pytest.fixture
def workbench(browser, config_dir, tmp_path):
    workspace = ReviewWorkspace(config_dir=config_dir)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(10_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )
    host, port = server.server_address
    try:
        page.goto(f"http://{host}:{port}/")
        expect(page.locator("body")).not_to_have_class("booting")
        expect(page.locator("#t-data_dir")).to_be_visible()
        yield page
    finally:
        # Failed tests retain tmp_path; successful traces are cleaned by pytest.
        context.tracing.stop(path=tmp_path / "browser-trace.zip")
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        workspace.close()
        assert not errors, "Browser errors: " + "\n".join(errors)


@dataclass
class FakeRuns:
    """Gate completion on an event so polling/progress assertions cannot race."""

    outcome: str = "success"
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def fake_runs(monkeypatch, tmp_path):
    control = FakeRuns()

    class FakeRun:
        def __init__(self, _inputs, *, progress, should_cancel):
            self.progress = progress
            self.should_cancel = should_cancel
            self.stage = "Browser fake analysis"
            self.output_dir = tmp_path / "results"
            self.available_outputs = ()

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
                "published_paths": [str(artifact)],
                "total_files": 1,
                "targets": [{"label": "Browser target"}],
                "sample_count": 1,
                "candidate_counts": {"total": 1},
            }

    monkeypatch.setattr("xic_workbench.targeted.TargetedRun", FakeRun)
    monkeypatch.setattr("xic_workbench.untargeted.UntargetedRun", FakeRun)
    yield control
    control.release.set()


def _view(page, name):
    with page.expect_response(
        lambda response: response.url.endswith("/api/preferences")
    ):
        page.locator(f".topbar [data-view='{name}']").click()
    expect(page.locator(f"#workspace-{name}")).to_be_visible()


def _start(page, kind):
    page.locator(f"button[form={kind}-form][type=submit]").click()


def test_navigation_and_theme_survive_reload(workbench):
    page = workbench
    for view in ("untargeted", "review", "targeted"):
        _view(page, view)
        for other in {"targeted", "untargeted", "review"} - {view}:
            expect(page.locator(f"#workspace-{other}")).to_be_hidden()
    _view(page, "untargeted")
    initial = page.locator("html").get_attribute("data-theme")
    page.locator("#theme-toggle").click()
    theme = "light" if initial == "dark" else "dark"
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("#workspace-untargeted")).to_be_visible()


def test_targeted_exposes_intent_only_and_editor_round_trips(workbench, config_dir):
    page = workbench
    expect(page.locator("#targeted-form .step-head h2")).to_have_text(
        ["批次與注射順序", "目標清單", "執行與輸出"]
    )
    expect(page.locator("#targeted-form .step-no")).to_have_text(["1", "2", "3"])
    expect(page.locator("#target-method-state")).not_to_be_visible()
    expect(page.locator("#targeted-advanced")).to_have_count(0)
    expect(page.locator("#t-ms2_precursor_tol_da")).to_have_count(0)
    page.locator("#target-edit").click()
    expect(page.locator("#target-editor")).to_be_visible()
    page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True).fill(
        "2.75"
    )
    page.get_by_role(
        "textbox", name="CID-NL tag（dR / R / MeR）（第 1 列）", exact=True
    ).fill("R")
    page.locator("#target-editor-save").click()
    expect(page.locator("#target-editor")).not_to_be_visible()
    page.reload()
    page.locator("#target-edit").click()
    expect(
        page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True)
    ).to_have_value("2.75")
    expect(
        page.get_by_role(
            "textbox", name="CID-NL tag（dR / R / MeR）（第 1 列）", exact=True
        )
    ).to_have_value("R")
    assert set(read_settings(config_dir=config_dir)) == set(CANONICAL_SETTINGS_DEFAULTS)


def test_target_list_save_and_load_preserves_batch(workbench):
    page = workbench
    original_batch = page.locator("#t-data_dir").input_value()
    page.get_by_role("button", name="另存清單", exact=True).click()
    page.get_by_label("清單名稱", exact=True).fill("Browser saved list")
    page.locator("#path-dialog").get_by_role("button", name="確定").click()
    expect(page.locator("#target-method-select option:checked")).to_have_text(
        "Browser saved list"
    )
    page.locator("#target-edit").click()
    page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True).fill("4")
    page.locator("#target-editor-save").click()
    page.get_by_role("button", name="載入清單", exact=True).click()
    expect(page.locator("#t-data_dir")).to_have_value(original_batch)
    page.locator("#target-edit").click()
    expect(
        page.get_by_role("textbox", name="預期 RT（min）（第 1 列）", exact=True)
    ).to_have_value("1.5")


def test_saved_intent_submits_without_advanced_settings(fake_runs, workbench):
    page = workbench
    _start(page, "targeted")
    expect(page.locator("#analysis-job-title")).to_contain_text("執行中")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )


@pytest.mark.parametrize(
    ("mode", "input_label"),
    [
        ("full", "RAW 資料夾"),
        ("discovery_only", "單一 RAW"),
        ("align_only", "Discovery batch index"),
    ],
)
def test_readiness_lists_mode_inputs_without_blocking_submit(
    workbench, mode, input_label
):
    page = workbench
    _view(page, "untargeted")
    page.locator(f"input[name=u-mode][value={mode}]").check()
    checklist = page.locator("#untargeted-checklist")
    expect(checklist.locator("li.todo").filter(has_text=input_label)).to_have_count(1)
    expect(checklist).to_contain_text("RawFileReader DLL")
    expect(checklist).to_contain_text("輸出資料夾")
    for label in {"RAW 資料夾", "單一 RAW", "Discovery batch index"} - (
        {input_label, "RAW 資料夾"} if mode == "align_only" else {input_label}
    ):
        expect(checklist).not_to_contain_text(label)
    expect(page.locator("button[form=untargeted-form][type=submit]")).to_be_enabled()


@pytest.mark.parametrize("kind", ["targeted", "untargeted"])
def test_job_progress_artifact_failure_retention_and_confirmed_stop(
    fake_runs, workbench, tmp_path, kind
):
    page = workbench
    if kind == "untargeted":
        _view(page, kind)
        page.locator("input[name=u-mode][value=discovery_only]").check()
        page.locator("#u-raw_file").fill(str(tmp_path / "fake.raw"))
        page.locator("#u-dll_dir").fill(str(tmp_path / "dll"))
        page.locator("#u-output_dir").fill(str(tmp_path / "results"))
    _start(page, kind)
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    expect(page.locator("#analysis-progress")).to_have_attribute("value", "1")
    expect(page.locator(f"button[form={kind}-form][type=submit]")).to_be_disabled()
    fake_runs.release.set()
    expect(page.locator("#analysis-job-title")).to_contain_text("已完成")
    result = page.locator(f"#{'target' if kind == 'targeted' else kind}-result")
    expect(result).to_be_visible()
    link = result.get_by_role("link", name="browser-result.tsv")
    expect(link).to_be_visible()
    with page.expect_download() as download:
        link.click()
    assert (
        Path(download.value.path())
        .read_text(encoding="utf-8")
        .endswith("Browser target\t1\n")
    )
    previous = result.inner_text()
    fake_runs.release.clear()
    fake_runs.outcome = "failure"
    _start(page, kind)
    expect(page.locator("#analysis-job-title")).to_contain_text("執行中")
    fake_runs.release.set()
    expect(page.locator("#analysis-job-title")).to_contain_text("執行失敗")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake failure"
    )
    expect(result).to_be_visible()
    expect(link).to_be_visible()
    expect(result).to_have_text(previous, use_inner_text=True)
    fake_runs.release.clear()
    _start(page, kind)
    expect(page.locator("#analysis-stop")).to_be_visible()
    page.locator("#analysis-stop").click()
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.locator("#confirm-dialog button[value=cancel]").click()
    expect(page.locator("#analysis-job-title")).to_contain_text("執行中")
    page.locator("#analysis-stop").click()
    page.locator("#confirm-accept").click()
    expect(page.locator("#analysis-job-title")).to_contain_text("已停止")
    expect(result).to_be_visible()
    expect(result).to_have_text(previous, use_inner_text=True)


def test_review_search_and_inspector_use_one_scroll_and_visible_zoom(
    workbench, tmp_path
):
    page = workbench
    db, _, _ = _create_mock_db(tmp_path)
    # Keep the existing curve-bearing fixture, extending only review row scope
    # beyond the UI's 25-item page. No scientific values are asserted here.
    groups = {"row-01": "G1"}
    with sqlite3.connect(db) as connection:
        for index in range(2, 27):
            row, group = f"row-{index:02}", f"G{index:02}"
            groups[row] = group
            for kind, identity, payload in (
                ("rows", row, {"row_key_id": row, "group_hypothesis_id": group}),
                (
                    "final_groups",
                    group,
                    {"group_hypothesis_id": group, "disposition": "completed"},
                ),
            ):
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?)",
                    (kind, identity, json.dumps(payload)),
                )
                _insert_link(connection, ("C1", "S1", kind, identity))
    _view(page, "review")
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
    # otherwise two detail responses can replace the frame during a scroll.
    expect(page.locator("#query-form button[type=submit]")).to_be_enabled()
    with page.expect_response(
        lambda response: response.url.endswith("/api/detail")
    ) as detail:
        page.locator("#item-list button").first.click()
    report_path = f"/reports/{detail.value.json()['view_id']}.html"
    expect(page.locator("#inspector")).to_have_attribute(
        "src", report_path + "?embed=1"
    )
    page.wait_for_function(
        """path => document.getElementById('inspector')
        .contentWindow.location.pathname === path""",
        arg=report_path,
    )
    frame = page.frame_locator("#inspector")
    zoom = frame.locator("button[data-zoom]").first
    expect(zoom).to_be_visible()
    page.wait_for_function("""() => {
        const frame = document.getElementById('inspector');
        const doc = frame.contentDocument;
        return doc && frame.clientHeight >= doc.documentElement.scrollHeight - 2;
    }""")
    zoom.scroll_into_view_if_needed()
    # Put the opener near the top of the host viewport, with the frame's own
    # top already scrolled away. Merely centering it can leave frame.top > 0,
    # which would not exercise viewport-relative dialog placement at all.
    page.evaluate(
        """offset => window.scrollBy(0,
            document.getElementById('inspector').getBoundingClientRect().top
            + offset - 120)""",
        arg=zoom.evaluate("button => button.getBoundingClientRect().top"),
    )
    assert page.evaluate("window.scrollY") > 0
    assert page.locator("#inspector").bounding_box()["y"] < 0
    zoom.click()
    dialog = frame.locator("#zoom-dialog")
    expect(dialog).to_be_visible()
    page.wait_for_function("""() => {
        const frame = document.getElementById('inspector');
        const dialog = frame.contentDocument.getElementById('zoom-dialog');
        const f = frame.getBoundingClientRect(), d = dialog.getBoundingClientRect();
        return f.top + d.top >= 0 && f.top + d.bottom <= innerHeight + 2
            && f.left + d.left >= 0 && f.left + d.right <= innerWidth + 2;
    }""")
    frame.locator("#zoom-close").click()
    expect(dialog).to_be_hidden()


def test_targeted_review_marks_the_rare_detection_and_opens_it_in_place(
    workbench, tmp_path
):
    from tests.test_xic_inspector_targeted_workbook import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    overview = page.locator("#targeted-overview")
    expect(overview.locator("h2")).to_have_text("先看 1 筆與多數不同的結果")
    overview.locator("td button", has_text="Analyte").click()
    expect(page.locator("#item-list")).to_be_hidden()
    expect(page.locator("#target-id")).to_be_visible()
    expect(page.locator("#review")).to_have_class("review-grid single-target")
    frame = page.frame_locator("#inspector")
    rows = frame.locator(".tgt-table tbody tr")
    # Rows stay in run order; the rare detection is marked where it ran.
    expect(rows.nth(2).locator(".tgt-sample")).to_have_text("S3")
    expect(rows.nth(2).locator(".tgt-badge")).to_have_text("少數")
    # The same sample's paired ISTD reads in the same row.
    expect(rows.nth(2).locator(".tgt-istd").first).to_have_text("9.2")
    rows.nth(2).locator(".tgt-sample").click()
    # The evidence is the next row, not a panel after every other sample.
    detail = frame.locator(".tgt-table tbody tr:nth-child(4)")
    expect(detail).to_have_class("tgt-detail-row")
    expect(detail).to_contain_text("配對 ISTD（ISTD）")
    page.locator("#target-id").select_option("")
    expect(page.locator("#item-list")).to_be_visible()
    expect(page.locator("#review")).to_have_class("review-grid")


def test_read_first_opens_on_minority_rows_and_clearing_lists_all(workbench, tmp_path):
    from tests.test_xic_inspector_targeted_workbook import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview .overview-actions .primary").click()
    frame = page.frame_locator("#inspector")
    rows = frame.locator(".tgt-table tbody tr:not([hidden])")
    # Reading first starts on the minority row; every sample is one click away.
    expect(rows).to_have_count(1)
    expect(frame.locator(".tgt-filter button")).to_have_text(
        ["全部（5）", "需先看（1）"]
    )
    # A filter change applies at once; no separate search press is needed.
    page.locator("#target-attention").uncheck()
    expect(rows).to_have_count(5)
