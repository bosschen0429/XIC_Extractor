"""Fixed sample-group colours shared by every view that shows groups."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from xic_extractor.sample_groups import sample_group_key

# The curve palette in inspector.css; QC and Other stay neutral.
PALETTE_SIZE = 8
_NEUTRAL = {"": "var(--group-other)", "qc": "var(--group-qc)"}


def group_colors(groups: Iterable[str]) -> dict[str, str]:
    """Colour each group of one batch by its name.

    A group starts at the slot its name hashes to and takes the next free slot
    when another group of the batch already holds it, so the same group set
    colours the same way on every screen whatever the input order.
    """
    displays = list(dict.fromkeys(" ".join(group.split()) for group in groups))
    slots: dict[str, int] = {}
    for key in sorted({sample_group_key(group) for group in displays} - set(_NEUTRAL)):
        start = sha256(key.encode("utf-8")).digest()[0] % PALETTE_SIZE
        slots[key] = next(
            (
                slot
                for step in range(PALETTE_SIZE)
                if (slot := (start + step) % PALETTE_SIZE) not in slots.values()
            ),
            start,
        )
    colors = {}
    for group in displays:
        key = sample_group_key(group)
        colors[group] = _NEUTRAL.get(key) or f"var(--curve-{slots[key] + 1})"
    return colors
