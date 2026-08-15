from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.models import DiscoverySettings

CidNlRecipeRoute = Literal[
    "full",
    "discovery_only",
    "align_only",
    "bounded_mode_track",
]


@dataclass(frozen=True, slots=True)
class BoundCidNlRecipe:
    """Immutable preset-owned method and current-route composition."""

    route: CidNlRecipeRoute
    discovery_settings: DiscoverySettings
    alignment_config: AlignmentConfig
    owner_build_xic_backend: Literal["raw", "raw_superwindow", "ms1_index"]
