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
    CurveIntegration,
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


def test_saved_result_explains_its_reason_and_labels_reference_only_actions(
    review_page,
):
    page = review_page
    payload = _signal_payload()
    payload["items"] = payload["items"][:1]
    payload["items"][0]["curves"] = []
    payload["items"][0]["reason_label"] = "S1：方法評估為 HOLD：方法證據不足。"
    page.set_content('<div id="saved-result"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => XicInspector.mount(
            document.getElementById('saved-result'), payload, {mode:'result'})""",
        payload,
    )
    root = page.locator("#saved-result")
    reason = root.locator(".detail-reason")
    reason.locator("summary").click()
    expect(reason).to_contain_text("方法評估為 HOLD：方法證據不足")
    reference = root.get_by_role(
        "button", name="查看支持記錄（僅引用）", exact=True
    ).first
    expect(reference).to_be_visible()
    reference.click()
    expect(_control(root, "evidence-panel")).to_contain_text("來源內容未提供")
    _control(root, "close-evidence").click()
    expect(reference).to_be_focused()


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


def test_candidate_integration_stays_separate_from_saved_quantity(
    review_page, tmp_path
):
    transition = _fixture_bundle().transitions[0]
    first = replace(
        transition.traces[0],
        integration=CurveIntegration(120.5, 6.1, "raw_trapezoid"),
    )
    second = replace(
        first,
        trace_id="alternative-s1",
        integration=CurveIntegration(999.0, 6.2, "raw_trapezoid"),
    )
    source = tmp_path / "candidates.json"
    write_curve_bundle(
        CurveBundle(
            (
                replace(
                    transition,
                    traces=(first, second),
                    quantifications=(
                        SampleQuantification("sample-1", 17.25, "counts·s"),
                    ),
                ),
            )
        ),
        source,
    )
    payload = prepare_report(source, SelectionRequest()).payload
    page = review_page
    page.set_content(
        build_report_html(
            title="Candidate review",
            evidence_capability="retained_curves",
            strategy="sample",
            request_id="candidates",
            run_total=1,
            eligible_count=1,
            selected_count=1,
            source_db_name=source.name,
            payload_data=payload,
        )
    )
    root = page.locator("#inspector-root")
    root.get_by_role("button", name="查看樣本", exact=True).click()
    integration = root.get_by_role("region", name="目前曲線積分", exact=True)
    saved = root.get_by_role("region", name="已保存的正式定量結果", exact=True)
    expect(integration.locator('[data-readout="area"]')).to_have_text("120.5 counts·s")
    expect(integration).to_contain_text("原始 RT")
    expect(saved.locator('[data-readout="quantity"]')).to_have_text("17.25 counts·s")
    page.set_viewport_size({"width": 680, "height": 720})
    _control(root, "candidate-select").select_option(index=1)
    expect(integration.locator('[data-readout="area"]')).to_have_text("999 counts·s")
    expect(saved.locator('[data-readout="quantity"]')).to_have_text("17.25 counts·s")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    _control(root, "btn-unfocus").click()
    expect(root.locator(".sample-table")).to_be_visible()
    expect(_control(root, "integration-readout")).to_be_hidden()


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


def _mount_offline_view(page, payload):
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """payload => XicInspector.mount(
            document.getElementById('host'), payload, {mode:'offline'})""",
        payload,
    )
    return page.locator("#host")


def _dense_review_payload(sample_count=12):
    template = _signal_payload()["items"][0]["curves"][0]
    samples, rows, witnesses, curves = [], [], [], []
    for index in range(1, sample_count + 1):
        sample_id, candidate_id = f"S{index}", f"C{index}"
        samples.append(
            {
                "id": sample_id,
                "label": "Breast_Cancer_Tissue_pooled_Batch_A_"
                f"Replicate_{index:02d}_DNA",
            }
        )
        rows.append(
            {
                "sample_id": sample_id,
                "quantitative_value": None
                if index == 10
                else 0
                if index == 9
                else index * 1000,
            }
        )
        witnesses.append(
            {
                "candidate_id": candidate_id,
                "sample_id": sample_id,
                "precursor_mz": 500.1234,
                "rt_apex_min": 1.1,
            }
        )
        if index == 11:
            continue
        curves.append(
            {
                **template,
                "sample_id": sample_id,
                "candidate_id": candidate_id,
                "evidence_ref": f"curve-{sample_id}",
                "stage_label": "首次擷取曲線",
                "area": 120.5 + index,
                "bound_left_rt": 1.0,
                "bound_right_rt": 1.2,
                "apex_rt": 1.1,
                "processed_curves": [] if index == 8 else template["processed_curves"],
            }
        )
    sample_twelve = next(curve for curve in curves if curve["sample_id"] == "S12")
    curves.extend(
        [
            {
                **sample_twelve,
                "evidence_ref": "reacquired-S12",
                "stage_label": "重新擷取曲線",
            },
            {
                **sample_twelve,
                "candidate_id": "C12-alt",
                "evidence_ref": "alternative-S12",
                "area": 999,
            },
        ]
    )
    witnesses.append({**witnesses[11], "candidate_id": "C12-alt"})
    return {
        "samples": samples,
        "items": [
            {
                "id": "dense-group",
                "kind": "group",
                "title": "分組紀錄",
                "precursor_mz": 500.1234,
                "rt_apex_min": 1.1,
                "reason_label": f"{sample_count} 個樣本，"
                "包含空值、零值、未保存曲線與多候選。",
                "samples": rows,
                "witnesses": witnesses,
                "curves": curves,
            }
        ],
    }


def test_dense_review_keeps_identity_curve_and_key_values_in_view(review_page):
    page = review_page
    page.set_viewport_size({"width": 1280, "height": 800})
    payload = _dense_review_payload()
    root = _mount_offline_view(page, payload)
    expect(
        root.get_by_role("heading", name="m/z 500.1234 · RT 1.10 min", exact=True)
    ).to_be_visible()
    expect(root.locator(".detail-eyebrow")).to_contain_text("分組紀錄")
    picker = root.get_by_role("combobox", name="樣本", exact=True)
    picker.select_option("S1")
    expect(picker).to_have_attribute("title", payload["samples"][0]["label"])
    expect(picker.locator("option:checked")).to_have_text("…01_DNA")
    expect(root.locator('[data-current-candidate="C1"]')).to_contain_text("500.1234")
    expect(_control(root, "candidate-select")).to_have_count(0)
    expect(_control(root, "capture-select")).to_have_count(0)
    for panel in (
        _control(root, "svg-raw"),
        root.get_by_role("region", name="目前曲線積分", exact=True),
        root.get_by_role("region", name="已保存的正式定量結果", exact=True),
    ):
        expect(panel).to_be_in_viewport(ratio=1)

    root.locator('[data-zoom="raw"]').click()
    zoom = _control(root, "zoom-container")
    expect(zoom).to_be_visible()
    assert zoom.locator("svg").evaluate(
        r"""svg => [...svg.querySelectorAll('[clip-path]')].every(line => {
            const id = line.getAttribute('clip-path').match(/^url\(#(.+)\)$/)?.[1];
            return id && svg.contains(document.getElementById(id));
        })"""
    )
    _control(root, "zoom-close").click()

    picker.select_option("S12")
    expect(_control(root, "candidate-select")).to_be_visible()
    expect(_control(root, "capture-select")).to_be_visible()
    _control(root, "candidate-select").select_option("C12-alt")
    expect(_control(root, "capture-select")).to_have_count(0)
    expect(root.locator('[data-readout="area"]')).to_have_text("999 counts·s")
    expect(root.locator('[data-readout="quantity"]')).to_have_text("12,000")
    page.set_viewport_size({"width": 680, "height": 720})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("mode", ["offline", "curves"])
def test_large_sample_roster_preserves_display_choices_without_displacing_plot(
    review_page, mode
):
    page = review_page
    page.set_viewport_size({"width": 1280, "height": 800})
    page.set_content('<div id="host"></div>')
    _load_view_assets(page)
    page.evaluate(
        """([payload, mode]) => XicInspector.mount(
            document.getElementById('host'), payload, {mode})""",
        [_dense_review_payload(48), mode],
    )
    root = page.locator("#host")
    heading = root.locator(".inspection-heading")
    picker = heading.get_by_role("combobox", name="樣本", exact=True)
    picker.select_option("S48")
    raw = heading.get_by_role("button", name="原始", exact=True)
    expect(raw).to_be_visible()
    roster = _control(root, "sample-display")
    expect(roster).not_to_have_attribute("open", "")
    expect(roster.locator("summary")).to_contain_text("48 / 48")
    expect(roster.locator("summary")).to_contain_text("2 個缺少平滑曲線")
    expect(_control(root, "svg-raw")).to_be_in_viewport(ratio=1)

    roster.locator("summary").click()
    search = roster.get_by_role("searchbox", name="搜尋樣本", exact=True)
    search.fill("48_DNA")
    checkbox = roster.get_by_role("checkbox")
    expect(checkbox).to_have_count(1)
    checkbox.uncheck()
    expect(roster.locator("summary")).to_contain_text("47 / 48")
    expect(checkbox).not_to_be_checked()
    expect(checkbox).to_be_focused()
    raw.click()
    expect(raw).to_be_focused()
    expect(roster).not_to_have_attribute("open", "")
    roster.locator("summary").click()
    expect(search).to_have_value("48_DNA")
    expect(checkbox).not_to_be_checked()
    expect(_control(root, "svg-raw")).to_contain_text("目前樣本已隱藏")
    expect(
        _control(root, "svg-raw").locator('[data-curve-ref="curve-S48"]')
    ).to_have_count(0)
    expect(
        _control(root, "svg-raw").locator('[data-curve-ref="curve-S1"]')
    ).to_have_count(1)
    page.set_viewport_size({"width": 680, "height": 720})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    search.press("Escape")
    expect(roster).not_to_have_attribute("open", "")
    expect(roster.locator("summary")).to_be_focused()
    if mode == "curves":
        expect(root).to_contain_text("不是重新定量")
        expect(root.locator('[data-readout="quantity"]')).to_have_count(0)


@pytest.mark.parametrize(
    ("missing", "plot_name", "message"),
    [
        ("smoothed", "plot-raw", "目前樣本未保存平滑訊號"),
        ("curve", "plot-raw", "目前樣本未提供保存曲線"),
        ("alignment", "plot-aligned", "目前樣本未提供對齊座標"),
        ("hidden", "plot-raw", "目前樣本已隱藏"),
    ],
)
def test_missing_focused_curve_is_not_mistaken_for_peer_evidence(
    review_page, missing, plot_name, message
):
    payload = _signal_payload()
    payload["items"] = payload["items"][:1]
    item = payload["items"][0]
    item["samples"][0]["quantitative_value"] = 17.25
    if missing == "smoothed":
        item["curves"][0]["processed_curves"] = []
    elif missing == "curve":
        item["curves"] = item["curves"][1:]
    elif missing == "alignment":
        peer = item["curves"][1]
        peer["aligned_rts"] = peer["raw_rts"]
        for processed in peer["processed_curves"]:
            processed["aligned_rts"] = peer["raw_rts"]
    root = _mount_offline_view(review_page, payload)
    root.get_by_role("combobox", name="樣本", exact=True).select_option("S1")
    if missing == "hidden":
        root.locator('.legend-item [data-sample-visible="S1"]').uncheck()
    expect(_control(root, plot_name)).to_contain_text(message)
    expect(_control(root, plot_name)).to_contain_text("其他樣本")
    saved = root.get_by_role("region", name="已保存的正式定量結果", exact=True)
    expect(saved.locator('[data-readout="quantity"]')).to_have_text("17.25")
    if missing == "smoothed":
        expect(root.locator('.legend-item [title="未保存平滑訊號"]')).to_be_visible()
        root.get_by_role("button", name="原始", exact=True).click()
        expect(_control(root, plot_name)).not_to_contain_text(message)
        expect(root.locator('.legend-item [title="未保存平滑訊號"]')).to_have_count(0)


@pytest.mark.parametrize("item_count", [1, 3])
def test_offline_item_layout_stays_stable_while_filtering(review_page, item_count):
    payload = _signal_payload()
    payload["items"] = payload["items"][:item_count]
    root = _mount_offline_view(review_page, payload)
    sidebar = root.locator(".list-panel")
    expect(sidebar).to_be_visible(visible=item_count > 1)
    if item_count == 1:
        assert root.locator(".workbench").evaluate(
            "node => Math.abs(node.getBoundingClientRect().width - "
            "node.querySelector('.detail-panel').getBoundingClientRect().width) < 1"
        )

    _control(root, "search").fill("Item 1")
    expect(root.locator(".record-button")).to_have_count(1)
    expect(sidebar).to_be_visible(visible=item_count > 1)
    _control(root, "search").fill("no-matching-item")
    expect(_control(root, "workspace-content")).to_contain_text("沒有符合的項目")
    _control(root, "clear-inline").click()
    expect(root.get_by_role("heading", name="Item 1", exact=True)).to_be_visible()
    expect(root.locator(".record-button")).to_have_count(item_count)
    expect(sidebar).to_be_visible(visible=item_count > 1)
    if item_count == 1:
        picker = root.get_by_role("combobox", name="樣本", exact=True)
        picker.select_option("S1")
        root.get_by_role("heading", name="XIC", exact=True).press("ArrowDown")
        expect(picker).to_have_value("S1")


def test_single_excluded_item_keeps_its_counted_entry(review_page):
    payload = _signal_payload()
    payload["items"] = [
        {**payload["items"][0], "kind": "candidate", "stratum": "excluded"}
    ]
    root = _mount_offline_view(review_page, payload)
    expect(root.locator(".list-panel")).to_be_visible()
    entry = root.get_by_role("button", name=re.compile("已排除候選.*1 筆"))
    expect(entry).to_have_attribute("aria-expanded", "false")
    expect(_control(root, "workspace-content")).to_contain_text("目前未選取項目")
    entry.click()
    root.locator(".record-button").click()
    expect(root.get_by_role("heading", name="Item 1", exact=True)).to_be_visible()


def test_sample_picker_keeps_item_display_and_evidence_return(review_page, tmp_path):
    transition = replace(
        _fixture_bundle().transitions[0],
        quantifications=(
            SampleQuantification("sample-1", 17.25, "counts·s"),
            SampleQuantification("sample-2", None, "counts·s"),
        ),
    )
    source = tmp_path / "review-path.json"
    write_curve_bundle(
        CurveBundle((transition, replace(transition, key="transition-B"))), source
    )
    payload = prepare_report(source, SelectionRequest()).payload
    page = review_page
    root = _mount_offline_view(page, payload)
    root.locator(".record-button").nth(1).click()
    selected = root.locator('.record-button[aria-current="true"]')
    expect(selected).to_have_attribute("data-item", payload["items"][1]["id"])
    saved_rows = root.locator(".sample-table").inner_text()
    raw = root.get_by_role("button", name="原始", exact=True)
    raw.click()
    bounds = root.get_by_role("checkbox", name="顯示積分範圍與峰頂", exact=True)
    bounds.uncheck()

    picker = root.get_by_role("combobox", name="樣本", exact=True)
    picker.select_option("sample-1")
    expect(picker).to_be_focused()
    curves = _control(root, "svg-raw").locator("polyline")
    first_sample = curves.filter(has_text="sample-one")
    second_sample = curves.filter(has_text="sample-two")
    assert float(first_sample.get_attribute("opacity")) > float(
        second_sample.get_attribute("opacity")
    )
    expect(root.locator('[data-readout="quantity"]')).to_have_text("17.25 counts·s")
    opener = root.get_by_role("button", name="查看曲線來源", exact=True)
    opener.click()
    expect(_control(root, "evidence-panel")).to_contain_text("raw-source-1")
    _control(root, "close-evidence").click()
    expect(opener).to_be_focused()
    expect(picker).to_have_value("sample-1")

    page.set_viewport_size({"width": 680, "height": 720})
    picker.select_option("sample-2")
    expect(root.locator('[data-readout="quantity"]')).to_have_text("空值")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    root.get_by_role("button", name="← 返回所有樣本", exact=True).click()
    expect(picker).to_have_value("")
    expect(picker).to_be_focused()
    expect(selected).to_have_attribute("data-item", payload["items"][1]["id"])
    expect(raw).to_have_attribute("aria-pressed", "true")
    expect(bounds).not_to_be_checked()
    expect(curves).to_have_count(2)
    assert first_sample.get_attribute("opacity") == second_sample.get_attribute(
        "opacity"
    )
    expect(root.locator(".sample-table")).to_have_text(saved_rows, use_inner_text=True)


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
    expect(rows.nth(2).locator(".tgt-sample")).to_have_accessible_name("S3")
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
