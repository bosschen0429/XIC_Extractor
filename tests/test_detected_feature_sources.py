from __future__ import annotations

import csv
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.discovery.detected_features import (
    DETECTED_FEATURES_COLUMNS,
    DetectedFeature,
    load_detected_features_csv,
    write_detected_features_csv,
)


def _write_detected_csv(path: Path, row: dict[str, object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETECTED_FEATURES_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {column: row.get(column, "") for column in DETECTED_FEATURES_COLUMNS}
        )


def _local_detected_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_stem": "Sample_1",
        "feature_id": "Sample_1|local_ms1_feature|000001",
        "source_feature_row_id": "Sample_1|local_ms1_feature|000001",
        "mz": "500.0",
        "rt_apex_min": "10.0",
        "rt_start_min": "9.95",
        "rt_end_min": "10.05",
        "height": "1000.0",
        "area": "2500.0",
        "trace_quality": "OK",
        "shape_score": "",
        "source": "local_ms1_feature",
    }
    row.update(overrides)
    return row


def _local_detected_feature(**overrides: object) -> DetectedFeature:
    values = _local_detected_row(**overrides)
    return DetectedFeature(
        sample_stem=str(values["sample_stem"]),
        feature_id=str(values["feature_id"]),
        source_feature_row_id=str(values["source_feature_row_id"]),
        mz=float(values["mz"]),
        rt_apex_min=float(values["rt_apex_min"]),
        rt_start_min=float(values["rt_start_min"]),
        rt_end_min=float(values["rt_end_min"]),
        height=float(values["height"]),
        area=float(values["area"]),
        trace_quality=str(values["trace_quality"]),
        shape_score=None,
        source=str(values["source"]),
    )


def test_local_ms1_feature_detected_csv_accepts_ok_and_weak_support(
    tmp_path: Path,
) -> None:
    ok_csv = tmp_path / "ok.csv"
    weak_csv = tmp_path / "weak.csv"
    _write_detected_csv(ok_csv, _local_detected_row(trace_quality="OK"))
    _write_detected_csv(weak_csv, _local_detected_row(trace_quality="WEAK_SUPPORT"))

    assert load_detected_features_csv(ok_csv)[0].trace_quality == "OK"
    assert load_detected_features_csv(weak_csv)[0].trace_quality == "WEAK_SUPPORT"


def test_local_ms1_feature_detected_csv_rejects_fail_and_unknown_quality(
    tmp_path: Path,
) -> None:
    fail_csv = tmp_path / "fail.csv"
    typo_csv = tmp_path / "typo.csv"
    _write_detected_csv(fail_csv, _local_detected_row(trace_quality="FAIL"))
    _write_detected_csv(typo_csv, _local_detected_row(trace_quality="GOOD"))

    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(fail_csv)
    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(typo_csv)


def test_write_detected_features_csv_emits_public_header(tmp_path: Path) -> None:
    features = (_local_detected_feature(),)
    output = tmp_path / "detected_features.csv"

    write_detected_features_csv(output, features)

    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert tuple(next(reader)) == DETECTED_FEATURES_COLUMNS


@pytest.mark.parametrize("trace_quality", ["FAIL", "GOOD"])
def test_write_detected_features_csv_rejects_invalid_local_ms1_trace_quality(
    tmp_path: Path, trace_quality: str
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature(trace_quality=trace_quality)

    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        write_detected_features_csv(output, [feature])

    assert not output.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sample_stem": ""}, "sample_stem"),
        ({"feature_id": ""}, "feature_id"),
        ({"mz": float("nan")}, "mz"),
        ({"rt_apex_min": float("inf")}, "rt_apex_min"),
        ({"height": 0.0}, "height"),
        ({"area": float("nan")}, "area"),
        ({"source": ""}, "source"),
    ],
)
def test_write_detected_features_csv_rejects_rows_loader_would_reject(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature(**overrides)

    with pytest.raises(ValueError, match=message):
        write_detected_features_csv(output, [feature])

    assert not output.exists()


def test_written_detected_features_round_trip_through_public_loader(
    tmp_path: Path,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = _local_detected_feature()

    write_detected_features_csv(output, [feature])

    assert load_detected_features_csv(output) == (feature,)


@pytest.mark.parametrize("field", ["mz", "area", "shape_score"])
def test_write_detected_features_csv_rejects_boolean_numeric_values(
    tmp_path: Path,
    field: str,
) -> None:
    output = tmp_path / "detected_features.csv"
    feature = replace(_local_detected_feature(), **{field: True})

    with pytest.raises(ValueError, match=field):
        write_detected_features_csv(output, [feature])

    assert not output.exists()


@pytest.mark.parametrize("route", ("features", "local_ms1", "cid_nl"))
def test_generic_feature_and_cid_nl_routes_work_without_delta_mass_provider(
    tmp_path: Path, route: str
) -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys
        from pathlib import Path

        class UnavailableDeltaMassProvider(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in {
                    "xic_extractor.discovery.delta_mass_strategy",
                    "xic_extractor.discovery.delta_mass_patterns",
                }:
                    raise ImportError(f"Delta Mass provider is unavailable: {fullname}")
                return None

        sys.meta_path.insert(0, UnavailableDeltaMassProvider())
        if sys.argv[1] == "local_ms1":
            import xic_extractor.discovery.local_ms1_features
        elif sys.argv[1] == "features":
            from xic_extractor.discovery.detected_features import (
                DetectedFeature,
                load_detected_features_csv,
                write_detected_features_csv,
            )

            features = tuple(
                DetectedFeature(
                    sample_stem=sample, feature_id="feature-1",
                    source_feature_row_id=None, mz=mz, rt_apex_min=8.5,
                    rt_start_min=8.4, rt_end_min=8.6, height=height,
                    area=height * 2, trace_quality="OK", shape_score=None,
                    source="test_feature_source",
                )
                for sample, mz, height in (
                    ("sample-a", 500.0, 250.0), ("sample-b", 600.0, 800.0)
                )
            )
            path = Path(sys.argv[2]) / "features.csv"
            write_detected_features_csv(path, features)
            assert load_detected_features_csv(path) == features
        else:
            from scripts.run_discovery import main
            from xic_extractor.presets import (
                apply_to_discovery, bind_cid_nl_recipe, load_preset,
            )

            preset = load_preset("dna_dr")
            settings = apply_to_discovery(preset)
            assert settings.selected_tag_names == ("DNA_dR",)
            assert settings.neutral_loss_profiles[0].neutral_loss_da == 116.0474
            recipe = bind_cid_nl_recipe(preset, "discovery_only")
            assert recipe.discovery_settings == settings
            try:
                main(["--help"])
            except SystemExit as exc:
                assert exc.code == 0
            else:
                raise AssertionError("CLI help did not complete argparse handling")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, route, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
