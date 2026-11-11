"""Exercise the shared review view in multiple roots and server-free reports."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from importlib.resources import files

import pytest
from playwright.sync_api import expect

from tests.browser_runtime import browser as browser
from tests.xic_inspector_curve_bundle_fixtures import _fixture_bundle
from tests.xic_inspector_html_fixtures import build_origin_curve_payload
from tests.xic_inspector_targeted_workbook_fixtures import write_rare_detection_workbook
from xic_inspector.api import SelectionRequest, prepare_report
from xic_inspector.rendering.html_builder import build_report_html
from xic_inspector.rendering.template import INSPECTOR_JS
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
    page.add_script_tag(content=INSPECTOR_JS)


def _control(root, name):
    return root.locator(f'[data-inspector-id="{name}"]')


def _sample_sidebar_payload():
    groups = ("Benign", "Normal", "Tumor", "QC")
    samples, curves, rows = [], [], []
    for index in range(8):
        sid = f"sample-{index}"
        samples.append(
            {
                "id": sid,
                "label": f"long_shared_sample_filename_{index}_QC{index}",
                "group": groups[index // 2],
            }
        )
        rows.append({"sample_id": sid, "quantitative_value": index + 10})
        curves.append(
            {
                "sample_id": sid,
                "raw_rts": [1, 1.25, 1.5, 1.75, 2],
                "raw_intensities": [0, 10, 100 + 20 * index, 10, 0],
                "processed_curves": [
                    {
                        "kind": "smoothed",
                        "rts": [1, 1.25, 1.5, 1.75, 2],
                        "intensities": [0, 20, 80 + 15 * index, 20, 0],
                    }
                ],
                "quality_flags": {
                    "new_diagnostic_extraction": True,
                    "acquired_at_group_position": index % 2 == 0,
                },
            }
        )
    missing = {"id": "without-curve", "label": "missing_sample", "group": "Tumor"}
    return {
        "samples": samples,
        "items": [
            {
                "id": "feature",
                "kind": "group",
                "title": "saved group",
                "samples": rows,
                "curves": curves,
                "review_rt_window": [1, 2],
            }
        ],
    }, samples + [missing]


def test_workbench_curve_plot_uses_saved_aligned_coordinates_and_labels_raw_fallback(
    review_page,
):
    page = review_page
    payload, catalog = _sample_sidebar_payload()
    payload["summary"] = {"sample_rt_calibration": {"schema": "sample_rt_calibration"}}
    for index, curve in enumerate(payload["items"][0]["curves"]):
        shift = index + 10 if index < 7 else 0
        curve["aligned_rts"] = [rt + shift for rt in curve["raw_rts"]]
        curve["rt_alignment_methods"] = ["loess" if index < 7 else "raw_fallback"]
        curve["rt_alignment_label"] = (
            "校正 RT：loess" if index < 7 else "無 RT 估計；顯示原始 RT"
        )
        for processed in curve["processed_curves"]:
            processed["aligned_rts"] = [rt + shift for rt in processed["rts"]]
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """({payload,catalog})=>XicInspector.mount(
        document.getElementById('host'),payload,{mode:'curves',samples:catalog})""",
        {"payload": payload, "catalog": catalog},
    )
    root = page.locator("#host")
    chart = _control(root, "svg-raw").locator("svg")
    expect(chart).to_have_attribute("aria-label", "對齊時間層析圖")
    expect(root).to_contain_text(
        "無 RT 估計，顯示原始 RT：long_shared_sample_filename_7_QC7"
    )
    plotted = chart.evaluate("""svg=>Object.fromEntries(
        [...svg.querySelectorAll('polyline')].map(line=>{
          const d=svg.dataset,x=line.points[2].x;
          return [line.dataset.sampleId,+d.domainStart+
            (x-Number(d.chartLeft))/Number(d.chartWidth)*
            (Number(d.domainEnd)-Number(d.domainStart))];
        }))""")
    assert plotted == pytest.approx(
        {f"sample-{i}": 11.5 + i if i < 7 else 1.5 for i in range(8)}
    )
    root.get_by_role("button", name="聚焦 long_shared_sample_filename_0_QC0").hover()
    expect(root.get_by_role("tooltip")).to_contain_text("校正 RT：loess")
    root.get_by_role("button", name="放大層析圖").click()
    expect(_control(root, "zoom-title")).to_contain_text("對齊時間")
    expect(_control(root, "zoom-container").locator("svg")).to_have_attribute(
        "aria-label", "對齊時間層析圖"
    )
    _control(root, "zoom-close").click()
    root.get_by_label("顯示 RT 起點").fill("11")
    root.get_by_label("顯示 RT 終點").fill("14")
    root.get_by_role("button", name="套用範圍").click()
    expect(chart).to_have_attribute("data-domain-start", "11")
    expect(chart).to_have_attribute("data-domain-end", "14")


def test_inspector_reads_injection_order_from_final_calibration_summary(review_page):
    payload = build_origin_curve_payload()
    payload["summary"] = {
        "sample_rt_calibration": None,
        "injection_order": {"Sample 0": 8, "Sample 1": 3, "Sample 2": 6},
    }
    item = payload["items"][0]
    item["relationship_members"] = [
        {"subject_id": f"M{i}", "sample_id": f"S{i}", "sample_label": f"Sample {i}"}
        for i in range(3)
    ]
    page = review_page
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload=>XicInspector.mount(document.getElementById('host'),payload)", payload
    )
    lanes = page.locator("#host .relationship-lane")
    expect(lanes).to_have_count(3)
    assert lanes.evaluate_all("nodes=>nodes.map(n=>n.dataset.laneMember)") == [
        "M1",
        "M2",
        "M0",
    ]
    assert lanes.evaluate_all("nodes=>nodes.map(n=>n.dataset.injectionOrder)") == [
        "3",
        "6",
        "8",
    ]
    expect(page.locator("#host .relationship-lane .injection-order")).to_have_text(
        ["03", "06", "08"]
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_curve_sidebar_groups_are_additive_and_collapse_does_not_hide_curves(
    review_page, theme
):
    from itertools import combinations

    page = review_page
    payload, catalog = _sample_sidebar_payload()
    page.set_content('<div id="host"></div>')
    page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
    _load_view_assets(page)
    page.evaluate(
        """({payload,catalog})=>XicInspector.mount(
        document.getElementById('host'),payload,{mode:'curves',samples:catalog})""",
        {"payload": payload, "catalog": catalog},
    )
    root = page.locator("#host")
    lines = _control(root, "svg-raw").locator("polyline[data-sample-id]")
    expect(lines).to_have_count(8)
    for pair in combinations(("Benign", "Normal", "Tumor", "QC"), 2):
        root.get_by_role("button", name="全部隱藏", exact=True).click()
        for group in pair:
            root.get_by_role("checkbox", name=f"顯示 {group} 整組", exact=True).check()
        expect(lines).to_have_count(4)
        assert set(
            lines.evaluate_all("lines=>lines.map(line=>line.dataset.sampleId)")
        ) == {
            sample["id"]
            for sample in catalog
            if sample.get("group") in pair and sample["id"] != "without-curve"
        }
    root.get_by_role("button", name="全部顯示", exact=True).click()
    root.get_by_role("button", name="收合 Benign 組別", exact=True).click()
    expect(lines).to_have_count(8)
    root.get_by_role("checkbox", name="顯示 Benign 整組", exact=True).uncheck()
    expect(lines).to_have_count(6)
    root.get_by_role("checkbox", name="顯示 Benign 整組", exact=True).check()
    expect(
        root.get_by_role("button", name="展開 Benign 組別", exact=True)
    ).to_be_visible()
    root.get_by_role("button", name="展開 Benign 組別", exact=True).click()
    root.get_by_role(
        "checkbox", name=f"在圖中顯示 {catalog[0]['label']}", exact=True
    ).uncheck()
    expect(lines).to_have_count(7)
    expect(
        root.get_by_role("checkbox", name="顯示 Benign 整組", exact=True)
    ).to_have_attribute("aria-checked", "mixed")
    expect(
        root.get_by_role("checkbox", name="在圖中顯示 missing_sample", exact=True)
    ).to_be_disabled()
    root.get_by_role("searchbox", name="搜尋樣本名稱").fill("QC7")
    expect(root.locator(".curve-sample-row")).to_have_count(1)
    expect(lines).to_have_count(7)
    root.get_by_role("checkbox", name="顯示 Benign 整組", exact=True).check()
    expect(lines).to_have_count(8)
    root.get_by_role("searchbox", name="搜尋樣本名稱").fill("")
    expect(root.locator(".curve-sample-row")).to_have_count(9)
    assert (
        page.evaluate(
            "() => document.querySelector('#host').querySelectorAll('polyline').length"
        )
        == 8
    )


def test_curve_sidebar_focus_names_and_colours_stay_bound_to_each_sample(review_page):
    page = review_page
    payload, catalog = _sample_sidebar_payload()
    page.set_content('<div id="host"></div><div id="narrow"></div>')
    _load_view_assets(page)
    page.evaluate(
        """({payload,catalog})=>{
        XicInspector.mount(document.getElementById('host'),payload,{mode:'curves',samples:catalog});
        const subset=structuredClone(payload);subset.samples=subset.samples.slice(3,5);
        subset.items[0].curves=subset.items[0].curves.slice(3,5);
        subset.items[0].samples=subset.items[0].samples.slice(3,5);
        XicInspector.mount(document.getElementById('narrow'),subset,{mode:'curves',samples:catalog,sampleIds:subset.samples.map(s=>s.id)});
    }""",
        {"payload": payload, "catalog": catalog},
    )
    root = page.locator("#host")
    line = _control(root, "svg-raw").locator('polyline[data-sample-id="sample-3"]')
    other = _control(page.locator("#narrow"), "svg-raw").locator(
        'polyline[data-sample-id="sample-3"]'
    )
    assert line.evaluate("line=>getComputedStyle(line).stroke") == other.evaluate(
        "line=>getComputedStyle(line).stroke"
    )
    row = root.locator('.curve-sample-row[data-sample-id="sample-3"]')
    assert row.locator(".curve-swatch line").evaluate(
        "line=>getComputedStyle(line).stroke"
    ) == line.evaluate("line=>getComputedStyle(line).stroke")
    button = root.get_by_role("button", name=f"聚焦 {catalog[3]['label']}", exact=True)
    button.hover()
    expect(line).to_have_css("opacity", "1")
    expect(
        _control(root, "svg-raw").locator('polyline[data-sample-id="sample-4"]')
    ).to_have_css("opacity", "0.15")
    button.click()
    page.mouse.move(5, 5)
    expect(button).to_have_attribute("aria-pressed", "true")
    expect(root.get_by_role("button", name="解除聚焦", exact=True)).to_be_visible()
    root.get_by_role("button", name="解除聚焦", exact=True).click()
    expect(_control(root, "svg-raw").locator("polyline")).to_have_count(8)
    name = row.locator(".curve-sample-name")
    assert name.evaluate("name=>getComputedStyle(name).whiteSpace") == "nowrap"
    expect(row.locator(".curve-name-tail")).to_contain_text("QC3")
    button.focus()
    expect(root.get_by_role("tooltip")).to_contain_text(catalog[3]["label"])
    # Moving onto an actual plotted point identifies and pins its sample.
    fourth = _control(root, "svg-raw").locator('polyline[data-sample-id="sample-4"]')
    point = fourth.evaluate("""line=>{
        const svg=line.ownerSVGElement, point=svg.createSVGPoint();
        point.x=line.points[2].x;point.y=line.points[2].y;
        const screen=point.matrixTransform(svg.getScreenCTM());
        return {x:screen.x,y:screen.y};
    }""")
    page.mouse.move(point["x"], point["y"])
    expect(root.get_by_role("tooltip")).to_contain_text(catalog[4]["label"])
    expect(root.get_by_role("tooltip")).to_contain_text("依群組位置擷取")
    expect(line).to_have_css("opacity", "0.15")
    page.mouse.click(point["x"], point["y"])
    page.mouse.move(5, 5)
    pinned = root.get_by_role("button", name=f"聚焦 {catalog[4]['label']}", exact=True)
    expect(pinned).to_have_attribute("aria-pressed", "true")
    root.get_by_role("button", name="原始", exact=True).click()
    expect(root.get_by_role("button", name="原始", exact=True)).to_be_focused()
    expect(pinned).to_have_attribute("aria-pressed", "true")
    expect(_control(root, "svg-raw").locator("polyline")).to_have_count(8)
    expect(fourth).to_have_attribute("data-kind", "raw")
    root.get_by_role(
        "checkbox", name=f"在圖中顯示 {catalog[4]['label']}", exact=True
    ).uncheck()
    expect(pinned).to_have_attribute("aria-pressed", "false")
    expect(root.get_by_role("button", name="解除聚焦", exact=True)).to_be_hidden()
    # Main and enlarged charts must not share a clip-path ID or clip width.
    button.click()
    root.locator('[data-zoom="raw"]').click()
    enlarged = _control(root, "zoom-container").locator(
        'polyline[data-sample-id="sample-3"]'
    )
    expect(enlarged).to_have_css("opacity", "1")
    point = enlarged.evaluate("""line=>{
        const svg=line.ownerSVGElement, point=svg.createSVGPoint();
        point.x=line.points[2].x;point.y=line.points[2].y;
        const screen=point.matrixTransform(svg.getScreenCTM());
        return {x:screen.x,y:screen.y};
    }""")
    page.mouse.move(point["x"], point["y"])
    expect(_control(root, "zoom-dialog").get_by_role("tooltip")).to_contain_text(
        catalog[3]["label"]
    )
    assert page.evaluate("""()=>{
        const ids=[...document.querySelectorAll('[id]')].map(node=>node.id);
        return ids.length===new Set(ids).size;
    }""")
    main_clip = (
        _control(root, "svg-raw").locator("clipPath rect").get_attribute("width")
    )
    zoom_clip = (
        _control(root, "zoom-container").locator("clipPath rect").get_attribute("width")
    )
    assert main_clip != zoom_clip
    page.keyboard.press("Escape")
    expect(_control(root, "zoom-dialog")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(button).to_have_attribute("aria-pressed", "false")


def test_enlarged_curves_pin_and_unpin_samples_without_hiding_other_curves(review_page):
    page = review_page
    payload, catalog = _sample_sidebar_payload()
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload=>XicInspector.mount(document.getElementById('host'),payload,{mode:'curves'})",
        payload,
    )
    root = page.locator("#host")
    root.locator('[data-zoom="raw"]').click()
    dialog = _control(root, "zoom-dialog")
    line = dialog.locator('polyline[data-sample-id="sample-3"]')
    point = line.evaluate("""line=>{
        const svg=line.ownerSVGElement, point=svg.createSVGPoint();
        point.x=line.points[2].x;point.y=line.points[2].y;
        const screen=point.matrixTransform(svg.getScreenCTM());
        return {x:screen.x,y:screen.y};
    }""")
    button = root.locator('[data-curve-focus="sample-3"]')
    for pressed in (True, False):
        page.mouse.click(point["x"], point["y"])
        dialog.locator(".dialog-header").hover()
        expect(button).to_have_attribute("aria-pressed", str(pressed).lower())
        expect(line).to_have_css("opacity", "1" if pressed else "0.92")
        expect(dialog.locator('polyline[data-sample-id="sample-4"]')).to_have_css(
            "opacity", "0.15" if pressed else "0.92"
        )
        expect(dialog.locator("polyline")).to_have_count(8)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(
        root.get_by_role("button", name=f"聚焦 {catalog[3]['label']}", exact=True)
    ).to_have_attribute("aria-pressed", "false")


def test_curve_sidebar_bounds_many_samples_without_squeezing_the_plot(review_page):
    from copy import deepcopy

    page = review_page
    payload, _ = _sample_sidebar_payload()
    prototype = payload["items"][0]["curves"]
    payload["samples"] = [
        {
            "id": f"s-{i}",
            "label": f"long_shared_name_for_many_samples_{i:03}_DNA",
            "group": ("Benign", "Normal", "Tumor", "QC")[i % 4],
        }
        for i in range(85)
    ]
    payload["items"][0]["curves"] = [
        {**deepcopy(prototype[i % 8]), "sample_id": f"s-{i}"} for i in range(85)
    ]
    payload["items"][0]["samples"] = [{"sample_id": f"s-{i}"} for i in range(85)]
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload=>XicInspector.mount(document.getElementById('host'),payload,{mode:'curves'})",
        payload,
    )
    root = page.locator("#host")
    for width in (1360, 1024, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("""()=>{
            const plot=document.querySelector('.curve-main .svg-container');
            const width=plot.querySelector('svg').viewBox.baseVal.width;
            return Math.abs(width-plot.clientWidth)<1;
        }""")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        sidebar = root.locator(".curve-sidebar").bounding_box()
        chart = root.locator(".curve-main").bounding_box()
        if width > 1000:
            assert sidebar["width"] <= 240
            assert chart["width"] > sidebar["width"] * 2
        else:
            assert chart["y"] >= sidebar["y"] + sidebar["height"]
        sample_list = root.locator(".curve-sample-list")
        assert sample_list.evaluate("node=>node.scrollHeight>node.clientHeight")
        sample_list.evaluate("node=>node.scrollTop=node.scrollHeight")
        expect(
            root.get_by_role("checkbox", name="顯示 Benign 整組", exact=True)
        ).to_be_in_viewport()
        expect(_control(root, "svg-raw").locator("polyline")).to_have_count(85)


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

    from tests.xic_inspector_targeted_workbook_fixtures import (
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


def test_low_snr_hint_is_sample_scoped_and_does_not_replace_matrix_value(
    review_page, tmp_path
):
    from tests.explanation_review_fixtures import _quality_review_report

    report, matrix = _quality_review_report(tmp_path)
    before = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    page = review_page
    page.set_content('<div id="quality-view"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload => XicInspector.mount("
        "document.getElementById('quality-view'), payload)",
        payload,
    )
    strong = page.locator('[data-sample-row="S1"]')
    weak = page.locator('[data-sample-row="S2"]')
    expect(strong).not_to_contain_text("低 SNR")
    expect(weak).to_contain_text("低 SNR（非 LOQ 判定）")
    expect(strong).to_contain_text("1,000")
    expect(weak).to_contain_text("2,000")
    assert matrix.read_bytes() == before


def test_measured_mz_readout_keeps_sample_values_and_missing_measurements(
    review_page, tmp_path
):
    from tests.explanation_review_fixtures import _quality_review_report

    report, matrix = _quality_review_report(
        tmp_path, measured_mz_by_sample={"S1": 500.2010, "S2": None}
    )
    before = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    assert {
        witness["sample_key"]: witness["ms1_apex_mz"]
        for witness in payload["items"][0]["witnesses"]
    } == {"S1": 500.2010, "S2": None}
    page = review_page
    page.set_content('<div id="mass-view"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload => XicInspector.mount(document.getElementById('mass-view'), payload)",
        payload,
    )
    page.locator('[data-focus="S1"]').click()
    expect(page.locator('[data-readout="measured-mz"]')).to_have_text(
        "實測 MS1 apex m/z：500.201000"
    )
    page.locator('[data-focus="S2"]').click()
    expect(page.locator('[data-readout="measured-mz"]')).to_have_text(
        "實測 MS1 apex m/z：未提供"
    )
    assert matrix.read_bytes() == before


@pytest.mark.parametrize(
    "phase,flag",
    [("initial", "accepted_competition"), ("backfill", "backfill_competition")],
)
def test_accepted_competition_is_visible_only_on_its_sample(
    review_page, tmp_path, phase, flag
):
    from tests.explanation_review_fixtures import _competition_review_report

    report, matrix = _competition_review_report(tmp_path, phase=phase, flag=flag)
    before = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    page = review_page
    page.set_content('<div id="risk-view"></div>')
    _load_view_assets(page)
    page.evaluate(
        "payload => XicInspector.mount(document.getElementById('risk-view'), payload)",
        payload,
    )
    root = page.locator("#risk-view")
    _control(root, "search").fill(flag)
    expect(_control(root, "record-list").locator(".record-button")).to_have_count(1)
    _control(root, "search").fill("not_a_membership_flag")
    expect(_control(root, "record-list").locator(".record-button")).to_have_count(0)
    _control(root, "search").fill(flag)
    strong = page.locator('[data-sample-row="S1"]')
    competing = page.locator('[data-sample-row="S2"]')
    expect(strong).not_to_contain_text("已配對，有其他合理候選")
    expect(competing).to_contain_text("已配對，有其他合理候選")
    expect(strong).to_contain_text("1,000")
    expect(competing).to_contain_text("2,000")
    competing.get_by_role("button", name="查看定量依據").click()
    page.get_by_text("完整紀錄與引用", exact=True).click()
    expect(
        page.get_by_text("同分決勝：平均配合成本（越低越好）", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("替代的平均配合成本", exact=True)).to_be_visible()
    expect(page.get_by_text("中位數平移（較弱估計）", exact=True)).to_be_visible()
    expect(page.get_by_text("最近對照距離（分鐘）", exact=True)).to_be_visible()
    assert matrix.read_bytes() == before
