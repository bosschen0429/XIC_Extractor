"""Helpers shared by test_search_scope_capture and other test modules."""

from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.search_scope import (
    CaptureCandidateBinding,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def _capture_binding(
    *,
    candidate_id: str,
    method_subject_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source_id: str,
    mz: float,
    ppm_tol: float,
    rt_min: float,
    rt_max: float,
) -> CaptureCandidateBinding:
    return CaptureCandidateBinding(
        candidate_id=candidate_id,
        method_subject_id=method_subject_id,
        sample_key=sample.identity.sample_key,
        acquisition_source_id=acquisition_source_id,
        query_mz=mz,
        query_rt_min=rt_min,
        query_rt_max=rt_max,
        query_ppm_tol=ppm_tol,
        preferred_rt=8.0,
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
