import csv
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import NLResult
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.output.excel_pipeline import (
    _diagnostics_to_rows,
    _run_output_to_long_rows,
)
from xic_extractor.peak_detection.evidence_facts import (
    build_candidate_evidence_facts,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.signal_quality import SignalQuality
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent, RawReaderError

_OPEN_RAW_SEAM = "xic_extractor.extraction.target_extraction.open_raw"

_active_nl_fixture_results: list[NLResult] = []


@pytest.fixture(autouse=True)
def _disable_reader_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    global _active_nl_fixture_results
    _active_nl_fixture_results = []
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: [],
        raising=False,
    )
    install_fake_raw_headers(monkeypatch)


def test_run_raises_before_processing_when_reader_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: ["pythonnet is not installed"],
        raising=False,
    )
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args: pytest.fail(
            "open_raw should not be called after preflight failure"
        ),
    )

    with pytest.raises(RawReaderError, match="pythonnet is not installed"):
        _run(config, [_target("Analyte")])

    assert not config.output_csv.exists()
    assert not config.diagnostics_csv.exists()


def test_run_writes_success_rows_with_area_columns_and_optional_nl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[9.5, 9.5]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    8.5,
                    1200.0,
                    3400.25,
                    neutral_loss_required=False,
                ),
                _ok_peak(9.5, 2200.0, 4400.75),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.5, 3, 0, 2)])

    output = _run(config, targets, progress_callback=lambda *_args: None)

    results = output.file_results[0].results
    assert all(d.issue != "FILE_ERROR" for d in output.diagnostics)
    assert len(output.file_results) == 1
    long_rows = _run_output_to_long_rows(output, targets)
    no_nl_projection = results["NoNL"].targeted_product_projection
    assert no_nl_projection is not None
    assert no_nl_projection.conflict_reasons == ()
    assert [_core_long_row(row) for row in long_rows] == [
        {
            **_formatted_long_success_row("NoNL", results["NoNL"], nl=""),
            "Confidence": "LOW",
        },
        {
            **_formatted_long_success_row("WithNL", results["WithNL"], nl="OK"),
            "Confidence": "LOW",
        },
    ]
    assert [row["Counted Detection"] for row in long_rows] == ["TRUE", "TRUE"]
    assert [row["Product State"] for row in long_rows] == ["detected_clean"] * 2
    assert all(not row["Projection Conflict Reasons"] for row in long_rows)
    assert results["WithNL"].candidate_ms2_evidence is not None
    assert results["WithNL"].candidate_ms2_evidence.nl_match is True


@pytest.mark.parametrize("outside_height", [0.0, 50000.0])
def test_outside_strong_peak_cannot_erase_in_window_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside_height: float
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    windows: list[tuple[float, float]] = []

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            windows.append((rt_min, rt_max))
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            inside = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.04) ** 2)
            outside = outside_height * np.exp(-0.5 * ((rt - 6.0) / 0.04) ** 2)
            return rt, 10.0 + inside + outside

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    target = replace(_target("Inside", neutral_loss_da=None), rt_min=4.5, rt_max=5.5)
    output = _run(config, [target])
    result = output.file_results[0].results["Inside"]
    assert windows == [(0.0, 20.0)]
    assert result.reported_rt == pytest.approx(5.0, abs=0.002)
    assert result.reported_peak_area is not None
    known_area = 1000.0 * 0.04 * np.sqrt(2 * np.pi) * 60.0
    if outside_height == 0:
        assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    # A full-acquisition AsLS baseline can change quantitation under interference;
    # Targeted must retain the component and publish the shared physical answer.
    assert result.physical_evaluation is not None
    assert result.physical_evaluation.facts is not None
    assert (
        result.reported_peak_area
        == result.physical_evaluation.facts.area_counts_seconds
    )
    assert result.reported_peak_area > 0


def test_peak_only_outside_selection_still_answers_physical_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            outside = 5000.0 * np.exp(-0.5 * ((rt - 6.0) / 0.04) ** 2)
            return rt, 10.0 + outside

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    target = replace(_target("Empty", neutral_loss_da=None), rt_min=4.5, rt_max=5.5)
    output = _run(config, [target])
    result = output.file_results[0].results["Empty"]

    assert result.reported_rt == pytest.approx(6.0, abs=0.002)
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert evaluation.state == "available"
    assert evaluation.question.query_rt_min == pytest.approx(6.0)
    known_area = 5000.0 * 0.04 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert result.targeted_product_projection.counted_detection is True


@pytest.mark.parametrize("with_supported_main", [True, False])
def test_formal_target_ranks_only_physically_supported_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_supported_main: bool
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = replace(
        _target("Qualified", neutral_loss_da=None),
        expected_rt_min=4.7 if with_supported_main else 6.0,
        rt_min=4.0 if with_supported_main else 5.5,
        rt_max=7.0 if with_supported_main else 6.5,
    )

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, round((rt_max - rt_min) * 100) + 1)
            raw = np.zeros_like(rt)
            if with_supported_main:
                sigma = np.where(rt < 4.7, 0.07, 0.25)
                raw = 1000.0 * np.exp(-0.5 * ((rt - 4.7) / sigma) ** 2)
                raw[raw < 1e-4] = 0.0
            fragmented = 1000.0 * np.exp(-0.5 * ((rt - 6.0) / 0.12) ** 2)
            fragmented[fragmented < 1e-4] = 0.0
            fragmented[::2] = 0.0
            # The observed apex supplies adjacent acquired support. With empty
            # flanks, this cluster also qualifies through local hit enrichment.
            fragmented[int(np.argmin(np.abs(rt - 6.0)))] = 1000.0
            return rt, raw + fragmented

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, [target])
    result = output.file_results[0].results[target.label]
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert len(evaluation.segment_universe.segments) == (
        2 if with_supported_main else 1
    )
    projection = result.targeted_product_projection
    assert projection is not None
    long_rows = _run_output_to_long_rows(output, [target])

    if with_supported_main:
        # The asymmetric analytic lobe is supported even though its canonical
        # shape is less symmetric than the smoothed, interrupted tail.
        assert result.reported_rt == pytest.approx(4.7, abs=0.04)
        assert evaluation.state == "available"
        assert evaluation.facts is not None
        assert result.reported_peak_area == evaluation.facts.area_counts_seconds
        # The interrupted component fails the stronger local contrast gate;
        # only the supported main peak participates in ranking and shape review.
        assert projection.product_state == "detected_flagged"
        assert projection.counted_detection is True
        assert "trace_morphology_review" in projection.review_reasons
        assert projection.conflict_reasons == ()
        assert sorted(
            candidate.selection_apex_rt for candidate in result.peak_result.candidates
        ) == pytest.approx([4.7], abs=0.04)
        assert long_rows[0]["Counted Detection"] == "TRUE"
    else:
        # A dense hit cluster with empty flanks is acquired censored support.
        # Its intensity interruptions do not justify an unavailable answer.
        assert result.reported_rt == pytest.approx(6.0, abs=0.04)
        assert evaluation.state == "available"
        assert evaluation.facts is not None
        assert evaluation.integration is not None
        assert evaluation.facts.area_source == (
            "trapezoid_counts_seconds_on_observed_censored_signal"
        )
        assert "near_detection_limit" in evaluation.integration.boundary_sources
        assert result.reported_peak_area == evaluation.facts.area_counts_seconds
        assert result.reported_peak_area > 0
        assert projection.counted_detection is True
        assert _result_row(output, [target], "Qualified", 0)["RT"] != "ND"
        assert _result_row(output, [target], "Qualified", 0)["Area"] != "ND"
        assert long_rows[0]["Counted Detection"] == "TRUE"


def test_formal_target_drops_selection_when_final_physical_owner_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.peak_detection import (
        physical_peak_candidate_adapter as physical_owner,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        answer_physical_peak_question,
        mint_physical_peak_question,
    )
    from xic_extractor.peak_detection.physical_peak_models import (
        PHYSICAL_PEAK_NO_SELECTION_POLICY,
    )

    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = _target("Rejected", neutral_loss_da=None)
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory(peak_centers=[9.0]))

    def unavailable_answer(prepared, universe, question):
        # Consume a real, owner-bound unavailable answer. The consumer must
        # not fall back to the value ranked before the final owner response.
        rejected_question = mint_physical_peak_question(
            prepared.projection,
            universe,
            query_rt_min=universe.context_rt_start_min,
            selection_policy_version=PHYSICAL_PEAK_NO_SELECTION_POLICY,
        )
        return answer_physical_peak_question(prepared, universe, rejected_question)

    monkeypatch.setattr(
        physical_owner, "answer_physical_peak_question", unavailable_answer
    )
    output = _run(config, [target])
    result = output.file_results[0].results[target.label]

    assert result.physical_evaluation is not None
    assert result.physical_evaluation.reason == "no_selection"
    assert result.reported_rt is None
    assert result.reported_peak_area is None
    assert result.peak is None
    assert result.selected_hypothesis is None
    assert result.selection_decision is None
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.counted_detection is False
    row = _run_output_to_long_rows(output, [target])[0]
    assert row["RT"] == row["Area"] == "ND"


@pytest.mark.parametrize("neutral_loss_da", [None, 116.0474])
def test_retained_apex_outside_rt_hint_is_measured_without_inventing_nl_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    neutral_loss_da: float | None,
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    target = replace(
        _target("OutsideApex", neutral_loss_da=neutral_loss_da),
        rt_min=9.3,
        rt_max=12.3,
    )

    class BroadTailRaw(_FakeRaw):
        def iter_ms2_scans(self, rt_min, rt_max):
            return iter(())

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, 1801)
            return rt, 5000.0 * np.exp(-0.5 * ((rt - 12.6) / 0.8) ** 2)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: BroadTailRaw())
    _set_ms2_fixtures([NLResult("OK", 1.0, 10.8, 1, 0, 1)])

    output = _run(config, [target])
    result = output.file_results[0].results[target.label]
    evaluation = result.physical_evaluation
    assert evaluation is not None
    assert evaluation.segment_universe.state == "available"
    assert evaluation.segment_universe.segments
    assert all(
        segment.apex_rt_min > target.rt_max
        for segment in evaluation.segment_universe.segments
    )
    assert any(
        segment.left_rt_min < target.rt_max < segment.right_rt_min
        for segment in evaluation.segment_universe.segments
    )
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert evaluation.integration is not None
    assert result.peak is not None
    assert result.selected_hypothesis is not None
    assert result.reported_rt == pytest.approx(12.6, abs=0.02)
    assert result.reported_peak_area == evaluation.facts.area_counts_seconds
    assert result.reported_peak_area > 0
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.counted_detection is (
        neutral_loss_da is None
    )
    row = _run_output_to_long_rows(output, [target])[0]
    assert float(row["RT"]) == pytest.approx(12.6, abs=0.02)
    assert float(row["Area"]) > 0

    from xic_extractor.applications.targeted.application import (
        build_targeted_application_request,
    )
    from xic_extractor.applications.targeted.production_workflow import (
        execute_targeted_production_workflow,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config, [target], injection_order={"SampleA": 1}
        ),
        output,
    )
    assert len(finalized) == 1
    assert finalized[0].scientific_core.cell_observations == ()
    # A retained measurement can be saved without establishing CID-NL identity.
    assert finalized[0].completed_projection.accepted_cell_count == 1
    assert finalized[0].completed_projection.no_write_count == 0


def test_target_selection_window_uses_one_wide_physical_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    raw = _WidePhysicalContextRaw()
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    assert raw.windows == [(0.0, 20.0), (0.0, 20.0)]
    no_nl = output.file_results[0].results["NoNL"].physical_evaluation
    with_nl = output.file_results[0].results["WithNL"].physical_evaluation
    assert no_nl is not None and no_nl.facts is not None
    assert with_nl is not None and with_nl.facts is not None
    assert no_nl.prepared.profile == with_nl.prepared.profile
    assert no_nl.prepared == with_nl.prepared
    assert no_nl.segment_universe == (with_nl.segment_universe)
    assert no_nl.facts.apex_rt_min == pytest.approx(9.6, abs=0.03)
    assert no_nl.facts.right_rt_min > 10.0
    assert no_nl.facts.apex_rt_min < 10.0
    assert with_nl.facts.apex_rt_min == pytest.approx(no_nl.facts.apex_rt_min)
    assert with_nl.facts.left_rt_min == pytest.approx(no_nl.facts.left_rt_min)
    assert with_nl.facts.right_rt_min == pytest.approx(no_nl.facts.right_rt_min)
    assert with_nl.facts.area_counts_seconds == pytest.approx(
        no_nl.facts.area_counts_seconds
    )


def test_run_multi_sample_preserves_sample_attribution_and_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    (config.data_dir / "SampleB.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte", neutral_loss_da=None)]

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(
            peak_centers_by_sample={
                "SampleA.raw": [8.50],
                "SampleB.raw": [9.50],
            }
        ),
    )

    output = _run(config, targets)

    assert len(output.file_results) == 2
    res_a = output.file_results[0].results["Analyte"].peak_result.peak
    assert res_a.rt == pytest.approx(8.50, abs=0.05)
    assert output.file_results[1].sample_name == "SampleB"
    res_b = output.file_results[1].results["Analyte"].peak_result.peak
    assert res_b.rt == pytest.approx(9.50, abs=0.05)

    assert len(output.file_results) == 2
    assert output.file_results[0].sample_name == "SampleA"
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) == pytest.approx(
        8.50, abs=0.05
    )
    assert output.file_results[1].sample_name == "SampleB"
    assert float(_result_row(output, targets, "Analyte", 1)["RT"]) == pytest.approx(
        9.50, abs=0.05
    )
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) != float(
        _result_row(output, targets, "Analyte", 1)["RT"]
    )

    long_rows = _run_output_to_long_rows(output, targets)
    assert [r["SampleName"] for r in long_rows] == ["SampleA", "SampleB"]
    assert float(long_rows[0]["RT"]) == pytest.approx(8.50, abs=0.05)
    assert float(long_rows[1]["RT"]) == pytest.approx(9.50, abs=0.05)


def test_run_wires_candidate_ms2_evidence_into_scoring_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory(peak_centers=[8.5]))
    _set_ms2_fixtures([NLResult("OK", 1.0, 8.5, 1, 0, 1)])

    output = _run(config, [_target("WithNL")])

    assert output.file_results[0].error is None
    result = output.file_results[0].results["WithNL"]
    index = result.selected_cid_nl_candidate_index
    assert index is not None
    assert result.candidate_ms2_evidence is result.cid_nl_candidate_evidence[index]
    assert result.candidate_ms2_evidence.nl_match is True
    assert result.selected_cid_nl_supported is True
    assert result.targeted_product_projection.counted_detection is True


def test_rt_recovery_keeps_supported_physical_candidate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_bytes(b"")
    target = _target("WeakSupported")
    weak_height = 1000.0

    class Raw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.linspace(rt_min, rt_max, round((rt_max - rt_min) * 300) + 1)
            strong = 10000 * np.exp(-0.5 * ((rt - 8.0) / 0.08) ** 2)
            supported = weak_height * np.exp(-0.5 * ((rt - 9.0) / 0.08) ** 2)
            return rt, 10 + strong + supported

        def iter_ms2_scans(self, rt_min, rt_max):
            if rt_min <= 9.0 <= rt_max:
                scan = Ms2Scan(
                    scan_number=1,
                    rt=9.0,
                    precursor_mz=target.mz,
                    masses=np.array([target.mz - target.neutral_loss_da]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=1)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, [target])

    file_result = output.file_results[0]
    assert file_result.error is None
    result = file_result.results[target.label]
    assert result.reported_rt == pytest.approx(9.0, abs=0.02)
    known_area = (
        weak_height
        * 0.08
        * np.sqrt(2 * np.pi)
        * 60
        * math.erf(math.sqrt(math.log(10.0)))
    )
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert result.selected_cid_nl_supported is True
    assert result.targeted_product_projection.counted_detection is True


def test_run_does_not_write_intermediate_csv_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1200.0, 3400.25)]),
    )

    output = _run(config, targets)

    assert len(output.file_results) == 1
    assert not config.output_csv.exists()
    assert not config.output_csv.with_name("xic_results_long.csv").exists()
    assert not config.diagnostics_csv.exists()
    assert not config.output_csv.with_name("method_manifest.json").exists()
    assert not config.output_csv.with_name("peak_candidates.tsv").exists()
    assert not config.output_csv.with_name("peak_candidate_boundaries.tsv").exists()
    assert not config.output_csv.with_name("selected_envelope_diagnostics.tsv").exists()
    assert not config.output_csv.with_name(
        "peak_candidate_boundary_summary.tsv"
    ).exists()


def test_run_loads_scoring_inputs_from_config_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "injection_order_source": tmp_path / "sample_info.csv",
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1200.0, 3400.25)]),
    )
    config.injection_order_source.write_text(
        "Sample_Name,Injection_Order\nSampleA,1\n", encoding="utf-8"
    )

    output = _run(config, targets)

    assert [result.sample_name for result in output.file_results] == ["SampleA"]


def test_run_falls_back_to_main_pass_when_prepass_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True), _target("Analyte", istd_pair="ISTD")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(9.05, 1500.0, 2000.0),
                _ok_peak(9.07, 1200.0, 1800.0),
            ]
        ),
    )

    output = _run(config, targets)

    assert output.file_results[0].results["ISTD"].peak_result.peak is not None
    assert output.file_results[0].results["Analyte"].peak_result.peak is not None


def test_istd_single_pass_preserves_scoring_metadata_without_ms2_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    1500.0,
                    2000.0,
                    confidence="LOW",
                    reason="concerns: rt_prior (major)",
                    severities=((2, "rt_prior"),),
                )
            ]
        ),
    )

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.confidence == "LOW"
    assert result.peak_result.reason == "concerns: rt_prior (major)"
    assert result.confidence == "LOW"
    assert result.selection_decision is not None
    assert result.confidence == result.selection_decision.projected_confidence
    assert result.severities == ((2, "rt_prior"),)


def test_run_does_not_publish_peak_nl_without_a_physical_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]

    class FlatRaw(_FakeRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
            return rt, np.full(len(rt), 10.0)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: FlatRaw())
    _set_ms2_fixtures([NLResult("OK", 1.2, None, 3, 0, 2)])

    output = _run(config, targets)

    assert _result_row(output, targets, "WithNL", 0)["RT"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["Int"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["Area"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakStart"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakEnd"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["PeakWidth"] == "ND"
    assert _result_row(output, targets, "WithNL", 0)["NL"] == ""
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["Issue"] == "PEAK_NOT_FOUND"
    assert diagnostics[0]["Target"] == "WithNL"
    assert diagnostics[0]["Reason"] == "Physical peak unavailable: peak_not_found"


def test_run_projects_very_low_without_peak_scoped_nl_for_nd_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_failed_peak("PEAK_NOT_FOUND", n_points=15, max_smoothed=1234.0)]
        ),
    )
    _set_ms2_fixtures([NLResult("NL_FAIL", 200.0, None, 10, 1, 3)])

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    assert [_core_long_row(row) for row in long_rows] == [
        {
            "SampleName": "SampleA",
            "Group": "Other",
            "Target": "WithNL",
            "Role": "Analyte",
            "ISTD Pair": "",
            "RT": "ND",
            "Area": "ND",
            "NL": "",
            "Int": "ND",
            "PeakStart": "ND",
            "PeakEnd": "ND",
            "PeakWidth": "ND",
            "Confidence": "VERY_LOW",
            "Reason": "搜尋範圍內沒有可定量的 MS1 峰。",
        }
    ]
    assert long_rows[0]["Projection Reason"] == (
        "decision: not_counted; support: trace_coherent; "
        "not_counted: missing_positive_ms1_peak"
    )
    assert long_rows[0]["Product State"] == "not_counted"
    assert long_rows[0]["Counted Detection"] == "FALSE"
    assert "missing_positive_ms1_peak" in long_rows[0]["Projection Not Counted Reasons"]
    result = output.file_results[0].results["WithNL"]
    assert result.confidence == ""
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.product_state == "not_counted"


def test_run_leaves_confidence_blank_for_file_error_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "Bad.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(errors={"Bad.raw": RuntimeError("file locked")}),
    )

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    assert [_core_long_row(row) for row in long_rows] == [
        {
            "SampleName": "Bad",
            "Group": "Other",
            "Target": "WithNL",
            "Role": "Analyte",
            "ISTD Pair": "",
            "RT": "ERROR",
            "Area": "ERROR",
            "NL": "ERROR",
            "Int": "ERROR",
            "PeakStart": "ERROR",
            "PeakEnd": "ERROR",
            "PeakWidth": "ERROR",
            "Confidence": "",
            "Reason": "RAW 檔無法處理。",
        }
    ]
    assert long_rows[0]["Product State"] == "excluded"
    assert long_rows[0]["Counted Detection"] == "FALSE"
    assert long_rows[0]["Projection Exclusion Reasons"] == "file_error"


def test_run_writes_file_error_row_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "Bad.raw").write_text("", encoding="utf-8")
    (config.data_dir / "Good.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None), _target("WithNL")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(errors={"Bad.raw": RuntimeError("file locked")}),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0), _ok_peak(9.5, 1100.0, 2100.0)]),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, None, 2, 0, 1)])

    output = _run(config, targets)

    assert output.file_results[1].sample_name == "Good"
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["SampleName"] == "Bad"
    assert diagnostics[0]["Target"] == ""
    assert diagnostics[0]["Issue"] == "FILE_ERROR"
    assert "file locked" in diagnostics[0]["Reason"]
    assert output.file_results[0].error is not None


@pytest.mark.parametrize(
    ("peak_status", "n_points", "max_smoothed", "issue", "reason_part"),
    [
        ("NO_SIGNAL", 0, None, "NO_SIGNAL", "XIC empty"),
        ("WINDOW_TOO_SHORT", 7, None, "WINDOW_TOO_SHORT", "Only 7 scans"),
        ("PEAK_NOT_FOUND", 15, 25.0, "PEAK_NOT_FOUND", "prominence"),
    ],
)
def test_peak_diagnostic_owner_preserves_status_and_reason(
    tmp_path: Path,
    peak_status: str,
    n_points: int,
    max_smoothed: float | None,
    issue: str,
    reason_part: str,
) -> None:
    from xic_extractor.output.messages import build_diagnostic_records

    config = _config(tmp_path)
    target = _target("NoNL", neutral_loss_da=None)
    result = ExtractionResult(
        peak_result=_failed_peak(
            peak_status,
            n_points=n_points,
            max_smoothed=max_smoothed,
        ),
        nl=None,
    )
    diagnostics = build_diagnostic_records("SampleA", target, result, config)
    assert diagnostics[0].issue == issue
    assert diagnostics[0].target_label == "NoNL"
    assert reason_part in diagnostics[0].reason


@pytest.mark.parametrize(
    ("nl_result", "issue", "reason_part"),
    [
        (
            NLResult("NL_FAIL", 200.0, None, 10, 1, 3),
            "NL_FAIL",
            "subcause=no_target_product_support",
        ),
        (
            NLResult("NL_FAIL", None, None, 10, 1, 3),
            "NL_FAIL",
            "target-peak CID-NL support for 116.047 Da was not established",
        ),
        (
            NLResult("NO_MS2", None, None, 42, 2, 0),
            "NO_MS2",
            "no candidate-aligned MS2 trigger",
        ),
    ],
)
def test_run_writes_neutral_loss_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nl_result: NLResult,
    issue: str,
    reason_part: str,
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("WithNL")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0)]),
    )
    _set_ms2_fixtures([nl_result])

    output = _run(config, targets)

    assert _result_row(output, targets, "WithNL", 0)["NL"] == nl_result.to_token()
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert diagnostics[0]["Issue"] == issue
    assert diagnostics[0]["Target"] == "WithNL"
    assert reason_part in diagnostics[0]["Reason"]


def test_istd_no_ms2_keeps_ms1_peak_and_writes_confidence_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[9.05]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(9.05, 1200.0, 3400.25)]),
    )
    _set_ms2_fixtures([NLResult("NO_MS2", None, None, 12, 0, 0)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    expected = _formatted_peak_columns("ISTD", result)
    assert _result_row(output, targets, "ISTD", 0)["RT"] == expected["ISTD_RT"]
    assert _result_row(output, targets, "ISTD", 0)["Int"] == expected["ISTD_Int"]
    assert _result_row(output, targets, "ISTD", 0)["Area"] == expected["ISTD_Area"]
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert any(
        record["Target"] == "ISTD" and record["Issue"] == "NO_MS2"
        for record in diagnostics
    )
    assert any(
        record["Target"] == "ISTD"
        and record["Issue"] == "ISTD_CONFIDENCE"
        and "confidence=MEDIUM" in record["Reason"]
        and "flags=NO_MS2" in record["Reason"]
        and "MS1 peak retained" in record["Reason"]
        for record in diagnostics
    )


def test_run_reports_progress_and_stops_between_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    targets = [_target("NoNL", neutral_loss_da=None)]
    progress_calls: list[tuple[int, int, str]] = []
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0)]),
    )

    output = _run(
        config,
        targets,
        progress_callback=lambda current, total, filename: progress_calls.append(
            (current, total, filename)
        ),
        should_stop=lambda: bool(progress_calls),
    )

    assert progress_calls == [(1, 2, "A.raw")]
    assert [file_result.sample_name for file_result in output.file_results] == ["A"]


def test_paired_analyte_uses_strict_anchor_peak_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]
    strict_flags: list[bool] = []

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[13.70, 13.75]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _capturing_peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.75, 500.0, 800.0)],
            strict_flags,
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.75, 1, 0, 1),
        ]
    )

    _run(config, targets)

    assert strict_flags == [False, True]


def test_unpaired_isomers_use_expected_rt_to_select_their_own_supported_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        replace(_target("Early", rt_min=4.0, rt_max=6.0), expected_rt_min=4.9),
        replace(_target("Late", rt_min=24.0, rt_max=26.0), expected_rt_min=25.3),
    ]

    class Raw(_FakeRaw):
        def acquisition_rt_bounds(self):
            return 0.0, 60.0

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt = np.arange(rt_min, rt_max + 0.005, 0.01)
            sigma = np.where(rt < 5.2, 0.08, 0.23)
            early = 2500.0 * np.exp(-0.5 * ((rt - 5.2) / sigma) ** 2)
            late = 10000.0 * np.exp(-0.5 * ((rt - 25.35) / 0.08) ** 2)
            return rt, 10.0 + early + late

        def iter_ms2_scans(self, rt_min, rt_max):
            # Both isomers have independent NL support; the later peak has
            # denser DDA acquisition, which must not erase their RT identity.
            scan_rts = (5.2, 25.29, 25.32, 25.35, 25.38, 25.41)
            for number, scan_rt in enumerate(scan_rts, start=1):
                scan = Ms2Scan(
                    scan_number=number,
                    rt=scan_rt,
                    precursor_mz=258.1085,
                    masses=np.array([258.1085 - 116.0474]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=number)

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: Raw())
    output = _run(config, targets)
    results = output.file_results[0].results

    assert results["Early"].reported_rt == pytest.approx(5.2, abs=0.1)
    assert results["Late"].reported_rt == pytest.approx(25.35, abs=0.1)
    for result in results.values():
        assert result.reported_peak_area is not None
        assert result.reported_peak_area > 0
        assert result.targeted_product_projection is not None
        assert result.targeted_product_projection.counted_detection is True


def test_istd_far_peak_uses_expected_rt_as_identity_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    anchor_reference_rts: list[float | None] = []
    preferred_rts: list[float | None] = []

    def _fake_find_nl_anchor_rt(*_args, **kwargs) -> float:
        anchor_reference_rts.append(kwargs["reference_rt"])
        return 7.08

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        return _with_runtime_typed_scores(
            _ok_peak(7.08, 1200.0, 3400.25),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 7.08, 1, 0, 1)])

    _run(config, targets)

    assert anchor_reference_rts == []
    assert _dedupe_consecutive(preferred_rts) == [9.0]


def test_istd_near_peak_uses_expected_rt_as_identity_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    anchor_reference_rts: list[float | None] = []
    preferred_rts: list[float | None] = []

    def _fake_find_nl_anchor_rt(*_args, **kwargs) -> float:
        anchor_reference_rts.append(kwargs["reference_rt"])
        return 8.55

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        return _with_runtime_typed_scores(
            _ok_peak(8.55, 1200.0, 3400.25),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 8.55, 1, 0, 1)])

    _run(config, targets)

    assert anchor_reference_rts == []
    assert _dedupe_consecutive(preferred_rts) == [9.0]


def test_istd_peak_not_found_does_not_reacquire_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw()

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_failed_peak("PEAK_NOT_FOUND", n_points=15, max_smoothed=1234.0)]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "PEAK_NOT_FOUND"
    assert result.peak is None
    assert raw.windows == [(0.0, 20.0)]


def test_istd_no_signal_does_not_reacquire_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]

    class NoSignalRaw(_RecordingRaw):
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            rt, _intensity = super().extract_xic(mz, rt_min, rt_max, ppm_tol)
            return rt, np.zeros(len(rt))

    raw = NoSignalRaw()

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence([_failed_peak("NO_SIGNAL", n_points=0, max_smoothed=None)]),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "NO_SIGNAL"
    assert result.peak is None
    assert raw.windows == [(0.0, 20.0)]


def test_istd_weak_peak_keeps_single_complete_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw(peak_centers=[9.05], intensity_scale=0.02)

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    100.0,
                    200.0,
                    quality_flags=("poor_edge_recovery", "low_trace_continuity"),
                ),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.05, abs=0.02)
    known_area = 100.0 * 0.08 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert raw.windows == [(0.0, 20.0)]


def test_istd_low_confidence_peak_keeps_single_complete_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("ISTD", is_istd=True)]
    raw = _RecordingRaw(peak_centers=[9.05], intensity_scale=0.02)

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(
                    9.05,
                    100.0,
                    200.0,
                    confidence="VERY_LOW",
                    reason="decision: review only, not counted",
                ),
            ]
        ),
    )
    _set_ms2_fixtures([NLResult("OK", 1.0, 9.0, 1, 0, 1)])

    output = _run(config, targets)

    result = output.file_results[0].results["ISTD"]
    assert result.peak_result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.05, abs=0.02)
    known_area = 100.0 * 0.08 * np.sqrt(2 * np.pi) * 60
    assert result.reported_peak_area == pytest.approx(known_area, rel=0.04)
    assert raw.windows == [(0.0, 20.0)]


def test_paired_analyte_with_nl_remains_counted_despite_target_anchor_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=13.4, rt_max=13.8),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 13.06])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.06, 5000.0, 8000.0)]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.06, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    # The fixture supplies a symmetric Gaussian on a fixed acquired
    # grid. Its nearest sample to the known center is the independent apex
    # oracle; the mocked ranking result does not own physical peak facts.
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 13.06))]
    assert _result_row(output, targets, "Analyte", 0)["RT"] == f"{expected_apex_rt:.4f}"
    assert (
        _result_row(output, targets, "Analyte", 0)["Int"]
        == expected_peak["Analyte_Int"]
    )
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"]
        == expected_peak["Analyte_Area"]
    )
    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    assert analyte_row["Confidence"] == "LOW"
    assert analyte_row["Product State"] == "detected_clean"
    assert analyte_row["Counted Detection"] == "TRUE"
    assert analyte_row["Projection Conflict Reasons"] == ""
    assert output.file_results[0].results["Analyte"].confidence == "LOW"
    # Expected RT and the paired offset are identity priors, not DDA anchor crops.
    assert all(record.issue != "NL_ANCHOR_FALLBACK" for record in output.diagnostics)
    assert output.file_results[0].results["Analyte"].candidate_ms2_evidence.nl_match


def test_paired_analyte_accepts_peak_close_to_target_anchor_even_if_farther_from_istd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(peak_centers=[13.70, 13.95]),
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [_ok_peak(13.70, 2000.0, 3000.0), _ok_peak(13.95, 5000.0, 8000.0)]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("OK", 1.0, 13.95, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    result = output.file_results[0].results["Analyte"]
    expected = _formatted_peak_columns("Analyte", result)
    assert _result_row(output, targets, "Analyte", 0)["RT"] == expected["Analyte_RT"]
    assert float(_result_row(output, targets, "Analyte", 0)["RT"]) == pytest.approx(
        13.95, abs=0.03
    )
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"] == expected["Analyte_Area"]
    )
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert not any(
        record["Target"] == "Analyte" and record["Issue"] == "ANCHOR_RT_MISMATCH"
        for record in diagnostics
    )


def test_paired_analyte_keeps_target_nl_anchor_far_from_istd_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=14.1, rt_max=16.1),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]
    raw = _RecordingRaw(peak_centers=[13.70, 15.12])
    preferred_rts: list[float | None] = []

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        preferred_rts.append(preferred_rt)
        peak_rt = 13.70 if len(preferred_rts) == 1 else 15.12
        return _with_runtime_typed_scores(
            _ok_peak(peak_rt, 5000.0, 8000.0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _fake_select_peak_and_area,
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    _run(config, targets)

    assert _dedupe_consecutive(preferred_rts) == pytest.approx([13.70, 15.10])
    assert raw.windows[1] == (0.0, 20.0)


def test_paired_analyte_istd_rt_fallback_does_not_force_counted_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 13.72])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(13.70, 2000.0, 3000.0),
                _ok_peak(13.72, 5000.0, 8000.0, nl_match=False),
            ]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 13.72))]
    assert analyte_row["RT"] == f"{expected_apex_rt:.4f}"
    assert analyte_row["Area"] == expected_peak["Analyte_Area"]
    assert analyte_row["NL"] == "NL_FAIL"
    assert analyte_row["Product State"] == "not_counted"
    assert analyte_row["Counted Detection"] == "FALSE"
    assert (
        "cid_nl_method_not_supported" in analyte_row["Projection Not Counted Reasons"]
    )


def test_paired_analyte_fallback_preserves_not_counted_peak_in_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [
        _target("Analyte", istd_pair="ISTD", rt_min=12.7, rt_max=14.7),
        _target("ISTD", is_istd=True, rt_min=12.7, rt_max=14.7),
    ]

    raw = _RecordingRaw(peak_centers=[13.70, 14.31])
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_peak_selection.select_peak_and_area",
        _peak_sequence(
            [
                _ok_peak(13.70, 2000.0, 3000.0),
                _ok_peak(14.31, 5000.0, 8000.0, nl_match=False),
            ]
        ),
    )
    _set_ms2_fixtures(
        [
            NLResult("OK", 1.0, 13.70, 1, 0, 1),
            NLResult("NL_FAIL", None, None, 1, 0, 1),
        ]
    )

    output = _run(config, targets)

    expected_peak = _formatted_peak_columns(
        "Analyte", output.file_results[0].results["Analyte"]
    )
    assert len(raw.windows) == 2
    sampled_rt = np.arange(raw.windows[1][0], raw.windows[1][1] + 0.0025, 0.0025)
    expected_apex_rt = sampled_rt[np.argmin(np.abs(sampled_rt - 14.31))]
    assert _result_row(output, targets, "Analyte", 0)["RT"] == f"{expected_apex_rt:.4f}"
    assert (
        _result_row(output, targets, "Analyte", 0)["Area"]
        == expected_peak["Analyte_Area"]
    )
    long_rows = _run_output_to_long_rows(output, targets)
    analyte_row = next(row for row in long_rows if row["Target"] == "Analyte")
    assert analyte_row["RT"] == expected_peak["Analyte_RT"]
    assert analyte_row["Area"] == expected_peak["Analyte_Area"]
    assert analyte_row["Confidence"] == "VERY_LOW"
    assert analyte_row["Product State"] == "not_counted"
    assert analyte_row["Counted Detection"] == "FALSE"
    assert (
        "cid_nl_method_not_supported" in analyte_row["Projection Not Counted Reasons"]
    )
    assert output.file_results[0].results["Analyte"].confidence == "LOW"
    selected_peak = output.file_results[0].results["Analyte"].peak_result.peak
    assert selected_peak is not None
    istd_rt = output.file_results[0].results["ISTD"].reported_rt
    assert istd_rt is not None
    diagnostics = _diagnostics_to_rows(output.diagnostics)
    assert any(
        record["Target"] == "Analyte"
        and record["Issue"] == "ANCHOR_RT_MISMATCH"
        and f"Paired analyte peak RT {selected_peak.rt:.3f}" in record["Reason"]
        and f"ISTD anchor at {istd_rt:.3f}" in record["Reason"]
        and "allowed ±0.50 min" in record["Reason"]
        for record in diagnostics
    )


def _run(config: ExtractionConfig, targets: list[Target], **kwargs):
    from xic_extractor.extractor import acquire

    return acquire(config, targets, **kwargs)


def _result_row(output, targets, label, sample_index=0):
    rows = _run_output_to_long_rows(output, targets)
    sample = output.file_results[sample_index].sample_name
    return next(
        row for row in rows if row["SampleName"] == sample and row["Target"] == label
    )


def _config(tmp_path: Path) -> ExtractionConfig:
    data_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    output_dir.mkdir()
    dll_dir.mkdir()
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(
    label: str,
    *,
    neutral_loss_da: float | None = 116.0474,
    is_istd: bool = False,
    istd_pair: str = "",
    rt_min: float = 8.0,
    rt_max: float = 10.0,
) -> Target:
    return Target(
        label=label,
        mz=261.1085 if is_istd else 258.1085,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss_da,
        cid_nl_tag="dR",
        nl_ppm_warn=20.0 if neutral_loss_da is not None else None,
        nl_ppm_max=50.0 if neutral_loss_da is not None else None,
        is_istd=is_istd,
        istd_pair=istd_pair,
    )


def _ok_peak(
    rt: float,
    intensity: float,
    area: float,
    *,
    confidence: str | None = None,
    reason: str | None = None,
    severities: tuple[tuple[int, str], ...] = (),
    quality_flags: tuple[str, ...] = (),
    selection_apex_index: int = 7,
    neutral_loss_required: bool = True,
    ms2_present: bool = True,
    nl_match: bool = True,
) -> PeakDetectionResult:
    peak = PeakResult(
        rt=rt,
        intensity=intensity,
        intensity_smoothed=intensity,
        area=area,
        peak_start=rt - 0.5,
        peak_end=rt + 0.5,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=intensity,
        selection_apex_index=selection_apex_index,
        raw_apex_rt=rt,
        raw_apex_intensity=intensity,
        raw_apex_index=selection_apex_index,
        prominence=intensity * 0.5,
        quality_flags=quality_flags,
        region_scan_count=15,
        region_duration_min=1.0,
        region_edge_ratio=1.5,
    )
    return PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=15,
        max_smoothed=intensity,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence=confidence,
        reason=reason,
        severities=severities,
        candidate_scores=(
            _typed_candidate_score(
                candidate,
                neutral_loss_required=neutral_loss_required,
                ms2_present=ms2_present,
                nl_match=nl_match,
            ),
        ),
    )


def _ok_peak_with_rejected_candidate(
    rt: float,
    intensity: float,
    area: float,
) -> PeakDetectionResult:
    selected = _candidate(rt, intensity=intensity, area=area)
    rejected = _candidate(
        rt + 0.4,
        intensity=intensity * 0.4,
        area=area * 0.25,
        proposal_sources=("local_minimum",),
    )
    return PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=15,
        max_smoothed=intensity,
        n_prominent_peaks=2,
        candidates=(selected, rejected),
        candidate_scores=(
            _candidate_score(selected, confidence="HIGH", raw_score=90),
            _candidate_score(rejected, confidence="LOW", raw_score=45),
        ),
    )


def _candidate(
    rt: float,
    *,
    intensity: float = 1200.0,
    area: float = 3400.25,
    proposal_sources: tuple[str, ...] = ("legacy_savgol",),
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=intensity,
        intensity_smoothed=intensity,
        area=area,
        peak_start=rt - 0.5,
        peak_end=rt + 0.5,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=intensity,
        selection_apex_index=7,
        raw_apex_rt=rt,
        raw_apex_intensity=intensity,
        raw_apex_index=7,
        prominence=intensity * 0.5,
        proposal_sources=proposal_sources,
        source_apex_rank=1,
        region_scan_count=15,
        region_duration_min=1.0,
        region_edge_ratio=1.5,
    )


def _candidate_score(
    candidate: PeakCandidate,
    *,
    confidence: str,
    raw_score: int,
) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        confidence=confidence,
        reason=f"decision: {confidence.lower()}",
        raw_score=raw_score,
        support_labels=("strict_nl_ok",),
        concern_labels=() if confidence == "HIGH" else ("nl_fail",),
        cap_labels=() if confidence == "HIGH" else ("nl_fail_cap",),
    )


def _failed_peak(
    status: str, *, n_points: int, max_smoothed: float | None
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status=status,
        peak=None,
        n_points=n_points,
        max_smoothed=max_smoothed,
        n_prominent_peaks=0,
    )


def _peak_sequence(results: list[PeakDetectionResult]):
    pending = list(results)
    by_candidates: dict[PeakCandidatesResult, PeakDetectionResult] = {}

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        cached = by_candidates.get(candidates_result)
        if cached is not None:
            return cached
        result = _with_runtime_typed_scores(
            pending.pop(0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )
        by_candidates[candidates_result] = result
        return result

    return _fake_select_peak_and_area


def _capturing_peak_sequence(
    results: list[PeakDetectionResult],
    strict_flags: list[bool],
):
    pending = list(results)
    by_candidates: dict[PeakCandidatesResult, PeakDetectionResult] = {}

    def _fake_select_peak_and_area(
        candidates_result: PeakCandidatesResult,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        cached = by_candidates.get(candidates_result)
        if cached is not None:
            return cached
        strict_flags.append(strict_preferred_rt)
        result = _with_runtime_typed_scores(
            pending.pop(0),
            candidates_result=candidates_result,
            preferred_rt=preferred_rt,
            evidence_role=_typed_kwarg(_kwargs, "evidence_role"),
            istd_pair=_typed_kwarg(_kwargs, "istd_pair"),
            paired_istd_anchor_rt=_typed_float_kwarg(
                _kwargs,
                "paired_istd_anchor_rt",
            ),
        )
        by_candidates[candidates_result] = result
        return result

    return _fake_select_peak_and_area


def _anchor_sequence(results: list[float | None]):
    pending = list(results)

    def _fake_find_nl_anchor_rt(*_args, **_kwargs) -> float | None:
        return pending.pop(0)

    return _fake_find_nl_anchor_rt


def _dedupe_consecutive(values: list[float | None]) -> list[float | None]:
    deduped: list[float | None] = []
    for value in values:
        if not deduped or value != deduped[-1]:
            deduped.append(value)
    return deduped


def _set_ms2_fixtures(results: list[NLResult]) -> None:
    global _active_nl_fixture_results
    _active_nl_fixture_results = list(results)


def _typed_kwarg(kwargs: dict[str, object], name: str) -> str | None:
    value = kwargs.get(name)
    return value if isinstance(value, str) else None


def _typed_float_kwarg(kwargs: dict[str, object], name: str) -> float | None:
    value = kwargs.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _open_raw_factory(
    *,
    errors: dict[str, Exception] | None = None,
    peak_centers: list[float] | None = None,
    peak_centers_by_sample: dict[str, list[float]] | None = None,
):
    error_by_name = errors or {}
    sample_centers = peak_centers_by_sample or {}

    def _fake_open_raw(path: Path, dll_dir: Path, **_options):
        if path.name in error_by_name:
            raise error_by_name[path.name]
        centers = sample_centers.get(path.name, peak_centers)
        return _FakeRaw(peak_centers=centers)

    return _fake_open_raw


class _FakeRaw:
    def acquisition_rt_bounds(self):
        return 0.0, 20.0

    def __init__(self, *, peak_centers: list[float] | None = None) -> None:
        self._peak_centers = list(peak_centers or [])
        self._scan_rt: float | None = (
            self._peak_centers[0] if self._peak_centers else 10.0
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        center = (
            self._peak_centers.pop(0) if self._peak_centers else (rt_min + rt_max) / 2
        )
        self._scan_rt = center
        rt = np.arange(rt_min, rt_max + 0.0025, 0.0025)
        intensity = 10.0 + 5000.0 * np.exp(-0.5 * ((rt - center) / 0.08) ** 2)
        return rt, intensity

    def iter_ms2_scans(self, rt_min: float, rt_max: float):
        # Acquisition is immutable and shared across all targets, not generated
        # by whichever target happened to query NL most recently.
        scans = _active_nl_fixture_results
        for index, result in enumerate(scans):
            scan_rt = (
                result.best_scan_rt
                if result.best_scan_rt is not None
                else self._scan_rt
            )
            if (
                result.status == "NO_MS2"
                or scan_rt is None
                or not rt_min <= scan_rt <= rt_max
            ):
                continue
            precursors = (
                (258.1085, 261.1085)
                if len(scans) == 1
                else ((261.1085,) if index == 0 else (258.1085,))
            )
            for precursor_mz in precursors:
                product_mz = (
                    precursor_mz - 116.0474 * (1 + result.best_ppm / 1_000_000)
                    if result.best_ppm is not None
                    else 300.0
                )
                scan_number = index * 2 + (1 if precursor_mz == 258.1085 else 2)
                scan = Ms2Scan(
                    scan_number=scan_number,
                    rt=scan_rt,
                    precursor_mz=precursor_mz,
                    masses=np.array([product_mz]),
                    intensities=np.array([1000.0]),
                    base_peak=1000.0,
                )
                yield Ms2ScanEvent(scan=scan, parse_error=None, scan_number=scan_number)


class _RecordingRaw(_FakeRaw):
    def __init__(
        self, *, peak_centers: list[float] | None = None, intensity_scale: float = 1.0
    ) -> None:
        super().__init__(peak_centers=peak_centers)
        self.windows: list[tuple[float, float]] = []
        self.intensity_scale = intensity_scale

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        self.windows.append((rt_min, rt_max))
        rt, intensity = super().extract_xic(mz, rt_min, rt_max, ppm_tol)
        return rt, intensity * self.intensity_scale


class _WidePhysicalContextRaw(_FakeRaw):
    def __init__(self) -> None:
        super().__init__()
        self._scan_rt = 9.6
        self.windows: list[tuple[float, float]] = []

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        self.windows.append((rt_min, rt_max))
        rt = np.linspace(rt_min, rt_max, 1201)
        selected = 5000.0 * np.exp(-0.5 * ((rt - 9.6) / 0.55) ** 2)
        outside_selection = 15000.0 * np.exp(-0.5 * ((rt - 11.4) / 0.12) ** 2)
        return rt, 10.0 + selected + outside_selection


def _core_long_row(row: dict[str, str]) -> dict[str, str]:
    core_headers = (
        "SampleName",
        "Group",
        "Target",
        "Role",
        "ISTD Pair",
        "RT",
        "Area",
        "NL",
        "Int",
        "PeakStart",
        "PeakEnd",
        "PeakWidth",
        "Confidence",
        "Reason",
    )
    return {header: row[header] for header in core_headers}


def _formatted_peak_columns(
    label: str,
    result: ExtractionResult,
) -> dict[str, str]:
    evaluation = result.physical_evaluation
    assert evaluation is not None
    facts = evaluation.facts
    integration = evaluation.integration
    assert facts is not None
    assert integration is not None
    return {
        f"{label}_RT": f"{facts.apex_rt_min:.4f}",
        f"{label}_Int": f"{integration.height_raw:.0f}",
        f"{label}_IntSmoothed": f"{facts.height:.0f}",
        f"{label}_Area": f"{facts.area_counts_seconds:.2f}",
        f"{label}_PeakStart": f"{facts.left_rt_min:.4f}",
        f"{label}_PeakEnd": f"{facts.right_rt_min:.4f}",
        f"{label}_PeakWidth": (f"{facts.right_rt_min - facts.left_rt_min:.4f}"),
    }


def _formatted_long_success_row(
    label: str,
    result: ExtractionResult,
    *,
    nl: str,
) -> dict[str, str]:
    peak = _formatted_peak_columns(label, result)
    return {
        "SampleName": "SampleA",
        "Group": "Other",
        "Target": label,
        "Role": "Analyte",
        "ISTD Pair": "",
        "RT": peak[f"{label}_RT"],
        "Area": peak[f"{label}_Area"],
        "NL": nl,
        "Int": peak[f"{label}_Int"],
        "PeakStart": peak[f"{label}_PeakStart"],
        "PeakEnd": peak[f"{label}_PeakEnd"],
        "PeakWidth": peak[f"{label}_PeakWidth"],
        "Confidence": "HIGH",
        "Reason": "MS2 見到預期的中性丟失。" if nl == "OK" else "MS1 峰形合格。",
    }


def _typed_candidate_score(
    candidate: PeakCandidate,
    *,
    neutral_loss_required: bool = True,
    ms2_present: bool = True,
    nl_match: bool = True,
    role: str = "Analyte",
    istd_pair: str = "",
    preferred_rt: float | None = None,
    paired_istd_anchor_rt: float | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
) -> PeakCandidateScore:
    rt_array, intensity_array = _synthetic_peak_trace(candidate.selection_apex_rt)
    apex_index = int(np.argmin(np.abs(rt_array - candidate.selection_apex_rt)))
    facts = build_candidate_evidence_facts(
        candidate,
        ScoringContext(
            rt_array=rt_array,
            intensity_array=intensity_array,
            apex_index=apex_index,
            signal_quality=SignalQuality("available", 1.0, 100.0, False),
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=ms2_present,
            nl_match=nl_match,
            rt_prior=(
                preferred_rt
                if preferred_rt is not None
                else candidate.selection_apex_rt
            ),
            rt_prior_sigma=0.1,
            rt_min=rt_min if rt_min is not None else candidate.peak.peak_start,
            rt_max=rt_max if rt_max is not None else candidate.peak.peak_end,
            neutral_loss_required=neutral_loss_required,
            ms2_trace_strength="strong" if ms2_present and nl_match else "none",
            ms2_alignment_source="fixture_candidate",
            trigger_scan_count=3 if ms2_present else 0,
            strict_nl_scan_count=1 if ms2_present and nl_match else 0,
        ),
        role=role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt_min=paired_istd_anchor_rt,
    )
    return PeakCandidateScore(
        candidate=candidate,
        confidence="HIGH",
        reason="decision: accepted by typed fixture",
        raw_score=95,
        support_labels=("legacy_fixture_support",),
        evidence_facts=facts,
    )


def _synthetic_peak_trace(apex_rt: float) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(apex_rt - 0.5, apex_rt + 0.5, 201)
    intensity = 10.0 + 5000.0 * np.exp(-0.5 * ((rt - apex_rt) / 0.08) ** 2)
    return rt, intensity


def _with_runtime_typed_scores(
    result: PeakDetectionResult,
    *,
    candidates_result: PeakCandidatesResult,
    preferred_rt: float | None,
    evidence_role: str | None,
    istd_pair: str | None,
    paired_istd_anchor_rt: float | None,
) -> PeakDetectionResult:
    if result.peak is None:
        return result
    result = _bind_fixture_candidates_to_physical_segments(result, candidates_result)
    rt_min = min(
        candidate.peak.peak_start for candidate in candidates_result.candidates
    )
    rt_max = max(candidate.peak.peak_end for candidate in candidates_result.candidates)
    existing_scores = {score.candidate: score for score in result.candidate_scores}
    scores = tuple(
        _typed_candidate_score(
            candidate,
            neutral_loss_required=_existing_neutral_loss_required(
                existing_scores.get(candidate)
            ),
            ms2_present=_existing_ms2_present(existing_scores.get(candidate)),
            nl_match=_existing_nl_match(existing_scores.get(candidate)),
            role=evidence_role or "Analyte",
            istd_pair=istd_pair or "",
            preferred_rt=preferred_rt,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        for candidate in result.candidates
    )
    return replace(result, candidate_scores=scores)


def _bind_fixture_candidates_to_physical_segments(
    result: PeakDetectionResult,
    candidates_result: PeakCandidatesResult,
) -> PeakDetectionResult:
    if not result.candidates:
        return result
    physical_candidates = candidates_result.candidates
    if not physical_candidates:
        return result
    replacements: dict[PeakCandidate, PeakCandidate] = {}
    for candidate in result.candidates:
        physical = min(
            physical_candidates,
            key=lambda item: abs(item.selection_apex_rt - candidate.selection_apex_rt),
        )
        # Selection can choose an existing physical candidate, not invent new
        # measurements while copying its segment label.
        replacements[candidate] = physical
    selected = next(
        candidate for candidate in result.candidates if candidate.peak == result.peak
    )
    return replace(
        result,
        peak=replacements[selected].peak,
        candidates=tuple(replacements[candidate] for candidate in result.candidates),
        candidate_scores=tuple(
            replace(score, candidate=replacements.get(score.candidate, score.candidate))
            for score in result.candidate_scores
        ),
    )


def _existing_neutral_loss_required(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return facts.chemical.neutral_loss_required


def _existing_ms2_present(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return bool(facts.chemical.ms2_present)


def _existing_nl_match(score: PeakCandidateScore | None) -> bool:
    facts = score.evidence_facts if score is not None else None
    if facts is None:
        return True
    return bool(facts.chemical.nl_match)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
