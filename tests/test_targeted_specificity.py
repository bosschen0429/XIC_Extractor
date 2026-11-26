"""Per-target specificity: CID-NL value and Delta Mass ions."""

import json

import openpyxl
import pytest

from tests.targeted_fixtures import ScopedMs2WindowReader
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.target_list_files import (
    create_target_list,
    load_target_list,
    save_target_list,
)
from xic_extractor.configuration.targeted_identification import (
    DeltaMassCondition,
    QuantRelativeIon,
    target_specificity,
)


def row(**changes):
    return {
        "label": "target",
        "mz": "302",
        "expected_rt_min": "2",
        "role": "analyte",
        "istd_pair": "",
        **changes,
    }


def light_pair(**ion):
    return {
        "settings": {},
        "ions": {
            "target": [
                {"label": "light", "offset_mz": -2.0, "compare_shape": True, **ion}
            ]
        },
    }


@pytest.mark.parametrize(
    "invalid",
    [
        {"label": 42},
        {"compare_shape": "false"},
    ],
)
def test_target_details_reject_non_text_labels_and_non_boolean_shape_flags(invalid):
    with pytest.raises(ConfigError, match="Delta Mass 設定無法讀取"):
        create_target_list(
            [row(delta_mass_mz="-2.0")], delta_mass=light_pair(**invalid)
        )


def test_negative_offsets_normalize_without_moving_quantitation():
    condition = DeltaMassCondition((QuantRelativeIon("light", -2.0),))
    pattern = condition.pattern_settings("target:1")
    assert [(p.label, p.delta_da) for p in pattern.positions] == [
        ("light", 0),
        ("base", 2),
    ]
    assert pattern.shape_reference_position == "base"
    assert pattern.ratio_reference_position == "base"
    assert pattern.anchor_positions == ("base",)
    assert pattern.discriminating_positions == ("light",)


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, "MS1 only"),
        ({"cid_nl_loss_da": "116.0474"}, "CID-NL 116.0474"),
        ({"delta_mass_mz": "+1.0021, +4.005213"}, "Delta Mass +1.0021, +4.0052"),
        (
            {"cid_nl_loss_da": "162.0528", "delta_mass_mz": "-2.0"},
            "CID-NL 162.0528 + Delta Mass -2.0000",
        ),
    ],
)
def test_specificity_is_derived_from_the_two_parameter_columns(changes, expected):
    (target,) = create_target_list([row(**changes)]).targets
    assert target_specificity(target) == expected


def test_cid_nl_alone_ranks_while_with_delta_mass_it_is_a_required_condition():
    alone, both = create_target_list(
        [
            row(label="alone", cid_nl_loss_da="116.0474"),
            row(label="both", cid_nl_loss_da="116.0474", delta_mass_mz="-2.0"),
        ],
    ).targets
    assert alone.neutral_loss_da == 116.0474 and alone.identification is None
    assert both.neutral_loss_da is None
    assert both.identification.cid_nl_loss_da == 116.0474
    assert [c.kind for c in both.identification.conditions] == [
        "cid_nl",
        "delta_mass",
    ]


def _workspace_targets_entry(tmp_path, column):
    from xic_extractor.configuration.loader import prepare_targeted_config

    (tmp_path / "workspace.json").write_text(
        json.dumps(
            {
                "application": {"dll_dir": str(tmp_path)},
                "batch": {"data_dir": str(tmp_path)},
                "targets": [row(**{column: "dR"})],
            }
        ),
        encoding="utf-8",
    )
    prepare_targeted_config(tmp_path)


def _csv_entry(tmp_path, column):
    from xic_extractor.configuration.targets import read_targets

    path = tmp_path / "targets.csv"
    path.write_text(
        f"label,mz,expected_rt_min,{column},role,istd_pair\nX,302,2,dR,analyte,\n",
        encoding="utf-8",
    )
    read_targets(path)


def _xlsx_entry(tmp_path, column):
    path = tmp_path / "targets.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Targets"
    book.active.append(["label", "mz", "expected_rt_min", column, "role", "istd_pair"])
    book.active.append(["X", "302", "2", "dR", "analyte", ""])
    book.save(path)
    load_target_list(path)


@pytest.mark.parametrize("column", ["method", "cid_nl_tag"])
@pytest.mark.parametrize("entry", [_csv_entry, _xlsx_entry, _workspace_targets_entry])
def test_earlier_method_column_is_rejected_not_ignored(tmp_path, entry, column):
    with pytest.raises(ConfigError, match=f"column {column} .*unsupported target"):
        entry(tmp_path, column)


def test_workspace_with_earlier_identification_methods_is_rejected(tmp_path):
    from xic_extractor.configuration.workspace_state import read_workspace

    (tmp_path / "workspace.json").write_text(
        json.dumps(
            {
                "application": {},
                "batch": {},
                "targets": [row(cid_nl_loss_da="116.0474")],
                "identification_methods": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="工作區設定檔無法讀取"):
        read_workspace(tmp_path)


def extract(tmp_path, raw, target_row, delta_mass, *, acquire_ms2=False):
    from tests.targeted_peak_selection_fixtures import _config
    from xic_extractor.extraction.target_extraction import extract_one_target
    from xic_extractor.ms2_scan_models import Ms2Window

    (target,) = create_target_list([target_row], delta_mass=delta_mass).targets
    results = {}
    extract_one_target(
        raw,
        _config(tmp_path),
        "sample",
        target,
        reference_rt=None,
        sample_window=None
        if acquire_ms2
        else Ms2Window(raw_stem="sample", rt_min=0, rt_max=4, events=()),
        results=results,
        diagnostics=[],
    )
    return results[target.label]


def test_delta_mass_reports_ratio_relative_to_the_quantitation_ion(tmp_path):
    from tests.targeted_delta_mass_fixtures import PatternRaw

    result = extract(tmp_path, PatternRaw(), row(delta_mass_mz="-2.0"), light_pair())
    assert result.selected_method_supported
    assert result.reported_peak_intensity == pytest.approx(100_100, rel=0.03)
    candidate = next(c for c in result.method_candidate_evidence if c.accepted)
    position = next(
        p
        for p in candidate.conditions[0].delta_mass_candidates[0].positions
        if p.position_label == "light"
    )
    assert position.height_ratio == pytest.approx(2, rel=0.03)
    assert position.shape_r > 0.99


def test_missing_ms2_fails_cid_nl_when_both_conditions_are_required(tmp_path):
    from tests.targeted_delta_mass_fixtures import PatternRaw

    result = extract(
        tmp_path,
        PatternRaw(),
        row(cid_nl_loss_da="116.0474", delta_mass_mz="-2.0"),
        light_pair(),
    )
    evidence = result.method_candidate_evidence[0]
    assert [c.status for c in evidence.conditions] == ["not_acquired", "matched"]
    assert [c.label for c in evidence.conditions] == ["CID-NL 116.0474", "Delta Mass"]
    assert not evidence.accepted
    assert not result.selected_method_supported
    assert result.reported_peak_area is None


@pytest.mark.parametrize(
    "loss,product_loss,status",
    [
        ("116.0474", 116.047344, "matched"),
        ("116.0474", 117.047344, "not_matched"),
        ("162.0528", 162.052823, "matched"),
        ("162.0528", 116.047344, "not_matched"),
    ],
)
def test_cid_nl_uses_the_declared_loss_value_on_the_same_peak(
    tmp_path, loss, product_loss, status
):
    import numpy as np

    from tests.targeted_delta_mass_fixtures import PatternRaw
    from xic_extractor.ms2_scan_models import (
        Ms2PrecursorMetadata,
        Ms2Scan,
        Ms2ScanEvent,
    )
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    class CidRaw(ScopedMs2WindowReader, PatternRaw):
        ms2_calls = 0

        def iter_ms2_scans(self, rt_min, rt_max):
            self.ms2_calls += 1
            yield Ms2ScanEvent(scan, None, 7)

        def read_ms1_mass_windows(self, requests):
            return tuple(Ms1PrecursorObservation(6, 1.99, (302.0,)) for _ in requests)

    scan = Ms2Scan(
        7,
        2.0,
        302.0,
        np.array([302 - product_loss]),
        np.array([1000.0]),
        1000.0,
        Ms2PrecursorMetadata(
            charge=1,
            isolation_width_da=1.6,
            isolation_offset_da=0,
            master_scan_number=6,
        ),
    )
    raw = CidRaw()
    result = extract(
        tmp_path,
        raw,
        row(cid_nl_loss_da=loss, delta_mass_mz="-2.0"),
        light_pair(),
        acquire_ms2=True,
    )
    assert raw.ms2_calls == 1
    candidate = next(
        c for c in result.method_candidate_evidence if 1.9 < c.rt_apex_min < 2.1
    )
    assert [c.status for c in candidate.conditions] == [status, "matched"]
    assert result.selected_method_supported is (status == "matched")
    assert (result.reported_peak_area is not None) is (status == "matched")


def test_list_shared_delta_mass_settings_round_trip_through_the_method_sheet(
    tmp_path,
):
    from dataclasses import fields

    from xic_extractor.configuration.delta_mass_table import DELTA_MASS_SETTING_KEYS
    from xic_extractor.configuration.targeted_identification import DeltaMassSettings

    assert set(DELTA_MASS_SETTING_KEYS.values()) == {
        item.name for item in fields(DeltaMassSettings)
    }
    settings = {
        "mz_tolerance_ppm": 12.0,
        "apex_floor_sec": 2.0,
        "scan_interval_multiplier": 1.5,
        "shape_window_sec": 8.0,
        "min_shape_r": 0.8,
    }
    method = create_target_list(
        [row(delta_mass_mz="-2.0")],
        delta_mass={"settings": settings, "ions": {}},
    )
    path = tmp_path / "shared.xlsx"
    save_target_list(method, path)
    loaded = load_target_list(path)
    assert loaded.delta_mass["settings"] == settings
    assert loaded.targets[0].ppm_tol == 12.0
    assert loaded.targets == method.targets
