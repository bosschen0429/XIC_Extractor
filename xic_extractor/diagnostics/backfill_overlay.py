from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.diagnostics.diagnostic_io import text_value


def selected_overlay_row(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_group_id: str,
    support_verdict: str,
) -> Mapping[str, str]:
    if not rows:
        return {}
    seed_specific = [
        row
        for row in rows
        if text_value(row.get("seed_group_id")) == seed_group_id
    ]
    if not seed_group_id or not seed_specific:
        return {}
    return sorted(
        seed_specific,
        key=lambda row: overlay_verdict_sort_key(row, support_verdict=support_verdict),
    )[0]


def overlay_verdict_sort_key(
    row: Mapping[str, str],
    *,
    support_verdict: str,
) -> tuple[int, str]:
    verdict = text_value(row.get("family_verdict"))
    if verdict and verdict != support_verdict:
        return (0, verdict)
    if verdict == support_verdict:
        return (1, verdict)
    return (2, verdict)
