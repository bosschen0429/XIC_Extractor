"""Helpers shared by test_output_metadata and other test modules."""

from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig


def _config(output_dir: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=17,
        smooth_polyorder=3,
        ms1_morphology_smoothing_window_points=21,
        signal_processing_window_points=21,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.12,
        ms2_precursor_tol_da=0.4,
        nl_min_intensity_ratio=0.02,
        resolver_mode="local_minimum",
        target_config_hash="abc12345",
    )
