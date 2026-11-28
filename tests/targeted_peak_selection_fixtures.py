"""Helpers shared by test_targeted_peak_selection and other test modules."""

from tests.execution_fixtures import in_process_execution
from xic_extractor.configuration.models import ExtractionConfig


def _config(tmp_path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        execution=in_process_execution(),
    )
