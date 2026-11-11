"""Targeted Delta Mass ions: portable target parameters and peak-local qualification."""

import json

import numpy as np
import pytest

from tests.targeted_delta_mass_fixtures import PatternRaw
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_method_files import (
    create_targeted_method,
    load_targeted_method,
    save_targeted_method,
)


def pattern_matches(result):
    return tuple(
        match
        for candidate in result.method_candidate_evidence
        for condition in candidate.conditions
        for match in condition.delta_mass_candidates
    )


def target_row(**changes):
    return {
        "label": "custom target",
        "mz": "302",
        "expected_rt_min": "2",
        "role": "analyte",
        "istd_pair": "",
        "cid_nl_loss_da": "",
        "delta_mass_mz": "-2.0",
        **changes,
    }


def details(*ions, label="custom target", **settings):
    return {
        "settings": {"mz_tolerance_ppm": 12, "apex_floor_sec": 2, **settings},
        "ions": {
            label: list(ions)
            or [
                {
                    "label": "light",
                    "offset_mz": -2.0,
                    "ratio_min": 0.1,
                    "ratio_max": 10,
                    "compare_shape": True,
                }
            ]
        },
    }


def test_delta_mass_round_trip_keeps_target_as_the_quantitation_ion(tmp_path):
    method = create_targeted_method({}, [target_row()], delta_mass=details())
    path = tmp_path / "custom.xlsx"
    save_targeted_method(method, path)
    loaded = load_targeted_method(path)
    assert loaded.targets == method.targets
    assert loaded.delta_mass == method.delta_mass
    target = loaded.targets[0]
    condition = target.identification.delta_mass
    assert condition.quant_position == "base"
    assert [(ion.label, ion.offset_mz) for ion in condition.ions] == [("light", -2.0)]
    assert target.ppm_tol == 12.0
    assert target.neutral_loss_da is None


def test_new_ions_are_named_by_offset_and_default_to_record_only_ratios():
    target = create_targeted_method(
        {}, [target_row(delta_mass_mz="+4.005213, -2.0157")]
    ).targets[0]
    ions = target.identification.delta_mass.ions
    assert [ion.label for ion in ions] == ["+4.0052", "-2.0157"]
    # Stored offsets keep full precision; only the name is rounded.
    assert ions[0].offset_mz == 4.005213
    assert all(
        (ion.ratio_min, ion.ratio_max, ion.compare_shape) == (0.1, 10.0, True)
        for ion in ions
    )


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"mz": "1"}, "positive m/z"),
        ({"delta_mass_mz": "-0.001"}, "overlapping"),
        ({"delta_mass_mz": "+1.00001, +1.00002"}, "太接近"),
        ({"delta_mass_mz": "0"}, "不可為 0"),
        ({"delta_mass_mz": "abc"}, "Δm/z numbers"),
    ],
)
def test_invalid_delta_mass_is_not_silently_ms1_only(changes, message):
    with pytest.raises(ConfigError, match=message):
        create_targeted_method({}, [target_row(**changes)])


def test_details_must_name_declared_ions_and_targets():
    stray = {"label": "x", "offset_mz": 3.0}
    with pytest.raises(ConfigError, match="一致"):
        create_targeted_method({}, [target_row()], delta_mass=details(stray))
    with pytest.raises(ConfigError, match="不存在的目標"):
        create_targeted_method(
            {}, [target_row()], delta_mass=details(label="someone else")
        )


@pytest.mark.parametrize("use_method_file", [False, True])
def test_workspace_and_run_receipts_keep_delta_mass_details(tmp_path, use_method_file):
    from xic_extractor.configuration.loader import (
        load_config_snapshot,
        prepare_targeted_config,
    )
    from xic_extractor.configuration.workspace_state import save_workspace

    config_dir = tmp_path / "config"
    row = target_row()
    save_workspace(
        config_dir,
        settings={"data_dir": str(tmp_path), "dll_dir": str(tmp_path)},
        targets=[row],
        delta_mass=details(),
    )
    saved = json.loads((config_dir / "workspace.json").read_text(encoding="utf-8"))
    assert saved["delta_mass"]["settings"]["mz_tolerance_ppm"] == 12
    method_path = None
    if use_method_file:
        method_path = tmp_path / "portable.xlsx"
        save_targeted_method(
            create_targeted_method({}, [row], delta_mass=details()), method_path
        )
    prepared = prepare_targeted_config(
        config_dir, method_path=method_path, output_dir=tmp_path / "results"
    )
    try:
        _, replayed = load_config_snapshot(
            prepared.input_config_dir, output_dir=tmp_path / "replay"
        )
        assert replayed == list(prepared.targets)
        assert replayed[0].identification.delta_mass.ions[0].label == "light"
    finally:
        prepared.cleanup()


def test_workbench_export_import_carries_details_without_mutating_preview_state(
    tmp_path,
):
    import base64

    from tests.workbench_config_fixtures import analysis_workspace
    from xic_workbench.target_import import preview_target_upload

    workspace = analysis_workspace(tmp_path / "config", {})
    values = {
        "settings": {"data_dir": str(tmp_path), "dll_dir": str(tmp_path)},
        "targets": [target_row()],
        "delta_mass": details(),
    }
    workspace.action("targeted/save", values)
    original = (workspace.config_dir / "workspace.json").read_bytes()
    path = tmp_path / "portable.xlsx"
    workspace.action("targets/export", {**values, "path": str(path)})
    preview = preview_target_upload(
        path.name, base64.b64encode(path.read_bytes()).decode(), {}
    )
    assert preview["kind"] == "method"
    assert preview["targets"][0]["delta_mass_mz"] == "-2.0"
    assert preview["delta_mass"]["ions"]["custom target"][0]["offset_mz"] == -2.0
    assert (workspace.config_dir / "workspace.json").read_bytes() == original
    workspace.action("targeted/method/save", {**values, "name": "Reusable"})
    loaded = workspace.action(
        "targeted/method/load",
        {
            "path": str(workspace.config_dir / "methods" / "Reusable.xlsx"),
            "settings": values["settings"],
        },
    )
    assert loaded["delta_mass"] == preview["delta_mass"]


def extract_pattern(
    tmp_path, raw, *, delta_mass=None, row=None, defer=False, sample="sample"
):
    from tests.targeted_peak_selection_fixtures import _config
    from xic_extractor.extraction.target_extraction import extract_one_target

    target = create_targeted_method(
        {}, [row or target_row()], delta_mass=delta_mass or details()
    ).targets[0]
    results = {}
    extract_one_target(
        raw,
        _config(tmp_path),
        sample,
        target,
        reference_rt=None,
        results=results,
        diagnostics=[],
        defer_selection=defer,
    )
    return target, results[target.label]


def test_targeted_delta_mass_quantifies_declared_position_not_lowest_mass(tmp_path):
    raw = PatternRaw()
    target, result = extract_pattern(tmp_path, raw)
    assert result.peak is not None
    assert result.reported_rt == pytest.approx(2.0, abs=0.01)
    # The heavy channel has half the light channel's height. Quantitation must
    # remain the declared 302 channel; sum/pattern-base quantitation is wrong.
    assert result.reported_peak_intensity == pytest.approx(100_100.0, rel=0.03)
    assert result.selected_method_supported
    assert result.targeted_product_projection.counted_detection
    assert (
        "identification_method_supported"
        in result.targeted_product_projection.support_reasons
    )
    assert result.nl_token is None
    assert {request.mz for request in raw.requests} == {300.0, 302.0}
    assert all(
        position.observed_mz is None
        for match in pattern_matches(result)
        for position in match.positions
    )


@pytest.mark.parametrize(
    "raw", [PatternRaw(partner_rt=2.5), PatternRaw(partner_mz=300.1)]
)
def test_unmatched_pattern_never_falls_back_to_ms1_only(tmp_path, raw):
    _, result = extract_pattern(tmp_path, raw)
    assert not result.selected_method_supported
    assert not result.targeted_product_projection.counted_detection
    assert result.reported_peak_area is None


def test_pattern_ratio_outlier_remains_evidence_not_a_veto(tmp_path):
    ion = {"label": "light", "offset_mz": -2.0, "ratio_min": 8.0, "ratio_max": 9.0}
    _, result = extract_pattern(tmp_path, PatternRaw(), delta_mass=details(ion))
    assert result.selected_method_supported
    assert result.targeted_product_projection.counted_detection
    light = next(
        p for p in pattern_matches(result)[0].positions if p.position_label == "light"
    )
    assert light.ratio_status == "below_range"


def test_coeluting_partner_with_incompatible_shape_is_not_counted(tmp_path):
    _, result = extract_pattern(tmp_path, PatternRaw(partner_width=0.035))
    assert not result.targeted_product_projection.counted_detection
    assert result.reported_peak_area is None
    assert any(
        match.reject_reason == "discriminating_shape_below_threshold"
        for match in pattern_matches(result)
    )


def test_batch_completion_preserves_sample_specific_delta_mass_areas(tmp_path):
    from tests.targeted_peak_selection_fixtures import _config
    from xic_extractor.extraction.result_models import FileResult, RunOutput
    from xic_extractor.extraction.targeted_batch_selection import (
        complete_targeted_candidate_batch,
    )

    files = []
    for sample, scale in [("one", 1.0), ("two", 3.0)]:
        target, result = extract_pattern(
            tmp_path, PatternRaw(scale=scale), defer=True, sample=sample
        )
        files.append(FileResult(sample, {target.label: result}))
    completed = complete_targeted_candidate_batch(
        RunOutput(files, []), _config(tmp_path)
    )
    first, second = [file.results[target.label] for file in completed.file_results]
    assert first.selected_method_supported and second.selected_method_supported
    assert second.reported_peak_area == pytest.approx(
        first.reported_peak_area * 3.0, rel=0.03
    )


def test_delta_mass_reaches_finalized_workbook_and_readonly_inspector(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    from dataclasses import replace

    from openpyxl import load_workbook

    from tests.raw_header_fakes import install_fake_raw_headers
    from tests.targeted_peak_selection_fixtures import _config
    from xic_extractor.applications.targeted.application import (
        TargetedApplicationService,
        build_targeted_application_request,
    )
    from xic_extractor.extraction import pipeline
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    install_fake_raw_headers(monkeypatch)
    config = replace(
        _config(tmp_path), data_dir=tmp_path / "raw", parallel_mode="serial"
    )
    config.data_dir.mkdir()
    for sample in ["one", "two", "missing"]:
        (config.data_dir / f"{sample}.raw").write_bytes(
            b"placeholder " + sample.encode()
        )
    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _path: [])
    raws = {
        "one": PatternRaw(),
        "two": PatternRaw(scale=3),
        "missing": PatternRaw(partner_mz=300.1),
    }
    monkeypatch.setattr(
        "xic_extractor.extraction.target_extraction.open_raw",
        lambda path, *_args, **_kwargs: nullcontext(raws[path.stem]),
    )
    target = create_targeted_method({}, [target_row()], delta_mass=details()).targets[0]
    path = tmp_path / "results.xlsx"
    execution = TargetedApplicationService(pipeline.run_pipeline).execute_application(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"one": 1, "two": 2, "missing": 3},
            excel_output_path=path,
        )
    )
    by_sample = {
        file.sample_name: file.results[target.label]
        for file in execution.active_output.file_results
    }
    assert by_sample["one"].targeted_product_projection.counted_detection
    assert by_sample["two"].reported_peak_area == pytest.approx(
        by_sample["one"].reported_peak_area * 3, rel=0.03
    )
    assert not by_sample["missing"].targeted_product_projection.counted_detection
    assert by_sample["missing"].reported_peak_area is None
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(book["Delta Mass Evidence"].values)
        assert (
            len(rows) == 5
        )  # header + 2 measured positions per hit; misses are in Method Evidence
        header = rows[0]
        evidence = [dict(zip(header, row, strict=True)) for row in rows[1:]]
        assert {(r["SampleName"], r["Pattern Status"]) for r in evidence} == {
            ("one", "pass"),
            ("two", "pass"),
        }
        assert {r["Specificity"] for r in evidence} == {"Delta Mass -2.0000"}
        targets = list(book["Targets"].values)
        assert dict(zip(targets[0], targets[1], strict=True))["Specificity"] == (
            "Delta Mass -2.0000"
        )
        results = list(book["XIC Results"].values)
        reasons = [
            dict(zip(results[0], row, strict=True))["Reason"] for row in results[1:]
        ]
        assert any("特異性條件" in reason for reason in reasons)
    finally:
        book.close()
    source = TargetedWorkbookSource(path)
    (ion,) = source.delta_mass_ions["custom target"]
    assert (ion["Position"], float(ion["Delta m/z"])) == ("light", -2.0)
    assert {row["SampleName"] for row in source.method_evidence} == set(raws)


class ThreeIonRaw(PatternRaw):
    """Quant 302 at 2.0 min; light 300 and heavy 304 eluting later by seconds."""

    def __init__(self, *, light_sec, heavy_sec):
        super().__init__()
        self.peaks = [
            (302.0, 2.0),
            (300.0, 2.0 + light_sec / 60),
            (304.0, 2.0 + heavy_sec / 60),
        ]

    def extract_xic(self, mz, start, end, ppm):
        values = np.full(self.rt.shape, 100.0)
        for mass, apex in self.peaks:
            if abs(mass - mz) <= mz * ppm * 1e-6:
                values += 100_000.0 * np.exp(-0.5 * ((self.rt - apex) / 0.08) ** 2)
        keep = (self.rt >= start) & (self.rt <= end)
        return self.rt[keep], values[keep]


@pytest.mark.parametrize("heavy_sec,counted", [(0.5, True), (1.5, False)])
def test_coelution_is_measured_from_the_quantitation_ion(tmp_path, heavy_sec, counted):
    # The light ion (lowest m/z) elutes 0.75 s after the quantitation ion; the
    # heavy ion must still be within the 1 s tolerance of the quantitation ion,
    # not merely of the light ion.
    ions = (
        {"label": "light", "offset_mz": -2.0, "compare_shape": False},
        {"label": "heavy", "offset_mz": 2.0, "compare_shape": False},
    )
    _, result = extract_pattern(
        tmp_path,
        ThreeIonRaw(light_sec=0.75, heavy_sec=heavy_sec),
        row=target_row(delta_mass_mz="-2.0, +2.0"),
        delta_mass=details(*ions, apex_floor_sec=1),
    )
    assert result.targeted_product_projection.counted_detection is counted
    assert (result.reported_peak_area is not None) is counted
