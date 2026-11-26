"""Helpers shared by test_targeted_peak_selection and other test modules."""

from xic_extractor.configuration.execution_profile import in_process_execution
from xic_extractor.configuration.models import ExtractionConfig


def _config(tmp_path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
        execution=in_process_execution(),
    )
