from __future__ import annotations

from statistics import median

from xic_extractor.configuration.models import Target


def estimate_sample_drift(
    targets: list[Target], istd_anchor_rts: dict[str, float]
) -> float:
    """Estimate sample-level RT offset from successful ISTD anchors."""
    deltas: list[float] = []
    for target in targets:
        if not target.is_istd:
            continue
        anchor_rt = istd_anchor_rts.get(target.label)
        if anchor_rt is None:
            continue
        rt_center = (target.rt_min + target.rt_max) / 2.0
        deltas.append(anchor_rt - rt_center)
    return median(deltas) if deltas else 0.0
