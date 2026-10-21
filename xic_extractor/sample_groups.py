from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.sample_metadata import SampleMetadata, SampleRole

# Shared by sequence-conflict reporting and naming suggestions. Dates alone
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


def resolve_sample_group(
    metadata: SampleMetadata | None = None, *, sample_role: SampleRole = "unknown"
) -> str:
    """Explicit group, then QC role, then Other; never infer from names."""
    if metadata is not None:
        if sample_group_key(metadata.group):
            return " ".join(metadata.group.split())
        sample_role = metadata.sample_role
    return "QC" if sample_role in {"qc", "pooled_qc"} else "Other"


def suggest_sample_groups(names: Sequence[str]) -> dict[str, str]:
    """Suggest labels from names without applying them to sample metadata."""
    labels = []
    for name in names:
        value = re.sub(r"\.[^.\\/]+$", "", name.strip())
        value = SAMPLE_CODE.sub("", value)
        value = re.sub(r"[_-]\d+$", "", value)
        labels.append(re.sub(r"[_\s-]+", " ", value).strip())
    return dict(zip(names, canonicalize_sample_groups(labels), strict=True))
