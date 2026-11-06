"""Candidate positions, RAW coordinates and MS2 scan identity in the real view."""

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
    "status,short,full",
    [
        ("no_match", "NL：未見支持", "未找到 NL 支持"),
        ("no_ms2", "NL：無相關 MS2", "沒有相關 MS2"),
    ],
)
def test_sidebar_collapses_equal_nl_results_but_keeps_detail_and_search(
    review_page, tmp_path, status, short, full
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
    expect(page.locator(".record-reason")).to_have_text(short)
    expect(
        page.get_by_label("各位置 NL 判讀").get_by_role(
            "cell", name=short.removeprefix("NL："), exact=True
        )
    ).to_have_count(1)
    expect(page.get_by_label("各位置 NL 判讀")).to_contain_text("M / partner / third")
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


def test_nl_result_opens_exact_support_without_scan_search(review_page, tmp_path):
    page = review_page
    source = write_nl_review(tmp_path)
    output = tmp_path / "nl.html"
    generate_report(source, SelectionRequest(), output)
    page.goto(output.as_uri())
    root = page.locator(".dm-review")
    expect(page.locator(".record-button").first).to_contain_text("M 支持")
    expect(root.get_by_label("各位置 NL 判讀")).to_contain_text("無相關 MS2")
    expect(root.get_by_label("各位置 NL 判讀")).to_contain_text("未見支持")
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
    expect(root.locator(".dm-xic [data-nl-boundary]")).to_have_count(2)
    root.get_by_role("button", name="下一筆支持").click()
    expect(root.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "22"
    )
    expect(root.locator(".dm-nl-measurement").last).to_contain_text("37.5%")
    # The browsing window is display-only, not a new NL assessment.
    root.get_by_text("詳細資料", exact=True).click()
    root.get_by_role("button", name="完整範圍", exact=True).click()
    expect(
        root.get_by_role("button", name="M · test-NL：有 NL 支持（2）", exact=True)
    ).to_be_visible()
    root.get_by_text("其他 MS2", exact=True).click()
    root.get_by_label("MS2 scan", exact=True).select_option("11")
    expect(root.locator("[data-nl-product-mz]")).to_have_count(0)
    expect(root).to_contain_text("手動檢閱的 MS2，不代表 NL 支持")
    page.get_by_role("button", name=re.compile("^sample-b")).click()
    expect(root.locator("[data-spectrum-scan]")).to_have_count(0)
    expect(root.get_by_label("各位置 NL 判讀")).to_contain_text("無相關 MS2")
    expect(root.get_by_role("button", name="下一筆支持")).not_to_be_visible()
    page.set_viewport_size({"width": 420, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


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
    expect(root.get_by_text("未平滑、未正規化", exact=False)).to_be_visible()
    expect(root.get_by_role("columnheader", name="Height / partner")).to_be_visible()
    expect(
        root.get_by_role("columnheader", name="Ratio 證據（不篩選）")
    ).to_be_visible()
    expect(root.get_by_role("cell", name="outside", exact=True)).to_be_visible()
    chart = root.locator(".dm-xic svg")
    expect(chart.locator("polyline")).to_have_count(3)
    assert chart.evaluate("""svg => [...svg.querySelectorAll('polyline')].map(line => {
        const d=svg.dataset, y=line.points[2].y;
        return Number(d.signalLow)+(Number(d.signalHigh)-Number(d.signalLow))*
            (Number(d.chartTop)+Number(d.chartHeight)-y)/Number(d.chartHeight);
    })""") == pytest.approx([100.0, 200.0, 300.0])
    original = chart.locator("polyline").first.get_attribute("points")
    root.get_by_label("顯示 RT 起點").fill("1.8")
    root.get_by_label("顯示 RT 終點").fill("2.2")
    root.get_by_role("button", name="套用範圍", exact=True).click()
    expect(chart).to_have_attribute("data-domain-start", "1.8")
    expect(chart).to_have_attribute("data-domain-end", "2.2")
    root.get_by_role("button", name="完整範圍").click()
    expect(chart.locator("polyline").first).to_have_attribute("points", original)
    chart.focus()
    chart.press("ArrowRight")
    expect(root.locator(".dm-readout").first).to_contain_text("partner: RT 1.9 · 40")
    root.locator(".dm-data summary").first.click()
    expect(root.locator(".dm-data").first).to_contain_text("200")
    assert not requests


def test_dense_ms2_markers_select_the_nearest_time_not_the_last_hitbox(
    review_page, tmp_path
):
    candidate = review_fixture().candidates[0]
    candidate = replace(
        candidate,
        spectra=tuple(
            replace(candidate.spectra[0], scan_number=number, rt_min=rt)
            for number, rt in ((11, 1.999), (22, 2.0), (33, 2.001))
        ),
    )
    source = tmp_path / "dense.json"
    write_delta_mass_review(DeltaMassReview("saved-discovery", (candidate,)), source)
    output = tmp_path / "dense.html"
    generate_report(source, SelectionRequest(), output)
    page = review_page
    page.goto(output.as_uri())
    chart = page.locator(".dm-xic svg")
    chart.wait_for()
    for number in (11, 22, 33):
        point = chart.evaluate(
            """(svg, number) => {
            const tick = svg.querySelector(`line[data-ms2-scan="${number}"]`);
            return new DOMPoint(+tick.getAttribute('x1'), +tick.getAttribute('y1') + 6)
                .matrixTransform(svg.getScreenCTM()).toJSON();
        }""",
            number,
        )
        page.mouse.click(point["x"], point["y"])
        expect(page.get_by_label("MS2 scan", exact=True)).to_have_value(str(number))
        expect(page.locator("[data-spectrum-scan]")).to_have_attribute(
            "data-spectrum-scan", str(number)
        )


def test_ms2_scan_selection_does_not_attribute_time_context_to_the_candidate(
    review_page, tmp_path
):
    page = review_page
    root = _open(page, tmp_path)
    expect(root).to_contain_text("未判定與候選的前驅關係")
    scans = root.get_by_label("MS2 scan", exact=True)
    scans.select_option("11")
    expect(root.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "11"
    )
    expect(root).to_contain_text("記錄的 precursor m/z 999")
    expect(root).to_contain_text("charge 2")
    spectrum = root.locator("[data-spectrum-scan]")
    spectrum.focus()
    spectrum.press("ArrowRight")
    expect(root.locator(".dm-readout").last).to_have_text("m/z 80 · 原始強度 100")
    marker = root.locator('.dm-xic rect[data-ms2-scan="22"]')
    marker.click()
    expect(scans).to_have_value("22")
    expect(root.locator("[data-spectrum-scan]")).to_have_attribute(
        "data-spectrum-scan", "22"
    )
    expect(root).to_contain_text("記錄的 precursor m/z 100")
    root.get_by_label("顯示 RT 起點").fill("1")
    root.get_by_label("顯示 RT 終點").fill("1.5")
    root.get_by_role("button", name="套用範圍", exact=True).click()
    expect(scans).to_be_disabled()
    expect(root.locator("[data-spectrum-scan]")).to_have_count(0)
    expect(root).to_contain_text("此時間窗沒有 MS2")
    root.get_by_role("button", name="完整範圍").click()
    expect(scans).to_be_enabled()
    page.get_by_role("button", name=re.compile("^sample-b")).click()
    expect(root).to_contain_text("sample-b")
    expect(scans).to_be_disabled()
    expect(root.locator("[data-spectrum-scan]")).to_have_count(0)


def test_narrow_layout_and_sample_filter_keep_positions_together(review_page, tmp_path):
    page = review_page
    page.set_viewport_size({"width": 420, "height": 900})
    root = _open(page, tmp_path)
    expect(root.get_by_role("heading", name="Three positions")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("textbox", name="本頁搜尋").fill("sample-b")
    expect(page.locator(".record-button")).to_have_count(1)
    expect(root).to_contain_text("sample-b")


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
    expect(root).to_contain_text("沒有可用 spectrum；請查看讀取錯誤")
    root.locator(".dm-errors summary").click()
    expect(root.locator(".dm-errors")).to_contain_text("read failed")
