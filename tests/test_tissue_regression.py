from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RT_TOLERANCE_MIN = 0.05
_AREA_REL_TOLERANCE = 0.05
_FIXTURE_DIR = Path("tests/fixtures/tissue_regression")


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    root = os.environ.get("XIC_TISSUE_FIXTURE_DIR")
    if not root:
        pytest.skip("XIC_TISSUE_FIXTURE_DIR not set; regression fixture unavailable")
    return Path(root)


def test_tissue_regression(
    tmp_path: Path,
    fixture_root: Path,
) -> None:
    subset_root = _materialize_subset(tmp_path, fixture_root)
    baseline_path = _FIXTURE_DIR / "baseline.json"
    baseline = {
        (row["sample"], row["target"]): row
        for row in json.loads(baseline_path.read_text(encoding="utf-8"))
    }
    config_dir = _write_example_config(tmp_path, subset_root)
    current = _run_fixture_extraction(config_dir, _FIXTURE_DIR / "sample_subset.csv")

    failures: list[str] = []
    for key, expected in baseline.items():
        extraction_result = current.get(key)
        if extraction_result is None or extraction_result["rt"] is None:
            if expected["rt"] is not None:
                failures.append(
                    f"{key}: regressed - baseline detected, current missing"
                )
            continue
        if expected["rt"] is None:
            continue
        rt_shift = abs(extraction_result["rt"] - expected["rt"])
        if rt_shift > _RT_TOLERANCE_MIN:
            failures.append(
                (
                    f"{key}: RT shifted {expected['rt']:.4f} "
                    f"-> {extraction_result['rt']:.4f}"
                )
            )
        area = expected["area"]
        if area is not None and area > 0:
            area_shift = abs(extraction_result["area"] - area) / area
            if area_shift > _AREA_REL_TOLERANCE:
                failures.append(
                    (
                        f"{key}: Area regressed {area:.2f} "
                        f"-> {extraction_result['area']:.2f}"
                    )
                )
        if extraction_result["confidence"] not in {"HIGH", "MEDIUM"}:
            failures.append(
                f"{key}: Confidence dropped to {extraction_result['confidence']}"
            )

    assert not failures, "Regression failures:\n" + "\n".join(failures[:20])


def _write_example_config(
    tmp_path: Path,
    subset_root: Path,
) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_temp_settings(
        source=Path("config/settings.csv"),
        destination=config_dir / "settings.csv",
        data_dir=subset_root,
        injection_order_source=_FIXTURE_DIR / "sample_subset.csv",
    )
    shutil.copy2("config/targets.csv", config_dir / "targets.csv")
    return config_dir


def _write_temp_settings(
    *,
    source: Path,
    destination: Path,
    data_dir: Path,
    injection_order_source: Path,
) -> None:
    with source.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        key = row["key"]
        if key == "data_dir":
            row["value"] = str(data_dir)
        elif key == "injection_order_source":
            row["value"] = str(injection_order_source)
        elif key == "rt_prior_library_path":
            row["value"] = ""

    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "value", "description"])
        writer.writeheader()
        writer.writerows(rows)


def _load_subset_order(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [str(row["Sample_Name"]).strip() for row in reader]


def _materialize_subset(tmp_path: Path, fixture_root: Path) -> Path:
    subset_dir = tmp_path / "raw"
    if subset_dir.exists():
        shutil.rmtree(subset_dir)
    subset_dir.mkdir(parents=True, exist_ok=True)
    for sample_name in _load_subset_order(_FIXTURE_DIR / "sample_subset.csv"):
        source = fixture_root / f"{sample_name}.raw"
        target = subset_dir / source.name
        if not source.exists():
            raise FileNotFoundError(f"Missing fixture RAW: {source}")
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return subset_dir


def _run_fixture_extraction(
    config_dir: Path,
    injection_order_path: Path,
) -> dict[tuple[str, str], dict[str, float | str | None]]:
    script = """
from __future__ import annotations
import json
import sys
from pathlib import Path

from xic_extractor.configuration.loader import load_config
from xic_extractor.extractor import run
from xic_extractor.injection_rolling import read_injection_order

config_dir = Path(sys.argv[1])
injection_order_path = Path(sys.argv[2])
config, targets = load_config(config_dir)
run_output = run(
    config=config,
    targets=targets,
    injection_order=read_injection_order(injection_order_path),
)
payload = {}
for file_result in run_output.file_results:
    for extraction_result in file_result.extraction_results:
        key = f"{file_result.sample_name}\\t{extraction_result.target_label}"
        payload[key] = {
            "rt": (
                extraction_result.peak.rt
                if extraction_result.peak is not None
                else None
            ),
            "area": (
                extraction_result.peak.area
                if extraction_result.peak is not None
                else None
            ),
            "confidence": extraction_result.confidence,
        }
print(json.dumps(payload))
""".strip()
    completed = subprocess.run(
        [sys.executable, "-c", script, str(config_dir), str(injection_order_path)],
        capture_output=True,
        check=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    return {
        tuple(key.split("\t", maxsplit=1)): value
        for key, value in payload.items()
    }
