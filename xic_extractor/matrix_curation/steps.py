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
class FeatureIdentity:
    """A Matrix row as its bound Review describes it."""

    feature_id: str
    matrix_mz: float
    matrix_rt: float
    center_mz_basis: str


@dataclass(frozen=True)
class CellEvidence:
    """One published cell: whether it was detected directly, its value and apex."""

    direct: bool
    value: float
    # None when the published output records no apex for the cell.
    apex_rt_min: float | None


@dataclass(frozen=True)
class CurationInputs:
    """What the source offers, so a step can say whether it can run."""

    sample_binding: SampleBinding
    # Empty when per-cell evidence can be read; otherwise why not.
    cell_evidence_problem: str = ""


@dataclass(frozen=True)
class CurationMatrix:
    """The remaining source rows entering one curation step; values are unchanged."""

    sample_binding: SampleBinding
    values: np.ndarray
    features: tuple[FeatureIdentity, ...] = ()
    # Per Matrix row, published cells by explanation sample key; loaded only
    # when an enabled step needs it.
    cells: tuple[Mapping[str, CellEvidence], ...] | None = None


@dataclass(frozen=True)
class StepTable:
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class StepResult:
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    keep: tuple[bool, ...]
    summary: Mapping[str, Any] = field(default_factory=dict)
    # Extra output files of this step, by file name.
    tables: Mapping[str, StepTable] = field(default_factory=dict)


class CurationStep(Protocol):
    key: str
    label: str
    description: str
    parameters: tuple[StepParameter, ...]
    needs_cell_evidence: bool

    def unavailable_reason(self, inputs: CurationInputs) -> str:
        """Return why the step cannot run on these inputs, or an empty string."""
        ...

    def evaluate(
        self, matrix: CurationMatrix, parameters: Mapping[str, float]
    ) -> StepResult: ...
