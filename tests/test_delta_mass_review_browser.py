"""Candidate positions, RAW coordinates and MS2 scan identity in the real view."""

import json
import re
from dataclasses import replace

import pytest
from playwright.sync_api import expect

from tests.delta_mass_review_fixtures import (
    review_fixture,
    write_nl_review,
    write_review,
)
from tests.workbench_browser_fixtures import browser as browser
from xic_extractor.diagnostics.delta_mass_review_data import (
    DeltaMassReview,
    ReviewNlPosition,
    ReviewReadError,
    write_delta_mass_review,
)
from xic_inspector.api import SelectionRequest, generate_report

pytestmark = pytest.mark.browser_e2e
pytest_plugins = ("tests.browser_diagnostics",)


@pytest.mark.parametrize(
    "status,full",
    [
        ("no_match", "未找到 NL 支持"),
        ("no_ms2", "沒有相關 MS2"),
    ],
)
def test_empty_nl_results_are_omitted_not_moved_into_details(
    review_page, tmp_path, status, full
):
    candidate = review_fixture().candidates[0]
    candidate = replace(
        candidate,
        neutral_losses=tuple(
            ReviewNlPosition(p.label, "test-NL", 40.0, status, (), ())
            for p in candidate.positions
        ),
    )
    source = tmp_path / "summary.json"
    write_delta_mass_review(DeltaMassReview("saved", (candidate,)), source)
    report = tmp_path / "summary.html"
    generate_report(source, SelectionRequest(), report)
    page = review_page
    page.set_viewport_size({"width": 792, "height": 884})
    page.goto(report.as_uri())
    expect(page.locator(".record-reason")).to_have_count(0)
    expect(page.get_by_label("各位置 NL 判讀")).to_have_count(0)
    expect(page.locator(".dm-review").get_by_text(full, exact=False)).to_have_count(0)
    expect(page.locator(".scope-summary")).not_to_be_visible()
    expect(page.locator(".header-actions > .badge")).to_have_count(0)
    page.get_by_label("本頁搜尋").fill(full)
    expect(page.locator(".record-button")).to_have_count(1)
    expect(page.locator(".filter-chip")).to_have_count(0)


def test_compact_candidate_count_still_discloses_a_sampled_report(
    review_page, tmp_path
):
    source = write_nl_review(tmp_path)
    output = tmp_path / "sampled.html"
    generate_report(source, SelectionRequest(limit=1), output)
    review_page.goto(output.as_uri())
    expect(review_page.locator(".list-header")).to_contain_text("全批 2")


@pytest.mark.parametrize(
    "status,label",
    [
        ("incomplete", "讀取不完整"),
        ("no_peak_bounds", "缺少峰範圍"),
        ("no_spectrum", "缺少 spectrum"),
    ],
)
def test_evidence_limitations_remain_visible_when_empty_results_are_silent(
    review_page, tmp_path, status, label
):
    candidate = review_fixture().candidates[0]
    candidate = replace(
        candidate,
        spectra=(),
        ms2_read_errors=(ReviewReadError(11, (1.0, 3.0), "read failed"),)
        if status == "incomplete"
        else (),
        neutral_losses=tuple(
            ReviewNlPosition(p.label, "test-NL", 40.0, status, (), ())
            for p in candidate.positions
        ),
    )
    source = tmp_path / "incomplete.json"
    write_delta_mass_review(DeltaMassReview("saved", (candidate,)), source)
    output = tmp_path / "incomplete.html"
    generate_report(source, SelectionRequest(), output)
    review_page.goto(output.as_uri())
    expect(review_page.locator(".record-reason")).to_have_text(f"NL：{label}")
    expect(review_page.get_by_label("各位置 NL 判讀")).to_contain_text(label)
    expect(review_page.locator(".dm-nl-support")).to_have_count(0)


def test_nl_result_opens_exact_support_without_scan_search(review_page, tmp_path):
    page = review_page
    source = write_nl_review(tmp_path)
    output = tmp_path / "nl.html"
    generate_report(source, SelectionRequest(), output)
    page.goto(output.as_uri())
    root = page.locator(".dm-review")
    expect(root.get_by_label("Δm/z partner − M", exact=True)).to_have_text(
        "Δm/z partner − M：實測 3.000321 · 設定 3"
    )
    expect(page.locator(".record-button").first).to_contain_text("M 有 test-NL 支持")
    expect(root.get_by_label("各位置 NL 判讀").locator("tbody tr")).to_have_count(1)
    expect(root.get_by_label("MS2 scan", exact=True)).not_to_be_visible()
    expect(root.get_by_label("顯示 RT 起點")).not_to_be_visible()
    expect(root.get_by_role("heading", name="NL 證據", exact=True)).not_to_be_visible()
    root.get_by_role("button", name="M · test-NL：有 NL 支持（2）", exact=True).click()
    expect(root.get_by_role("heading", name="NL 證據", exact=True)).to_be_in_viewport()
    expect(root.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "11"
    )
    expect(root.locator("[data-nl-product-mz]")).to_have_attribute(
        "data-nl-product-mz", "60"
    )
    expect(root.locator(".dm-nl-measurement").first).to_contain_text("實測 NL 40 Da")
    expect(root.locator("[data-nl-product-label]")).to_have_text("NL 產物 m/z 60")
    expect(root.locator("[data-nl-leader]")).to_have_count(1)
    for width in (420, 1915):
        page.set_viewport_size({"width": width, "height": 884})
        expect(root.locator("[data-nl-product-label]")).to_be_visible()
        assert root.locator(".dm-spectrum svg").evaluate("""svg => {
            const box=svg.querySelector('[data-nl-product-label]').getBBox();
            const stems=[...svg.querySelectorAll('line[x1][y1][x2][y2]')]
                .filter(line=>line.getAttribute('x1')===line.getAttribute('x2'));
            const top=Math.min(...stems.flatMap(line=>[
                +line.getAttribute('y1'),+line.getAttribute('y2')]));
            return box.y+box.height < top && box.x>=0
                && box.x+box.width<=svg.viewBox.baseVal.width;
        }""")
    expect(root.locator(".dm-xic [data-nl-boundary]")).to_have_count(2)
    root.get_by_role("button", name="下一筆支持").click()
    expect(root.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "22"
    )
    expect(root.locator(".dm-nl-measurement").last).to_contain_text("37.5%")
    expect(root.get_by_label("MS2 scan", exact=True)).to_have_count(0)
    expect(root.get_by_label("顯示 RT 起點")).to_have_count(0)
    page.get_by_role("button", name=re.compile("^sample-b")).click()
    expect(root.locator("[data-spectrum-scan]")).to_have_count(0)
    expect(root.get_by_label("各位置 NL 判讀")).to_have_count(0)
    expect(root.get_by_role("button", name="下一筆支持")).not_to_be_visible()
    page.set_viewport_size({"width": 420, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_supported_candidate_leads_without_discarding_quiet_candidates(
    review_page, tmp_path
):
    source = write_nl_review(tmp_path)
    payload = json.loads(source.read_text())
    payload["candidates"][1]["sample_stem"] = "0-quiet"
    payload["candidates"][1]["candidate_id"] = "0-quiet|200"
    payload["candidates"][1]["raw_file"] = "0-quiet.raw"
    source.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "ranked.html"
    generate_report(source, SelectionRequest(), output)
    page = review_page
    page.goto(output.as_uri())
    expect(page.locator(".record-button")).to_have_count(2)
    expect(page.locator(".record-button").first).to_contain_text("sample-a")
    expect(page.locator(".record-button").last.locator(".record-reason")).to_have_count(
        0
    )
    page.get_by_role("button", name="M · test-NL：有 NL 支持（2）", exact=True).click()
    expect(page.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "11"
    )


def _open(page, tmp_path):
    source = write_review(tmp_path)
    output = tmp_path / "review.html"
    generate_report(source, SelectionRequest(strategy="query", mz_min=99), output)
    page.goto(output.as_uri())
    return page.locator(".dm-review")


def test_offline_positions_share_a_scale_and_keep_original_scan_values(
    review_page, tmp_path
):
    page = review_page
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.url.startswith(("http://", "https://"))
            else None
        ),
    )
    page.route(re.compile(r"^https?://"), lambda route: route.abort())
    root = _open(page, tmp_path)
    expect(root.get_by_label("MS2 scan", exact=True)).to_have_count(0)
    expect(root.locator("[data-ms2-scan]")).to_have_count(0)
    root.get_by_text("詳細資料", exact=True).click()
    expect(root.get_by_role("columnheader", name="Feature 峰界 (min)")).to_be_visible()
    expect(
        root.get_by_role("columnheader", name=re.compile("Ratio|Shape|Height"))
    ).to_have_count(0)
    chart = root.locator(".dm-xic svg")
    expect(chart.locator("polyline")).to_have_count(3)
    assert chart.locator("polyline").evaluate_all(
        "lines => lines.map(line => [line.getAttribute('stroke'), "
        "line.getAttribute('stroke-dasharray'), line.getAttribute('stroke-width')])"
    ) == [
        ["var(--dm-series-1)", "", "2"],
        ["var(--dm-series-2)", "7 4", "2"],
        ["var(--dm-series-3)", "2 3", "2"],
    ]
    assert chart.evaluate("""svg => [...svg.querySelectorAll('polyline')].map(line => {
        const d=svg.dataset, y=line.points[2].y;
        return Number(d.signalLow)+(Number(d.signalHigh)-Number(d.signalLow))*
            (Number(d.chartTop)+Number(d.chartHeight)-y)/Number(d.chartHeight);
    })""") == pytest.approx([100.0, 200.0, 300.0])
    chart.focus()
    chart.press("ArrowRight")
    expect(root.locator(".dm-readout").first).to_contain_text("partner: RT 1.9 · 40")
    root.locator(".dm-data summary").first.click()
    expect(root.locator(".dm-data").first).to_contain_text("200")
    assert not requests


@pytest.mark.parametrize("with_nl", [False, True])
def test_narrow_layout_and_sample_filter_keep_positions_together(
    review_page, tmp_path, with_nl
):
    page = review_page
    page.set_viewport_size({"width": 420, "height": 900})
    source = write_nl_review(tmp_path) if with_nl else write_review(tmp_path)
    output = tmp_path / "layout.html"
    generate_report(source, SelectionRequest(), output)
    page.goto(output.as_uri())
    root = page.locator(".dm-review")
    expect(
        root.get_by_role("heading", name="m/z 100 · 2 min", exact=True)
    ).to_be_visible()
    expect(root.get_by_label("顯示 RT 起點")).not_to_be_visible()
    expect(root.get_by_label("MS2 scan", exact=True)).not_to_be_visible()
    expect(root.get_by_label("保存的 Delta Mass 位置證據")).not_to_be_visible()
    expect(root.locator(".dm-xic svg")).to_be_visible()
    assert (
        root.locator(".dm-xic svg").evaluate("svg => svg.viewBox.baseVal.height") == 260
    )
    expect(page.locator(".scope-summary")).not_to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("textbox", name="本頁搜尋").fill("sample-b")
    expect(page.locator(".record-button")).to_have_count(1)
    expect(
        root.get_by_role("heading", name="m/z 200 · 2 min", exact=True)
    ).to_be_visible()


def test_missing_xic_and_failed_ms2_remain_unavailable_not_negative(
    review_page, tmp_path
):
    candidate = review_fixture().candidates[0]
    candidate = replace(
        candidate,
        positions=tuple(
            replace(p, raw_rt=(), raw_intensity=()) for p in candidate.positions
        ),
        status="rejected",
        reject_reason="saved rejection",
        spectra=(),
        ms2_read_errors=(ReviewReadError(11, (1.0, 3.0), "read failed"),),
    )
    source = tmp_path / "partial.json"
    write_delta_mass_review(DeltaMassReview("saved-discovery", (candidate,)), source)
    output = tmp_path / "partial.html"
    generate_report(source, SelectionRequest(), output)
    page = review_page
    page.goto(output.as_uri())
    root = page.locator(".dm-review")
    expect(root).to_contain_text("保存判定：rejected")
    expect(root).to_contain_text("沒有可用的 RAW XIC")
    expect(root).to_contain_text("third（無曲線）")
    expect(root.locator(".dm-xic polyline")).to_have_count(0)
    expect(root.locator(".dm-errors summary")).to_be_visible()
    root.locator(".dm-errors summary").click()
    expect(root.locator(".dm-errors")).to_contain_text("read failed")
