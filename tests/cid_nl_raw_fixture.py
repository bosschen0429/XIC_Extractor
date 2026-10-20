"""Shared synthetic RAW acquisition fixtures for CID-NL workflow tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_candidates,
)
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    Ms2Scan,
    Ms2ScanEvent,
    RawFileHandle,
    open_raw,
)
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474
SECONDARY_NEUTRAL_LOSS_DA = 132.0423
PRECURSOR_MZ = 500.0
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


def _current_candidates(
    raw: _FakeRaw,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
):
    seeds = collect_strict_nl_seeds(raw, raw_file=raw_file, settings=settings)
    groups = group_discovery_seeds(seeds, settings=settings)
    return capture_ms1_candidates(
        raw,
        groups,
        acquisition_source=raw.acquisition_source or fake_acquisition_source(),
        settings=settings,
        peak_config=peak_config,
    ).candidates


def fake_acquisition_source() -> AcquisitionSourceIdentity:
    with tempfile.TemporaryDirectory() as directory:
        raw_path = Path(directory) / "sample.raw"
        raw_path.write_bytes(b"owner-bound-discovery")
        return build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        rt_min=7.5,
        rt_max=8.6,
        seed_rt_gap_min=0.20,
        ms1_search_padding_min=0.20,
        resolver_mode="local_minimum",
    )


def _multi_tag_settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
            NeutralLossProfile("RNA_R", SECONDARY_NEUTRAL_LOSS_DA),
        ),
        selected_tag_names=("DNA_dR", "RNA_R"),
        tag_combine_mode="union",
        rt_min=7.5,
        rt_max=8.6,
        seed_rt_gap_min=0.20,
        ms1_search_padding_min=0.20,
        resolver_mode="local_minimum",
    )


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


def _source_for(fixture, sample_stem: str) -> AcquisitionSourceIdentity:
    return resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
    )


def _open_bound_raw(
    fixture,
    sample_stem: str,
    source_raw: _FakeRaw,
    *,
    tmp_path: Path,
) -> tuple[RawFileHandle, _FakeThermoRaw]:
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)
    handle = open_raw(
        tmp_path / f"{sample_stem}.synthetic",
        tmp_path,
        expected_acquisition_source=_source_for(fixture, sample_stem),
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
        ),
    )
    return handle, vendor_raw


def _working_imports(name: str):
    if name == "pythonnet":
        return SimpleNamespace(get_runtime_info=lambda: "ok")
    if name == "clr":
        return SimpleNamespace(AddReference=lambda _path: None)
    return SimpleNamespace()


class _FakeThermoRaw:
    def __init__(self, source: _FakeRaw) -> None:
        self.source = source
        self.RunHeaderEx = SimpleNamespace(
            StartTime=float(source.rt[0]), EndTime=float(source.rt[-1])
        )
        self.disposed = False
        self.chromatogram_request_counts: list[int] = []
        self.scans = {scan.scan_number: scan for scan in source.scans}
        self.trailer_labels: list[str] = []

    def Dispose(self) -> None:
        self.disposed = True

    def SelectInstrument(self, *_args: object) -> None:
        return None

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        index = int(np.searchsorted(self.source.rt, rt, side="left"))
        return min(max(index + 1, 1), len(self.source.rt))

    def RetentionTimeFromScanNumber(self, scan_number: int) -> float:
        return float(self.source.rt[scan_number - 1])

    def GetChromatogramData(self, settings, start_scan: int, end_scan: int):
        self.chromatogram_request_counts.append(len(settings))
        rt = self.source.rt[start_scan - 1 : end_scan]
        intensity = self.source.intensity[start_scan - 1 : end_scan]
        return SimpleNamespace(
            PositionsArray=[rt.tolist() for _setting in settings],
            IntensitiesArray=[intensity.tolist() for _setting in settings],
        )

    def GetFilterForScanNumber(self, scan_number: int):
        scan = self.scans.get(scan_number)
        if scan is None:
            return SimpleNamespace(MSOrder="Ms1")
        return SimpleNamespace(
            MSOrder="Ms2",
            Filter=SimpleNamespace(
                Reactions=[
                    SimpleNamespace(PrecursorMass=scan.precursor_mz),
                ]
            ),
        )

    def GetSimplifiedScan(self, scan_number: int):
        scan = self.scans[scan_number]
        return SimpleNamespace(
            Masses=scan.masses,
            Intensities=scan.intensities,
        )

    def GetTrailerExtraHeaderInformation(self):
        return [SimpleNamespace(Label=label) for label in self.trailer_labels]


class _FakeRaw:
    def __init__(
        self,
        scan_rts: tuple[float, ...],
        *,
        scan_precursor_mz: float = PRECURSOR_MZ,
        neutral_loss_das: tuple[float, ...] | None = None,
        acquisition_source: AcquisitionSourceIdentity | None = None,
    ) -> None:
        if neutral_loss_das is None:
            product_mzs = (PRODUCT_MZ,) * len(scan_rts)
        else:
            if len(neutral_loss_das) != len(scan_rts):
                raise ValueError("neutral_loss_das must match scan_rts")
            product_mzs = tuple(
                scan_precursor_mz - neutral_loss_da
                for neutral_loss_da in neutral_loss_das
            )
        self.acquisition_source = acquisition_source
        self.rt = np.linspace(7.4, 8.7, 521)
        self.intensity = sum(
            (
                1000.0 * np.exp(-0.5 * ((self.rt - center) / 0.035) ** 2)
                for center in scan_rts
            ),
            np.zeros_like(self.rt),
        )
        self.scans = tuple(
            Ms2Scan(
                scan_number=int(np.argmin(np.abs(self.rt - rt))) + 1,
                rt=rt,
                precursor_mz=scan_precursor_mz,
                masses=np.asarray(
                    (
                        product_mz,
                        product_mz + 10.0,
                    )
                ),
                intensities=np.asarray((800.0, 200.0)),
                base_peak=1000.0,
            )
            for rt, product_mz in zip(scan_rts, product_mzs, strict=True)
        )
        self.batch_calls = 0

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self.batch_calls

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        request = XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol)
        trace = self._trace(request)
        return trace.rt, trace.intensity

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_calls += 1
        return tuple(self._trace(request) for request in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        indices = np.flatnonzero(mask)
        return int(indices[0]), int(indices[-1])

    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ):
        for scan in self.scans:
            if rt_min <= scan.rt <= rt_max:
                yield Ms2ScanEvent(
                    scan=scan,
                    parse_error=None,
                    scan_number=scan.scan_number,
                )

    def _trace(self, request: XICRequest) -> XICTrace:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        return XICTrace(rt=self.rt[mask], intensity=self.intensity[mask])
