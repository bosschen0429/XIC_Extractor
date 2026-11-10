"""Helpers shared by test_discovery_physical_fact_persistence and other test modules."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    acquire_trace_batch,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
)
from xic_extractor.discovery.physical_fact_record import discovery_trace_capability
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
    )


def _spec(tmp_path: Path, *, profile_points: int = 3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample acquisition")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=profile_points),
    )
    return raw_path, spec, spec.acquisition_inputs[0]


def _work(
    ordinal: int,
    acquisition_input,
    *,
    terminal: str,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalWorkItem:
    if terminal == "available":
        query = 0.8
        context = (0.0, 3.0)
    elif terminal == "unavailable":
        query = 1.0
        context = (1.0, 1.0)
    else:
        query = 1.5
        context = (1.0, 1.5)
    return DiscoveryPhysicalWorkItem(
        work_ordinal=ordinal,
        acquisition_source_id=(
            acquisition_input.acquisition_source.acquisition_source_id
        ),
        sample_stem=acquisition_input.sample_stem,
        best_ms2_scan_id=100 + ordinal,
        precursor_mz=500.0 + ordinal,
        product_mz=383.9526 + ordinal,
        query_rt_min=query,
        context_rt_start_min=context[0],
        context_rt_end_min=context[1],
        ion_mz=acquired_trace.coordinate.request.mz,
        xic_mz=acquired_trace.coordinate.request.mz,
        xic_rt_min=acquired_trace.coordinate.request.rt_min,
        xic_rt_max=acquired_trace.coordinate.request.rt_max,
        xic_ppm_tol=acquired_trace.coordinate.request.ppm_tol,
    )


def _record(
    work_item: DiscoveryPhysicalWorkItem,
    *,
    terminal: str,
    profile: SignalProcessingProfile,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalFactRecord:
    rt = acquired_trace.trace.rt
    raw = acquired_trace.trace.intensity
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        query_rt_min=work_item.query_rt_min,
        request_context_rt_start_min=work_item.context_rt_start_min,
        request_context_rt_end_min=work_item.context_rt_end_min,
    )
    return DiscoveryPhysicalFactRecord(
        work_item=work_item,
        acquired_trace=acquired_trace,
        evaluation=evaluation,
    )


def _acquired_trace(acquisition_input, *, terminal: str, ordinal: int) -> AcquiredTrace:
    if terminal == "available":
        rt = np.linspace(0.0, 3.0, 31)
        raw = 10.0 + 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
        request_rt = (0.0, 3.0)
    elif terminal == "unavailable":
        rt = np.asarray([1.0])
        raw = np.asarray([10.0])
        request_rt = (1.0, 1.0)
    else:
        rt = np.asarray([1.0, 2.0, 1.5])
        raw = np.asarray([1.0, 2.0, 1.0])
        request_rt = (1.0, 2.0)

    class Adapter:
        raw_chromatogram_call_count = 0

        def extract_xic_many(self, _requests):
            return (XICTrace.from_arrays(rt, raw),)

        def scan_window_for_request(self, _request):
            return (1000 + ordinal, 1010 + ordinal)

    capability = discovery_trace_capability(acquisition_input.acquisition_source)
    request = XICRequest(
        mz=500.0 + ordinal,
        rt_min=request_rt[0],
        rt_max=request_rt[1],
        ppm_tol=20.0,
    )
    return acquire_trace_batch(capability, (request,), Adapter()).traces[0]


def _population(spec, acquisition_input):
    terminals = ("available", "unavailable", "failed")
    manifest = []
    records = []
    for index, terminal in enumerate(terminals):
        acquired = _acquired_trace(
            acquisition_input,
            terminal=terminal,
            ordinal=index,
        )
        item = _work(
            index,
            acquisition_input,
            terminal=terminal,
            acquired_trace=acquired,
        )
        manifest.append(item)
        records.append(
            _record(
                item,
                terminal=terminal,
                profile=spec.signal_processing_profile,
                acquired_trace=acquired,
            )
        )
    return tuple(manifest), tuple(records)
