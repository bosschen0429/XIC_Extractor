from __future__ import annotations

import math

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


def owner_quantification_eligible(owner: SampleLocalMS1Owner) -> bool:
    if not owner.quantification_eligible:
        return False
    apex = owner.owner_apex_rt
    left = owner.owner_peak_start_rt
    right = owner.owner_peak_end_rt
    integration = owner.selected_integration
    if integration is not None:
        apex = integration.rt_apex_min
        left = integration.rt_left_min
        right = integration.rt_right_min
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in (apex, left, right)
    ):
        return False
    return left < apex < right
