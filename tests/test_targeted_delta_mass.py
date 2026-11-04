"""Targeted custom patterns: portable intent and peak-local method qualification."""

import json

import numpy as np
import pytest

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_method_files import (
    create_targeted_method,
    load_targeted_method,
    save_targeted_method,
)


def pattern():
    return {
        "combine_mode": "all",
        "mz_tolerance_ppm": 12,
        "conditions": [
            {
                "kind": "delta_mass",
                "positions": [
                    {
                        "label": "light",
                        "offset_mz": -2.0,
                        "ratio_min": 0.1,
                        "ratio_max": 10,
                        "compare_shape": True,
                    },
                    {
                        "label": "heavy",
                        "offset_mz": 0.0,
                        "ratio_min": 0.1,
                        "ratio_max": 10,
                    },
                ],
                "apex_floor_sec": 2,
                "min_shape_r": 0.9,
            }
        ],
    }


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
        "method": "my pair",
        **changes,
    }


def test_delta_mass_method_round_trip_binds_quantitation_position(tmp_path):
    method = create_targeted_method(
        {}, [target_row()], identification_methods={"my pair": pattern()}
    )
    path = tmp_path / "custom.xlsx"
    save_targeted_method(method, path)
    loaded = load_targeted_method(path)
    assert loaded.targets == method.targets
    target = loaded.targets[0]
    assert target.method_name == "my pair"
    assert target.identification_method.conditions[0].quant_position == "heavy"
    assert target.ppm_tol == 12.0
    assert target.identification_method.conditions[0].positions[0].offset_mz == -2.0
    assert target.neutral_loss_da is None
    assert loaded.identification_methods == method.identification_methods


@pytest.mark.parametrize("changes", [{"method": "absent"}, {"mz": "1"}])
def test_invalid_pattern_binding_is_not_silently_ms1_only(changes):
    with pytest.raises(ConfigError):
        create_targeted_method(
            {}, [target_row(**changes)], identification_methods={"my pair": pattern()}
        )


def test_missing_pattern_definition_is_rejected():
    with pytest.raises(ConfigError, match="my pair"):
        create_targeted_method({}, [target_row()])


def test_overlapping_query_windows_cannot_count_one_ion_as_two_positions():
    method = pattern()
    method["conditions"][0]["positions"][0]["offset_mz"] = -0.001
    with pytest.raises(ConfigError, match="overlap"):
        create_targeted_method(
            {}, [target_row()], identification_methods={"my pair": method}
        )


@pytest.mark.parametrize("use_method_file", [False, True])
def test_workspace_and_run_receipts_keep_custom_method(tmp_path, use_method_file):
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
        identification_methods={"my pair": pattern()},
    )
    saved = json.loads((config_dir / "workspace.json").read_text(encoding="utf-8"))
    assert saved["identification_methods"]["my pair"]["mz_tolerance_ppm"] == 12.0
    method_path = None
    if use_method_file:
        method_path = tmp_path / "portable.xlsx"
        save_targeted_method(
            create_targeted_method(
                {},
                [row],
                identification_methods={"my pair": pattern()},
            ),
            method_path,
        )
    prepared = prepare_targeted_config(
        config_dir, method_path=method_path, output_dir=tmp_path / "results"
    )
    try:
        _, replayed = load_config_snapshot(
            prepared.input_config_dir, output_dir=tmp_path / "replay"
        )
        assert replayed == list(prepared.targets)
        assert replayed[0].identification_method.conditions[0].quant_position == "heavy"
    finally:
        prepared.cleanup()


def test_workbench_export_import_carries_method_without_mutating_preview_state(
    tmp_path,
):
    import base64

    from xic_workbench.analysis_workspace import AnalysisWorkspace
    from xic_workbench.target_import import preview_target_upload

    workspace = AnalysisWorkspace(tmp_path / "config", {})
    values = {
        "settings": {"data_dir": str(tmp_path), "dll_dir": str(tmp_path)},
        "targets": [target_row()],
        "identification_methods": {"my pair": pattern()},
    }
    workspace.action("targeted/save", values)
    original = (workspace.config_dir / "workspace.json").read_bytes()
    path = tmp_path / "portable.xlsx"
    workspace.action("targets/export", {**values, "path": str(path)})
    preview = preview_target_upload(
        path.name, base64.b64encode(path.read_bytes()).decode(), {}
    )
    assert preview["kind"] == "method"
    assert preview["targets"][0]["method"] == "my pair"
    assert (
        preview["identification_methods"]["my pair"]["conditions"][0]["positions"][0][
            "offset_mz"
        ]
        == -2.0
    )
    assert (workspace.config_dir / "workspace.json").read_bytes() == original
    workspace.action("targeted/method/save", {**values, "name": "Reusable"})
    loaded = workspace.action(
        "targeted/method/load",
        {
            "path": str(workspace.config_dir / "methods" / "Reusable.xlsx"),
            "settings": values["settings"],
        },
    )
    assert loaded["identification_methods"] == preview["identification_methods"]


class PatternRaw:
    """Independent analytic chromatograms; mass windows select actual channels."""

    def __init__(
        self, *, scale=1.0, partner_rt=2.0, partner_mz=300.0, partner_width=0.08
    ):
        self.rt = np.linspace(0, 4, 481)
        self.scale = scale
        self.partner_rt = partner_rt
        self.partner_mz = partner_mz
        self.partner_width = partner_width
        self.requests = []

    def acquisition_rt_bounds(self):
        return 0.0, 4.0

    def extract_xic(self, mz, start, end, ppm):
        values = np.full(self.rt.shape, 100.0)
        for mass, apex, height, width in [
            (302.0, 2.0, 100_000.0, 0.08),
            (self.partner_mz, self.partner_rt, 200_000.0, self.partner_width),
        ]:
            if abs(mass - mz) <= mz * ppm * 1e-6:
                values += (
                    self.scale * height * np.exp(-0.5 * ((self.rt - apex) / width) ** 2)
                )
        keep = (self.rt >= start) & (self.rt <= end)
        return self.rt[keep], values[keep]

    def extract_xic_many(self, requests):
        from xic_extractor.xic_models import XICTrace

        self.requests.extend(requests)
        return tuple(
            XICTrace.from_arrays(*self.extract_xic(r.mz, r.rt_min, r.rt_max, r.ppm_tol))
            for r in requests
        )


def extract_pattern(
    tmp_path, raw, *, methods=None, row=None, defer=False, sample="sample"
):
    from tests.test_targeted_peak_selection import _config
    from xic_extractor.extraction.target_extraction import extract_one_target

    target = create_targeted_method(
        {},
        [row or target_row()],
        identification_methods=methods or {"my pair": pattern()},
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
    method = pattern()
    method["conditions"][0]["positions"][0]["ratio_min"] = 8.0
    method["conditions"][0]["positions"][0]["ratio_max"] = 9.0
    _, result = extract_pattern(tmp_path, PatternRaw(), methods={"my pair": method})
    assert result.selected_method_supported
    assert result.targeted_product_projection.counted_detection
    assert pattern_matches(result)[0].positions[0].ratio_status == "below_range"


def test_coeluting_partner_with_incompatible_shape_is_not_counted(tmp_path):
    _, result = extract_pattern(tmp_path, PatternRaw(partner_width=0.035))
    assert not result.targeted_product_projection.counted_detection
    assert result.reported_peak_area is None
    assert any(
        match.reject_reason == "discriminating_shape_below_threshold"
        for match in pattern_matches(result)
    )


def test_batch_completion_preserves_sample_specific_delta_mass_areas(tmp_path):
    from tests.test_targeted_peak_selection import _config
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


def test_custom_pattern_reaches_finalized_workbook_and_readonly_inspector(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    from dataclasses import replace

    from openpyxl import load_workbook

    from tests.raw_header_fakes import install_fake_raw_headers
    from tests.test_targeted_peak_selection import _config
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
    target = create_targeted_method(
        {}, [target_row()], identification_methods={"my pair": pattern()}
    ).targets[0]
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
        results = list(book["XIC Results"].values)
        reasons = [
            dict(zip(results[0], row, strict=True))["Reason"] for row in results[1:]
        ]
        assert any("辨認方法" in reason for reason in reasons)
    finally:
        book.close()
    source = TargetedWorkbookSource(path)
    assert (
        source.identification_methods["my pair"]["conditions"][0]["positions"][0][
            "offset_mz"
        ]
        == -2.0
    )
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
    method = {
        "conditions": [
            {
                "kind": "delta_mass",
                "apex_floor_sec": 1,
                "positions": [
                    {"label": "light", "offset_mz": -2.0},
                    {"label": "quant", "offset_mz": 0.0},
                    {"label": "heavy", "offset_mz": 2.0},
                ],
            }
        ]
    }
    _, result = extract_pattern(
        tmp_path,
        ThreeIonRaw(light_sec=0.75, heavy_sec=heavy_sec),
        methods={"my pair": method},
    )
    assert result.targeted_product_projection.counted_detection is counted
    assert (result.reported_peak_area is not None) is counted
