"""Browser contracts for the shipped HTML entry; no RAW/DLL analysis is run."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_xic_inspector_api import _create_mock_db, _insert_link
from xic_extractor.sample_metadata import SampleMetadata
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
    output_dirs: list = field(default_factory=list)
    targeted_workbook: Path | None = None
    istd_warnings: list = field(default_factory=list)


@pytest.fixture
def fake_runs(monkeypatch, tmp_path):
    control = FakeRuns()

    class FakeRun:
        def __init__(self, _inputs, *, progress, should_cancel, output_dir=None):
            control.output_dirs.append(output_dir)
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
                **(
                    {"excel_path": str(control.targeted_workbook)}
                    if control.targeted_workbook
                    else {}
                ),
                "published_paths": [str(artifact)],
                "total_files": 1,
                "istd_warnings": control.istd_warnings,
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
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-conflicts li")).to_have_text(
        [
            "序號 7 出現 2 次：A、B",
            "A：檔名是 BC2312，描述寫 BC2303",
            "讀不到檔頭，已排除（不分析）：blank.raw",
        ]
    )
    expect(page.locator("#samples-summary")).to_contain_text("1 筆上機資訊未配對")
    expect(page.locator("#samples-summary")).to_contain_text("以 RAW 為準")
    page.reload()
    expect(page.locator("body")).not_to_have_class("booting")
    expect(page.locator("#samples-conflicts li")).to_have_count(3)
    expect(page.locator("#samples-summary")).to_contain_text("以 RAW 為準")
    summary["conflicts"] = []
    summary["unreadable_raw"] = []
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-conflicts")).to_be_hidden()


def test_targeted_reports_sequence_warnings_before_analysis_and_after_reload(
    workbench,
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from tests.test_analysis_set import header
    from xic_extractor.raw_reader import RawReaderError

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

    monkeypatch.setattr("xic_extractor.raw_reader._analysis_header", read_header)
    release = threading.Event()

    class Service:
        def execute_application(self, request):
            assert release.wait(30)
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        "xic_workbench.targeted.build_canonical_targeted_application_service",
        lambda _: Service(),
    )
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


def test_targeted_exposes_intent_only_and_editor_round_trips(workbench, config_dir):
    page = workbench
    expect(page.locator("#targeted-form .step-head h2")).to_have_text(
        ["資料與輸出", "目標清單", "執行資源"]
    )
    expect(page.locator("#targeted-form .step-no")).to_have_text(["1", "2", "3"])
    expect(page.locator("#target-method-state")).not_to_be_visible()
    page.locator("summary", has_text="處理策略與設定來源").click()
    expect(page.locator("#target-method-state")).to_have_text("共用 CID-NL 策略")
    expect(page.locator("#targeted-form")).not_to_contain_text("版本")
    page.locator("summary", has_text="處理策略與設定來源").click()
    for cell in page.locator("#target-preview th, #target-preview td").all():
        expect(cell).to_have_css("text-align", "center")
    expect(page.locator("#targeted-advanced")).to_have_count(0)
    expect(page.locator("#t-ms2_precursor_tol_da")).to_have_count(0)
    expect(page.locator("#t-parallel_workers")).to_be_visible()
    page.locator("#t-parallel_workers").fill("2")
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
    assert read_settings(config_dir=config_dir)["parallel_workers"] == "2"
    expect(page.locator("#t-parallel_workers")).to_have_value("2")


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
    labels = page.locator("#targeted-data .path-line > label")
    expect(labels).to_have_text(
        ["RAW 資料夾", "上機資訊", "輸出資料夾", "RawFileReader DLL 資料夾"]
    )
    expect(page.locator("#t-output_dir")).to_have_value(
        str(config_dir.parent / "output")
    )
    chosen = tmp_path / "chosen-output"
    page.locator("#t-output_dir").fill(str(chosen))
    _start(page, "targeted")
    expect(page.locator("#analysis-job-message")).to_contain_text(
        "Browser fake progress"
    )
    assert fake_runs.output_dirs == [chosen]
    fake_runs.release.set()
    expect(page.locator("#analysis-job-title")).to_contain_text("已完成")
    page.reload()
    expect(page.locator("#t-output_dir")).to_have_value(str(chosen))
    _view(page, "untargeted")
    expect(page.locator("#untargeted-paths .path-line:visible > label")).to_have_text(
        ["RAW 資料夾", "輸出資料夾", "RawFileReader DLL 資料夾"]
    )


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


def test_review_search_and_saved_results_keep_one_scroll_and_visible_evidence(
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
    page.wait_for_function(
        """path => document.getElementById('inspector')
        .contentWindow.location.pathname === path""",
        arg=report_path,
    )
    frame = page.frame_locator("#inspector")
    expect(frame.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(frame.locator("#svg-raw")).to_have_count(0)
    page.wait_for_function("""() => {
        const frame = document.getElementById('inspector');
        const doc = frame.contentDocument;
        return doc && frame.clientHeight >= doc.documentElement.scrollHeight - 2;
    }""")
    frame.get_by_role("button", name="查看依據").first.click()
    expect(frame.locator("#evidence-panel")).to_be_visible()
    frame.locator("#close-evidence").click()
    expect(frame.locator("#evidence-panel")).to_have_count(0)


def test_untargeted_review_starts_with_results_and_explains_missing_curves(
    workbench, tmp_path
):
    from tests.test_xic_workbench import _review_batch

    report, _ = _review_batch(tmp_path)
    page = workbench
    _view(page, "review")
    page.locator("#source").fill(str(report))
    page.locator("#raw-dir").fill("")
    page.locator("#open-form button[type=submit]").click()
    overview = page.locator("#targeted-overview")
    expect(overview.get_by_role("heading", name="先看 1 組未解決定量")).to_be_visible()
    expect(page.locator("#review")).to_be_hidden()
    overview.get_by_role("button", name="先看未解決定量", exact=True).click()
    expect(page.locator("#item-list button")).to_have_count(1)
    expect(page.locator("#inline-curve-status")).to_contain_text(
        "尚未設定 RAW 或 DLL 位置"
    )
    saved = page.frame_locator("#inspector")
    expect(saved.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(saved.locator(".sample-table tbody tr").first).to_contain_text("4.000e+2")
    expect(saved.locator("#svg-raw")).to_have_count(0)
    page.locator("#back-to-overview").click()
    expect(overview).to_be_visible()
    overview.get_by_role("button", name="查看全部矩陣群組").click()
    expect(page.locator("#item-list button")).to_have_count(3)
    page.locator("#back-to-overview").click()
    overview.locator("summary").click()
    overview.get_by_role("button", name="1 組未採用").click()
    expect(page.locator("#list-summary")).to_contain_text("未採用群組 · 1 組")
    expect(
        page.frame_locator("#inspector").locator(".sample-table tbody tr").first
    ).to_contain_text("5.000e+2")


@pytest.mark.parametrize("sample_count", (1, 6))
def test_untargeted_curves_open_beside_saved_report_and_reuse_loaded_view(
    workbench, tmp_path, monkeypatch, sample_count
):
    from dataclasses import replace

    from tests.test_xic_inspector_curve_bundle import _fixture_bundle
    from xic_inspector.sources.curve_bundle import CurveBundle, write_curve_bundle
    from xic_workbench import acquisition

    calls = []

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
    from tests.test_xic_workbench import _review_batch

    db = (
        _create_mock_db(tmp_path)[0]
        if sample_count == 1
        else _review_batch(tmp_path, sample_count=sample_count)[0]
    )
    _view(page, "review")
    page.locator("#source").fill(str(db))
    page.locator("#raw-dir").fill(str(tmp_path))
    page.locator("#dll-dir").fill(str(tmp_path))
    page.locator("#open-form button[type=submit]").click()
    if sample_count > 1:
        page.get_by_role("button", name="查看全部矩陣群組").click()
        expect(
            page.frame_locator("#inline-curve-frame").locator("#svg-raw polyline")
        ).to_have_count(1)
        page.locator("#item-list button").filter(has_text="6 / 6 個樣本有值").click()
    expect(page.locator(".acquisition-panel > summary")).to_be_visible()
    expect(page.locator("#sample-list")).to_be_hidden()
    page.locator(".acquisition-panel > summary").click()
    recorded = page.locator("#inspector").evaluate(
        "frame => frame.contentWindow.location.href"
    )
    frame = page.frame_locator("#inline-curve-frame")
    expect(frame.locator("#svg-raw polyline")).to_have_count(sample_count)
    expect(page.locator("#xic-dialog")).to_be_hidden()
    saved = page.frame_locator("#inspector")
    expect(saved.get_by_role("heading", name="已保存的樣本結果")).to_be_visible()
    expect(saved.locator("#svg-raw")).to_have_count(0)
    assert (
        page.locator("#inspector").evaluate(
            "frame => frame.contentWindow.location.href"
        )
        == recorded
    )
    page.locator("#load-curves").click()
    expect(frame.locator("#svg-raw polyline")).to_have_count(sample_count)
    assert set(calls[-1]) == {f"S{i + 1}" for i in range(sample_count)}
    assert len(calls) == (1 if sample_count == 1 else 2)
    frame.locator("button[data-zoom]").click()
    expect(frame.locator("#zoom-dialog")).to_be_visible()
    page.wait_for_function("""() => {
        const frame = document.getElementById('inline-curve-frame');
        const dialog = frame.contentDocument.getElementById('zoom-dialog');
        const f = frame.getBoundingClientRect(), d = dialog.getBoundingClientRect();
        return f.top + d.top >= 0 && f.top + d.bottom <= innerHeight + 2;
    }""")
    frame.locator("#zoom-close").click()
    expect(frame.locator("#zoom-dialog")).to_be_hidden()


def test_targeted_nd_shows_original_points_and_support_failure(
    workbench, tmp_path, monkeypatch
):
    from openpyxl import load_workbook

    from tests.test_xic_inspector_targeted_workbook import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "raw"
    install_targeted_raws(
        monkeypatch, raw_dir, {"S1": 13000, "S2": 1000}, impulse_samples=("S1",)
    )
    path = write_rare_detection_workbook(tmp_path / "nd.xlsx")
    book = load_workbook(path)
    book["XIC Results"].cell(2, 8, "峰不在內標位置，報 ND")
    book.save(path)
    book.close()
    original = path.read_bytes()
    _view(page, "review")
    page.locator("#source").fill(str(path))
    page.locator("#raw-dir").fill(str(raw_dir))
    page.locator("#dll-dir").fill(str(tmp_path))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    saved = page.frame_locator("#inspector")
    expect(saved.locator(".tgt-reason").first).to_contain_text("排除原因未記錄")
    saved.get_by_role("button", name="看 S1 的 Analyte XIC", exact=True).click()
    frame = page.frame_locator("#xic-inspector")
    expect(frame.locator(".tx-support-note")).to_contain_text("原始訊號支撐不足")
    expect(frame.locator(".tx-support-note")).to_contain_text("本次讀取 XIC")
    expect(frame.locator('.tx-chart polyline[data-kind="smoothed"]')).to_have_count(2)
    raw_points = frame.locator(".tx-chart").first.locator('circle[data-kind="raw"]')
    values = raw_points.locator("title").all_text_contents()
    assert sum(not value.endswith("· 0") for value in values) == 1
    # Raw spikes must fit the shared per-ion Y scale, not be clipped by smoothing.
    assert (
        min(float(raw_points.nth(i).get_attribute("cy")) for i in range(len(values)))
        >= 14
    )
    page.locator("#xic-next").click()
    expect(page.locator("#xic-sample")).to_have_value("S2")
    expect(frame.locator(".tx-support-note")).to_have_count(0)
    expect(frame.locator(".tx-chart polyline")).to_have_count(2)
    assert path.read_bytes() == original


def test_targeted_row_loads_paired_xic_and_zoom_reuses_curves(
    workbench,
    tmp_path,
    monkeypatch,
):
    from contextlib import contextmanager

    from tests.test_xic_inspector_targeted_workbook import (
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
        assert release.wait(10)
        with open_raw(*args, **kwargs) as raw:
            yield raw

    monkeypatch.setattr(targeted_curves, "open_raw", delayed_raw)
    for sample in ("S3", "S4"):
        batch = raw_dir / sample
        batch.mkdir()
        (raw_dir / f"{sample}.raw").rename(batch / f"{sample}.raw")
    workbook = write_rare_detection_workbook(tmp_path / "paired.xlsx")
    original = workbook.read_bytes()
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#raw-dir").fill(str(raw_dir))
    page.locator("#dll-dir").fill(str(tmp_path))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    recorded = page.frame_locator("#inspector")
    row_button = recorded.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True)
    expect(row_button).to_be_visible()
    row_button.scroll_into_view_if_needed()
    reading_scroll = page.evaluate("window.scrollY")
    recorded_url = page.locator("#inspector").evaluate(
        "frame => frame.contentWindow.location.href"
    )
    row_button.click()
    dialog = page.locator("#xic-dialog")
    expect(dialog).to_be_visible()
    page.go_back()
    expect(dialog).to_be_hidden()
    page.wait_for_function("y => Math.abs(window.scrollY - y) < 2", arg=reading_scroll)
    release.set()
    expect(page.locator("#status")).to_contain_text("診斷曲線已載入")
    page.go_forward()
    expect(dialog).to_be_visible()
    expect(page.locator(".acquisition-panel")).to_be_hidden()
    expect(page.locator("#xic-sample")).to_have_value("S3")
    frame = page.frame_locator("#xic-inspector")
    curves = frame.locator(".tx-chart polyline")
    expect(frame.locator('.tx-chart circle[data-kind="raw"]')).not_to_have_count(0)
    expect(frame.get_by_text("原始 scan 點", exact=True)).to_be_visible()
    expect(curves).to_have_count(2)
    expect(frame.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    expect(frame.locator(".tx-provenance")).to_contain_text("無法驗證是否為原分析檔")
    # The result table never navigates away; each ion has its own y scale.
    assert (
        page.locator("#inspector").evaluate(
            "frame => frame.contentWindow.location.href"
        )
        == recorded_url
    )
    expect(frame.locator(".tx-chart")).to_have_count(2)
    for line in curves.all():
        ys = [
            float(point.split(",")[1]) for point in line.get_attribute("points").split()
        ]
        assert min(ys) < 25 and max(ys) > 150  # Neither ion is flattened by its pair.
    original_points = curves.first.get_attribute("points")
    frame.get_by_label("顯示 RT 起點", exact=True).fill("8.95")
    frame.get_by_label("顯示 RT 終點", exact=True).fill("9.05")
    frame.get_by_role("button", name="套用範圍", exact=True).click()
    assert curves.first.get_attribute("points") != original_points
    frame.get_by_role("button", name="完整 RT", exact=True).click()
    expect(frame.get_by_label("顯示 RT 起點", exact=True)).to_have_value("0")
    expect(frame.get_by_label("顯示 RT 終點", exact=True)).to_have_value("12")
    page.go_back()
    expect(dialog).to_be_hidden()
    expect(row_button).to_be_focused()
    page.go_forward()
    expect(dialog).to_be_visible()
    expect(frame.locator(".tx-sample-name")).to_have_text("S3")
    assert len(reads) == 2
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    page.go_forward()
    expect(dialog).to_be_visible()
    page.locator("#xic-close").click()
    expect(dialog).to_be_hidden()
    expect(row_button).to_be_focused()
    row_button.click()
    expect(curves).to_have_count(2)
    assert len(reads) == 2  # Reopening the same sample reuses the loaded report.
    page.locator("#xic-next").click()
    expect(page.locator("#xic-sample")).to_have_value("S4")
    expect(frame.locator(".tx-sample-name")).to_have_text("S4")
    expect(curves).to_have_count(2)
    assert len(reads) == 4
    page.locator("#xic-next").click()  # S5 has no RAW: keep a recoverable local error.
    expect(page.locator("#xic-status")).to_contain_text("找不到")
    expect(page.locator("#xic-inspector")).to_be_hidden()
    expect(page.locator("#xic-retry")).to_be_visible()
    page.locator("#xic-previous").click()
    expect(frame.locator(".tx-sample-name")).to_have_text("S4")
    expect(page.locator("#xic-inspector")).to_be_visible()
    assert len(reads) == 4
    page.set_viewport_size({"width": 420, "height": 760})
    bounds = dialog.bounding_box()
    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 421
    assert (
        page.locator("#inspector").evaluate(
            "frame => frame.contentWindow.location.href"
        )
        == recorded_url
    )
    assert workbook.read_bytes() == original


def test_targeted_review_marks_the_rare_detection_and_opens_it_in_place(
    workbench, tmp_path
):
    from openpyxl import load_workbook

    from tests.test_xic_inspector_targeted_workbook import (
        write_rare_detection_workbook,
    )

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    # Current Targeted workbooks store one expected RT as equal RT min/max.
    book = load_workbook(workbook)
    book["Targets"]["E2"] = book["Targets"]["F2"] = 9.25
    book.save(workbook)
    book.close()
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
    expect(frame.locator(".tgt-context")).to_contain_text("預期 RT 9.25 min")
    rows = frame.locator(".tgt-table tbody tr")
    # Rows stay in run order; the rare detection is marked where it ran.
    expect(rows.nth(2).locator(".tgt-sample")).to_have_text("S3")
    expect(rows.nth(2).locator(".tgt-badge")).to_have_text("與多數不同")
    # The same sample's paired ISTD reads in the same row.
    expect(rows.nth(2).locator(".tgt-istd").first).to_have_text("9.2")
    rows.nth(2).locator(".tgt-sample").click()
    # The evidence is the next row, not a panel after every other sample.
    detail = frame.locator(".tgt-table tbody tr:nth-child(4)")
    expect(detail).to_have_class("tgt-detail-row")
    expect(detail).to_contain_text("積分範圍")
    expect(detail).not_to_contain_text("配對 ISTD（ISTD）")
    expect(detail).not_to_contain_text("檢閱註記")
    page.locator("#target-id").select_option("")
    expect(page.locator("#item-list")).to_be_visible()
    expect(page.locator("#review")).to_have_class("review-grid")


def test_review_back_forward_preserves_target_and_filters(workbench, tmp_path):
    from tests.test_xic_inspector_targeted_workbook import write_rare_detection_workbook

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    page.locator("#target-attention").check()
    expect(
        page.frame_locator("#inspector").locator(".tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    page.go_back()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#review")).to_be_visible()
    expect(page.locator("#target-id")).to_have_value("Analyte")
    expect(page.locator("#target-attention")).to_be_checked()
    expect(
        page.frame_locator("#inspector").locator(".tgt-table tbody tr:not([hidden])")
    ).to_have_count(1)
    page.locator("#back-to-overview").click()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    _view(page, "targeted")
    page.go_back()
    expect(page.locator("#workspace-review")).to_be_visible()
    expect(page.locator("#target-attention")).to_be_checked()
    page.go_forward()
    expect(page.locator("#workspace-targeted")).to_be_visible()


@pytest.mark.parametrize("query_pending", [False, True], ids=["completed", "pending"])
def test_review_history_restores_server_query_before_loading_xic(
    workbench, tmp_path, monkeypatch, query_pending
):
    from tests.test_xic_inspector_targeted_workbook import (
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
                assert release.wait(10)
            return result

        monkeypatch.setattr(ReviewWorkspace, "query", hold_query_response)
    raw_dir = tmp_path / "review-raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S3": 1000}, pair_scale=1000)
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#raw-dir").fill(str(raw_dir))
    page.locator("#dll-dir").fill(str(tmp_path))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    analyte_xic = page.frame_locator("#inspector").get_by_role(
        "button", name="看 S3 的 Analyte XIC", exact=True
    )
    expect(analyte_xic).to_be_visible()
    _view(page, "targeted")
    _view(page, "review")
    page.locator("#target-id").select_option("ISTD")
    try:
        if query_pending:
            assert queried.wait(10)
        else:
            expect(page.locator("#item-title")).to_contain_text("ISTD")
            expect(
                page.frame_locator("#inspector").get_by_role(
                    "button", name="看 S3 的 ISTD XIC", exact=True
                )
            ).to_be_visible()
        page.go_back()
        expect(page.locator("#workspace-targeted")).to_be_visible()
        page.go_back()
    finally:
        release.set()
    expect(page.locator("#workspace-review")).to_be_visible()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    expect(page.locator("#load-curves")).to_be_enabled()
    with page.expect_response("**/api/curves") as response:
        analyte_xic.click()
    assert response.value.status == 202, response.value.json()
    expect(
        page.frame_locator("#xic-inspector").locator(".tx-chart-title")
    ).to_have_text(["Analyte", "ISTD"])


def test_review_overview_button_skips_workspace_tab_history(workbench, tmp_path):
    from tests.test_xic_inspector_targeted_workbook import write_rare_detection_workbook

    page = workbench
    workbook = write_rare_detection_workbook(tmp_path / "navigation.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    expect(page.locator("#item-title")).to_contain_text("Analyte")
    _view(page, "targeted")
    _view(page, "review")
    page.locator("#back-to-overview").click()
    expect(page.locator("#workspace-review")).to_be_visible()
    expect(page.locator("#targeted-overview")).to_be_visible()
    page.go_forward()
    expect(page.locator("#item-title")).to_contain_text("Analyte")


def test_targeted_row_xic_without_raw_points_at_the_missing_folder(workbench, tmp_path):
    from tests.test_xic_inspector_targeted_workbook import (
        write_rare_detection_workbook,
    )

    page = workbench
    page.set_viewport_size({"width": 1280, "height": 480})
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    _view(page, "review")
    page.locator("#source").fill(str(workbook))
    page.locator("#open-form button[type=submit]").click()
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    row_button = page.frame_locator("#inspector").get_by_role(
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
    expect(page.locator("#status")).to_be_in_viewport()


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
    page.locator("#samples-refresh").click()
    expect(page.locator("#samples-count")).to_have_text("1 個樣本")
    # Restoration must not depend on the most recent job being a samples job.
    _view(page, "targeted")
    fake_runs.release.set()
    _start(page, "targeted")
    expect(page.locator("#analysis-job-title")).to_contain_text("已完成")
    _view(page, "untargeted")
    if changed_input == "saved_role":
        page.locator("#samples-edit").click()
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
        if changed_input == "raw_dir":
            assert payload["custom_roles"] == {}
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
        page.get_by_role("link", name="下載方法範例 XLSX", exact=True).click()
    path = tmp_path / "downloaded-method.xlsx"
    download.value.save_as(path)
    original = path.read_bytes()
    assert dict(load_targeted_method(path).settings) == {"strategy": "cid-nl"}
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="選擇檔案", exact=True).click()
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
    assert saved.target_rows[0]["cid_nl_tag"] == "dR"
    assert path.read_bytes() == original
    assert not (config_dir / "settings.csv").exists()
    assert not (config_dir / "targets.csv").exists()
    assert (config_dir / "workspace.json").is_file()
    page.screenshot(path=str(tmp_path / "xlsx-method-workspace.png"), full_page=True)


@pytest.mark.parametrize("extension", ["xlsx", "csv", "tsv"])
def test_drop_target_table_preview_cancel_and_confirm(workbench, tmp_path, extension):
    import base64

    from tests.test_targeted_method_files import rows
    from xic_extractor.configuration.targeted_method_files import save_target_table

    page = workbench
    path = tmp_path / f"renamed.{extension}"
    if extension == "xlsx":
        save_target_table(rows(), path)
    else:
        import csv

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(dict.fromkeys(k for row in rows() for k in row)),
                delimiter="\t" if extension == "tsv" else ",",
            )
            writer.writeheader()
            writer.writerows(rows())
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


def test_istd_warning_distinguishes_confirmation_from_signal(workbench, fake_runs):
    fake_runs.istd_warnings = [{"label": "d4-N6-2HE-dA", "detected": 0, "total": 1}]
    fake_runs.release.set()
    _start(workbench, "targeted")
    warning = workbench.locator(".result-warning")
    expect(warning).to_contain_text("ISTD d4-N6-2HE-dA：0 / 1 個樣本通過確認")
    expect(warning).to_contain_text("未通過確認不代表沒有訊號")
    expect(warning).not_to_contain_text("個樣本偵測到")


def test_analysis_review_uses_run_paths_even_after_batch_edit(
    workbench, fake_runs, tmp_path, monkeypatch
):
    from tests.test_xic_inspector_targeted_workbook import (
        install_targeted_raws,
        write_rare_detection_workbook,
    )

    page = workbench
    raw_dir = tmp_path / "run-raw"
    install_targeted_raws(monkeypatch, raw_dir, {"S1": 13000})
    fake_runs.targeted_workbook = write_rare_detection_workbook(tmp_path / "run.xlsx")
    fake_runs.release.set()
    page.locator("#t-data_dir").fill(str(raw_dir))
    page.locator("#t-dll_dir").fill(str(tmp_path))
    page.get_by_role("button", name="開始 Targeted 分析", exact=True).click()
    expect(page.locator("#analysis-job-message")).to_contain_text("工作已完成")
    page.locator("#t-data_dir").fill(str(tmp_path / "next-batch"))
    page.locator("#t-dll_dir").fill(str(tmp_path / "next-dll"))
    page.get_by_role("button", name="在結果檢閱開啟", exact=True).click()
    expect(page.locator("#raw-dir")).to_have_value(str(raw_dir))
    expect(page.locator("#dll-dir")).to_have_value(str(tmp_path))
    page.locator("#targeted-overview td button", has_text="Analyte").click()
    page.frame_locator("#inspector").get_by_role(
        "button", name="看 S1 的 Analyte XIC", exact=True
    ).click()
    frame = page.frame_locator("#xic-inspector")
    expect(frame.locator('.tx-chart polyline[data-kind="smoothed"]')).to_have_count(2)
    expect(page.locator("#status")).not_to_contain_text("請先選擇 RAW 資料夾")


@pytest.mark.parametrize("extension,drop", [("xlsx", True), ("tsv", False)])
def test_untargeted_istd_shared_upload_and_persistence(
    workbench, config_dir, tmp_path, extension, drop
):
    import base64
    import csv

    from tests.test_targeted_method_files import rows
    from xic_extractor.configuration.targeted_method_files import save_target_table
    from xic_extractor.configuration.targets import read_istd_targets

    page = workbench
    path = tmp_path / f"shared.{extension}"
    if extension == "xlsx":
        save_target_table(rows(), path)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(dict.fromkeys(k for r in rows() for k in r)),
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerows(rows())
    original_targets = page.locator("#target-preview").inner_text()
    page.get_by_role("button", name="Untargeted", exact=True).click()
    expect(page.get_by_role("link", name="下載共用清單範例 XLSX")).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("link", name="下載共用清單範例 XLSX").click()
    download.value.save_as(tmp_path / "example.xlsx")
    assert read_istd_targets(tmp_path / "example.xlsx").targets
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
        page.locator("#istd-dropzone").dispatch_event(
            "drop", {"dataTransfer": transfer}
        )
    else:
        page.locator("#istd-import-file").set_input_files(path)
    expect(page.locator("#istd-import-status")).to_contain_text(
        f"已匯入 shared.{extension}"
    )
    expect(page.locator("#istd-summary")).to_have_text(
        "1 個適用內標；0 個不適用此方法；略過 1 個一般目標。"
    )
    expect(page.locator("#istd-preview")).to_contain_text("standard")
    expect(page.locator("#istd-preview")).not_to_contain_text("analyte")
    retained = page.locator("#u-targets_csv").input_value()
    page.locator("#istd-import-file").set_input_files(
        {"name": "bad.xls", "mimeType": "application/octet-stream", "buffer": b"bad"}
    )
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
    page.locator("#untargeted-clear").click()
    expect(page.locator("#istd-summary")).to_have_text("本次不使用內標清單。")
    expect(page.locator("#istd-preview")).to_be_empty()
    page.get_by_role("button", name="Targeted", exact=True).click()
    expect(page.locator("#target-preview")).to_have_text(
        original_targets, use_inner_text=True
    )


def test_untargeted_istd_applicability_tracks_method_not_nd(workbench):
    page = workbench
    page.get_by_role("button", name="Untargeted", exact=True).click()
    rna = page.locator("#istd-preview tr").filter(has_text="[13C,15N2]-8-oxo-Guo")
    expect(page.locator("#istd-summary")).to_have_text(
        "6 個適用內標；1 個不適用此方法；略過 7 個一般目標。"
    )
    expect(rna).to_contain_text("不適用此方法（略過）")
    expect(rna.locator("td").nth(1)).to_have_text("R")
    page.locator("#u-preset").select_option("rna_ribose")
    expect(page.locator("#istd-summary")).to_have_text(
        "1 個適用內標；6 個不適用此方法；略過 7 個一般目標。"
    )
    expect(rna.locator("td").last).to_have_text("適用")
    page.locator("#u-preset").select_option("rna_mer")
    expect(page.locator("#istd-summary")).to_contain_text("0 個適用內標")
    expect(page.locator("#istd-summary")).to_contain_text("本次不使用內標參考")
    expect(rna.locator("td").last).to_have_text("不適用此方法（略過）")
    expect(page.locator("#istd-preview")).not_to_contain_text("ND")
    page.locator("#u-preset").select_option("dna_dr")
    expect(page.locator("#istd-summary")).to_contain_text("6 個適用內標")
