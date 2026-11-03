"""Keep features whose published values are available in enough study samples."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from xic_extractor.matrix_curation.steps import (
    CurationInputs,
    CurationMatrix,
    StepParameter,
    StepResult,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True)
class FiniteFraction:
    finite_count: int
    eligible_sample_count: int
    finite_fraction: float
    keep: bool
    reason: str


def evaluate_finite_fraction(
    values: np.ndarray,
    *,
    eligible_samples: Sequence[bool],
    min_finite_fraction: float,
) -> tuple[FiniteFraction, ...]:
    """Select by finite-value coverage, including zero, without changing values.

    Availability describes published numbers, not counted detection. The caller
    supplies sample eligibility from explicit metadata; no role is inferred.
    """
    if not math.isfinite(min_finite_fraction) or not 0 <= min_finite_fraction <= 1:
        raise ValueError("min_finite_fraction must be finite and between 0 and 1")
    mask = np.asarray(eligible_samples, dtype=bool)
    if values.ndim != 2 or mask.ndim != 1 or values.shape[1] != mask.size:
        raise ValueError("values and eligible_samples must have the same sample census")
    count = int(mask.sum())
    if not count:
        raise ValueError(
            "no eligible study samples: require study_sample and excluded=FALSE"
        )
    finite_counts = np.isfinite(values[:, mask]).sum(axis=1)
    results = []
    for finite_count in finite_counts:
        fraction = int(finite_count) / count
        keep = fraction >= min_finite_fraction
        results.append(
            FiniteFraction(
                finite_count=int(finite_count),
                eligible_sample_count=count,
                finite_fraction=fraction,
                keep=keep,
                reason=(
                    "finite_fraction_at_or_above_minimum"
                    if keep
                    else "finite_fraction_below_minimum"
                ),
            )
        )
    return tuple(results)


def _eligible(samples: Sequence[SampleMetadata]) -> tuple[bool, ...]:
    return tuple(
        row.sample_role == "study_sample" and not row.excluded for row in samples
    )


class FiniteFractionStep:
    key = "finite_fraction"
    label = "有效值比例篩選"
    description = (
        "保留在研究樣本中有足夠數值的 feature。分母是 sample_role=study_sample "
        "且未排除的樣本；0 與有限的回填值都算有數值，空白不算。"
        "這是數值可用率，不是偵測率。"
    )
    parameters: tuple[StepParameter, ...] = (
        StepParameter(
            key="min_finite_fraction",
            label="最低有效值比例",
            help="0 至 1；比例大於或等於此值的 feature 保留。0 代表不篩選。",
            minimum=0.0,
            maximum=1.0,
        ),
    )

    needs_cell_evidence = False

    def unavailable_reason(self, inputs: CurationInputs) -> str:
        samples = inputs.sample_binding
        if samples.samples is None:
            return samples.problem
        if not any(_eligible(samples.samples)):
            return "樣本資料中沒有可用的研究樣本（sample_role=study_sample 且未排除）。"
        return ""

    def evaluate(
        self, matrix: CurationMatrix, parameters: Mapping[str, float]
    ) -> StepResult:
        samples = matrix.sample_binding.samples
        if samples is None:
            raise ValueError(matrix.sample_binding.problem)
        eligible = _eligible(samples)
        results = evaluate_finite_fraction(
            matrix.values,
            eligible_samples=eligible,
            min_finite_fraction=parameters["min_finite_fraction"],
        )
        return StepResult(
            columns=(
                "finite_count",
                "eligible_sample_count",
                "finite_fraction",
                "finite_fraction_decision",
                "finite_fraction_reason",
            ),
            rows=tuple(
                {
                    "finite_count": result.finite_count,
                    "eligible_sample_count": result.eligible_sample_count,
                    "finite_fraction": repr(result.finite_fraction),
                    "finite_fraction_decision": "keep" if result.keep else "drop",
                    "finite_fraction_reason": result.reason,
                }
                for result in results
            ),
            keep=tuple(result.keep for result in results),
            summary={
                "eligible_matrix_columns": [
                    column
                    for column, use in zip(
                        matrix.sample_binding.columns, eligible, strict=True
                    )
                    if use
                ],
            },
        )


FINITE_FRACTION_STEP = FiniteFractionStep()
