from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.sample_metadata import SampleMetadata, SampleRole

# Sequence-conflict reporting. Dates alone
# are not sample codes; TumorBC2312_DNA contains BC2312.
SAMPLE_CODE = re.compile(r"[A-Z]{1,3}\d{3,}")


def sample_group_key(group: str) -> str:
    """Case-insensitive group identity; blank and Other mean unassigned."""
    key = " ".join(group.split()).casefold()
    return "" if key == "other" else key


def canonicalize_sample_groups(groups: Iterable[str]) -> tuple[str, ...]:
    """Normalize explicit groups, retaining the first spelling of each key."""
    display: dict[str, str] = {"": ""}
    return tuple(
        display.setdefault(sample_group_key(g), " ".join(g.split())) for g in groups
    )


# Without an explicit group, the role still tells study samples from blanks.
_ROLE_GROUPS = {
    "sample": "Sample",
    "qc": "QC",
    "std": "STD",
    "blank": "Blank",
    "other": "Other",
}


def resolve_sample_group(
    metadata: SampleMetadata | None = None, *, sample_role: SampleRole | None = None
) -> str:
    """Explicit group, then the role; no role is Other. Never use names."""
    if metadata is not None:
        if sample_group_key(metadata.group):
            return " ".join(metadata.group.split())
        sample_role = metadata.sample_role
    return _ROLE_GROUPS[sample_role] if sample_role is not None else "Other"
