"""Shared synthetic CID-NL runtime wiring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.alignment_pipeline_helpers import physical_facts_fingerprint_beside
from tests.cid_nl_raw_fixture import (
    _FakeRaw,
    _FakeThermoRaw,
    _working_imports,
)
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryPeakUniverseReceipt,
    mint_discovery_peak_universe_receipt,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.csv_writer import (
    write_discovery_events_csv,
    write_discovery_peaks_csv,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryPeak, EventAttachment
from xic_extractor.discovery.physical_fact_persistence import (
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.processing_identity import (
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


def build_discovery_receipts(
    root: Path,
    raw_paths: tuple[Path, ...],
    peaks_by_sample: dict[str, tuple[DiscoveryPeak, ...]],
) -> dict[str, DiscoveryPeakUniverseReceipt]:
    receipts: dict[str, DiscoveryPeakUniverseReceipt] = {}
    for raw_path in raw_paths:
        candidates = peaks_by_sample[raw_path.stem]
        candidate_csv = root / f"{raw_path.stem}.owner-candidates.csv"
        write_discovery_peaks_csv(candidate_csv, candidates)
        source = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        receipts[raw_path.stem] = mint_discovery_peak_universe_receipt(
            sample_stem=raw_path.stem,
            acquisition_source=source,
            peak_csv=candidate_csv,
            physical_facts=physical_facts_fingerprint_beside(candidate_csv),
            peaks=candidates,
        )
    return receipts


def persist_discovery_batch(
    *,
    raw_paths: tuple[Path, ...],
    settings: DiscoverySettings,
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
    physical_records_by_peak: Mapping[str, DiscoveryPhysicalFactRecord],
    signal_processing_profile: SignalProcessingProfile,
    root: Path,
) -> DiscoveryBatchInput:
    """Write in-memory Discovery results as the batch Discovery publishes.

    Each sample gets its peak table, event table and physical-fact sidecar,
    bound by an owner-closed receipt.
    """
    spec = compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings,
        signal_processing_profile=signal_processing_profile,
    )
    peak_csvs: dict[str, Path] = {}
    event_csvs: dict[str, Path | None] = {}
    receipts: dict[str, DiscoveryPeakUniverseReceipt] = {}
    for raw_path, acquisition_input in zip(
        raw_paths, spec.acquisition_inputs, strict=True
    ):
        stem = raw_path.stem
        peaks = tuple(peaks_by_sample.get(stem, ()))
        folder = root / stem
        folder.mkdir(parents=True, exist_ok=True)
        # A fixture may keep only some records or join several captures;
        # the published manifest numbers its work contiguously from zero.
        records = tuple(
            replace(record, work_item=replace(record.work_item, work_ordinal=index))
            for index, record in enumerate(
                record
                for record in physical_records_by_peak.values()
                if record.work_item.sample_stem == stem
            )
        )
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.json",
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=tuple(record.work_item for record in records),
            records=records,
        )
        receipts.update(build_discovery_receipts(folder, (raw_path,), {stem: peaks}))
        peak_csvs[stem] = folder / f"{stem}.owner-candidates.csv"
        event_csvs[stem] = write_discovery_events_csv(
            folder / "discovery_events.csv",
            [
                EventAttachment(
                    event=event,
                    xic_mz=peak.peak_mz,
                    precursor_verified=True,
                    peak_id=peak.peak_id,
                )
                for peak in peaks
                for event in peak.located_events
            ],
        )
    return DiscoveryBatchInput(
        sample_order=tuple(path.stem for path in raw_paths),
        peak_csvs=peak_csvs,
        raw_files={path.stem: path for path in raw_paths},
        event_csvs=event_csvs,
        peak_receipts=receipts,
    )


def prepare_persisted_discovery_plan(
    *,
    raw_paths: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]],
    physical_records_by_peak: Mapping[str, DiscoveryPhysicalFactRecord],
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
    root: Path,
) -> UntargetedInputPlan:
    """Plan Alignment from persisted Discovery results through its input path."""
    batch = persist_discovery_batch(
        raw_paths=raw_paths,
        settings=settings,
        peaks_by_sample=peaks_by_sample,
        physical_records_by_peak=physical_records_by_peak,
        signal_processing_profile=signal_processing_profile,
        root=root / "discovery",
    )
    prepared = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
        raw_dir=raw_paths[0].parent,
        dll_dir=dll_dir,
        output_dir=root / "alignment_inputs",
        settings=settings,
        peak_config=peak_config,
        sample_metadata=sample_metadata,
        signal_processing_profile=signal_processing_profile,
        raw_workers=1,
    )
    return prepared.plan


def load_plan_inputs(
    plan: UntargetedInputPlan,
) -> tuple[
    dict[str, tuple[DiscoveryPeak, ...]], dict[str, DiscoveryPhysicalFactRecord]
]:
    """Load every Alignment candidate of a plan with its physical record."""
    candidates, records, _support = plan.partition_input_loader(
        tuple(cid for cid, _ in plan.subject_batch.candidate_memberships)
    )
    return candidates, records


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
    ):
        assert path == raw_path
        return open_raw(
            path,
            dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
            prepared_mass_recalibration=prepared_mass_recalibration,
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
    # Keep synthetic spectra on the production worker/reader path, without vendor I/O.
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)

    def _forbid_vendor_api(_import_module):
        pytest.fail("Synthetic RAW fixtures must not reach the real Thermo API")

    monkeypatch.setattr("xic_extractor.raw_reader._load_thermo_api", _forbid_vendor_api)

    # This fixture supplies spectra, not vendor headers; the analysis-set owner
    # still orders the synthetic files without opening fake bytes natively.
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
    monkeypatch.setattr(
        "xic_extractor.raw_mass_recalibration_batch.open_raw", _open_runtime_raw
    )
    return opened
