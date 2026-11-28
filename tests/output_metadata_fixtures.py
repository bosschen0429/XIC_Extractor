"""Helpers shared by test_output_metadata and other test modules."""

from pathlib import Path

from tests.execution_fixtures import in_process_execution
from xic_extractor.configuration.models import ExtractionConfig


def _config(output_dir: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        ms1_morphology_smoothing_window_points=21,
        signal_processing_window_points=21,
        ms2_precursor_tol_da=0.4,
        nl_min_intensity_ratio=0.02,
        target_config_hash="abc12345",
        execution=in_process_execution(),
    )
