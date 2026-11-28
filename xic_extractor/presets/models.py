from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

PresetStrategy = Literal["neutral_loss", "product_ion", "delta_mass"]
CombineMode = Literal["single", "union", "intersection"]

SUPPORTED_STRATEGIES = frozenset({"neutral_loss", "delta_mass"})
SUPPORTED_COMBINE_MODES = frozenset({"single", "union", "intersection"})


class PresetError(ValueError):
    """Raised when a preset file is missing, invalid, or unsupported."""


def checked_tag_name(name: str, *, label: str) -> str:
    """Discovery tables join a peak's tag names with ';', so a name has none."""
    if ";" in name:
        raise PresetError(
            f"{label} {name!r} must not contain ';', which joins tag names "
            "in Discovery tables"
        )
    return name


@dataclass(frozen=True)
class PresetTag:
    strategy: str
    name: str
    value: float | None = None
    pattern_id: str | None = None


def uses_delta_mass(tags: Iterable[PresetTag]) -> bool:
    return any(tag.strategy == "delta_mass" for tag in tags)


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    tags: tuple[PresetTag, ...]
    combine_mode: CombineMode
    discovery_overrides: Mapping[str, object]
    alignment_overrides: Mapping[str, object]
    source: str
