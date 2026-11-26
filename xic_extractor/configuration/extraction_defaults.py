"""Peak-shape and MS2 defaults shared by Targeted and Untargeted extraction."""

from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.configuration.models import ExtractionConfig


def default_extraction_config(
    *, data_dir: Path, dll_dir: Path, output_dir: Path, execution: ExecutionProfile
) -> ExtractionConfig:
    """Return the one source of the shared scientific extraction defaults.

    Workflow builders apply their own choices with ``dataclasses.replace``.
    """
    policy = DEFAULT_CID_NL_MASS_POLICY
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=policy.ms2_precursor_tol_da,
        nl_min_intensity_ratio=policy.nl_min_intensity_ratio,
        execution=execution,
    )
