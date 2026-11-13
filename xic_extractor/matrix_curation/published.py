"""A completed curation as review reads it: which features left and why."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import read_delimited_rows

SUMMARY_NAME = "matrix_curation_summary.json"
CURATED_MATRIX_NAME = "analysis_matrix.tsv"
DECISIONS_NAME = "feature_decisions.tsv"
# The default output name; its timestamp orders a result's curations.
FOLDER_PREFIX = "matrix_curation_"

_RELATION_NAMES = {
    "13C_M+1": "13C 同位素",
    "13C2_M+2": "13C₂ 同位素",
    "15N_M+1": "15N 同位素",
    "13C_z2": "13C 同位素（z=2）",
    "41K": "41K 同位素",
    "M+2_unassigned": "M+2 同位素（元素未定）",
}


@dataclass(frozen=True)
class PublishedCuration:
    folder: Path
    # The Alignment output the curation read.
    source_dir: Path
    matrix_path: Path
    # Removed feature_id → one line saying why.
    removed: dict[str, str]


def is_curation(folder: Path) -> bool:
    # The summary is written last; a folder without it is incomplete.
    return (folder / SUMMARY_NAME).is_file()


def latest_curation(result_dir: Path) -> Path | None:
    folders = sorted(
        path for path in result_dir.glob(f"{FOLDER_PREFIX}*") if is_curation(path)
    )
    return folders[-1] if folders else None


def read_published_curation(folder: Path) -> PublishedCuration:
    summary = json.loads((folder / SUMMARY_NAME).read_text(encoding="utf-8"))
    parameters = {step["key"]: step["parameters"] for step in summary["steps"]}
    rows = read_delimited_rows(
        folder / DECISIONS_NAME,
        required_columns=("feature_id", "decision", "decision_reason"),
    )
    return PublishedCuration(
        folder,
        Path(summary["inputs"]["source_dir"]),
        folder / CURATED_MATRIX_NAME,
        {
            row["feature_id"]: _removal_reason(row, parameters)
            for row in rows
            if row["decision"] == "drop"
        },
    )


def _removal_reason(row: dict[str, str], parameters: dict[str, dict]) -> str:
    # Only the first removing step judged the row; later steps skip it.
    step = row["decision_reason"].removeprefix("dropped_by:").split(",")[0]
    if step == "same_compound":
        relations = "、".join(
            _RELATION_NAMES.get(token, token)
            for token in row["same_compound_relation"]
            .replace("ion_family:", "")
            .split("|")
            if token
        )
        representative = row["same_compound_representative_feature_id"]
        if representative:
            line = f"{representative} 的 {relations}"
        elif "non_protonated" in row["same_compound_reason"]:
            # Its ion family has no retained [M+H]+ row to represent it.
            line = f"{relations}，沒有保留的 [M+H]+"
        else:
            line = f"{relations}，代表特徵未定"
        # Rows it derives from, other than the representative.
        via = row["same_compound_via_feature_ids"].replace(";", "、")
        return f"{line}（來自 {via}）" if via else line
    if step == "finite_fraction":
        threshold = parameters["finite_fraction"]["min_finite_fraction"]
        return (
            f"有效值比例 {row['finite_count']}/{row['eligible_sample_count']}，"
            f"低於門檻 {threshold:g}"
        )
    return f"由 {step} 移除"
