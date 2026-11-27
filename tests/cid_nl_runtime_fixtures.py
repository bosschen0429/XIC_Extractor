"""Shared synthetic CID-NL runtime wiring."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cid_nl_raw_fixture import (
    _FakeRaw,
    _FakeThermoRaw,
    _working_imports,
)
from tests.discovery_store_fixtures import SampleRecords, write_discovery
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak, EventAttachment
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.store import DiscoveryBatchInput, read_discovery_batch
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS, open_raw
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.alignment import (
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.input_planning import UntargetedInputPlan
from xic_extractor.untargeted_execution.partition import execution as runtime_module
from xic_extractor.untargeted_execution.partition import workers as partition_workers


def raw_acquisition_source(raw_path: Path) -> AcquisitionSourceIdentity:
    """The acquisition identity Alignment expects for a fixture RAW file."""
    return build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )


def persisted_sample(
    raw_path: Path,
    peaks: tuple[DiscoveryPeak, ...],
    *,
    unattached: tuple[EventAttachment, ...] = (),
) -> SampleRecords:
    """One sample whose peaks' located events all attach, plus ``unattached``."""
    return SampleRecords(
        raw_path.stem,
        raw_path,
        peaks=peaks,
        attachments=[
            EventAttachment(event=event, peak_id=peak.peak_id)
            for peak in peaks
            for event in peak.located_events
        ]
        + list(unattached),
        acquisition_source=raw_acquisition_source(raw_path),
    )


def persist_discovery_batch(
    *,
    raw_paths: tuple[Path, ...],
    settings: DiscoverySettings,
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
    root: Path,
) -> DiscoveryBatchInput:
    """Write in-memory Discovery results as the Discovery file it publishes."""
    return read_discovery_batch(
        write_discovery(
            root,
            [
                persisted_sample(path, tuple(peaks_by_sample.get(path.stem, ())))
                for path in raw_paths
            ],
            neutral_loss_by_tag=settings.neutral_loss_by_tag,
        )
    )


def prepare_persisted_discovery_plan(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
    root: Path,
) -> UntargetedInputPlan:
    """Plan Alignment from persisted Discovery results through its input path."""
    batch = persist_discovery_batch(
        raw_paths=raw_paths,
        settings=settings,
        peaks_by_sample=peaks_by_sample,
        root=root / "discovery",
    )
    return prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
        raw_dir=raw_paths[0].parent,
        dll_dir=dll_dir,
        output_dir=root / "alignment_inputs",
        settings=settings,
        sample_metadata=sample_metadata,
        signal_processing_profile=signal_processing_profile,
        execution=ExecutionProfile(alignment_workers=1, backend="thread"),
    )


def load_plan_inputs(
    plan: UntargetedInputPlan,
) -> dict[str, tuple[DiscoveryPeak, ...]]:
    """Load every Alignment candidate of a plan."""
    candidates, _support = plan.partition_input_loader(
        tuple(cid for cid, _ in plan.subject_batch.candidate_memberships)
    )
    return candidates


def install_runtime_raw(
    monkeypatch,
    *,
    source_raw: _FakeRaw,
    raw_path: Path,
    tmp_path: Path,
) -> _FakeThermoRaw:
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)

    def _open_runtime_raw(
        path,
        dll_dir,
        *,
        mass_recalibration="none",
        expected_acquisition_source,
        _expected_path_identity=None,
        prepared_mass_recalibration=None,
        timing_recorder=None,
    ):
        assert path == raw_path
        return open_raw(
            path,
            dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
            prepared_mass_recalibration=prepared_mass_recalibration,
            timing_recorder=timing_recorder,
            _import_module=_working_imports,
            _thermo=SimpleNamespace(
                raw_file_reader_adapter=SimpleNamespace(
                    FileFactory=lambda _path: vendor_raw
                ),
                device_ms="MS",
                mass_range_factory=lambda low, high: (low, high),
                trace_settings_factory=lambda *_args: SimpleNamespace(
                    MassRanges=None,
                    Filter=None,
                ),
                mass_range_trace_type="MassRange",
                ms2_order="Ms2",
                ms1_order="Ms1",
            ),
        )

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    return vendor_raw


def install_runtime_raws(
    monkeypatch,
    *,
    sources_by_path: dict[Path, _FakeRaw],
    tmp_path: Path,
) -> dict[Path, list[_FakeThermoRaw]]:
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)

    def _forbid_vendor_api(_import_module):
        pytest.fail("Synthetic RAW fixtures must not reach the real Thermo API")

    monkeypatch.setattr("xic_extractor.raw_reader._load_thermo_api", _forbid_vendor_api)

    install_fake_raw_headers(monkeypatch)
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    opened: dict[Path, list[_FakeThermoRaw]] = {}

    def _open_runtime_raw(
        path,
        dll_dir,
        *,
        mass_recalibration="none",
        expected_acquisition_source,
        _expected_path_identity=None,
        prepared_mass_recalibration=None,
        timing_recorder=None,
    ):
        vendor_raw = _FakeThermoRaw(sources_by_path[path])
        opened.setdefault(path, []).append(vendor_raw)
        return open_raw(
            path,
            dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
            prepared_mass_recalibration=prepared_mass_recalibration,
            timing_recorder=timing_recorder,
            _import_module=_working_imports,
            _thermo=SimpleNamespace(
                raw_file_reader_adapter=SimpleNamespace(
                    FileFactory=lambda _path: vendor_raw
                ),
                device_ms="MS",
                mass_range_factory=lambda low, high: (low, high),
                trace_settings_factory=lambda *_args: SimpleNamespace(
                    MassRanges=None,
                    Filter=None,
                ),
                mass_range_trace_type="MassRange",
                ms2_order="Ms2",
                ms1_order="Ms1",
            ),
        )

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", _open_runtime_raw)
    return opened


def representative_precursors(
    candidates_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
) -> dict[str, float]:
    """Each candidate's representative MS2 precursor, from its located events."""
    return {
        peak.peak_id: next(
            event.precursor_mz
            for event in peak.located_events
            if event.scan_number == peak.best_ms2_scan_id
        )
        for peaks in candidates_by_sample.values()
        for peak in peaks
    }
