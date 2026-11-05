"""Helpers shared by test_result_assembly and other test modules."""

from xic_extractor.configuration.models import Target


def _target(
    *,
    is_istd: bool = False,
) -> Target:
    return Target(
        label="ISTD" if is_istd else "Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair="ISTD",
    )
