from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from tests.discovery_ms1_capture_fixtures import (
    _channel,
    _multi_tag_settings,
    _seed,
)
from tests.discovery_ms1_capture_fixtures import (
    _settings as _capture_settings,
)
from tests.discovery_physical_fact_integration_fixtures import (
    _OwnerBoundBatchRaw,
)
from tests.discovery_pipeline_fixtures import (
    _OwnerBoundFakeRawHandle,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_peaks,
)
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.pipeline import run_discovery
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICTrace


def _acquisition_totals(batches):
    """Sum the per-batch acquisition counts that timing reports."""
    names = (
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
    )
    return SimpleNamespace(
        **{name: sum(getattr(batch, name) for batch in batches) for name in names}
    )


@pytest.mark.parametrize("parent_support", [True, False])
def test_recorded_mono_nl_match_keeps_distinct_inferred_physical_search(
    tmp_path: Path, parent_support: bool
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation
    from xic_extractor.xic_models import XICTrace

    raw_path = tmp_path / "coisolated.raw"
    raw_path.write_bytes(b"distinct monoisotopic and inferred mass windows")
    settings = _settings(ms2_precursor_tol_da=0.8, nl_tolerance_ppm=20.0)
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
        signal_processing_profile=SignalProcessingProfile(),
    )
    event = _scan_event(scan_number=100, rt=9.02, precursor_mz=500.5, masses=[383.9646])
    event = replace(
        event,
        scan=replace(
            event.scan,
            precursor_metadata=Ms2PrecursorMetadata(
                monoisotopic_mz=500.0,
                charge=1,
                isolation_width_da=1.6,
                isolation_offset_da=0.0,
                master_scan_number=99,
            ),
        ),
    )
    actual_mz = 500.016
    rt = np.arange(8.5, 9.5, 0.002)
    intensity = 100.0 + 10_000 * np.exp(-0.5 * ((rt - 9.02) / 0.004) ** 2)

    class MassResolvedRaw(_OwnerBoundFakeRawHandle):
        def extract_xic_many(self, requests):
            return tuple(
                XICTrace.from_arrays(
                    rt,
                    intensity
                    if abs(request.mz - actual_mz) <= request.mz * request.ppm_tol / 1e6
                    else np.zeros_like(rt),
                )
                for request in requests
            )

        def read_ms1_mass_windows(self, requests):
            return tuple(
                Ms1PrecursorObservation(
                    scan,
                    9.018,
                    (actual_mz,)
                    if parent_support and lower <= actual_mz <= upper
                    else (),
                )
                for scan, lower, upper in requests
            )

    raw = MassResolvedRaw(
        spec.acquisition_inputs[0].acquisition_source,
        [event],
        rt=rt,
        intensity=intensity,
    )
    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )
    candidates = read_discovery_peaks_csv(outputs.peaks_csv)
    supported = [
        candidate for candidate in candidates if candidate.status == "admitted"
    ]
    # The recorded mono passes the NL gate but its MS1 window misses the ion.
    # Product+NL must still search its distinct window, without becoming its
    # own independent NL witness when the parent MS1 observation is absent.
    assert len(supported) == int(parent_support)
    if parent_support:
        assert supported[0].ms1_apex_rt == pytest.approx(9.02)
        assert supported[0].ms1_area > 0
        assert supported[0].precursor_mz_basis == "product_plus_neutral_loss"


@pytest.mark.parametrize("strongest_first", [True, False])
@pytest.mark.parametrize("second_seed_rt", [10.02, 10.15])
def test_pipeline_retains_each_seeded_physical_peak_from_one_rt_group(
    tmp_path: Path,
    strongest_first: bool,
    second_seed_rt: float,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.discovery.physical_fact_persistence import (
        IndexedDiscoveryPhysicalFacts,
    )

    raw_path = tmp_path / "two-peaks.raw"
    raw_path.write_bytes(b"two analytical peaks in one RT seed group")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
        signal_processing_profile=SignalProcessingProfile(),
    )
    rt = np.arange(9.3, 10.9, 0.002)
    intensity = (
        100.0
        + 10_000 * np.exp(-0.5 * ((rt - 10.00) / 0.012) ** 2)
        + 15_000 * np.exp(-0.5 * ((rt - 10.15) / 0.012) ** 2)
    )
    raw = _OwnerBoundFakeRawHandle(
        spec.acquisition_inputs[0].acquisition_source,
        [
            _scan_event(
                scan_number=100,
                rt=10.00,
                precursor_mz=500.0,
                product_intensity=20_000 if strongest_first else 10_000,
            ),
            _scan_event(
                scan_number=200,
                rt=second_seed_rt,
                precursor_mz=500.0,
                product_intensity=10_000 if strongest_first else 20_000,
            ),
        ],
        rt=rt,
        intensity=intensity,
    )
    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )
    candidates = sorted(
        read_discovery_peaks_csv(outputs.peaks_csv),
        key=lambda candidate: candidate.ms1_apex_rt,
    )
    expected_apices = [10.00] if second_seed_rt == 10.02 else [10.00, 10.15]
    assert [candidate.ms1_apex_rt for candidate in candidates] == pytest.approx(
        expected_apices,
    )
    assert len(_read_csv(outputs.events_csv)) == 2
    assert [candidate.ms1_area for candidate in candidates] == pytest.approx(
        [
            height * 0.012 * np.sqrt(2 * np.pi) * 60
            for height in [10_000, 15_000][: len(expected_apices)]
        ],
        rel=0.03,
    )
    assert all(candidate.status == "admitted" for candidate in candidates)
    assert sorted(
        scan for candidate in candidates for scan in candidate.event_scan_ids
    ) == [100, 200]
    # More physical questions must not repeat RAW acquisition or lose their
    # independent candidate -> physical-answer binding after persistence.
    assert raw.raw_chromatogram_call_count == 1
    assert outputs.physical_facts_json is not None
    saved = IndexedDiscoveryPhysicalFacts(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        candidate_ids=frozenset(candidate.peak_id for candidate in candidates),
    )
    try:
        records = saved.load_candidates([candidate.peak_id for candidate in candidates])
        for candidate in candidates:
            record = records[candidate.peak_id]
            assert record.evaluation.facts is not None
            assert record.evaluation.facts.apex_rt_min == candidate.ms1_apex_rt
            assert record.evaluation.facts.area_counts_seconds == candidate.ms1_area
    finally:
        saved.close()


@pytest.mark.parametrize(
    ("fixture_name", "expected_apices", "representative_scan"),
    (
        ("discovery_normal_single_peak", (18.4,), 14067),
        ("discovery_same_peak_seeds", (38.2,), None),
        ("discovery_true_double_peak", (), None),
        ("discovery_non_peak_background", (), None),
    ),
)
def test_discovery_retained_physical_peak_cardinality(
    tmp_path: Path,
    fixture_name: str,
    expected_apices: tuple[float, ...],
    representative_scan: int | None,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.discovery.csv_writer import write_discovery_peaks_csv

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / f"{fixture_name}.json").read_text()
    )
    rt = np.asarray([float.fromhex(v) for v in fixture["trace"]["rt"]])
    intensity = np.asarray([float.fromhex(v) for v in fixture["trace"]["intensity"]])
    raw_path = tmp_path / f"{fixture_name}.raw"
    raw_path.write_bytes(b"retained exact numerical XIC fixture")
    settings = _capture_settings()
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

    basis = fixture.get("precursor_mz_basis", "product_plus_neutral_loss")
    seeds = tuple(
        replace(
            _seed(scan_number=scan, rt=seed_rt, precursor_mz=mz, product_mz=product),
            raw_file=raw_path,
            sample_stem=raw_path.stem,
            precursor_mz_basis=basis,
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
    result = capture_ms1_peaks(
        RetainedTraceRaw(source),
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    path = write_discovery_peaks_csv(tmp_path / "peaks.csv", result.peaks)
    valid = list(read_discovery_peaks_csv(path))
    if representative_scan is not None:
        # The complete main envelope has one representative. Earlier seeds
        # have clipped whole contours, rather than independently closed peaks.
        assert all(not c.is_provisional for c in valid)
    # This retained-input test protects peak cardinality and localization, not
    # an old smoother's exact apex scan or an unverified quantitative truth.
    assert sorted(c.ms1_apex_rt for c in valid) == pytest.approx(
        expected_apices, abs=0.06
    )
    # Method queries on one raw basin share one retained measurement; events
    # without a measured peak publish no physical record.
    assert len(result.physical_work_manifest) == len(expected_apices)
    assert len(result.attachments) == len(seeds)
    if fixture_name == "discovery_same_peak_seeds":
        # Both tail NL events locate the same acquired raw basin even outside
        # its quantitative tenth-height return.
        (peak,) = valid
        assert {event.peak_id for event in result.attachments if event.peak_id} <= {
            peak.peak_id
        }
    if fixture_name in {"discovery_true_double_peak", "discovery_non_peak_background"}:
        assert all(not event.peak_id for event in result.attachments)
    if representative_scan is not None:
        assert [c.best_ms2_scan_id for c in valid] == [representative_scan]
        assert len(result.physical_evaluations) == 1


@pytest.mark.parametrize("combine_mode", ("union", "intersection"))
@pytest.mark.parametrize("fallback", (False, True))
def test_merged_physical_candidate_persists_representative_search_window(
    tmp_path: Path, combine_mode: str, fallback: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.acquisition_contract import serialize_acquired_trace
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.output_publication import (
        publish_discovery_batch_sample,
    )
    from xic_extractor.discovery.strict_event_receipts import (
        read_parent_ms1_observations,
    )
    from xic_extractor.execution.timing import TimingRecorder
    from xic_extractor.peak_detection.physical_peak_codec import (
        serialize_physical_peak_evaluation,
    )
    from xic_extractor.untargeted_execution.alignment import (
        _restore_physical_peak,
    )

    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"representative-window")
    settings = DiscoverySettings(
        selected_tag_names=("dR", "R"),
        nl_tolerance_ppm=100.0 if fallback else 20.0,
        tag_combine_mode=combine_mode,
        neutral_loss_profiles=_multi_tag_settings().neutral_loss_profiles[:2],
    )
    profile = SignalProcessingProfile(window_points=3)
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings, signal_processing_profile=profile
    )
    source = spec.acquisition_inputs[0].acquisition_source
    seeds = tuple(
        replace(
            _seed(
                scan_number=scan,
                rt=rt,
                product_mz=500.0 - loss,
                product_intensity=20000.0 if scan == 20 else 10000.0,
            ),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            observed_neutral_loss_da=loss,
        )
        for scan, rt, tag, loss in (
            (10, 7.78, "dR", 116.047344),
            (20, 7.82, "R", 132.0423),
        )
    )
    if fallback:
        from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
        from xic_extractor.precursor_evidence import Ms1PrecursorObservation

        seeds = tuple(
            replace(
                seed,
                precursor_mz=500.0 + offset,
                product_mz=seed.product_mz + offset,
                precursor_mz_basis="product_plus_neutral_loss",
                observed_loss_error_ppm=None,
                scan_precursor_mz=500.5,
                precursor_metadata=Ms2PrecursorMetadata(
                    master_scan_number=9,
                    charge=1,
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                ),
            )
            for seed, offset in zip(seeds, (0.006, 0.006), strict=True)
        )
        parent_batches = []

        def read_windows(self, requests):
            parent_batches.append(requests)
            return tuple(
                Ms1PrecursorObservation(
                    scan, 7.75, (500.015,) if lower <= 500.015 <= upper else ()
                )
                for scan, lower, upper in requests
            )

        monkeypatch.setattr(
            _OwnerBoundBatchRaw, "read_ms1_mass_windows", read_windows, raising=False
        )
    raw = _OwnerBoundBatchRaw(source)
    measured = read_parent_ms1_observations(
        raw, seeds, tolerance_ppm=settings.precursor_mz_tolerance_ppm
    )
    result = capture_ms1_peaks(
        raw,
        build_discovery_ions(measured, settings=settings),
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    # The physical peak is measured and represented once; each attached event
    # keeps its own parent-ion proof in tag_evidence_json.
    (candidate,) = result.peaks
    (binding,) = result.physical_bindings
    assert binding.peak is candidate
    assert binding.physical_fact_record.work_item.work_ordinal == 0
    # The configured tag order picks the representative before abundance.
    assert candidate.best_ms2_scan_id == 10
    if fallback:
        # Both events read the same parent MS1 window, so it is read once.
        assert len(parent_batches) == 1 and len(parent_batches[0]) == 1
    assert candidate.admitted
    assert candidate.matched_tag_names == ("dR", "R")
    assert candidate.event_scan_ids == (10, 20)
    assert candidate.ms2_product_max_intensity == 20000.0
    assert {event.peak_id for event in result.attachments} == {candidate.peak_id}
    assert (
        len(
            {
                (work.xic_rt_min, work.xic_rt_max)
                for work in result.physical_work_manifest
            }
        )
        == 1
    )
    selected = binding.physical_fact_record
    before_trace = serialize_acquired_trace(selected.acquired_trace)
    before_evaluation = serialize_physical_peak_evaluation(
        selected.rt, selected.raw, profile=profile, evaluation=selected.evaluation
    )
    outputs = publish_discovery_batch_sample(
        raw_path,
        result.peaks,
        result.attachments,
        output_dir=tmp_path,
        sample_output_dir=tmp_path / "discovery",
        acquisition_source=source,
        timing_recorder=TimingRecorder.disabled("discovery"),
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
        physical_evaluations=result.physical_evaluations,
        physical_bindings=result.physical_bindings,
    ).outputs
    csv_path = outputs.peaks_csv
    sidecar_path = outputs.physical_facts_json
    assert sidecar_path is not None
    records = read_discovery_physical_fact_sidecar(
        sidecar_path,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=result.physical_work_manifest,
    )
    assert len(records) == 1
    restored_record = next(
        record for record in records if record.peak_id == candidate.peak_id
    )
    (csv_candidate,) = read_discovery_peaks_csv(csv_path)
    restored = _restore_physical_peak(csv_candidate, restored_record)
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
                peak=replace(candidate, **{name: getattr(candidate, name) + 0.01}),
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

    def batch_xic_source(self):
        return self

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)


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

    def counted_answer(*args, **kwargs):
        physical_calls["evaluate"] += 1
        return answer_physical_peak_question(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.prepare_physical_signal",
        counted_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.answer_physical_peak_question",
        counted_answer,
    )
    monkeypatch.setattr(physical_owner, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(physical_owner, "evaluate_physical_peak", counted_evaluate)
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
        dll_dir=tmp_path / "dll",
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
    assert records[0].peak_id == _read_csv(outputs.peaks_csv)[0]["peak_id"]
    assert records[0].evaluation.prepared.profile == spec.signal_processing_profile
    # Evidence readers decode stored facts and never repeat signal processing.
    assert physical_calls == {"prepare": 1, "evaluate": 1}
    # The raw return touches an acquired edge; one shared bounded read
    # precedes the final trace's single preparation.
    assert raw.raw_chromatogram_call_count == 2


@pytest.mark.parametrize(
    ("binding", "expected_error"),
    [("missing", "invalid acquisition source"), ("crossed", "crosses")],
)
def test_spec_bound_handle_closes_source_before_ms2_or_xic(
    tmp_path: Path,
    binding: str,
    expected_error: str,
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

    with pytest.raises(ValueError, match=expected_error):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "out",
            settings=settings,
            dll_dir=tmp_path / "dll",
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
        dll_dir=tmp_path / "dll",
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )

    assert _read_csv(outputs.peaks_csv) == []
    (event,) = _read_csv(outputs.events_csv)
    assert event["peak_id"] == ""
    assert event["unattached_reason"]
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
    ("peak_sigma", "physical_state"), [(0.12, "available"), (0.03, "unavailable")]
)
def test_short_discovery_request_reacquires_points_without_assuming_peak_support(
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
    group = _channel(
        (
            _seed(scan_number=10, rt=12.063),
            replace(
                _seed(scan_number=11, rt=12.181),
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    )

    result = capture_ms1_peaks(
        raw,
        (group,),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )

    assert len(result.measurements.acquisition_batches) == 2
    assert result.measurements.initial_request_count == 1
    assert result.measurements.reacquisition_request_count == 1
    first, reacquired = result.measurements.acquisition_batches
    assert first.traces[0].coordinate.trace_point_count < profile.window_points
    assert reacquired.traces[0].coordinate.trace_point_count >= profile.window_points
    if physical_state == "unavailable":
        # No peak survives the reacquired support check; events record it.
        assert result.peaks == ()
        assert {event.unattached_reason for event in result.attachments} == {
            "insufficient_original_xic_support"
        }
        return
    evaluation = result.physical_evaluations[0].evaluation
    assert evaluation.state == physical_state
    assert evaluation.prepared.profile == profile
    (candidate,) = result.peaks
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
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
    group = _channel((_seed(scan_number=10, rt=12.063),))

    result = capture_ms1_peaks(
        raw,
        (group,),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.peaks[0]
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
    assert candidate.ms1_peak_rt_end is not None
    assert candidate.ms1_search_rt_max - candidate.ms1_peak_rt_end >= 0.04
    full_trace_evaluation = evaluate_physical_peak(
        prepare_physical_signal(full_rt, full_intensity, profile=profile),
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
    group = _channel(
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

    result = capture_ms1_peaks(
        raw,
        (group,),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.peaks[0]
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
    group = _channel(
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

    result = capture_ms1_peaks(
        raw,
        (group,),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )

    assert len(result.measurements.acquisition_batches) == 1
    assert result.measurements.reacquisition_request_count == 0
    initial = result.measurements.acquisition_batches[0]
    assert initial.traces[0].coordinate.trace_point_count >= profile.window_points
    initial_request = initial.traces[0].coordinate.request
    assert initial_request.rt_min == pytest.approx(11.463)
    assert initial_request.rt_max == pytest.approx(12.700)
    work = result.physical_work_manifest[0]
    # Measurement localization uses the acquired raw apex, not an NL query.
    assert work.query_rt_min == pytest.approx(12.15, abs=0.04)
    assert work.xic_rt_min == initial_request.rt_min
    assert work.xic_rt_max == initial_request.rt_max
    assert work.xic_rt_min <= work.context_rt_start_min <= work.query_rt_min
    assert work.query_rt_min <= work.context_rt_end_min <= work.xic_rt_max
    candidate = result.peaks[0]
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)


@pytest.mark.parametrize("shared_request_count", (1, 2))
@pytest.mark.parametrize("rising_background", (False, True))
def test_qualification_survives_physical_fact_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_request_count: int,
    rising_background: bool,
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
    # A rising background is not acquired noise: the completed raw SNR stays
    # strong even when raw apex-to-edge contrast is low.
    background = 1000.0 + (1000.0 * (raw.rt - raw.rt[0]) if rising_background else 0.0)
    noise = np.random.default_rng(0).normal(size=raw.rt.size)
    raw.intensity = (
        background + 50.0 * np.exp(-0.5 * ((raw.rt - 7.8) / 0.05) ** 2) + noise
    )
    qualifier = Mock(wraps=is_provisional_peak_shape)
    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.is_provisional_peak_shape",
        qualifier,
    )
    group = _channel((_seed(scan_number=10, rt=7.8),))

    result = capture_ms1_peaks(
        raw,
        (group,) * shared_request_count,
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=SignalProcessingProfile(),
        ms1_centroid=lambda *_: None,
    )

    if rising_background:
        # This synthetic ramp has no resolved raw peak; residual SNR alone
        # does not guarantee a quantifiable local raw component.
        assert result.peaks == ()
        assert qualifier.call_count == 0
        return
    (candidate,) = result.peaks
    assert candidate.ms1_trace_quality == "clean"
    assert candidate.is_provisional is False
    # Raw extent requests one bounded shared read, but does not reprepare or
    # requalify the final trace for repeated NL events.
    assert result.measurements.reacquisition_request_count == 1
    assert raw.raw_chromatogram_call_count == 2
    assert qualifier.call_count == 1
    quality = qualifier.call_args.kwargs["signal_quality"]
    assert quality.status == "available"
    assert quality.signal_to_noise >= 5.0 / 1.4826


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
    first = _channel((_seed(scan_number=10, rt=7.80),))
    duplicate_trace = replace(
        first,
        events=(_seed(scan_number=20, rt=7.80),),
    )
    raw = _OwnerBoundBatchRaw(source)
    preparation_count = 0

    def _count_prepare(*args, **kwargs):
        nonlocal preparation_count
        preparation_count += 1
        return prepare_physical_signal(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.prepare_physical_signal",
        _count_prepare,
    )

    result = capture_ms1_peaks(
        raw,
        (first, duplicate_trace),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        ms1_centroid=lambda *_: None,
    )

    metrics = _acquisition_totals(result.measurements.acquisition_batches)
    assert preparation_count == 1
    # Multiple method events on one raw peak retain one measurement.
    assert len(result.physical_evaluations) == 1
    assert {record.evaluation.prepared for record in result.physical_evaluations} == {
        result.physical_evaluations[0].evaluation.prepared
    }
    assert metrics.logical_xic_request_count == 1
    assert metrics.xic_batch_count == 1
    assert metrics.adapter_chromatogram_call_count == 1
    assert [len(batch) for batch in raw.request_batches] == [1]


def test_physical_binding_rejects_cross_acquisition_reassembly(
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
    group = _channel((_seed(scan_number=10, rt=7.80),))
    profile = SignalProcessingProfile(window_points=3)
    current = capture_ms1_peaks(
        _OwnerBoundBatchRaw(current_source),
        (group,),
        acquisition_source=current_source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    foreign_raw = _OwnerBoundBatchRaw(foreign_source)
    foreign_raw.intensity *= 5.0
    foreign = capture_ms1_peaks(
        foreign_raw,
        (group,),
        acquisition_source=foreign_source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    foreign_record = foreign.physical_evaluations[0]

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
            # Raw valley separation leaves an isolated unsupported impulse.
            "insufficient_original_xic_support",
            15,
        ),
    ],
)
def test_terminal_physical_truth_is_the_events_unattached_reason(
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

    result = capture_ms1_peaks(
        raw,
        (_channel((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=window_points),
        ms1_centroid=lambda *_: None,
    )

    # No measured peak: the event records the physical answer, and no peak
    # row or physical record is published.
    assert result.peaks == ()
    assert result.physical_evaluations == ()
    (event,) = result.attachments
    assert not event.peak_id
    assert event.unattached_reason == physical_reason


@pytest.mark.parametrize(
    "trace_case",
    [
        "exterior_context_crop",
        "separate_noise_hits",
        "alternating_amplitude",
        "overlapping_components",
    ],
)
def test_discovery_publishes_physical_qualification(
    tmp_path: Path,
    trace_case: str,
) -> None:
    from tests.physical_peak_evaluation_persistence_fixtures import (
        _context_sensitive_trace,
        _overlapping_triplet_trace,
        _short_peak_with_separate_noise_hits,
    )

    query_rt = 8.3
    if trace_case == "exterior_context_crop":
        rt, intensity = _context_sensitive_trace()
        rt = query_rt + rt[:-1] - rt[23]
        intensity = intensity[:-1]
    elif trace_case == "separate_noise_hits":
        rt, intensity = _short_peak_with_separate_noise_hits()
        rt = query_rt + rt - rt[int(np.argmax(intensity))]
    elif trace_case == "alternating_amplitude":
        rt = np.linspace(0.0, 10.0, 101)
        intensity = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.5) ** 2)
        intensity[::2] *= 0.1
        rt += query_rt - 5.0
    else:
        rt, intensity = _overlapping_triplet_trace()
        rt += query_rt - 5.0
    profile = SignalProcessingProfile(window_points=15)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-supported-peak")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    raw.rt, raw.intensity = rt, intensity
    result = capture_ms1_peaks(
        raw,
        (_channel((_seed(scan_number=10, rt=query_rt),)),),
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    if trace_case == "alternating_amplitude":
        # Alternating impulses leave no measured raw owner at this query.
        assert result.peaks == ()
        (event,) = result.attachments
        assert event.unattached_reason
        return
    evaluation = result.physical_evaluations[0].evaluation
    candidate = result.peaks[0]
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert candidate.ms1_apex_rt == evaluation.facts.apex_rt_min
    # Shallow valleys stay in one envelope, whose apex is the highest lobe.
    expected_apex = query_rt + (2.0 if trace_case == "overlapping_components" else 0)
    assert candidate.ms1_apex_rt == pytest.approx(expected_apex, abs=0.02)
    assert candidate.ms1_area == evaluation.facts.area_counts_seconds
    assert rt[0] < evaluation.facts.left_rt_min < query_rt
    assert query_rt < evaluation.facts.right_rt_min < rt[-1]
    if trace_case == "overlapping_components":
        assert candidate.ms1_trace_quality == "physical_shoulder_overlap"
        assert candidate.status == "overlap"
        assert candidate.is_provisional
        assert candidate.review_priority == "MEDIUM"
        assert "physical_shoulder_overlap" in candidate.reason

    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.discovery.csv_writer import write_discovery_peaks_csv

    path = write_discovery_peaks_csv(
        tmp_path / "candidates.csv",
        result.peaks,
    )
    (published,) = read_discovery_peaks_csv(path)
    assert published.ms1_area == pytest.approx(candidate.ms1_area)
    assert published.ms1_apex_rt == pytest.approx(candidate.ms1_apex_rt)
    assert published.ms1_trace_quality == candidate.ms1_trace_quality
    assert published.is_provisional == candidate.is_provisional
    assert published.reason == candidate.reason
    assert published.review_priority == candidate.review_priority


@pytest.mark.parametrize("same_question", [True, False])
def test_physical_fact_sharing_uses_one_raw_peak(
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
        _seed(scan_number=10, rt=7.80, precursor_mz=500.0000001),
        neutral_loss_tag="dR",
        configured_neutral_loss_da=116.047344,
        observed_neutral_loss_da=116.047344,
    )
    second = (
        replace(
            first,
            neutral_loss_tag="R",
            configured_neutral_loss_da=132.0423,
            observed_neutral_loss_da=132.0423,
        )
        if same_question
        else replace(first, precursor_mz=500.0000002)
    )
    raw = _OwnerBoundBatchRaw(source)
    prepare = Mock(wraps=prepare_physical_signal)
    evaluate = Mock(wraps=answer_physical_peak_question)
    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.prepare_physical_signal", prepare
    )
    monkeypatch.setattr(
        "xic_extractor.acquisition.physical_capture.answer_physical_peak_question",
        evaluate,
    )

    result = capture_ms1_peaks(
        raw,
        build_discovery_ions((first, second), settings=settings),
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )

    # Sub-ppm mass changes and repeated NL events share one ion channel, so
    # they neither acquire twice nor mint another measurement.
    assert [len(batch) for batch in raw.request_batches] == [1]
    assert prepare.call_count == 1
    assert evaluate.call_count == 1
    assert len(result.physical_work_manifest) == 1
    assert len(result.physical_evaluations) == 1
    assert tuple(work.work_ordinal for work in result.physical_work_manifest) == tuple(
        range(1)
    )
    if same_question:
        candidate = result.peaks[0]
        assert candidate.matched_tag_names == ("dR", "R")
        # One scan matching two tags is two events: (scan, tag, product).
        assert candidate.attached_event_count == 2
        assert candidate.review_priority == "HIGH"
        assert candidate.status == "admitted"
    else:
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
    isotope_ratio: float = 0.5,
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
    group = _channel(
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
            _channel(
                (
                    replace(
                        _seed(scan_number=11, rt=12.083, product_intensity=900_000.0),
                        precursor_mz_basis=companion_basis,
                    ),
                )
            ),
        )

    def read_ion(rt: float, low: float, high: float) -> tuple[float, float] | None:
        mz = centroid(group.mz, rt)
        ions = (
            ()
            if mz is None
            else (
                (mz, 1_000_000.0),
                *(
                    (mz + offset, isotope_ratio * 1_000_000.0)
                    for offset in isotope_offsets
                ),
            )
        )
        return next((ion for ion in ions if low <= ion[0] <= high), None)

    result = capture_ms1_peaks(
        raw,
        groups,
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=15),
        ms1_centroid=read_ion,
    )
    return group, raw, result


def test_inferred_precursor_is_measured_once_in_the_window_of_its_apex_ion(
    tmp_path: Path,
) -> None:
    group, raw, result = _inferred_apex_case(
        tmp_path, lambda inferred, _rt: inferred * (1 - 18e-6)
    )

    apex_mz = group.mz * (1 - 18e-6)
    assert result.measurements.reacquisition_request_count == 0
    assert result.measurements.remeasurement_request_count == 1
    assert raw.request_batches[0][0].mz == pytest.approx(group.mz)
    assert raw.request_batches[-1][0].mz == pytest.approx(apex_mz)
    (candidate,) = result.peaks
    assert candidate.best_event_precursor_mz == group.mz
    assert candidate.ms1_apex_mz == pytest.approx(apex_mz)
    # The peak's one measurement is the apex ion's window, which Alignment
    # reads; the peak keeps the name of the ion XIC that located it.
    (record,) = result.physical_evaluations
    assert candidate.peak_mz == record.work_item.xic_mz
    assert record.work_item.xic_mz == pytest.approx(apex_mz)
    assert record.acquired_trace.coordinate.request.mz == pytest.approx(apex_mz)
    assert candidate.xic_mz == record.work_item.ion_mz == group.mz
    assert candidate.peak_id == record.work_item.peak_id
    assert candidate.peak_id == "TumorBC2312_DNA@mz500_rt12.1400"


def test_pipeline_publishes_inferred_precursor_ms1_ion_from_raw_handle(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "inferred.raw"
    raw_path.write_bytes(b"inferred precursor with a measured MS1 ion")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    apex_ion_mz = 500.0005

    class CentroidRaw(_OwnerBoundFakeRawHandle):
        def ms1_centroid(
            self, rt: float, mz_min: float, mz_max: float
        ) -> tuple[float, float] | None:
            return (apex_ion_mz, 1.0) if mz_min <= apex_ion_mz <= mz_max else None

    rt = np.arange(9.3, 10.7, 0.01)
    raw = CentroidRaw(
        spec.acquisition_inputs[0].acquisition_source,
        [
            _scan_event(
                scan_number=100,
                rt=10.0,
                precursor_mz=500.5,
                masses=[500.0 - 116.0474],
            )
        ],
        rt=rt,
        intensity=100.0 + 10_000 * np.exp(-0.5 * ((rt - 10.0) / 0.05) ** 2),
    )
    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )

    (candidate,) = _read_csv(outputs.peaks_csv)
    assert candidate["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert float(candidate["best_event_precursor_mz"]) == pytest.approx(500.0)
    assert float(candidate["ms1_apex_mz"]) == pytest.approx(apex_ion_mz)


def _two_ions(inferred: float, rt: float) -> float:
    # Alternating readings 12 ppm apart: two ions share the window.
    return inferred * (1 - 18e-6 if round(rt / 0.04) % 2 else 1 - 6e-6)


def test_inferred_precursor_meeting_two_ions_reports_no_ms1_ion(
    tmp_path: Path,
) -> None:
    group, _raw, result = _inferred_apex_case(tmp_path, _two_ions)

    (candidate,) = result.peaks
    assert candidate.best_event_precursor_mz == group.mz
    assert candidate.ms1_apex_mz is None
    assert candidate.status in {
        "admitted",
        "no_nl_event",
    }


def test_selected_precursor_reports_no_ms1_ion(tmp_path: Path) -> None:
    group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 - 18e-6),
        basis="scan_precursor",
    )

    (candidate,) = result.peaks
    assert candidate.best_event_precursor_mz == group.mz
    assert candidate.ms1_apex_mz is None


def test_inferred_precursor_with_a_doubly_charged_isotope_is_held_for_review(
    tmp_path: Path,
) -> None:
    group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 + 1e-6),
        isotope_offsets=(1.0033548 / 2,),
    )

    (candidate,) = result.peaks
    assert candidate.best_event_precursor_mz == group.mz
    assert candidate.status == "doubly_charged"
    assert "doubly charged" in candidate.reason
    (binding,) = result.physical_bindings
    assert binding.peak.status == ("doubly_charged")


@pytest.mark.parametrize(
    ("isotope_ratio", "expected_status"),
    [
        # Tissue apex MS1 read by eye: TumorBC2263 622.295 @34.28 is the
        # weakest clear doubly charged isotope; BenignfatBC1151 894.452 @24.46
        # is singly charged (+1.0 isotope at 26%) with a noise centroid at +0.5.
        (0.383, "doubly_charged"),
        (0.023, "no_nl_event"),
    ],
)
def test_doubly_charged_isotope_counts_only_at_isotope_intensity(
    tmp_path: Path, isotope_ratio: float, expected_status: str
) -> None:
    _group, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 + 1e-6),
        isotope_offsets=(1.0033548 / 2,),
        isotope_ratio=isotope_ratio,
    )

    (candidate,) = result.peaks
    assert candidate.status == expected_status


def test_held_inferred_peak_remains_in_review_and_physical_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
    from xic_extractor.discovery.physical_fact_persistence import (
        IndexedDiscoveryPhysicalFacts,
    )

    raw_path = tmp_path / "held-peak.raw"
    raw_path.write_bytes(b"inferred peak with a doubly charged isotope")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
        signal_processing_profile=SignalProcessingProfile(),
    )
    rt = np.arange(9.3, 10.7, 0.01)
    intensity = 100.0 + 10_000 * np.exp(-0.5 * ((rt - 10.0) / 0.05) ** 2)
    raw = _OwnerBoundFakeRawHandle(
        spec.acquisition_inputs[0].acquisition_source,
        [
            _scan_event(
                scan_number=100,
                rt=10.0,
                precursor_mz=500.5,
                masses=[500.0 - 116.0474],
            )
        ],
        rt=rt,
        intensity=intensity,
    )
    apex_ion_mz = 500.0005
    monkeypatch.setattr(
        raw,
        "ms1_centroid",
        lambda _rt, low, high: next(
            (
                (mz, 1.0)
                for mz in (apex_ion_mz, apex_ion_mz + 1.0033548 / 2)
                if low <= mz <= high
            ),
            None,
        ),
    )
    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )

    (candidate,) = read_discovery_peaks_csv(outputs.peaks_csv)
    assert candidate.precursor_mz_basis == "product_plus_neutral_loss"
    assert candidate.status == "doubly_charged"
    assert "doubly charged" in candidate.reason
    assert candidate.ms1_apex_rt == pytest.approx(10.0)
    assert candidate.ms1_apex_mz == pytest.approx(apex_ion_mz)
    assert candidate.ms1_area > 0
    # The held peak stays published; its inferred event reads no parent MS1
    # yet still names the peak it locates.
    (event,) = _read_csv(outputs.events_csv)
    assert event["peak_id"] == candidate.peak_id
    assert event["unattached_reason"] == "parent_ms1_unavailable"

    assert outputs.physical_facts_json is not None
    saved = IndexedDiscoveryPhysicalFacts(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        candidate_ids=frozenset({candidate.peak_id}),
    )
    try:
        record = saved.load_candidates([candidate.peak_id])[candidate.peak_id]
        assert record.evaluation.facts is not None
        facts = record.evaluation.facts
        assert facts.area_counts_seconds == candidate.ms1_area
        assert facts.apex_rt_min == candidate.ms1_apex_rt
        assert record.peak_id == candidate.peak_id
    finally:
        saved.close()


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
    _channel, _raw, result = _inferred_apex_case(
        tmp_path,
        lambda inferred, _rt: inferred * (1 + 1e-6),
        basis=basis,
        isotope_offsets=offsets,
    )

    (candidate,) = result.peaks
    assert candidate.status in {
        "admitted",
        "no_nl_event",
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
    _channel, _raw, result = _inferred_apex_case(
        tmp_path, _two_ions, basis=basis, companion_basis=companion_basis
    )

    assert sum(c.status == "admitted" for c in result.peaks) == 1
    if companion_basis is not None:
        # Different precursor-evidence origins locate one peak, not measured
        # copies; only the recorded precursor's event attaches.
        (candidate,) = result.peaks
        assert candidate.event_scan_ids == (10, 11)
        assert candidate.attached_event_count == 1
        assert len(result.physical_evaluations) == 1
        events = {event.ms2_scan_number: event for event in result.attachments}
        assert events[10].unattached_reason == "parent_ms1_unavailable"
        assert events[11].peak_id == candidate.peak_id


@pytest.mark.parametrize("has_isotope", [False, True])
@pytest.mark.parametrize("shared_request_count", [1, 2])
def test_inferred_precursor_rechecks_isotope_after_point_reacquisition(
    tmp_path: Path,
    has_isotope: bool,
    shared_request_count: int,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.05, 13.25, 0.10, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.12) ** 2)
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    # Inferred-only basis exercises apex/isotope reading. Two high-intensity
    # inferred seeds provide the existing authority for point reacquisition.
    group = _channel(
        tuple(
            replace(
                _seed(scan_number=scan, rt=rt, product_intensity=1_000_000.0),
                precursor_mz_basis="product_plus_neutral_loss",
            )
            for scan, rt in ((10, 12.063), (11, 12.181))
        )
    )
    apex_ion_mz = group.mz * (1 + 1e-6)
    isotope_mz = apex_ion_mz + 1.0033548 / 2

    readings = []

    def read_ion(rt: float, low: float, high: float) -> tuple[float, float] | None:
        readings.append((rt, low, high))
        ions = (apex_ion_mz, isotope_mz) if has_isotope else (apex_ion_mz,)
        return next(((mz, 1.0) for mz in ions if low <= mz <= high), None)

    result = capture_ms1_peaks(
        raw,
        (group,) * shared_request_count,
        acquisition_source=source,
        settings=_capture_settings(),
        signal_processing_profile=profile,
        ms1_centroid=read_ion,
    )
    first, reacquired, remeasured = result.measurements.acquisition_batches
    assert result.measurements.reacquisition_request_count == 1
    assert result.measurements.remeasurement_request_count == 1
    assert first.traces[0].coordinate.trace_point_count < profile.window_points
    assert reacquired.traces[0].coordinate.trace_point_count >= profile.window_points
    # The apex ion's window keeps the reacquired RT context.
    (remeasured_trace,) = remeasured.traces
    assert remeasured_trace.coordinate.request.mz == pytest.approx(apex_ion_mz)
    assert (
        remeasured_trace.coordinate.request.rt_min,
        remeasured_trace.coordinate.request.rt_max,
    ) == (
        reacquired.traces[0].coordinate.request.rt_min,
        reacquired.traces[0].coordinate.request.rt_max,
    )
    (record,) = result.physical_evaluations
    assert record.evaluation.state == "available"
    (candidate,) = result.peaks
    expected_state = "doubly_charged" if has_isotope else "no_nl_event"
    assert candidate.status == expected_state
    assert ("doubly charged" in candidate.reason) is has_isotope
    assert candidate.ms1_apex_mz == pytest.approx(apex_ion_mz)
    (binding,) = result.physical_bindings
    assert binding.peak.status == expected_state
    # Three apex scans read the ion to centre its window; the peak then reads
    # its three apex scans for ion and isotope once, even when two seed groups
    # reuse the resulting fact.
    assert len(readings) == 9
