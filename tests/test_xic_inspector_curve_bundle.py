"""The portable curve source must survive a file handoff without RAW access."""

import json
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from xic_inspector.api import InspectorError, SelectionRequest, generate_report
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
    curves = {curve["sample_id"]: curve for curve in payload["items"][0]["curves"]}
    assert curves["sample-1"]["processed_curves"][0]["intensities"] == [1, 4, 7, 1]
    assert curves["sample-2"]["processed_curves"][0]["intensities"] == [1, 3, 9, 1]


def test_bundle_cli_does_not_import_raw_reader(tmp_path: Path) -> None:
    source = tmp_path / "curves.json"
    output = tmp_path / "review.html"
    write_curve_bundle(_fixture_bundle(), source)
    code = (
        "import sys; from xic_inspector.cli import main; "
        f"status = main(['--report', {str(source)!r}, '--output', {str(output)!r}]); "
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
    script = re.search(r"<script>(.*?)</script>", content, re.S)
    assert script is not None
    prefix = script[1].split("  $('record-list').addEventListener", 1)[0]
    document = (
        "var document={getElementById:id=>id==='inspector-data'?{textContent:"
        + json.dumps(json.dumps(payload))
        + "}:{dataset:{strategy:'sample'}}};"
    )
    js = (
        document
        + "console.log("
        + prefix
        + (
            "state.sample='sample-1';normalize(items[0],true);"
            "return JSON.stringify({readout:readout(items[0]),"
            "table:sampleTable(items[0])});})());"
        )
    )
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the report JavaScript"
    rendered = json.loads(
        subprocess.run(
            [node],
            input=js,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
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
