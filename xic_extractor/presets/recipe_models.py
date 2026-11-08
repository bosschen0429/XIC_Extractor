from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.settings import DiscoverySettings

CidNlRecipeRoute = Literal[
    "full",
    "discovery_only",
    "align_only",
]


@dataclass(frozen=True, slots=True)
class BoundCidNlRecipe:
    """Immutable preset-owned method and current-route composition."""

    route: CidNlRecipeRoute
    discovery_settings: DiscoverySettings
    alignment_config: AlignmentConfig
