"""Unified method intent and same-peak condition composition."""

import pytest

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import (
    parse_identification_methods,
)
from xic_extractor.configuration.targeted_method_files import create_targeted_method


def pair_condition():
    return {
        "kind": "delta_mass",
        "positions": [
            {"label": "quant", "offset_mz": 0.0},
            {"label": "light", "offset_mz": -2.0, "compare_shape": True},
        ],
    }


def method_definition(combine_mode="all", *, with_cid=True):
    return {
        "combine_mode": combine_mode,
        "conditions": ([{"kind": "cid_nl", "tag": "dR"}] if with_cid else [])
        + [pair_condition()],
    }


def row(**changes):
    return {
        "label": "target",
        "mz": "302",
        "expected_rt_min": "2",
        "cid_nl_tag": "",
        "role": "analyte",
        "istd_pair": "",
        **changes,
    }


def test_method_normalizes_negative_offsets_without_moving_quantitation():
    methods = parse_identification_methods({"Pair": method_definition()})
    condition = methods["Pair"].conditions[1]
    pattern = condition.pattern_settings(methods["Pair"].mz_tolerance_ppm, "Pair", 1)
    assert [(p.label, p.delta_da) for p in pattern.positions] == [
        ("light", 0),
        ("quant", 2),
    ]
    assert pattern.shape_reference_position == "quant"
    assert pattern.ratio_reference_position == "quant"
    assert pattern.anchor_positions == ("quant",)


def test_default_method_and_per_target_override_are_one_portable_intent(tmp_path):
    from xic_extractor.configuration.targeted_method_files import (
        load_targeted_method,
        save_targeted_method,
    )

    method = create_targeted_method(
        {},
        [row(), row(label="exception", method="ms1")],
        identification_methods={"Pair": method_definition()},
        default_method="Pair",
    )
    path = tmp_path / "method.xlsx"
    save_targeted_method(method, path)
    loaded = load_targeted_method(path)
    assert loaded.targets == method.targets
    assert loaded.default_method == "Pair"
    from xic_extractor.configuration.targets import read_targets

    assert tuple(read_targets(path)) == loaded.targets
    assert loaded.targets[0].identification_method.name == "Pair"
    assert loaded.targets[1].identification_method is None
    assert loaded.targets[1].neutral_loss_da is None


@pytest.mark.parametrize("kind", ["hcd_pi", "unknown"])
def test_unimplemented_condition_is_not_silently_accepted(kind):
    with pytest.raises(ValueError):
        parse_identification_methods({"bad": {"conditions": [{"kind": kind}]}})


def test_unknown_default_is_rejected_even_when_every_row_overrides_it():
    with pytest.raises(ConfigError, match="default_method"):
        create_targeted_method({}, [row(method="ms1")], default_method="missing")


@pytest.mark.parametrize("retired", ["delta_mass_method", "quant_position"])
def test_superseded_delta_binding_cannot_silently_become_ms1(retired):
    with pytest.raises(ConfigError):
        create_targeted_method({}, [row(**{retired: "old binding"})])


def test_workbench_persists_effective_method_defaults_for_the_gui(tmp_path):
    from xic_workbench.analysis_workspace import AnalysisWorkspace

    workspace = AnalysisWorkspace(tmp_path / "config", {})
    workspace.action(
        "targeted/save",
        {
            "settings": {"data_dir": str(tmp_path), "dll_dir": str(tmp_path)},
            "targets": [row()],
            "identification_methods": {"Simple": {}},
            "default_method": "Simple",
        },
    )
    saved = workspace.bootstrap()["identification_methods"]["Simple"]
    assert saved == {"conditions": [], "combine_mode": "all", "mz_tolerance_ppm": 20}


def extract_method(tmp_path, raw, definition, *, window=None, acquire_ms2=False):
    from tests.test_targeted_peak_selection import _config
    from xic_extractor.extraction.target_extraction import extract_one_target
    from xic_extractor.ms2_scan_models import Ms2Window

    target = create_targeted_method(
        {}, [row()], identification_methods={"Pair": definition}, default_method="Pair"
    ).targets[0]
    results = {}
    extract_one_target(
        raw,
        _config(tmp_path),
        "sample",
        target,
        reference_rt=None,
        sample_window=None
        if acquire_ms2
        else window or Ms2Window(sample_key="sample", rt_min=0, rt_max=4, events=()),
        results=results,
        diagnostics=[],
    )
    return results[target.label]


def test_relative_pair_quantifies_quant_channel_and_reports_relative_ratio(tmp_path):
    from tests.test_targeted_delta_mass import PatternRaw

    result = extract_method(tmp_path, PatternRaw(), method_definition(with_cid=False))
    assert result.selected_method_supported
    assert result.reported_peak_intensity == pytest.approx(100_100, rel=0.03)
    assert result.targeted_product_projection.counted_detection
    candidate = next(c for c in result.method_candidate_evidence if c.accepted)
    position = next(
        p
        for p in candidate.conditions[0].delta_mass_candidates[0].positions
        if p.position_label == "light"
    )
    assert position.height_ratio == pytest.approx(2, rel=0.03)
    assert position.shape_r > 0.99


def test_empty_method_does_not_blame_identification_for_a_missing_peak(tmp_path):
    import numpy as np

    from tests.test_targeted_delta_mass import PatternRaw

    class FlatRaw(PatternRaw):
        def extract_xic(self, mz, start, end, ppm):
            keep = (self.rt >= start) & (self.rt <= end)
            return self.rt[keep], np.full(keep.sum(), 100.0)

    result = extract_method(tmp_path, FlatRaw(), {"conditions": []})
    assert result.reported_peak_area is None
    assert (
        "identification_method_not_supported"
        not in result.targeted_product_projection.not_counted_reasons
    )


@pytest.mark.parametrize("mode,accepted", [("all", False), ("any", True)])
def test_missing_ms2_is_separate_from_failed_condition(tmp_path, mode, accepted):
    from tests.test_targeted_delta_mass import PatternRaw

    result = extract_method(tmp_path, PatternRaw(), method_definition(mode))
    evidence = result.method_candidate_evidence[0]
    assert [condition.status for condition in evidence.conditions] == [
        "not_acquired",
        "matched",
    ]
    assert evidence.accepted is accepted
    assert result.selected_method_supported is accepted
    assert (result.reported_peak_area is not None) is accepted


@pytest.mark.parametrize(
    "product_offset,status,accepted", [(0, "matched", True), (1, "not_matched", False)]
)
def test_cid_and_pair_use_the_same_peak_and_keep_acquired_failures(
    tmp_path, product_offset, status, accepted
):
    import numpy as np

    from tests.test_targeted_delta_mass import PatternRaw
    from xic_extractor.ms2_scan_models import (
        Ms2PrecursorMetadata,
        Ms2Scan,
        Ms2ScanEvent,
    )
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    class CidRaw(PatternRaw):
        ms2_calls = 0

        def iter_ms2_scans(self, rt_min, rt_max):
            self.ms2_calls += 1
            yield Ms2ScanEvent(scan, None, 7)

        def read_ms1_mass_windows(self, requests):
            return tuple(Ms1PrecursorObservation(6, 1.99, (302.0,)) for _ in requests)

    # Independent synthetic dR loss (C5H8O3); an unrelated product is +1 Da.
    scan = Ms2Scan(
        7,
        2.0,
        302.0,
        np.array([302 - 116.047344 + product_offset]),
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
    result = extract_method(tmp_path, raw, method_definition(), acquire_ms2=True)
    assert raw.ms2_calls == 1
    candidate = next(
        c for c in result.method_candidate_evidence if 1.9 < c.rt_apex_min < 2.1
    )
    assert [c.status for c in candidate.conditions] == [status, "matched"]
    assert result.selected_method_supported is accepted
    assert (result.reported_peak_area is not None) is accepted


@pytest.mark.parametrize("mode,accepted", [("all", False), ("any", True)])
def test_conditions_on_different_peaks_cannot_satisfy_and(tmp_path, mode, accepted):
    import numpy as np

    from tests.test_targeted_delta_mass import PatternRaw

    class SplitRaw(PatternRaw):
        def extract_xic(self, mz, start, end, ppm):
            values = np.full(self.rt.shape, 100.0)
            for mass, apex in [(302, 2), (302, 3), (300, 2), (304, 3)]:
                if abs(mass - mz) <= mz * ppm * 1e-6:
                    values += 100_000 * np.exp(-0.5 * ((self.rt - apex) / 0.06) ** 2)
            keep = (self.rt >= start) & (self.rt <= end)
            return self.rt[keep], values[keep]

    definition = method_definition(mode, with_cid=False)
    second = pair_condition()
    second["positions"][1]["offset_mz"] = 2
    definition["conditions"].append(second)
    result = extract_method(tmp_path, SplitRaw(), definition)
    statuses = [
        [c.status for c in peak.conditions] for peak in result.method_candidate_evidence
    ]
    assert [status for status in statuses if "matched" in status] == [
        ["matched", "not_matched"],
        ["not_matched", "matched"],
    ]
    assert not any(all(answer == "matched" for answer in status) for status in statuses)
    assert result.selected_method_supported is accepted
    assert (result.reported_peak_area is not None) is accepted
