import importlib
import importlib.metadata
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

from tests.output_metadata_fixtures import (
    _config,
)


def test_app_version_reads_installed_distribution_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = importlib.import_module("xic_extractor.output.metadata")

    def fake_version(name: str) -> str:
        assert name == "xic-extractor"
        return "1.2.3"

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert metadata.app_version() == "1.2.3"


def test_app_version_returns_unknown_when_package_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = importlib.import_module("xic_extractor.output.metadata")

    def missing_version(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing_version)

    assert metadata.app_version() == "unknown"


def test_build_metadata_rows_returns_contract_keys_in_order() -> None:
    metadata = importlib.import_module("xic_extractor.output.metadata")
    config = _config(Path("output"))
    before = datetime.now(timezone.utc).replace(microsecond=0)

    rows = metadata.build_metadata_rows(config)

    assert [key for key, _value in rows] == [
        "app_version",
        "generated_at",
        "target_config_hash",
        "targeted_output_schema_version",
        "parallel_mode",
        "parallel_workers",
        "mass_recalibration",
        "targeted_strategy",
        "signal_processing_method",
        "signal_processing_window_points",
        "ms2_precursor_tol_da",
        "nl_min_intensity_ratio",
        "count_no_ms2_as_detected",
    ]
    values = dict(rows)
    assert values["target_config_hash"] == "abc12345"
    strategy = json.loads(values["targeted_strategy"])
    assert strategy["strategy"] == "cid-nl"
    assert strategy["cid_nl_identity_precursor_reference"] == "recorded_precursor"
    assert strategy["cid_nl_evidence_scope"] == "target_mass_and_physical_peak"
    assert strategy["cid_nl_ranking_priority"] == "positive_support_only"
    assert strategy["cid_nl_mass_policy"]["precursor_mz_tolerance_ppm"] == 20
    assert values["signal_processing_method"] == "gaussian"
    assert values["signal_processing_window_points"] == 21
    assert values["nl_min_intensity_ratio"] == 0.02
    assert values["ms2_precursor_tol_da"] == 0.4
    assert values["targeted_output_schema_version"] == "targeted_output"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", values["generated_at"])
    generated_at = datetime.strptime(values["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
    generated_at = generated_at.replace(tzinfo=timezone.utc)
    after = datetime.now(timezone.utc)
    assert before <= generated_at <= after


def test_build_metadata_sheet_writes_headers_values_layout_and_styles() -> None:
    from xic_extractor.output.sheet_metadata import _build_metadata_sheet

    wb = Workbook()
    ws = wb.active

    _build_metadata_sheet(ws, _config(Path("output")))

    # Value comes last so a long saved setting extends into empty space
    # instead of hiding behind its description.
    assert [ws.cell(1, column).value for column in (1, 2, 3)] == [
        "Key",
        "說明",
        "Value",
    ]
    assert ws["A2"].value == "app_version"
    assert ws["B2"].value
    assert ws["A3"].value == "generated_at"
    assert ws["A4"].value == "target_config_hash"
    assert ws["C4"].value == "abc12345"
    assert ws["A1"].font.name == "Microsoft JhengHei"
    assert ws["A1"].font.bold is True
    assert ws["C2"].border.bottom.style == "thin"
    assert ws.tables["TargetedRunMetadata"].ref == f"A1:C{ws.max_row}"
    assert ws.freeze_panes == "A2"
