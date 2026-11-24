"""Exclude remaining rows in method-specific or user-supplied nucleoside windows."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from xic_extractor.configuration.paths import dna_dr_nucleosides_path
from xic_extractor.matrix_curation.mass_rt_windows import (
    MzRtWindow,
    match_labels,
    mz_rt_window,
)
from xic_extractor.matrix_curation.steps import (
    CurationInputs,
    CurationMatrix,
    FeatureIdentity,
    NucleosideList,
    StepParameter,
    StepResult,
)
from xic_extractor.tabular_io import read_delimited_rows


def method_nucleosides(neutral_loss_by_tag: object) -> NucleosideList | None:
    """Only this complete published channel signature has reference RT windows."""
    if neutral_loss_by_tag == {"DNA_dR": 116.0474}:
        return NucleosideList(
            dna_dr_nucleosides_path(), "內建 DNA dR 清單：dG、dT、dA、dC"
        )
    return None


def read_nucleoside_list(path: Path) -> tuple[MzRtWindow, ...]:
    rows = read_delimited_rows(
        path,
        required_columns=("label", "mz"),
        encoding="utf-8-sig",
        allow_missing_trailing_columns=("rt_min", "rt_max", "expected_rt_min"),
    )
    if not rows:
        raise ValueError(f"{path}：核苷清單沒有資料列。")
    windows = []
    for line, row in enumerate(rows, 2):
        location = f"{path}:{line}"
        label = row["label"].strip()
        if not label:
            raise ValueError(f"{location}：缺少 label。")
        numbers = {}
        for field in ("mz", "rt_min", "rt_max", "expected_rt_min"):
            token = row.get(field, "").strip()
            try:
                value = float(token) if token else None
            except ValueError as exc:
                raise ValueError(f"{location}：{field} 必須是有限數字。") from exc
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{location}：{field} 必須是有限數字。")
            numbers[field] = value
        mz, low, high = numbers["mz"], numbers["rt_min"], numbers["rt_max"]
        if mz is None or mz <= 0:
            raise ValueError(f"{location}：mz 必須是正數。")
        if (low is None) != (high is None) or (
            low is not None and high is not None and high <= low
        ):
            raise ValueError(
                f"{location}：rt_min 與 rt_max 需成對，且 rt_max > rt_min。"
            )
        window = mz_rt_window(
            label=label,
            mz=mz,
            rt_min=low,
            rt_max=high,
            expected_rt_min=numbers["expected_rt_min"],
        )
        if window is None:
            raise ValueError(f"{location}：沒有 RT 範圍，請提供 expected_rt_min。")
        windows.append(window)
    return tuple(windows)


class KnownNucleosidesStep:
    key = "known_nucleosides"
    label = "排除清單核苷"
    description = (
        "移除 m/z 與 RT 同時落在清單視窗的列，以及已把這種列收斂進來的列；"
        "命中不代表身分確證。"
    )
    parameters: tuple[StepParameter, ...] = ()
    needs_cell_evidence = False

    def unavailable_reason(self, inputs: CurationInputs) -> str:
        return (
            ""
            if inputs.nucleoside_list
            else "此結果沒有適用的內建清單。可在「改用其他來源」選擇自訂核苷 TSV。"
        )

    def evaluate(
        self, matrix: CurationMatrix, parameters: Mapping[str, float]
    ) -> StepResult:
        def label_of(feature: FeatureIdentity) -> str:
            return match_labels(
                feature.matrix_mz, feature.matrix_rt, matrix.nucleoside_windows
            )

        members = matrix.members or ((),) * len(matrix.features)
        own = tuple(label_of(feature) for feature in matrix.features)
        labels = tuple(
            label or next(filter(None, map(label_of, folded)), "")
            for label, folded in zip(own, members, strict=True)
        )
        return StepResult(
            columns=(
                "known_nucleosides_label",
                "known_nucleosides_decision",
                "known_nucleosides_reason",
            ),
            rows=tuple(
                {
                    "known_nucleosides_label": label,
                    "known_nucleosides_decision": "drop" if label else "keep",
                    "known_nucleosides_reason": "window_match"
                    if direct
                    else "member_window_match"
                    if label
                    else "no_window_match",
                }
                for label, direct in zip(labels, own, strict=True)
            ),
            keep=tuple(not label for label in labels),
        )


KNOWN_NUCLEOSIDES_STEP = KnownNucleosidesStep()
