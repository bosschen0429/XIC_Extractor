from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from tests.test_discovery_ms1_backfill import (
    _group,
    _multi_tag_settings,
    _seed,
)
from tests.test_discovery_ms1_backfill import (
    _OwnerBoundBatchRaw as _BatchRaw,
)
from tests.test_discovery_ms1_backfill import (
    _peak_config as _backfill_peak_config,
)
from tests.test_discovery_ms1_backfill import (
    _settings as _backfill_settings,
)
from tests.test_discovery_pipeline import (
    _OwnerBoundFakeRawHandle,
    _peak_config,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.csv_writer import build_discovery_review_note
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem, DiscoverySettings
from xic_extractor.discovery.ms1_backfill import (
    _backfill_ms1_candidates_with_acquisition,
)
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.pipeline import run_discovery
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution.attempt import derive_runtime_acquisition_metrics
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICTrace

_TEST_RUN_SPEC_ID = "processing-run:" + ("0" * 64)


class _OwnerBoundBatchRaw(_BatchRaw):
    def __init__(self, source, **kwargs) -> None:
        super().__init__(source, **kwargs)
        # Include baseline-return samples and context beyond both peak boundaries.
        self.rt = np.asarray([7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 8.0, 8.1, 8.2])
        self.intensity = np.asarray([0.0, 0.0, 0.0, 10.0, 100.0, 10.0, 0.0, 0.0, 0.0])


@pytest.mark.parametrize(
    ("fixture_name", "expected_apices", "representative_scan"),
    (
        ("discovery_normal_single_peak", (18.45591733148333,), 14712),
        ("discovery_same_peak_seeds", (38.190113531483334,), None),
        ("discovery_true_double_peak", (15.25524139625,), None),
        ("discovery_non_peak_background", (), None),
    ),
)
def test_discovery_retained_physical_peak_cardinality(
    tmp_path: Path,
    fixture_name: str,
    expected_apices: tuple[float, ...],
    representative_scan: int | None,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_candidates_csv
    from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / f"{fixture_name}.json").read_text()
    )
    rt = np.asarray([float.fromhex(v) for v in fixture["trace"]["rt"]])
    intensity = np.asarray([float.fromhex(v) for v in fixture["trace"]["intensity"]])
    raw_path = tmp_path / f"{fixture_name}.raw"
    raw_path.write_bytes(b"retained exact numerical XIC fixture")
    settings = _backfill_settings()
    profile = SignalProcessingProfile()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings, signal_processing_profile=profile
    )
    source = spec.acquisition_inputs[0].acquisition_source

    class RetainedTraceRaw(_OwnerBoundBatchRaw):
        def scan_window_for_request(self, request):
            return (
                int(np.searchsorted(rt, request.rt_min)) + 1,
                int(np.searchsorted(rt, request.rt_max, side="right")),
            )

        def extract_xic_many(self, requests):
            self.request_batches.append(tuple(requests))
            self.raw_chromatogram_call_count += len(requests)
            return tuple(
                XICTrace.from_arrays(
                    rt[(rt >= request.rt_min) & (rt <= request.rt_max)],
                    intensity[(rt >= request.rt_min) & (rt <= request.rt_max)],
                )
                for request in requests
            )

    groups = tuple(
        replace(
            _group(
                (
                    replace(
                        _seed(
                            scan_number=scan,
                            rt=seed_rt,
                            precursor_mz=mz,
                            product_mz=product,
                        ),
                        raw_file=raw_path,
                        sample_stem=raw_path.stem,
                        precursor_mz_basis=fixture.get(
                            "precursor_mz_basis", "product_plus_neutral_loss"
                        ),
                    ),
                )
            ),
            precursor_mz_basis=fixture.get(
                "precursor_mz_basis", "product_plus_neutral_loss"
            ),
        )
        for scan, seed_rt, mz, product in zip(
            fixture["seed_scans"],
            fixture["seed_rt"],
            fixture["precursor_mz"],
            fixture.get(
                "product_mz", [mz - 116.0474 for mz in fixture["precursor_mz"]]
            ),
            strict=True,
        )
    )
    result = _backfill_ms1_candidates_with_acquisition(
        RetainedTraceRaw(source),
        groups,
        acquisition_source=source,
        run_spec_id=spec.run_spec_id,
        settings=settings,
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    path = write_discovery_candidates_csv(
        tmp_path / "candidates.csv", result.candidates
    )
    valid = [c for c in read_discovery_candidates_csv(path) if c.ms1_peak_found]
    if representative_scan is not None:
        # The complete main envelope has one representative. Earlier seeds
        # have clipped whole contours, rather than independently closed peaks.
        assert all(not c.is_provisional for c in valid)
    assert sorted(c.ms1_apex_rt for c in valid) == list(expected_apices)
    assert sorted(
        c.ms1_apex_rt
        for c in result.candidates
        if c.ms1_peak_found and (representative_scan is None or not c.is_provisional)
    ) == pytest.approx(expected_apices)
    assert len(result.physical_work_manifest) == len(fixture["seed_scans"])
    if fixture_name == "discovery_true_double_peak":
        # The exact acquired fixture has a censored core (8 hits in 3 runs),
        # not a continuous overlap. Shared hit-density qualification owns it.
        assert all(not candidate.is_provisional for candidate in valid)
        assert all(
            candidate.ms1_area is not None and candidate.ms1_area > 0
            for candidate in valid
        )
        assert len(result.physical_bindings) == 1
        assert len(result.physical_bindings[0].member_source_ordinals) == 2
        evaluation = result.physical_bindings[0].physical_fact_record.evaluation
        facts = evaluation.facts
        assert facts is not None
        assert facts.left_rt_min < 15.0077 < 15.2552 < facts.right_rt_min
        assert facts.area_source == (
            "trapezoid_counts_seconds_on_observed_censored_signal"
        )
        assert evaluation.integration is not None
        assert "near_detection_limit" in evaluation.integration.boundary_sources
    if representative_scan is not None:
        assert [c.best_ms2_scan_id for c in valid] == [representative_scan]
        # Baseline-return boundaries admit both supporting seeds. The merge
        # owner selects the earlier seed when intensity and mass error tie.
        assert {
            record.work_item.best_ms2_scan_id
            for record in result.physical_evaluations
            if record.evaluation.facts is not None
        } == {14712, 14922}
        assert {
            record.work_item.best_ms2_scan_id: record.evaluation.reason
            for record in result.physical_evaluations
            if record.evaluation.facts is None
        } == {14067: "incomplete_peak_context", 14291: "incomplete_peak_context"}


@pytest.mark.parametrize("combine_mode", ("union", "intersection"))
def test_merged_physical_candidate_persists_representative_search_window(
    tmp_path: Path, combine_mode: str
) -> None:
    from xic_extractor.acquisition_contract import serialize_acquired_trace
    from xic_extractor.discovery.csv_reader import read_discovery_candidates_csv
    from xic_extractor.discovery.output_publication import (
        write_dual_csvs,
    )
    from xic_extractor.execution.timing import TimingRecorder
    from xic_extractor.peak_detection.physical_peak_codec import (
        serialize_physical_peak_evaluation,
    )
    from xic_extractor.untargeted_execution.alignment import (
        _restore_physical_candidate,
    )

    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"representative-window")
    settings = DiscoverySettings(
        selected_tag_names=("dR", "R"),
        tag_combine_mode=combine_mode,
        neutral_loss_profiles=_multi_tag_settings().neutral_loss_profiles[:2],
    )
    profile = SignalProcessingProfile(window_points=3)
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings, signal_processing_profile=profile
    )
    source = spec.acquisition_inputs[0].acquisition_source
    groups = tuple(
        replace(
            _group((_seed(scan_number=scan, rt=rt, product_mz=500.0 - loss),)),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            matched_tag_names=(tag,),
        )
        for scan, rt, tag, loss in (
            (10, 7.78, "dR", 116.047344),
            (20, 7.82, "R", 132.0423),
        )
    )
    raw = _OwnerBoundBatchRaw(source)
    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        groups,
        acquisition_source=source,
        run_spec_id=spec.run_spec_id,
        settings=settings,
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    (candidate,) = result.candidates
    (binding,) = result.physical_bindings
    assert binding.candidate is candidate
    assert binding.member_source_ordinals == (0, 1)
    assert candidate.matched_tag_names == ("dR", "R")
    assert candidate.seed_scan_ids == (10, 20)
    assert (
        len(
            {
                (work.xic_rt_min, work.xic_rt_max)
                for work in result.physical_work_manifest
            }
        )
        == 2
    )
    selected = binding.physical_fact_record
    before_trace = serialize_acquired_trace(selected.acquired_trace)
    before_evaluation = serialize_physical_peak_evaluation(
        selected.rt, selected.raw, profile=profile, evaluation=selected.evaluation
    )
    outputs = write_dual_csvs(
        tmp_path / "discovery",
        (candidate,),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem=raw_path.stem,
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
        physical_work_manifest=result.physical_work_manifest,
        physical_evaluations=result.physical_evaluations,
        physical_bindings=result.physical_bindings,
    )
    csv_path = outputs.candidates_csv
    sidecar_path = outputs.physical_facts_json
    assert sidecar_path is not None
    records = read_discovery_physical_fact_sidecar(
        sidecar_path,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=result.physical_work_manifest,
    )
    assert len(records) == len(binding.member_work_ordinals) == 2
    restored_record = next(
        record for record in records if record.candidate_id == candidate.candidate_id
    )
    (csv_candidate,) = read_discovery_candidates_csv(csv_path)
    restored = _restore_physical_candidate(csv_candidate, restored_record)
    assert (
        restored.ms1_search_rt_min
        == candidate.ms1_search_rt_min
        == selected.work_item.xic_rt_min
    )
    assert (
        restored.ms1_search_rt_max
        == candidate.ms1_search_rt_max
        == selected.work_item.xic_rt_max
    )
    assert restored.best_ms2_scan_id == selected.work_item.best_ms2_scan_id
    assert selected.evaluation.facts is not None
    assert (
        restored.ms1_area
        == candidate.ms1_area
        == selected.evaluation.facts.area_counts_seconds
    )
    assert (
        restored.ms1_height == candidate.ms1_height == selected.evaluation.facts.height
    )
    assert serialize_acquired_trace(restored_record.acquired_trace) == before_trace
    assert (
        serialize_physical_peak_evaluation(
            restored_record.rt,
            restored_record.raw,
            profile=profile,
            evaluation=restored_record.evaluation,
        )
        == before_evaluation
    )
    assert raw.raw_chromatogram_call_count == 1
    for name in ("ms1_search_rt_min", "ms1_search_rt_max"):
        with pytest.raises(ValueError, match="search window"):
            replace(
                binding,
                candidate=replace(candidate, **{name: getattr(candidate, name) + 0.01}),
            )


class _CadenceBoundRaw:
    def __init__(self, source, rt: np.ndarray, intensity: np.ndarray) -> None:
        self.acquisition_source = source
        self.rt = rt
        self.intensity = intensity
        self.raw_chromatogram_call_count = 0
        self.request_batches = []

    def extract_xic_many(self, requests):
        self.request_batches.append(tuple(requests))
        self.raw_chromatogram_call_count += 1
        traces = []
        for request in requests:
            mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
            traces.append(XICTrace.from_arrays(self.rt[mask], self.intensity[mask]))
        return tuple(traces)

    def scan_window_for_request(self, request):
        return (int(request.rt_min * 100), int(request.rt_max * 100))


def test_spec_bound_single_run_uses_compiled_acquisition_and_writes_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    handle_source = spec.acquisition_inputs[0].acquisition_source
    trace_rt = np.linspace(7.20, 8.40, 25)
    raw = _OwnerBoundFakeRawHandle(
        handle_source,
        [_scan_event(scan_number=101, rt=7.80)],
        rt=trace_rt,
        intensity=100.0 + 500.0 * np.exp(-0.5 * ((trace_rt - 7.80) / 0.08) ** 2),
    )
    from xic_extractor.peak_detection import physical_peak_codec as physical_owner

    physical_calls = {"prepare": 0, "evaluate": 0}
    real_prepare = physical_owner.prepare_physical_signal
    real_evaluate = physical_owner.evaluate_physical_peak

    def counted_prepare(*args, **kwargs):
        physical_calls["prepare"] += 1
        return real_prepare(*args, **kwargs)

    def counted_evaluate(*args, **kwargs):
        physical_calls["evaluate"] += 1
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal",
        counted_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.evaluate_physical_peak",
        counted_evaluate,
    )
    monkeypatch.setattr(physical_owner, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(physical_owner, "evaluate_physical_peak", counted_evaluate)
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._acquisition_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("spec-bound run fingerprinted the RAW twice")
        ),
    )
    captured_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()

    def capture_sidecar(path, **kwargs):
        nonlocal captured_manifest
        captured_manifest = tuple(kwargs["work_manifest"])
        return write_discovery_physical_fact_sidecar(path, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.output_publication.write_discovery_physical_fact_sidecar",
        capture_sidecar,
    )

    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
        run_spec=spec,
    )

    assert outputs.physical_facts_json == (
        tmp_path / "out" / "discovery_physical_facts.json"
    )
    assert outputs.physical_facts_json is not None
    assert physical_calls == {"prepare": 1, "evaluate": 1}
    expected_work_manifest = captured_manifest
    assert len(expected_work_manifest) == 1
    records = read_discovery_physical_fact_sidecar(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=expected_work_manifest,
    )
    assert len(records) == 1
    assert (
        records[0].candidate_id == _read_csv(outputs.candidates_csv)[0]["candidate_id"]
    )
    assert records[0].evaluation.prepared.profile_id == spec.profile_id
    assert physical_calls == {"prepare": 2, "evaluate": 2}
    assert raw.raw_chromatogram_call_count == 1


@pytest.mark.parametrize("binding", ["missing", "crossed"])
def test_spec_bound_handle_closes_source_before_ms2_or_xic(
    tmp_path: Path,
    binding: str,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    if binding == "missing":
        source = None
    else:
        other = tmp_path / "Other.raw"
        other.write_bytes(b"different acquisition")
        source = build_acquisition_source_identity(
            other,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )

    class CountingHandle:
        acquisition_source = source
        raw_chromatogram_call_count = 0

        def __init__(self) -> None:
            self.ms2_calls = 0
            self.xic_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_ms2_scans(self, *_args):
            self.ms2_calls += 1
            return iter(())

        def extract_xic_many(self, requests):
            self.xic_calls += 1
            return tuple(XICTrace.empty() for _request in requests)

        def scan_window_for_request(self, _request):
            return (0, 0)

    handle = CountingHandle()

    with pytest.raises(ValueError, match="lacks|crosses"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "out",
            settings=settings,
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda *_args: handle,
            run_spec=spec,
        )

    assert handle.ms2_calls == 0
    assert handle.xic_calls == 0


def test_sparse_trace_outside_actual_context_persists_truthful_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"sparse actual context")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
    )
    raw = _OwnerBoundFakeRawHandle(
        spec.acquisition_inputs[0].acquisition_source,
        [_scan_event(scan_number=101, rt=7.80)],
        rt=np.asarray([7.90]),
        intensity=np.asarray([100.0]),
    )
    captured_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()

    def capture_sidecar(path, **kwargs):
        nonlocal captured_manifest
        captured_manifest = tuple(kwargs["work_manifest"])
        return write_discovery_physical_fact_sidecar(path, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.output_publication.write_discovery_physical_fact_sidecar",
        capture_sidecar,
    )

    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )

    row = _read_csv(outputs.candidates_csv)[0]
    assert row["discovery_candidate_state"] == "review_only_orphan_nl"
    assert row["ms1_peak_found"] == "FALSE"
    assert row["ms1_area"] == ""
    assert row["ms1_trace_quality"] == "physical_unavailable"
    assert "physical MS1 evaluation unavailable" in row["reason"]
    assert captured_manifest == ()
    assert outputs.physical_facts_json is not None
    records = read_discovery_physical_fact_sidecar(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=captured_manifest,
    )
    assert records == ()
    assert raw.raw_chromatogram_call_count == 1


@pytest.mark.parametrize(
    ("peak_sigma", "physical_state"), [(0.03, "available"), (0.08, "unavailable")]
)
def test_short_discovery_request_reacquires_points_without_assuming_complete_context(
    tmp_path: Path,
    peak_sigma: float,
    physical_state: str,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.05, 13.25, 0.10, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(
        -0.5 * ((full_rt - 12.15) / peak_sigma) ** 2
    )

    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        (
            _seed(scan_number=10, rt=12.063),
            replace(
                _seed(scan_number=11, rt=12.181),
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    )
    assert group.precursor_mz_basis == "mixed"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert len(result.measurements.acquisition_batches) == 2
    assert result.measurements.initial_request_count == 1
    assert result.measurements.reacquisition_request_count == 1
    first, reacquired = result.measurements.acquisition_batches
    assert first.traces[0].coordinate.trace_point_count < profile.window_points
    assert reacquired.traces[0].coordinate.trace_point_count >= profile.window_points
    evaluation = result.physical_evaluations[0].evaluation
    assert evaluation.state == physical_state
    assert (
        result.physical_evaluations[0].evaluation.prepared.profile_id
        == profile.profile_id
    )
    candidate = result.candidates[0]
    if physical_state == "available":
        assert candidate.ms1_peak_found is True
        assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
    else:
        assert evaluation.reason == "incomplete_peak_context"
        assert evaluation.facts is None
        assert candidate.ms1_peak_found is False
        assert candidate.ms1_area is None
        assert candidate.discovery_candidate_state == "review_only_orphan_nl"
    final_request = reacquired.traces[0].coordinate.request
    assert candidate.ms1_search_rt_min == final_request.rt_min
    assert candidate.ms1_search_rt_max == final_request.rt_max


def test_single_seed_discovery_acquires_complete_physical_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    # Keep the complete baseline-return peak inside the initial context so
    # the full-trace comparison tests acquisition, not a changed baseline fit.
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.06) ** 2)

    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group((_seed(scan_number=10, rt=12.063),))

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
    assert candidate.ms1_peak_rt_end is not None
    assert candidate.ms1_search_rt_max - candidate.ms1_peak_rt_end >= 0.04
    full_trace_evaluation = evaluate_physical_peak(
        prepare_physical_signal(full_rt, full_intensity, profile=profile),
        selection_subject_id="full-trace-oracle",
        query_rt_min=12.063,
        request_context_rt_start_min=float(full_rt[0]),
        request_context_rt_end_min=float(full_rt[-1]),
    )
    assert full_trace_evaluation.facts is not None
    assert candidate.ms1_peak_rt_start == pytest.approx(
        full_trace_evaluation.facts.left_rt_min,
        abs=0.04,
    )
    assert candidate.ms1_peak_rt_end == pytest.approx(
        full_trace_evaluation.facts.right_rt_min,
        abs=0.04,
    )
    assert candidate.ms1_area == pytest.approx(
        full_trace_evaluation.facts.area_counts_seconds,
        rel=0.10,
    )


def test_product_inferred_single_seed_uses_widened_initial_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.15) ** 2)
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        (
            replace(
                _seed(
                    scan_number=10,
                    rt=12.063,
                    product_intensity=1_000_000.0,
                ),
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    )
    assert group.precursor_mz_basis == "product_plus_neutral_loss"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_trace_quality == "clean"
    assert candidate.ms1_search_rt_min == pytest.approx(11.463)
    assert candidate.ms1_search_rt_max == pytest.approx(12.663)
    assert candidate.ms1_apex_rt == pytest.approx(12.14)


def test_strong_product_inferred_seed_group_uses_widened_initial_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.15) ** 2)
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        tuple(
            replace(
                _seed(
                    scan_number=scan_number,
                    rt=rt,
                    product_intensity=1_000_000.0,
                ),
                precursor_mz_basis="product_plus_neutral_loss",
            )
            for scan_number, rt in ((10, 12.063), (11, 12.100))
        )
    )
    assert group.precursor_mz_basis == "product_plus_neutral_loss"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert len(result.measurements.acquisition_batches) == 1
    assert result.measurements.reacquisition_request_count == 0
    initial = result.measurements.acquisition_batches[0]
    assert initial.traces[0].coordinate.trace_point_count >= profile.window_points
    initial_request = initial.traces[0].coordinate.request
    assert initial_request.rt_min == pytest.approx(11.463)
    assert initial_request.rt_max == pytest.approx(12.700)
    work = result.physical_work_manifest[0]
    assert work.query_rt_min == pytest.approx(12.063)
    assert work.xic_rt_min == initial_request.rt_min
    assert work.xic_rt_max == initial_request.rt_max
    assert work.xic_rt_min <= work.context_rt_start_min <= work.query_rt_min
    assert work.query_rt_min <= work.context_rt_end_min <= work.xic_rt_max
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)


@pytest.mark.parametrize("shared_request_count", (1, 2))
@pytest.mark.parametrize("provisional", (False, True))
def test_qualification_survives_physical_fact_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_request_count: int,
    provisional: bool,
) -> None:
    from xic_extractor.peak_detection.background_shape import is_provisional_peak_shape

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"provisional-qualification-reuse")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    raw.rt = np.linspace(7.45, 8.15, 141)
    if provisional:
        # An independently defined marginal peak stays in the SNR 3..5 band
        # across small flank crops, rather than relying on a ripple boundary.
        indices = np.arange(raw.rt.size)
        noise = 8.0 * np.sin(2.0 * np.pi * indices / 3.0) + 6.0 * np.cos(
            2.0 * np.pi * indices / 5.0
        )
        amplitude = 30.0
    else:
        # A high-SNR single peak has no unresolved canonical lobes. The
        # fixture tests qualification reuse, independently of shoulder review.
        noise = np.random.default_rng(0).normal(size=raw.rt.size)
        amplitude = 50.0
    raw.intensity = (
        1000.0 + amplitude * np.exp(-0.5 * ((raw.rt - 7.8) / 0.05) ** 2) + noise
    )
    qualifier = Mock(wraps=is_provisional_peak_shape)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.is_provisional_peak_shape", qualifier
    )
    group = _group((_seed(scan_number=10, rt=7.8),))

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,) * shared_request_count,
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(),
    )

    (candidate,) = result.candidates
    assert candidate.ms1_trace_quality == ("provisional" if provisional else "clean")
    assert candidate.is_provisional is provisional
    assert result.measurements.reacquisition_request_count == 0
    assert raw.raw_chromatogram_call_count == 1
    assert qualifier.call_count == 1
    _, qualified_raw = qualifier.call_args.args
    apex = qualifier.call_args.kwargs["apex_index"]
    edge_count = max(1, min(5, len(qualified_raw) // 6))
    edge = max(
        np.median(qualified_raw[:edge_count]), np.median(qualified_raw[-edge_count:])
    )
    centre = np.median(qualified_raw[max(0, apex - 1) : apex + 2])
    snr = (centre - edge) / qualifier.call_args.kwargs["raw_noise_mad"]
    if provisional:
        assert centre / edge < 1.5 and 3.0 < snr < 5.0
    else:
        assert snr >= 5.0


def test_spec_bound_physical_owner_reuses_preparation_without_extra_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    first = _group((_seed(scan_number=10, rt=7.80),))
    duplicate_trace = replace(
        first,
        seeds=(_seed(scan_number=20, rt=7.80),),
    )
    raw = _OwnerBoundBatchRaw(source)
    preparation_count = 0

    def _count_prepare(*args, **kwargs):
        nonlocal preparation_count
        preparation_count += 1
        return prepare_physical_signal(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal",
        _count_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("spec-bound route used legacy peak detection")
        ),
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first, duplicate_trace),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )

    metrics = derive_runtime_acquisition_metrics(
        result.measurements.acquisition_batches
    )
    assert preparation_count == 1
    assert len(result.physical_evaluations) == 2
    assert {
        record.evaluation.prepared.prepared_signal_id
        for record in result.physical_evaluations
    } == {result.physical_evaluations[0].evaluation.prepared.prepared_signal_id}
    assert metrics.logical_xic_request_count == 2
    assert metrics.xic_batch_count == 1
    assert metrics.adapter_chromatogram_call_count == 1
    assert [len(batch) for batch in raw.request_batches] == [2]


def test_physical_fact_owner_rejects_cross_acquisition_reassembly(
    tmp_path: Path,
) -> None:
    current_dir = tmp_path / "current"
    foreign_dir = tmp_path / "foreign"
    current_dir.mkdir()
    foreign_dir.mkdir()
    current_path = current_dir / "sample.raw"
    foreign_path = foreign_dir / "sample.raw"
    current_path.write_bytes(b"current-acquisition")
    foreign_path.write_bytes(b"foreign-acquisition")
    current_source = build_acquisition_source_identity(
        current_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    foreign_source = build_acquisition_source_identity(
        foreign_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    group = _group((_seed(scan_number=10, rt=7.80),))
    profile = SignalProcessingProfile(window_points=3)
    current = _backfill_ms1_candidates_with_acquisition(
        _OwnerBoundBatchRaw(current_source),
        (group,),
        acquisition_source=current_source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    foreign_raw = _OwnerBoundBatchRaw(foreign_source)
    foreign_raw.intensity = np.asarray(
        [0.0, 0.0, 0.0, 5.0, 500.0, 5.0, 0.0, 0.0, 0.0], dtype=float
    )
    foreign = _backfill_ms1_candidates_with_acquisition(
        foreign_raw,
        (group,),
        acquisition_source=foreign_source,
        run_spec_id="processing-run:" + ("1" * 64),
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    current_record = current.physical_evaluations[0]
    foreign_record = foreign.physical_evaluations[0]

    with pytest.raises(ValueError, match="crosses acquired trace content"):
        replace(current_record, evaluation=foreign_record.evaluation)
    with pytest.raises(ValueError, match="crosses acquisition record"):
        replace(
            current.physical_bindings[0],
            physical_fact_record=foreign_record,
        )


@pytest.mark.parametrize(
    ("rt", "intensity", "physical_state", "physical_reason", "window_points"),
    [
        (
            np.asarray([7.8]),
            np.asarray([10.0]),
            "unavailable",
            "insufficient_points_for_profile",
            3,
        ),
        (
            np.asarray([7.8, 7.7, 7.9]),
            np.asarray([1.0, 2.0, 1.0]),
            "failed",
            "non_monotonic_rt",
            3,
        ),
        (
            np.linspace(7.0, 8.5, 31),
            np.asarray([0.0] * 16 + [100.0] + [0.0] * 14),
            "unavailable",
            "insufficient_original_xic_support",
            3,
        ),
        (
            np.linspace(7.0, 8.5, 31),
            np.asarray([1000.0] * 16 + [1100.0] + [1000.0] * 14),
            "unavailable",
            "insufficient_original_xic_support",
            3,
        ),
        (
            np.linspace(7.0, 8.5, 31),
            np.asarray([0.0] * 16 + [100.0, 0.0, 100.0] + [0.0] * 12),
            "unavailable",
            "insufficient_original_xic_continuity",
            15,
        ),
        (
            np.linspace(6.8, 8.8, 101),
            np.where(
                (np.arange(101) % 2 == 1) | (np.arange(101) == 50),
                1000.0 * np.exp(-0.5 * ((np.arange(101) - 50) / 8.0) ** 2),
                0.0,
            ),
            "unavailable",
            "insufficient_local_hit_enrichment",
            15,
        ),
    ],
)
def test_spec_bound_terminal_physical_truth_remains_distinct_hold_state(
    tmp_path: Path,
    rt: np.ndarray,
    intensity: np.ndarray,
    physical_state: str,
    physical_reason: str,
    window_points: int,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    raw.rt = rt
    raw.intensity = intensity

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=window_points),
    )

    candidate = result.candidates[0]
    evaluation = result.physical_evaluations[0].evaluation
    assert evaluation.question.selection_policy_version == (
        "background_qualified_peak_contains_query"
        if window_points == 15
        else "baseline_peak_contains_query"
    )
    assert evaluation.state == physical_state
    assert evaluation.reason == physical_reason
    assert evaluation.facts is None
    assert evaluation.integration is None
    assert candidate.discovery_candidate_state == "review_only_orphan_nl"
    assert candidate.review_priority == "LOW"
    assert candidate.reason == (
        f"physical MS1 evaluation {physical_state}: {physical_reason}"
    )
    assert candidate.ms1_support == "missing"
    assert candidate.ms1_trace_quality == f"physical_{physical_state}"
    assert build_discovery_review_note(candidate) == (
        f"physical MS1 {physical_state}: {physical_reason}; missing RT"
    )
    assert candidate.ms1_peak_found is False
    assert candidate.ms1_apex_rt is None
    assert candidate.ms1_area is None
    assert candidate.ms1_height is None
    assert candidate.ms1_scan_support_score is None
    assert candidate.discovery_candidate_state not in {
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
        "rejected_noise_or_outside_rt",
    }


def test_discovery_recovers_near_baseline_peak_after_exterior_context_crop(
    tmp_path: Path,
) -> None:
    from tests.test_physical_peak_evaluation_persistence import _context_sensitive_trace

    rt, intensity = _context_sensitive_trace()
    rt = 7.8 + rt[:-1] - rt[23]
    intensity = intensity[:-1]
    profile = SignalProcessingProfile(window_points=15)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-exterior-context")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    raw.rt, raw.intensity = rt, intensity
    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    evaluation = result.physical_evaluations[0].evaluation
    candidate = result.candidates[0]
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(7.8)
    assert candidate.ms1_area == evaluation.facts.area_counts_seconds
    assert rt[0] < evaluation.facts.left_rt_min < 7.8
    assert 7.8 < evaluation.facts.right_rt_min < rt[-1]


@pytest.mark.parametrize("same_question", [True, False])
def test_physical_fact_sharing_uses_exact_owner_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_question: bool,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    settings = _multi_tag_settings()
    profile = SignalProcessingProfile(window_points=3)
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
        signal_processing_profile=profile,
    )
    source = spec.acquisition_inputs[0].acquisition_source
    first = replace(
        _group((_seed(scan_number=10, rt=7.80, precursor_mz=500.0000001),)),
        neutral_loss_tag="dR",
        configured_neutral_loss_da=116.047344,
        matched_tag_names=("dR",),
    )
    second = (
        replace(
            first,
            neutral_loss_tag="R",
            configured_neutral_loss_da=132.0423,
            matched_tag_names=("R",),
        )
        if same_question
        else replace(
            _group((_seed(scan_number=10, rt=7.80, precursor_mz=500.0000002),)),
            neutral_loss_tag="dR",
            configured_neutral_loss_da=116.047344,
            matched_tag_names=("dR",),
        )
    )
    raw = _OwnerBoundBatchRaw(source)
    prepare = Mock(wraps=prepare_physical_signal)
    evaluate = Mock(wraps=evaluate_physical_peak)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal", prepare
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.evaluate_physical_peak", evaluate
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first, second),
        acquisition_source=source,
        run_spec_id=spec.run_spec_id,
        settings=settings,
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    expected_count = 1 if same_question else 2
    assert [len(batch) for batch in raw.request_batches] == [expected_count]
    assert prepare.call_count == 1
    assert evaluate.call_count == expected_count
    assert len(result.physical_work_manifest) == expected_count
    assert len(result.physical_evaluations) == expected_count
    assert tuple(work.work_ordinal for work in result.physical_work_manifest) == tuple(
        range(expected_count)
    )
    if same_question:
        candidate = result.candidates[0]
        assert candidate.matched_tag_names == ("dR", "R")
        assert candidate.review_priority == "HIGH"
        assert candidate.discovery_candidate_state == "ms1_feature_nl_supported"
    else:
        first_work, second_work = result.physical_work_manifest
        assert first_work.candidate_id == second_work.candidate_id
        assert first_work.selection_subject_id != second_work.selection_subject_id
        path = tmp_path / "discovery_physical_facts.json"
        write_discovery_physical_fact_sidecar(
            path,
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
            work_manifest=result.physical_work_manifest,
            records=result.physical_evaluations,
        )

        restored = read_discovery_physical_fact_sidecar(
            path,
            expected_run_spec=spec,
            expected_acquisition_input=spec.acquisition_inputs[0],
            expected_work_manifest=result.physical_work_manifest,
        )

        assert tuple(record.work_item for record in restored) == (
            result.physical_work_manifest
        )
        assert raw.raw_chromatogram_call_count == 1


def _inferred_apex_case(
    tmp_path: Path,
    centroid,
    *,
    basis: str = "product_plus_neutral_loss",
    companion_basis: str | None = None,
    isotope_offsets: tuple[float, ...] = (),
):
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.15) ** 2)
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    group = _group(
        (
            replace(
                _seed(scan_number=10, rt=12.063, product_intensity=1_000_000.0),
                precursor_mz_basis=basis,
            ),
        )
    )
    groups = (group,)
    if companion_basis is not None:
        # A second seed group on the same peak, from another MS2 scan.
        groups += (
            _group(
                (
                    replace(
                        _seed(scan_number=11, rt=12.083, product_intensity=900_000.0),
                        precursor_mz_basis=companion_basis,
                    ),
                )
            ),
        )

    def read_ion(rt: float, low: float, high: float) -> float | None:
        mz = centroid(group.precursor_mz, rt)
        ions = () if mz is None else (mz, *(mz + offset for offset in isotope_offsets))
        return next((ion for ion in ions if low <= ion <= high), None)

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        groups,
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=15),
        ms1_centroid_mz=read_ion,
    )
    return group, raw, result


def test_inferred_precursor_reports_the_ms1_ion_read_at_the_apex(
    tmp_path: Path,
) -> None:
    group, raw, result = _inferred_apex_case(
        tmp_path, lambda inferred, _rt: inferred * (1 - 18e-6)
    )

    assert result.measurements.reacquisition_request_count == 0
    assert raw.request_batches[-1][0].mz == pytest.approx(group.precursor_mz)
    (candidate,) = result.candidates
    assert candidate.precursor_mz == group.precursor_mz
    assert candidate.ms1_apex_mz == pytest.approx(group.precursor_mz * (1 - 18e-6))
    assert candidate.ms1_peak_found is True
    (record,) = result.physical_evaluations
    assert record.work_item.xic_mz == pytest.approx(group.precursor_mz)


def _two_ions(inferred: float, rt: float) -> float:
    # Alternating readings 12 ppm apart: two ions share the window.
    return inferred * (1 - 18e-6 if round(rt / 0.04) % 2 else 1 - 6e-6)


def test_inferred_precursor_meeting_two_ions_reports_no_ms1_ion(
    tmp_path: Path,
) -> None:
    group, _raw, result = _inferred_apex_case(tmp_path, _two_ions)

    (candidate,) = result.candidates
    assert candidate.precursor_mz == group.precursor_mz
    assert candidate.ms1_apex_mz is None
    assert candidate.discovery_candidate_state in {
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
    }


def test_selected_precursor_reports_no_ms1_ion(tmp_path: Path) -> None:
    group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 - 18e-6),
        basis="scan_precursor",
    )

    (candidate,) = result.candidates
    assert candidate.precursor_mz == group.precursor_mz
    assert candidate.ms1_apex_mz is None


def test_inferred_precursor_with_a_doubly_charged_isotope_is_held_for_review(
    tmp_path: Path,
) -> None:
    group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 + 1e-6),
        isotope_offsets=(1.0033548 / 2,),
    )

    (candidate,) = result.candidates
    assert candidate.precursor_mz == group.precursor_mz
    assert candidate.discovery_candidate_state == "review_only_ambiguous_coisolation"
    assert "doubly charged" in candidate.reason
    (binding,) = result.physical_bindings
    assert binding.candidate.discovery_candidate_state == (
        "review_only_ambiguous_coisolation"
    )


@pytest.mark.parametrize(
    ("basis", "offsets"),
    [
        ("product_plus_neutral_loss", (1.0033548,)),
        ("scan_precursor", (1.0033548 / 2,)),
    ],
)
def test_singly_charged_or_selected_precursor_keeps_its_feature(
    tmp_path: Path, basis: str, offsets: tuple[float, ...]
) -> None:
    _group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 + 1e-6),
        basis=basis,
        isotope_offsets=offsets,
    )

    (candidate,) = result.candidates
    assert candidate.discovery_candidate_state in {
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
    }


@pytest.mark.parametrize(
    ("basis", "companion_basis"),
    [
        ("scan_precursor", None),
        ("product_plus_neutral_loss", "scan_precursor"),
    ],
)
def test_a_selected_precursor_carries_a_peak_with_two_ions(
    tmp_path: Path, basis: str, companion_basis: str | None
) -> None:
    _group, _raw, result = _inferred_apex_case(
        tmp_path, _two_ions, basis=basis, companion_basis=companion_basis
    )

    (candidate,) = result.candidates
    assert candidate.ms1_peak_found is True
    assert candidate.discovery_candidate_state in {
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
    }


@pytest.mark.parametrize("has_isotope", [False, True])
@pytest.mark.parametrize("shared_request_count", [1, 2])
def test_inferred_precursor_rechecks_isotope_after_point_reacquisition(
    tmp_path: Path, has_isotope: bool, shared_request_count: int,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.05, 13.25, 0.10, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(
        -0.5 * ((full_rt - 12.15) / 0.03) ** 2
    )
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    # Inferred-only basis exercises apex/isotope reading. Two high-intensity
    # inferred seeds provide the existing authority for point reacquisition.
    group = _group(
        tuple(
            replace(
                _seed(scan_number=scan, rt=rt, product_intensity=1_000_000.0),
                precursor_mz_basis="product_plus_neutral_loss",
            )
            for scan, rt in ((10, 12.063), (11, 12.181))
        )
    )
    assert group.precursor_mz_basis == "product_plus_neutral_loss"
    ion_mz = group.precursor_mz * (1 + 1e-6)
    isotope_mz = ion_mz + 1.0033548 / 2

    readings = []

    def read_ion(rt: float, low: float, high: float) -> float | None:
        readings.append((rt, low, high))
        ions = (ion_mz, isotope_mz) if has_isotope else (ion_mz,)
        return next((mz for mz in ions if low <= mz <= high), None)

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,) * shared_request_count,
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
        ms1_centroid_mz=read_ion,
    )
    first, reacquired = result.measurements.acquisition_batches
    assert result.measurements.reacquisition_request_count == 1
    assert first.traces[0].coordinate.trace_point_count < profile.window_points
    assert reacquired.traces[0].coordinate.trace_point_count >= profile.window_points
    (record,) = result.physical_evaluations
    assert record.evaluation.state == "available"
    (candidate,) = result.candidates
    assert candidate.ms1_peak_found is True
    expected_state = (
        "review_only_ambiguous_coisolation" if has_isotope else "ms1_feature_nl_rescued"
    )
    assert candidate.discovery_candidate_state == expected_state
    assert ("doubly charged" in candidate.reason) is has_isotope
    assert candidate.ms1_apex_mz == pytest.approx(ion_mz)
    (binding,) = result.physical_bindings
    assert binding.candidate.discovery_candidate_state == expected_state
    # Three final apex scans, each read for ion and isotope, once per physical
    # request even when two seed groups reuse the resulting fact.
    assert len(readings) == 6
