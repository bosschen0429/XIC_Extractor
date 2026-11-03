"""Shared shape of a curation step: inputs, parameters, availability and result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True)
class StepParameter:
    key: str
    label: str
    help: str
    minimum: float
    maximum: float


@dataclass(frozen=True)
class SampleBinding:
    """Matrix sample columns and their metadata, or why none is bound."""

    columns: tuple[str, ...]
    samples: tuple[SampleMetadata, ...] | None
    problem: str = ""


@dataclass(frozen=True)
class CurationMatrix:
    """The original Matrix as every step sees it; no step sees another's result."""

    sample_binding: SampleBinding
    values: np.ndarray


@dataclass(frozen=True)
class StepResult:
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    keep: tuple[bool, ...]
    summary: Mapping[str, Any] = field(default_factory=dict)


class CurationStep(Protocol):
    key: str
    label: str
    description: str
    parameters: tuple[StepParameter, ...]

    def unavailable_reason(self, samples: SampleBinding) -> str:
        """Return why the step cannot run on these inputs, or an empty string."""
        ...

    def evaluate(
        self, matrix: CurationMatrix, parameters: Mapping[str, float]
    ) -> StepResult: ...
