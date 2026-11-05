"""Helpers shared by test_alignment_known_istd_in_memory and other test modules."""

from xic_extractor.configuration.models import Target


def _make_target(label: str, is_istd: bool = True) -> Target:
    return Target(
        label=label,
        mz=200.0,
        rt_min=1.0,
        rt_max=5.0,
        ppm_tol=10.0,
        neutral_loss_da=100.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=is_istd,
        istd_pair="",
    )
