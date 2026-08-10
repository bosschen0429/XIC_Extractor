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
    standard_peak_backfill: bool
    standard_peak_backfill_chunk_size: int
    standard_peak_backfill_publication_mode: Literal[
        "matrix-only",
        "review-gallery",
        "deep-audit",
    ]
    standard_peak_backfill_write_gallery: bool
    standard_peak_backfill_reuse_existing: bool
    standard_peak_backfill_min_shape_r: float
    owner_build_xic_backend: Literal["raw", "raw_superwindow", "ms1_index"]
    backfill_expansion_productization: Literal["off", "clean-target-selective"]
    backfill_expansion_reuse_existing_raw_overlay: bool
    backfill_expansion_reuse_existing_shift_aware: bool
    backfill_expansion_render_shift_aware_images: bool
    backfill_expansion_min_shape_r: float
    product_ready_publication_check: bool
