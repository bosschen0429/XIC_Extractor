"""Helpers shared by test_discovery_pipeline and other test modules."""

import csv
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np

from tests.cid_nl_raw_fixture import fake_acquisition_source
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery import pipeline as _pipeline
from xic_extractor.discovery.models import DiscoveryRunOutputs
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474


def _settings(**overrides: object) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**cast(Any, values))


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


def _scan_event(
    *,
    scan_number: int,
    rt: float,
    product_intensity: float = 10000.0,
    precursor_mz: float = 258.1085,
    masses: list[float] | None = None,
    intensities: list[float] | None = None,
) -> Ms2ScanEvent:
    if masses is None:
        masses = [precursor_mz - NEUTRAL_LOSS_DA]
    if intensities is None:
        intensities = [product_intensity]
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=scan_number,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray(masses, dtype=float),
            intensities=np.asarray(intensities, dtype=float),
            base_peak=max(intensities),
        ),
        parse_error=None,
        scan_number=scan_number,
    )


def _compiled_run_spec(raw_paths: tuple[Path, ...], settings: DiscoverySettings) -> Any:
    for path in raw_paths:
        if not path.exists():
            path.write_bytes(f"discovery:{path.stem}".encode())
    return compile_discovery_processing_run_spec(raw_paths, discovery_settings=settings)


def _spec_bound_opener(spec: Any, opener: Any) -> Any:
    sources = {
        item.sample_stem: item.acquisition_source for item in spec.acquisition_inputs
    }

    def open_bound(path: Path, dll_dir: Path) -> Any:
        handle = opener(path, dll_dir)
        handle.acquisition_source = sources[path.stem]
        return handle

    return open_bound


def _run_discovery(raw_path: Path, **kwargs: Any) -> DiscoveryRunOutputs:
    spec = kwargs.pop("run_spec", None) or _compiled_run_spec(
        (raw_path,), kwargs["settings"]
    )
    opener = kwargs.pop("raw_opener", None)
    if opener is not None:
        opener = _spec_bound_opener(spec, opener)
    return _pipeline.run_discovery(raw_path, run_spec=spec, raw_opener=opener, **kwargs)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class _OwnerBoundFakeRawHandle:
    def ms1_centroid_mz(self, rt: float, mz_min: float, mz_max: float) -> float | None:
        return None

    raw_open_receipt = None
    mass_recalibration_result = MassRecalibrationResult("none")

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)

    def batch_xic_source(self):
        return self

    def __init__(
        self,
        source: AcquisitionSourceIdentity,
        events: list[Ms2ScanEvent],
        *,
        rt: np.ndarray,
        intensity: np.ndarray,
    ) -> None:
        self.acquisition_source = source
        self._events = events
        self._rt = rt
        self._intensity = intensity
        self.raw_chromatogram_call_count = 0
        self.entered = False
        self.closed = False

    def __enter__(self) -> "_OwnerBoundFakeRawHandle":
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ) -> Iterator[Ms2ScanEvent]:
        yield from self._events

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        return tuple(
            XICTrace.from_arrays(self._rt, self._intensity) for _request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return int(request.rt_min * 100), int(request.rt_max * 100)


class _FakeRawHandle:
    def ms1_centroid_mz(self, rt: float, mz_min: float, mz_max: float) -> float | None:
        return None

    raw_open_receipt = None
    mass_recalibration_result = MassRecalibrationResult("none")

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)

    def batch_xic_source(self):
        return self

    def __init__(
        self,
        events: list[Ms2ScanEvent],
        *,
        rt: np.ndarray | None = None,
        intensity: np.ndarray | None = None,
    ) -> None:
        self._events = events
        self._rt = np.asarray([] if rt is None else rt, dtype=float)
        self._intensity = np.asarray(
            [] if intensity is None else intensity, dtype=float
        )
        self.acquisition_source = fake_acquisition_source()
        self._chromatogram_call_count = 0
        self.entered = False
        self.closed = False

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self._chromatogram_call_count

    def __enter__(self) -> "_FakeRawHandle":
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        yield from self._events

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self._chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        return tuple(
            XICTrace.from_arrays(self._rt, self._intensity) for _request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 0, max(len(self._rt) - 1, 0)
