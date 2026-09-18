"""Small runtime guards for invariant-bearing numeric inputs."""

from __future__ import annotations

from xic_extractor.validation_primitives import (
    require_finite_real,
    require_integer,
)

__all__ = [
    "require_finite_real",
    "require_integer",
]

