"""Per-target specificity: CID-NL value, Delta Mass ions, and earlier-format reading."""

import json

import openpyxl
import pytest

from tests.targeted_fixtures import ScopedMs2WindowReader
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import (
    DeltaMassCondition,
    QuantRelativeIon,
    target_specificity,
)
from xic_extractor.configuration.targeted_method_files import (
    create_targeted_method,
    load_targeted_method,
    save_targeted_method,
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
    (target,) = create_targeted_method({}, [row(**changes)]).targets
    assert target_specificity(target) == expected


def test_cid_nl_alone_ranks_while_with_delta_mass_it_is_a_required_condition():
    alone, both = create_targeted_method(
        {},
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


def _legacy_workbook(path, method_rows, target_rows):
    book = openpyxl.Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Method")
    sheet.append(["key", "value"])
    sheet.append(["strategy", "cid-nl"])
    sheet = book.create_sheet("Targets")
    sheet.append(["label", "mz", "expected_rt_min", "method", "role", "istd_pair"])
    for values in target_rows:
        sheet.append(values)
    sheet = book.create_sheet("Identification Methods")
    header = [
        "Method",
        "Combine",
        "m/z Tolerance ppm",
        "Condition",
        "Kind",
        "CID-NL Tag",
        "Position",
        "Delta m/z",
        "Ratio Min",
        "Ratio Max",
        "Compare Shape",
        "Apex Floor sec",
    ]
    sheet.append(header)
    for values in method_rows:
        sheet.append([values.get(column, "") for column in header])
    book.save(path)


def _pair_rows(name="Pair", combine="all", ppm=20, apex=1, cid=True):
    rows = (
        [
            {
                "Method": name,
                "Combine": combine,
                "m/z Tolerance ppm": ppm,
                "Condition": 1,
                "Kind": "cid_nl",
                "CID-NL Tag": "dR",
            },
        ]
        if cid
        else []
    )
    number = 2 if cid else 1
    rows += [
        {
            "Method": name,
            "Combine": combine,
            "m/z Tolerance ppm": ppm,
            "Condition": number,
            "Kind": "delta_mass",
            "Position": "quant",
            "Delta m/z": 0,
            "Apex Floor sec": apex,
        },
        {
            "Method": name,
            "Combine": combine,
            "m/z Tolerance ppm": ppm,
            "Condition": number,
            "Kind": "delta_mass",
            "Position": "m1",
            "Delta m/z": 1.003355,
            "Ratio Min": 0.05,
            "Ratio Max": 0.5,
            "Apex Floor sec": apex,
        },
    ]
    return rows


def test_earlier_workbook_with_named_methods_reads_as_parameter_columns(tmp_path):
    path = tmp_path / "legacy.xlsx"
    _legacy_workbook(
        path,
        _pair_rows(),
        [
            ["iso", "302", "2", "Pair", "analyte", ""],
            ["plain", "258.1085", "9", "R", "analyte", ""],
            ["ms1", "200", "5", "", "analyte", ""],
        ],
    )
    method = load_targeted_method(path)
    iso, plain, ms1 = method.targets
    assert [dict(r)["cid_nl_loss_da"] for r in method.target_rows] == [
        "116.0474",
        "132.0423",
        "",
    ]
    assert dict(method.target_rows[0])["delta_mass_mz"] == "+1.003355"
    assert plain.neutral_loss_da == 132.0423 and ms1.neutral_loss_da is None
    (ion,) = iso.identification.delta_mass.ions
    # Names survive; the earlier method default did not compare shape.
    assert (ion.label, ion.offset_mz, ion.ratio_min, ion.compare_shape) == (
        "m1",
        1.003355,
        0.05,
        False,
    )
    assert iso.identification.cid_nl_loss_da == 116.0474
    resaved = tmp_path / "new.xlsx"
    save_targeted_method(method, resaved)
    book = openpyxl.load_workbook(resaved, read_only=True)
    try:
        assert set(book.sheetnames) == {"Method", "Targets", "Delta Mass"}
    finally:
        book.close()
    assert load_targeted_method(resaved).targets == method.targets


@pytest.mark.parametrize(
    "method_rows,message",
    [
        (_pair_rows(combine="any"), "任一符合"),
        (
            _pair_rows(cid=False)
            + [
                {
                    "Method": "Pair",
                    "Combine": "all",
                    "m/z Tolerance ppm": 20,
                    "Condition": 2,
                    "Kind": "cid_nl",
                    "CID-NL Tag": "dR",
                },
                {
                    "Method": "Pair",
                    "Combine": "all",
                    "m/z Tolerance ppm": 20,
                    "Condition": 3,
                    "Kind": "cid_nl",
                    "CID-NL Tag": "R",
                },
            ],
            "同類條件",
        ),
        (
            [
                {
                    "Method": "Pair",
                    "Combine": "all",
                    "m/z Tolerance ppm": 20,
                    "Condition": 1,
                    "Kind": "cid_nl",
                    "CID-NL Tag": "dR",
                }
            ],
            # A required CID-NL condition must not become ranking-only support.
            "必要條件",
        ),
    ],
)
def test_methods_the_new_format_cannot_express_are_rejected_with_targets(
    tmp_path, method_rows, message
):
    path = tmp_path / "legacy.xlsx"
    _legacy_workbook(path, method_rows, [["iso", "302", "2", "Pair", "analyte", ""]])
    with pytest.raises(ConfigError, match=f"iso.*{message}"):
        load_targeted_method(path)


def test_delta_mass_methods_with_different_shared_settings_are_rejected(tmp_path):
    path = tmp_path / "legacy.xlsx"
    _legacy_workbook(
        path,
        _pair_rows("A") + _pair_rows("B", apex=3),
        [
            ["a", "302", "2", "A", "analyte", ""],
            ["b", "402", "2", "B", "analyte", ""],
        ],
    )
    with pytest.raises(ConfigError, match="共用一組：a、b"):
        load_targeted_method(path)


def test_earlier_workspace_state_reads_as_parameter_columns(tmp_path):
    from xic_extractor.configuration.workspace_state import read_workspace

    (tmp_path / "workspace.json").write_text(
        json.dumps(
            {
                "application": {},
                "batch": {},
                "targets": [
                    row(label="iso", cid_nl_tag="Pair"),
                    row(label="plain", cid_nl_tag="dR"),
                ],
                "identification_methods": {
                    "Pair": {
                        "conditions": [
                            {
                                "kind": "delta_mass",
                                "positions": [
                                    {"label": "quant", "offset_mz": 0.0},
                                    {"label": "light", "offset_mz": -2.0},
                                ],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = read_workspace(tmp_path)
    assert "identification_methods" not in state
    assert [(t["cid_nl_loss_da"], t["delta_mass_mz"]) for t in state["targets"]] == [
        ("", "-2.0"),
        ("116.0474", ""),
    ]
    assert state["delta_mass"]["ions"]["iso"][0]["label"] == "light"


def test_named_method_without_definition_is_rejected(tmp_path):
    path = tmp_path / "targets.csv"
    path.write_text(
        "label,mz,expected_rt_min,method,role,istd_pair\nX,302,2,Mine,analyte,\n",
        encoding="utf-8",
    )
    from xic_extractor.configuration.targets import read_targets

    with pytest.raises(ConfigError, match="X: 找不到方法 'Mine'"):
        read_targets(path)


def extract(tmp_path, raw, target_row, delta_mass, *, acquire_ms2=False):
    from tests.targeted_peak_selection_fixtures import _config
    from xic_extractor.extraction.target_extraction import extract_one_target
    from xic_extractor.ms2_scan_models import Ms2Window

    (target,) = create_targeted_method({}, [target_row], delta_mass=delta_mass).targets
    results = {}
    extract_one_target(
        raw,
        _config(tmp_path),
        "sample",
        target,
        reference_rt=None,
        sample_window=None
        if acquire_ms2
        else Ms2Window(sample_key="sample", rt_min=0, rt_max=4, events=()),
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
        # A loss outside dR/R/MeR is used as declared.
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
    method = create_targeted_method(
        {},
        [row(delta_mass_mz="-2.0")],
        delta_mass={"settings": settings, "ions": {}},
    )
    path = tmp_path / "shared.xlsx"
    save_targeted_method(method, path)
    loaded = load_targeted_method(path)
    assert loaded.delta_mass["settings"] == settings
    assert loaded.targets[0].ppm_tol == 12.0
    assert loaded.targets == method.targets
