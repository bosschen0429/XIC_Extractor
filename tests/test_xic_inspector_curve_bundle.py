"""The portable curve source must survive a file handoff without RAW access."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from xic_inspector.api import InspectorError, SelectionRequest, generate_report
from xic_inspector.sources import curve_bundle as bundle_source
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    PeakBounds,
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
