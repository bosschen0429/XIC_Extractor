"""The portable curve source must survive a file handoff without RAW access."""

import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_xic_inspector_html import _evaluate_report_functions
from xic_inspector.api import (
    InspectorError,
    SelectionRequest,
    generate_report,
    prepare_report,
)
from xic_inspector.sources import curve_bundle as bundle_source
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    CurveIntegration,
    PeakBounds,
    SampleQuantification,
    TransitionCurves,
    read_curve_bundle,
    write_curve_bundle,
)


def _fixture_bundle() -> CurveBundle:
    return CurveBundle(
        transitions=(
            TransitionCurves(
                key="transition-A",
                review_rt_window=(4.0, 7.0),
                quality_flags={
                    "gate_labels": "mass and RT passed",
                    "identity_decisions": "successor supported",
                    "existing_count": 2,
                    "delta_mz": 0.012,
                    "delta_rt_min": 0.08,
                    "median_ratio": 1.25,
                },
                traces=(
                    CurveBundleTrace(
                        trace_id="source-s1",
                        sample_id="sample-1",
                        sample_label="sample-one",
                        role="source",
                        raw_identity="raw-source-1",
                        mz=501.2,
                        ppm=10.0,
                        extraction_rt_window=(3.5, 7.5),
                        producer="test-diagnostic",
                        producer_version="1.0",
                        raw=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 5.0, 9.0, 0.0)
                        ),
                        gaussian15=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (1.0, 4.0, 7.0, 1.0)
                        ),
                        baseline=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 1.0, 1.0, 0.0)
                        ),
                        peak_bounds=PeakBounds(4.0, 6.0, 7.0),
                        quality_flags={"gate": "accepted"},
                    ),
                    CurveBundleTrace(
                        trace_id="successor-s2",
                        sample_id="sample-2",
                        sample_label="sample-two",
                        role="successor",
                        raw_identity="raw-successor-2",
                        mz=501.188,
                        ppm=10.0,
                        extraction_rt_window=(3.5, 7.5),
                        producer="test-diagnostic",
                        producer_version="1.0",
                        raw=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 2.0, 12.0, 0.0)
                        ),
                        gaussian15=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (1.0, 3.0, 9.0, 1.0)
                        ),
                        peak_bounds=PeakBounds(4.0, 6.0, 7.0),
                    ),
                ),
            ),
        )
    )


def test_reacquired_group_has_readable_title_and_retains_source_identity(tmp_path):
    group_id = "xic.cross-sample-group:" + "0" * 64
    transition = replace(
        _fixture_bundle().transitions[0],
        key=group_id,
        quality_flags={"new_diagnostic_extraction": True},
    )
    source = tmp_path / "review.json"
    write_curve_bundle(CurveBundle((transition,)), source)
    item = prepare_report(source, SelectionRequest()).payload["items"][0]
    assert item["title"].startswith("診斷 XIC · m/z ")
    assert group_id not in item["title"]
    assert group_id in item["id"]


def test_bundle_round_trip_and_offline_report(tmp_path: Path) -> None:
    source = tmp_path / "curves.json"
    output = tmp_path / "review.html"
    bundle = _fixture_bundle()
    write_curve_bundle(bundle, source)
    assert read_curve_bundle(source) == bundle

    result = generate_report(source, SelectionRequest(), output)
    assert result.selected_count == 1
    assert result.evidence_capability == "retained_curves"
    content = output.read_text(encoding="utf-8")
    assert "transition-A" in content
    assert "mass and RT passed" in content
    assert "source-s1" in content and "successor-s2" in content
    assert "review_rt_window" in content
    assert "https://" not in content
    embedded = content.split('<script id="inspector-data" type="application/json">', 1)[
        1
    ]
    payload = json.loads(embedded.split("</script>", 1)[0])
    assert payload["items"][0]["relationship_availability"] == "unavailable"
    curves = {curve["sample_id"]: curve for curve in payload["items"][0]["curves"]}
    assert curves["sample-1"]["processed_curves"][0]["intensities"] == [1, 4, 7, 1]
    assert curves["sample-2"]["processed_curves"][0]["intensities"] == [1, 3, 9, 1]


def test_bundle_cli_does_not_import_raw_reader(tmp_path: Path) -> None:
    source = tmp_path / "curves.json"
    output = tmp_path / "review.html"
    write_curve_bundle(_fixture_bundle(), source)
    code = (
        "import sys; from xic_inspector.cli import main; "
        f"status = main(['--report', {str(source)!r}, '--output', {str(output)!r}, "
        "'--no-open']); "
        "raise SystemExit(88 if 'xic_extractor.raw_reader' in sys.modules "
        "else status)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8"
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()


def test_bundle_size_limit_rejects_without_replacing(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "curves.json"
    source.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(bundle_source, "MAX_BUNDLE_SIZE_BYTES", 128)
    with pytest.raises(bundle_source.CurveBundleError, match="50 MiB limit"):
        write_curve_bundle(_fixture_bundle(), source)
    assert source.read_text(encoding="utf-8") == "previous"
    monkeypatch.setattr(bundle_source, "MAX_BUNDLE_SIZE_BYTES", 1)
    with pytest.raises(bundle_source.CurveBundleError, match="50 MiB limit"):
        read_curve_bundle(source)


def test_bundle_expected_source_binds_file_content(tmp_path: Path) -> None:
    source = tmp_path / "curves.json"
    write_curve_bundle(_fixture_bundle(), source)
    first = generate_report(source, SelectionRequest(), tmp_path / "first.html")
    body = json.loads(source.read_text(encoding="utf-8"))
    body["transitions"][0]["quality_flags"]["gate_labels"] = "changed"
    source.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(InspectorError, match="expected_source contract"):
        generate_report(
            source,
            SelectionRequest(),
            tmp_path / "second.html",
            expected_source=first.source,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.pop("schema_version"),
        lambda body: body["transitions"][0]["traces"][0].pop("raw_identity"),
        lambda body: body["transitions"][0]["traces"][0].update(
            gaussian15={"rt": [4.0], "intensity": [1.0, 2.0]}
        ),
    ],
)
def test_invalid_bundle_fails_explicitly(tmp_path: Path, mutation) -> None:
    source = tmp_path / "bad.json"
    write_curve_bundle(_fixture_bundle(), source)
    body = json.loads(source.read_text(encoding="utf-8"))
    mutation(body)
    source.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(InspectorError, match="Invalid curve bundle") as exc:
        generate_report(source, SelectionRequest(), tmp_path / "review.html")
    assert exc.value.code == 4
    assert not (tmp_path / "review.html").exists()


@pytest.mark.parametrize(
    ("flags", "field"),
    [
        ({"review_kind": "cid_nl_differential"}, "source_rt_window"),
        (
            {
                "review_kind": "cid_nl_differential",
                "source_center_rt": 6.0,
                "successor_center_rt": 6.0,
                "source_rt_window": [7.0, 4.0],
                "successor_rt_window": [4.0, 7.0],
            },
            "source_rt_window",
        ),
        (
            {
                "review_kind": "cid_nl_differential",
                "source_rt_window": [4.0, 7.0],
                "source_center_rt": "6.0",
            },
            "source_center_rt",
        ),
        (
            {
                "review_kind": "cid_nl_differential",
                "source_rt_window": [4.0, 7.0],
                "source_center_rt": 8.0,
            },
            "source_center_rt",
        ),
    ],
)
def test_incomplete_differential_rejected_before_report_replacement(
    tmp_path: Path, flags: dict, field: str
) -> None:
    source = tmp_path / "curves.json"
    output = tmp_path / "review.html"
    write_curve_bundle(_fixture_bundle(), source)
    body = json.loads(source.read_text(encoding="utf-8"))
    body["transitions"][0]["quality_flags"] = flags
    source.write_text(json.dumps(body), encoding="utf-8")
    output.write_text("previous report", encoding="utf-8")

    with pytest.raises(InspectorError, match=field) as exc:
        generate_report(source, SelectionRequest(), output)
    assert exc.value.code == 4
    assert output.read_text(encoding="utf-8") == "previous report"


def test_differential_accepts_numeric_display_facts(tmp_path: Path) -> None:
    transition = _fixture_bundle().transitions[0]
    flags = {
        "review_kind": "cid_nl_differential",
        "source_rt_window": [4.0, 7.0],
        "successor_rt_window": [4.0, 7.0],
        "source_center_rt": 6.0,
        "successor_center_rt": 6.0,
        "source_accepted_cell_count": 0,
        "successor_accepted_cell_count": 2,
        "median_gaussian15_successor_source_max_ratio": 1.25,
    }
    source = tmp_path / "curves.json"
    output = tmp_path / "review.html"
    write_curve_bundle(CurveBundle((replace(transition, quality_flags=flags),)), source)

    result = generate_report(source, SelectionRequest(), output)
    assert result.selected_count == 1
    embedded = output.read_text(encoding="utf-8").split(
        '<script id="inspector-data" type="application/json">', 1
    )[1]
    item = json.loads(embedded.split("</script>", 1)[0])["items"][0]
    assert item["transition_review"]["source_accepted_cell_count"] == 0
    assert (
        item["transition_review"]["median_gaussian15_successor_source_max_ratio"]
        == 1.25
    )


@pytest.mark.parametrize(
    ("sample_ids", "eligible"),
    [
        ((), 2),
        (("sample-1",), 1),
        (("unknown-sample",), 0),
    ],
)
def test_bundle_run_total_is_independent_of_sample_scope(
    tmp_path: Path, sample_ids: tuple[str, ...], eligible: int
) -> None:
    transition = _fixture_bundle().transitions[0]
    bundle = CurveBundle(
        transitions=(
            replace(transition, traces=(transition.traces[0],)),
            replace(
                transition,
                key="transition-B",
                traces=(transition.traces[1],),
            ),
        )
    )
    source = tmp_path / "curves.json"
    write_curve_bundle(bundle, source)

    result = generate_report(
        source,
        SelectionRequest(sample_ids=sample_ids),
        tmp_path / "review.html",
    )
    assert (result.run_total, result.eligible_count, result.matched_count) == (
        2,
        eligible,
        eligible,
    )
    assert result.selected_count == eligible


@pytest.mark.parametrize("second_value", [0.0, None, 42.125])
def test_saved_quantity_and_integration_reach_offline_readout(tmp_path, second_value):
    """Formal values survive multiple traces, count=false, and empty results."""
    transition = _fixture_bundle().transitions[0]
    first, second = transition.traces
    first = replace(
        first,
        integration=CurveIntegration(120.5, 6.1, "raw_trapezoid"),
        positive_signal=ChromatogramCurve(first.raw.rt, (0.0, 3.0, 6.0, 0.0)),
        quality_flags={"counted_detection": False, "formal_area": 9999},
    )
    # The paired curve has its own area; it must not replace sample one's value.
    paired = replace(
        first,
        trace_id="paired-istd",
        role="istd",
        integration=CurveIntegration(999.0, 6.2, "raw_trapezoid"),
    )
    bundle = CurveBundle(
        (
            replace(
                transition,
                traces=(first, paired, second),
                quantifications=(
                    SampleQuantification("sample-1", 17.25, "counts·s"),
                    SampleQuantification("sample-2", second_value, "counts·s"),
                ),
            ),
        )
    )
    source, output = tmp_path / "curves.json", tmp_path / "review.html"
    write_curve_bundle(bundle, source)
    assert read_curve_bundle(source) == bundle
    generate_report(source, SelectionRequest(), output)
    content = output.read_text(encoding="utf-8")
    payload = json.loads(
        content.split(
            '<script id="inspector-data" type="application/json">',
            1,
        )[1].split("</script>", 1)[0]
    )
    item = payload["items"][0]
    rows = {r["sample_id"]: r for r in item["samples"]}
    assert rows["sample-1"]["quantitative_value"] == 17.25
    assert rows["sample-2"]["quantitative_value"] == second_value
    curves = {c["candidate_id"]: c for c in item["curves"]}
    assert curves["source-s1"]["area"] == 120.5
    assert curves["paired-istd"]["area"] == 999.0
    assert curves["source-s1"]["integration"]["raw_apex_rt_min"] == 6.1
    assert curves["source-s1"]["processed_curves"][-1] == {
        "kind": "positive_signal",
        "rts": [4, 5, 6, 7],
        "intensities": [0, 3, 6, 0],
    }
    assert curves["successor-s2"]["area"] is None
    rendered = _evaluate_report_functions(
        content,
        payload,
        "state.sample='sample-1';normalize(items[0],true);"
        "return JSON.stringify({readout:readout(items[0]),"
        "table:sampleTable(items[0])});",
    )
    assert 'data-readout="area">120.5' in rendered["readout"]
    assert 'data-readout="raw-apex">6.100' in rendered["readout"]
    assert 'result-number">17.25' in rendered["readout"]
    assert 'result-unit">counts·s' in rendered["readout"]
    expected = (
        'result-null">空值'
        if second_value is None
        else ('result-zero">0' if second_value == 0 else 'result-number">42.125')
    )
    assert expected in rendered["table"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["transitions"][0].update(
            quantifications=[
                {"sample_id": "missing-sample", "value": 12, "unit": "counts·s"},
            ]
        ),
        lambda body: body["transitions"][0].update(
            quantifications=[
                {"sample_id": "sample-1", "value": 12, "unit": "counts·s"},
                {"sample_id": "sample-1", "value": 99, "unit": "counts·s"},
            ]
        ),
        lambda body: body["transitions"][0].update(
            quantifications=[
                {"sample_id": "sample-1", "unit": "counts·s"},
            ]
        ),
        lambda body: body["transitions"][0].update(
            quantifications=[
                {"sample_id": "sample-1", "value": True, "unit": "counts·s"},
            ]
        ),
        lambda body: body["transitions"][0]["traces"][0].update(
            positive_signal={"rt": [4.0, 5.5, 6.0, 7.0], "intensity": [0, 3, 6, 0]},
        ),
        lambda body: body["transitions"][0]["traces"][0].update(
            integration={
                "area_raw_counts_seconds": True,
                "raw_apex_rt_min": 6.1,
                "integration_method": "raw_trapezoid",
            }
        ),
    ],
)
def test_invalid_saved_result_projection_is_rejected(tmp_path, mutation):
    source = tmp_path / "curves.json"
    write_curve_bundle(_fixture_bundle(), source)
    body = json.loads(source.read_text(encoding="utf-8"))
    mutation(body)
    source.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(InspectorError, match="Invalid curve bundle"):
        generate_report(source, SelectionRequest(), tmp_path / "review.html")
    assert not (tmp_path / "review.html").exists()


def test_distinct_trace_roles_read_as_channels_of_one_sample(tmp_path):
    transition = _fixture_bundle().transitions[0]
    base = replace(transition.traces[0], trace_id="s1-base", role="base")
    plus = replace(base, trace_id="s1-plus_1", role="plus_1", mz=502.2)
    # Two traces sharing a role in one sample remain competing candidates.
    first = replace(transition.traces[1], trace_id="s2-a", role="reacquisition")
    second = replace(first, trace_id="s2-b", mz=501.3)
    source, output = tmp_path / "channels.json", tmp_path / "review.html"
    write_curve_bundle(
        CurveBundle((replace(transition, traces=(base, plus, first, second)),)),
        source,
    )
    generate_report(source, SelectionRequest(), output)
    content = output.read_text(encoding="utf-8")
    payload = json.loads(
        content.split(
            '<script id="inspector-data" type="application/json">',
            1,
        )[1].split("</script>", 1)[0]
    )
    rendered = _evaluate_report_functions(
        content,
        payload,
        "const item=items[0];"
        "return JSON.stringify({notices:Object.fromEntries(item.samples.map(r=>"
        "[r.sample_id,sampleNotices(item,r).map(n=>n.kind)])),"
        "summary:summary(item),plot:plot(item,'raw'),key:channelKey(item),"
        "labels:candidates(item,'sample-1').map(candidateLabel)});",
    )
    assert "candidates" not in rendered["notices"]["sample-1"]
    assert "candidates" in rendered["notices"]["sample-2"]
    assert "<dt>多個候選</dt><dd>1<small>個樣本</small>" in rendered["summary"]
    assert 'title="sample-two"' in rendered["summary"]
    dashes = dict(
        re.findall(
            r'data-curve-ref="([^"]+)"[^>]*stroke-dasharray="([^"]*)"',
            rendered["plot"],
        )
    )
    assert dashes["s1-base"] != dashes["s1-plus_1"]
    assert dashes["s2-a"] == dashes["s2-b"] == ""
    assert re.findall(r"</svg>([^<]+)</span>", rendered["key"]) == ["base", "plus_1"]
    assert [label.split(" · ")[0] for label in rendered["labels"]] == [
        "base",
        "plus_1",
    ]


@pytest.mark.parametrize("change", ("missing", "extra"))
def test_saved_bundle_requires_complete_trace_fields(tmp_path, change):
    path = tmp_path / "bundle.json"
    write_curve_bundle(_fixture_bundle(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    trace = payload["transitions"][0]["traces"][0]
    if change == "missing":
        del trace["integration"]
    else:
        trace["unknown_field"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(bundle_source.CurveBundleError, match="rerun"):
        read_curve_bundle(path)
