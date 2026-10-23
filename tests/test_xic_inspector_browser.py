"""Exercise the shared review view in multiple roots and server-free reports."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from importlib.resources import files

import pytest
from playwright.sync_api import expect

from tests.test_workbench_browser import browser as browser
from tests.test_xic_inspector_curve_bundle import _fixture_bundle
from tests.test_xic_inspector_targeted_workbook import write_rare_detection_workbook
from xic_inspector.api import SelectionRequest, prepare_report
from xic_inspector.rendering.html_builder import build_report_html
from xic_inspector.sources.curve_bundle import (
    CurveBundle,
    SampleQuantification,
    write_curve_bundle,
)

pytestmark = pytest.mark.browser_e2e
pytest_plugins = ("tests.browser_diagnostics",)


def _curve_source(tmp_path):
    transition = replace(
        _fixture_bundle().transitions[0],
        quantifications=(
            SampleQuantification("sample-1", 17.25, "counts·s"),
            SampleQuantification("sample-2", None, "counts·s"),
        ),
    )
    path = tmp_path / "saved-curves.json"
    write_curve_bundle(CurveBundle((transition,)), path)
    return path


def _load_view_assets(page):
    assets = files("xic_inspector.rendering").joinpath("assets")
    page.add_style_tag(content=assets.joinpath("inspector.css").read_text("utf-8"))
    page.add_script_tag(content=assets.joinpath("inspector.js").read_text("utf-8"))


def _control(root, name):
    return root.locator(f'[data-inspector-id="{name}"]')


def _targeted_rows(root):
    root.get_by_role("button", name=re.compile(r"^全部")).click()
    return root.locator(".tgt-table tbody tr:not([hidden])")


def test_multiple_views_keep_controls_theme_dialogs_and_lifetime_local(
    review_page, tmp_path
):
    page = review_page
    payload = prepare_report(_curve_source(tmp_path), SelectionRequest()).payload
    page.set_content(
        "<style>body{margin:17px}#host-button{padding:13px;font-size:19px}</style>"
        '<button id="host-button">Host action</button>'
        '<div id="left"></div><div id="right"></div>'
        '<dialog id="host-dialog"></dialog>'
    )
    host_style = page.locator("#host-button").evaluate(
        "node => [getComputedStyle(document.body).margin, "
        "getComputedStyle(node).padding, getComputedStyle(node).fontSize]"
    )
    _load_view_assets(page)
    page.evaluate(
        """payload => {
            window.leftView = XicInspector.mount(
                document.getElementById('left'), payload, {mode:'curves'});
            window.rightView = XicInspector.mount(
                document.getElementById('right'), payload, {mode:'curves'});
        }""",
        payload,
    )
    left, right = page.locator("#left"), page.locator("#right")
    expect(_control(left, "svg-raw").locator("polyline")).to_have_count(2)
    expect(_control(right, "svg-raw").locator("polyline")).to_have_count(2)
    right.locator('[data-sample-visible="sample-2"]').uncheck()
    expect(_control(right, "svg-raw").locator("polyline")).to_have_count(1)
    expect(_control(left, "svg-raw").locator("polyline")).to_have_count(2)
    assert (
        page.locator("#host-button").evaluate(
            "node => [getComputedStyle(document.body).margin, "
            "getComputedStyle(node).padding, getComputedStyle(node).fontSize]"
        )
        == host_style
    )
    assert page.evaluate(
        """() => {
            const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
            return ids.length === new Set(ids).size;
        }"""
    )
    assert page.evaluate(
        r"""() => [...document.querySelectorAll('.xic-inspector [clip-path]')]
          .every(node => {
            const id = node.getAttribute('clip-path').match(/^url\(#(.+)\)$/)?.[1];
            return id && node.closest('.xic-inspector').contains(
                document.getElementById(id));
        })"""
    )
    ink = left.locator(".xic-inspector").evaluate(
        "node => getComputedStyle(node).getPropertyValue('--ink')"
    )
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    assert (
        left.locator(".xic-inspector").evaluate(
            "node => getComputedStyle(node).getPropertyValue('--ink')"
        )
        != ink
    )

    # A renderer dialog must close itself, not the workbench's surrounding drawer.
    page.evaluate(
        """() => {
            const host = document.getElementById('host-dialog');
            host.append(document.getElementById('left'));
            window.hostCloses = 0;
            host.addEventListener('close', () => window.hostCloses++);
            host.showModal();
        }"""
    )
    left.locator('[data-zoom="raw"]').click()
    expect(_control(left, "zoom-dialog")).to_be_visible()
    expect(_control(right, "zoom-dialog")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(_control(left, "zoom-dialog")).to_be_hidden()
    expect(page.locator("#host-dialog")).to_be_visible()
    expect(left.locator('[data-zoom="raw"]')).to_be_focused()
    assert page.evaluate("window.hostCloses") == 0
    page.evaluate("document.getElementById('host-dialog').close()")

    page.evaluate(
        """payload => {
            document.body.append(document.getElementById('left'));
            const previous = window.leftView;
            previous.destroy();
            window.leftView = XicInspector.mount(
                document.getElementById('left'), payload, {mode:'curves'});
            previous.destroy();
        }""",
        payload,
    )
    expect(left.locator(".xic-inspector")).to_have_count(1)
    expect(_control(left, "svg-raw").locator("polyline")).to_have_count(2)
    expect(_control(right, "svg-raw").locator("polyline")).to_have_count(1)
    page.evaluate("window.leftView.destroy(); window.rightView.destroy()")
    expect(page.locator(".xic-inspector")).to_have_count(0)
    expect(page.locator("#host-button")).to_be_visible()


def test_targeted_hosts_share_saved_values_but_only_workbench_can_request_xic(
    review_page, tmp_path
):
    page = review_page
    source = write_rare_detection_workbook(tmp_path / "saved.xlsx")
    prepared = prepare_report(
        source, SelectionRequest(strategy="query", target_ids=("Analyte",))
    )
    page.set_content('<div id="online"></div><div id="offline"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => {
            window.requests = [];
            const onRequestCurves = ({item_id, sample_id, opener}) =>
                window.requests.push({
                    item_id, sample_id, label: opener.getAttribute('aria-label'),
                });
            window.exclusions = [];
            const onRequestExclusion = ({sample_id}) =>
                window.exclusions.push(sample_id);
            window.online = XicInspector.mount(
                document.getElementById('online'), payload,
                {mode:'result', onRequestCurves, onRequestExclusion});
            // An offline report has no host to write to, whatever is passed.
            XicInspector.mount(
                document.getElementById('offline'), payload,
                {mode:'offline', onRequestExclusion});
        }""",
        prepared.payload,
    )
    online, offline = page.locator("#online"), page.locator("#offline")
    online_rows, offline_rows = _targeted_rows(online), _targeted_rows(offline)
    expect(online_rows).to_have_count(5)
    expect(offline_rows).to_have_count(5)
    for index in (0, 2):
        saved = offline_rows.nth(index).locator("td").all_text_contents()
        assert online_rows.nth(index).locator("td").all_text_contents()[:-1] == saved
    expect(online_rows.nth(0).locator("td").nth(2)).to_have_text("ND")
    expect(online_rows.nth(2).locator("td").nth(2)).to_have_text("5.55E+01")
    expect(online_rows.nth(2).locator("td").nth(4)).to_have_text("9.2")
    expect(offline.locator(".tgt-xic")).to_have_count(0)
    expect(offline.locator(".tgt-exclude")).to_have_count(0)
    online.get_by_role("button", name="標記排除 S2", exact=True).click()
    assert page.evaluate("window.exclusions") == ["S2"]
    online.get_by_role("button", name="看 S3 的 Analyte XIC", exact=True).click()
    assert page.evaluate("window.requests") == [
        {
            "item_id": prepared.payload["items"][0]["id"],
            "sample_id": "S3",
            "label": "看 S3 的 Analyte XIC",
        }
    ]


@pytest.mark.parametrize("source_kind", ["curves", "targeted"])
def test_cli_report_interacts_offline_without_server_or_network(
    review_page, tmp_path, source_kind
):
    source = (
        _curve_source(tmp_path)
        if source_kind == "curves"
        else write_rare_detection_workbook(tmp_path / "saved.xlsx")
    )
    original = source.read_bytes()
    output = tmp_path / "review.html"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xic_inspector",
            str(source),
            "--output",
            str(output),
            "--no-open",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    page = review_page
    network_requests = []
    page.on(
        "request",
        lambda request: (
            network_requests.append(request.url)
            if request.url.startswith(("http://", "https://"))
            else None
        ),
    )
    page.route(re.compile(r"^https?://"), lambda route: route.abort())
    page.goto(output.as_uri())
    root = page.locator("#inspector-root")
    if source_kind == "curves":
        expect(
            root.get_by_role("heading", name="Inspector", exact=True)
        ).to_be_visible()
        expect(_control(root, "svg-raw").locator("polyline")).to_have_count(2)
        _control(root, "search").fill("no-such-evidence")
        expect(_control(root, "record-list").locator(".record-button")).to_have_count(0)
        _control(root, "search").fill("")
        expect(_control(root, "record-list").locator(".record-button")).to_have_count(1)
        root.get_by_role("checkbox", name="在圖中顯示 sample-two", exact=True).uncheck()
        expect(_control(root, "svg-raw").locator("polyline")).to_have_count(1)
        root.locator('[data-zoom="raw"]').click()
        expect(_control(root, "zoom-dialog")).to_be_visible()
        _control(root, "zoom-close").click()
        _control(root, "btn-report-info").click()
        expect(_control(root, "report-info-dialog")).to_contain_text(source.name)
        _control(root, "report-info-close").click()
    else:
        expect(root.get_by_role("heading", name="Targeted 結果")).to_be_visible()
        analyte = root.locator(".tgt-review > section").filter(
            has=page.get_by_role("heading", name="Analyte", exact=True)
        )
        rows = _targeted_rows(analyte)
        expect(rows).to_have_count(5)
        expect(rows.nth(0).locator("td").nth(2)).to_have_text("ND")
        expect(rows.nth(2).locator("td").nth(2)).to_have_text("5.55E+01")
        rows.nth(2).locator(".tgt-sample").click()
        expect(analyte.locator(".tgt-detail-row")).to_contain_text("積分範圍")
        expect(root.locator(".tgt-xic")).to_have_count(0)
    assert not network_requests
    assert source.read_bytes() == original


def _signal_payload():
    """Items whose raw and smoothed curves differ; the last retains raw only."""

    def item(index, *, smoothed=True):
        rows, curves = [], []
        for sample in ("S1", "S2"):
            ref = f"curve-{index}-{sample}"
            rows.append(
                {
                    "sample_id": sample,
                    "support_origin": {"kind": "unavailable", "capture_refs": [ref]},
                }
            )
            processed = [{"kind": "baseline", "intensities": [0.0, 1.0, 0.0]}]
            if smoothed:
                processed.append({"kind": "smoothed", "intensities": [1.0, 6.0, 1.0]})
            curves.append(
                {
                    "sample_id": sample,
                    "evidence_ref": ref,
                    "raw_rts": [1.0, 1.1, 1.2],
                    "raw_intensities": [0.0, 10.0 + index, 0.0],
                    "processed_curves": processed,
                }
            )
        return {
            "id": f"item-{index}",
            "kind": "group",
            "title": f"Item {index}",
            "samples": rows,
            "curves": curves,
        }

    return {
        "samples": [
            {"id": "S1", "label": "Sample 1"},
            {"id": "S2", "label": "Sample 2"},
        ],
        "items": [item(1), item(2), item(3, smoothed=False)],
    }


def _drawn_kinds(root):
    return set(
        _control(root, "svg-raw")
        .locator("polyline")
        .evaluate_all("nodes => nodes.map(node => node.dataset.kind)")
    )


def test_offline_signal_choice_applies_to_every_item_and_view(review_page, tmp_path):
    payload = _signal_payload()
    output = tmp_path / "signals.html"
    output.write_text(
        build_report_html(
            title="Signals",
            evidence_capability="retained_curves",
            strategy="sample",
            request_id="signals",
            run_total=3,
            eligible_count=3,
            selected_count=3,
            source_db_name="signals.json",
            payload_data=payload,
        ),
        encoding="utf-8",
    )
    page = review_page
    page.goto(output.as_uri())
    root = page.locator("#inspector-root")
    switch = root.get_by_role("group", name=re.compile("曲線訊號"))
    raw, smoothed = (
        switch.get_by_role("button", name="原始", exact=True),
        switch.get_by_role("button", name="平滑", exact=True),
    )
    lanes = root.locator(".lane-raw")
    expect(smoothed).to_have_attribute("aria-pressed", "true")
    assert _drawn_kinds(root) == {"smoothed"}

    raw.click()
    expect(raw).to_have_attribute("aria-pressed", "true")
    assert _drawn_kinds(root) == {"raw"}
    expect(lanes.first).to_have_attribute("data-kind", "raw")
    expect(root.locator(".plot-card h4").first).to_contain_text("原始訊號")
    root.get_by_role("checkbox", name="疊加基線").check()
    assert _drawn_kinds(root) == {"raw", "baseline"}

    for title in ("Item 2", "Item 1"):
        root.locator(".record-button", has_text=title).click()
        expect(raw).to_have_attribute("aria-pressed", "true")
        assert _drawn_kinds(root) == {"raw", "baseline"}
        expect(lanes.first).to_have_attribute("data-kind", "raw")
    root.locator('[data-focus="S1"]').click()
    assert _drawn_kinds(root) == {"raw", "baseline"}

    root.locator(".record-button", has_text="Item 3").click()
    expect(raw).to_have_attribute("aria-pressed", "true")
    assert _drawn_kinds(root) == {"raw", "baseline"}
    smoothed.click()
    expect(smoothed).to_have_attribute("aria-pressed", "true")
    expect(_control(root, "svg-raw").locator("polyline")).to_have_count(0)
    expect(_control(root, "svg-raw")).to_contain_text("未保存平滑訊號")
    expect(lanes).to_have_count(0)
    expect(root.locator(".lane-missing").first).to_contain_text("未保存平滑")
    root.locator(".record-button", has_text="Item 2").click()
    assert _drawn_kinds(root) == {"smoothed", "baseline"}


def test_hosted_views_share_signal_choice_across_remounts(review_page):
    page = review_page
    page.set_content('<div id="left"></div><div id="right"></div>')
    _load_view_assets(page)
    payload = _signal_payload()
    page.evaluate(
        """payload => {
            window.views = ['left', 'right'].map(id => XicInspector.mount(
                document.getElementById(id), payload, {mode:'curves'}));
        }""",
        payload,
    )
    left, right = page.locator("#left"), page.locator("#right")
    left.get_by_role("button", name="原始", exact=True).click()
    assert _drawn_kinds(left) == _drawn_kinds(right) == {"raw"}
    expect(right.get_by_role("button", name="原始", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    # A host replaces the view when the reviewer opens another group.
    page.evaluate(
        """payload => {
            window.views[0].destroy();
            window.views[0] = XicInspector.mount(
                document.getElementById('left'), payload, {mode:'curves'});
        }""",
        payload,
    )
    assert _drawn_kinds(left) == {"raw"}
    expect(left.get_by_role("button", name="原始", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )


@pytest.fixture
def paired_curve_payload():
    rts = [0.0, 8.9, 9.0, 9.1, 12.0]
    curves = []
    for sample in ("S1", "S2"):
        for label, scale, mz in (("Analyte", 1, 258.1085), ("ISTD", 1000, 261.127)):
            impulse = sample == "S1" and label == "Analyte"
            curves.append(
                {
                    "sample_id": sample,
                    "raw_rts": rts,
                    "raw_intensities": [0, 0, 13000, 0, 0]
                    if impulse
                    else [0, scale, 10 * scale, scale, 0],
                    "processed_curves": [
                        {
                            "kind": "smoothed",
                            "rts": rts,
                            "intensities": [0, 20, 100, 20, 0]
                            if impulse
                            else [0, scale, 10 * scale, scale, 0],
                        }
                    ],
                    "provenance": {"mz": mz, "ppm": 5},
                    "apex_rt": None,
                    "quality_flags": {
                        "target_label": label,
                        "paired_istd": label == "ISTD",
                        "saved_area": "ND" if impulse else 55.5 * scale,
                        "reference_support_failures": [
                            "insufficient_original_xic_support"
                        ]
                        if impulse
                        else [],
                    },
                }
            )
    return {
        "items": [
            {
                "id": "Analyte",
                "title": "Analyte",
                "review_rt_window": [8.5, 9.5],
                "curves": curves,
            }
        ]
    }


def test_targeted_curve_rendering_preserves_raw_spikes_and_independent_scales(
    review_page, paired_curve_payload
):
    page = review_page
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => XicInspector.mount(
        document.getElementById('host'), payload, {mode:'curves'})""",
        paired_curve_payload,
    )
    review = page.locator("#host")
    expect(review.locator(".tx-chart-title")).to_have_text(["Analyte", "ISTD"])
    expect(review.locator(".tx-chart-meta").first).to_contain_text("面積 ND")
    expect(review.locator(".tx-support-note")).to_contain_text("原始訊號支撐不足")
    expect(review.locator(".tx-support-note")).to_contain_text("本次讀取 XIC")
    raw = review.locator(".tx-chart").first.locator('circle[data-kind="raw"]')
    assert (
        sum(
            not value.endswith("· 0")
            for value in raw.locator("title").all_text_contents()
        )
        == 1
    )
    # The raw impulse is much taller than its smoothed peak, but still fits.
    assert raw.evaluate_all("""points => points.every(point => {
        const svg = point.ownerSVGElement;
        const y = point.cy.baseVal.value;
        return y >= 0 && y <= svg.viewBox.baseVal.height;
    })""")
    expect(review.locator('polyline[data-kind="smoothed"]')).to_have_count(2)
    review.get_by_label("樣本", exact=True).select_option("S2")
    expect(review.locator(".tx-support-note")).to_have_count(0)
    lines = review.locator('polyline[data-kind="smoothed"]')
    ys = lines.evaluate_all("""lines => lines.map(line =>
        [...line.points].map(point => point.y))""")
    # The same shape spans the same height despite a 1000-fold ion intensity gap.
    assert ys[0] == ys[1]
    assert max(ys[0]) - min(ys[0]) > 100


def test_targeted_curve_range_changes_and_restores_the_drawn_points(
    review_page, paired_curve_payload
):
    page = review_page
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => XicInspector.mount(
        document.getElementById('host'), payload, {mode:'curves'})""",
        paired_curve_payload,
    )
    review = page.locator("#host")
    line = review.locator('polyline[data-kind="smoothed"]').first
    original_points = line.get_attribute("points")
    review.get_by_label("顯示 RT 起點", exact=True).fill("8.95")
    review.get_by_label("顯示 RT 終點", exact=True).fill("9.05")
    review.get_by_role("button", name="套用範圍", exact=True).click()
    assert line.get_attribute("points") != original_points
    review.get_by_role("button", name="完整 RT", exact=True).click()
    expect(review.get_by_label("顯示 RT 起點", exact=True)).to_have_value("0")
    expect(review.get_by_label("顯示 RT 終點", exact=True)).to_have_value("12")
    review.get_by_role("button", name="峰附近", exact=True).click()
    expect(line).to_have_attribute("points", original_points)


def test_targeted_rows_keep_rare_and_neutral_loss_evidence_in_place(
    review_page, tmp_path
):
    from openpyxl import load_workbook

    from tests.test_xic_inspector_targeted_workbook import (
        write_rare_detection_workbook,
    )

    page = review_page
    workbook = write_rare_detection_workbook(tmp_path / "rare.xlsx")
    # Current Targeted workbooks store one expected RT as equal RT min/max.
    book = load_workbook(workbook)
    book["Targets"]["E2"] = book["Targets"]["F2"] = 9.25
    results = book["XIC Results"]
    results.cell(2, 8, "峰不在內標位置，報 ND")
    nl_column = results.max_column + 1
    results.cell(1, nl_column, "NL")
    results.cell(1, nl_column + 1, "NL Evidence")
    for row in range(2, results.max_row + 1):
        # Only S1's Analyte (row 2) observed its neutral loss.
        observed = row == 2
        results.cell(row, nl_column, "OK" if observed else "NO_MS2")
        results.cell(
            row, nl_column + 1, "已觀察到" if observed else "所選峰沒有對應的 MS2"
        )
    book.save(workbook)
    book.close()
    payload = prepare_report(
        workbook, SelectionRequest(strategy="query", target_ids=("Analyte",))
    ).payload
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => XicInspector.mount(
        document.getElementById('host'), payload, {mode:'result'})""",
        payload,
    )
    review = page.locator("#host")
    expect(review.locator(".tgt-context")).to_contain_text("預期 RT 9.25 min")
    expect(review.locator(".tgt-reason").first).to_contain_text("排除原因未記錄")
    rows = review.locator(".tgt-table tbody tr")
    # Rows stay in run order; the rare detection is marked where it ran.
    expect(rows.nth(2).locator(".tgt-sample")).to_have_text("S3")
    expect(rows.nth(2).locator(".tgt-badge")).to_have_text("與多數不同")
    # The same sample's paired ISTD reads in the same row.
    expect(rows.nth(2).locator(".tgt-istd").first).to_have_text("9.2")
    rows.nth(2).locator(".tgt-sample").click()
    # The evidence is the next row, not a panel after every other sample.
    detail = review.locator(".tgt-table tbody tr:nth-child(4)")
    expect(detail).to_have_class("tgt-detail-row")
    expect(detail).to_contain_text("積分範圍")
    # A neutral loss that was not observed is a given fact under CID: it stays
    # with the selection record instead of the first layer.
    expect(detail.locator(".tgt-facts")).not_to_contain_text("NL 證據")
    expect(detail.locator(".tgt-raw")).to_contain_text("NL 證據：所選峰沒有對應的 MS2")
    expect(detail).not_to_contain_text("配對 ISTD（ISTD）")
    expect(detail).not_to_contain_text("檢閱註記")
    # An observed neutral loss supports the result, so it reads up front.
    rows.nth(0).locator(".tgt-sample").click()
    observed = review.locator(".tgt-table tbody tr:nth-child(2)")
    expect(observed).to_have_class("tgt-detail-row")
    expect(observed.locator(".tgt-facts")).to_contain_text("NL 證據")
    expect(observed.locator(".tgt-facts")).to_contain_text("已觀察到")
