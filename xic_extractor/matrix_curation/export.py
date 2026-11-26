"""Read a published Alignment folder, run the enabled steps and write a new folder."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME
from xic_extractor.matrix_curation.finite_fraction import FINITE_FRACTION_STEP
from xic_extractor.matrix_curation.istd import windows_from_target_snapshot
from xic_extractor.matrix_curation.known_nucleosides import (
    KNOWN_NUCLEOSIDES_STEP,
    method_nucleosides,
    read_nucleoside_list,
)
from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow, match_labels
from xic_extractor.matrix_curation.published import (
    CURATED_MATRIX_NAME,
    DECISIONS_NAME,
    FOLDER_PREFIX,
    SUMMARY_NAME,
)
from xic_extractor.matrix_curation.same_compound import (
    REMOVAL_CAUSES,
    SAME_COMPOUND_STEP,
    group_outcome_review,
    removal_cause,
)
from xic_extractor.matrix_curation.same_compound import (
    REVIEW_NAME as SAME_COMPOUND_REVIEW_NAME,
)
from xic_extractor.matrix_curation.source import (
    CurationSource,
)
from xic_extractor.matrix_curation.steps import (
    CellEvidence,
    CurationInputs,
    CurationMatrix,
    CurationStep,
    FeatureIdentity,
    NucleosideList,
    SampleBinding,
    StepResult,
    StepTable,
)
from xic_extractor.output.alignment.matrix_output_contract import matrix_review_rows
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadata,
    is_sample_metadata_source,
    load_sample_metadata,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    file_sha256,
    read_tsv_with_header_snapshot,
    validate_delimited_header,
    write_tsv,
)

STEPS: tuple[CurationStep, ...] = (
    SAME_COMPOUND_STEP,
    KNOWN_NUCLEOSIDES_STEP,
    FINITE_FRACTION_STEP,
)
DECISION_KEY_COLUMNS = (
    "source_matrix_row_index",
    "feature_id",
    "Mz",
    "RT",
    "istd_label",
)


def _run_evidence(source: CurationSource) -> tuple[dict[str, Any], str]:
    """Published method metadata, or why the explanation is unavailable."""
    from xic_extractor.alignment_explanation.errors import ExplanationError
    from xic_extractor.alignment_explanation.reader import (
        read_explanation_metadata,
    )

    if source.explanation_path is None:
        return {}, (
            f"資料夾中沒有 {EXPLANATION_ARTIFACT_NAME}，無法取得每格的直接偵測證據；"
            "請重新執行 Alignment 產生新輸出。"
        )
    try:
        return read_explanation_metadata(source.explanation_path), ""
    except ExplanationError as exc:
        return {}, f"無法讀取 {EXPLANATION_ARTIFACT_NAME}：{exc}"


def _curation_inputs(
    source: CurationSource,
    binding: SampleBinding,
    steps: Sequence[CurationStep],
    selected_list: Path | None,
    *,
    matrix_digest: str | None = None,
) -> CurationInputs:
    reference = (
        NucleosideList(selected_list, f"自訂清單：{selected_list.name}")
        if selected_list
        else None
    )
    lookup = KNOWN_NUCLEOSIDES_STEP in steps and reference is None
    if not lookup and not any(step.needs_cell_evidence for step in steps):
        return CurationInputs(binding, nucleoside_list=reference)
    metadata, problem = _run_evidence(source)
    if lookup and (builtin := method_nucleosides(metadata.get("neutral_loss_by_tag"))):
        digest = matrix_digest or file_sha256(source.matrix_path)
        if digest.lower() == metadata.get("matrix_sha256"):
            reference = builtin
    return CurationInputs(binding, problem, reference)


def read_run_istd(source: CurationSource) -> tuple[MzRtWindow, ...] | None:
    """The internal standards the run declared, or None when it cannot be read."""
    if source.target_snapshot is None:
        return None
    try:
        return windows_from_target_snapshot(source.target_snapshot)
    except ValueError:
        return None


def load_cell_evidence(
    source: CurationSource, features: Sequence[FeatureIdentity]
) -> tuple[dict[str, CellEvidence], ...]:
    """Published cells of each Matrix row, keyed by explanation sample."""
    from xic_extractor.alignment_explanation.published_cells import (
        read_published_cells,
    )

    by_feature: dict[str, dict[str, CellEvidence]] = {
        feature.feature_id: {} for feature in features
    }
    for cell in read_published_cells(source.source_dir):
        row = by_feature.get(cell.feature_id)
        if row is None or cell.value is None:
            continue
        row[cell.sample] = CellEvidence(
            direct=cell.origin == "detected",
            value=cell.value,
            apex_rt_min=cell.apex_rt_min,
        )
    return tuple(by_feature[feature.feature_id] for feature in features)


def default_output_dir(source: CurationSource) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return source.source_dir / f"{FOLDER_PREFIX}{stamp}"


def read_sample_metadata_file(path: Path) -> tuple[SampleMetadata, ...]:
    if path.suffix.lower() not in {".tsv", ".csv"} or not is_sample_metadata_source(
        path
    ):
        raise ValueError(
            f"請選擇從工作區樣本編輯器匯出的樣本資料（TSV 或 CSV）：{path}"
        )
    return load_sample_metadata(path)


def read_run_samples(
    source: CurationSource, *, dll_dir: Path | None = None
) -> tuple[SampleMetadata, ...] | None:
    """The run's saved sample data, else its RAW-header roles.

    RAW headers are read only with a DLL directory supplied by the current
    caller. Run metadata is provenance and never selects executable assemblies.
    """
    saved = source.source_dir / "sample_metadata.tsv"
    if saved.is_file():
        return read_sample_metadata_file(saved)
    if source.raw_dir is None or dll_dir is None:
        return None
    if not source.raw_dir.is_dir():
        return None
    from xic_extractor.raw_reader_errors import RawReaderError
    from xic_extractor.raw_sequence import read_analysis_set

    try:
        return read_analysis_set(
            source.raw_dir, dll_dir, analysis="untargeted"
        ).sample_metadata
    except OSError, RawReaderError, ValueError:
        return None


def bind_samples(
    sample_columns: Sequence[str],
    metadata: Sequence[SampleMetadata] | None,
    *,
    missing: str,
) -> SampleBinding:
    """Match each Matrix column to exactly one sample by name or RAW stem."""
    columns = tuple(sample_columns)
    if metadata is None:
        return SampleBinding(columns, None, missing)
    aliases: dict[str, SampleMetadata] = {}
    for row in metadata:
        for alias in {row.sample_name, row.raw_stem}:
            column = escape_excel_formula(alias)
            if column in aliases and aliases[column] != row:
                return SampleBinding(columns, None, f"樣本名稱對應不明確：{column!r}")
            aliases[column] = row
    absent = [column for column in columns if column not in aliases]
    if absent:
        return SampleBinding(
            columns, None, f"樣本資料缺少 Matrix 欄位對應的樣本：{', '.join(absent)}"
        )
    matched = tuple(aliases[column] for column in columns)
    if len({row.sample_name for row in matched}) != len(columns):
        return SampleBinding(columns, None, "多個 Matrix 欄位對應到同一個樣本。")
    return SampleBinding(columns, matched)


def describe_steps(
    source: CurationSource,
    metadata: Sequence[SampleMetadata] | None,
    *,
    missing_samples: str,
    nucleoside_list: Path | None = None,
) -> list[dict[str, Any]]:
    """Each step with its parameters and, when it cannot run, the reason."""
    with source.matrix_path.open(encoding="utf-8-sig", newline="") as stream:
        header = validate_delimited_header(
            next(csv.reader(stream, delimiter="\t"), ()),
            path=source.matrix_path,
            required_columns=("Mz", "RT"),
        )
    inputs = _curation_inputs(
        source,
        bind_samples(header[2:], metadata, missing=missing_samples),
        STEPS,
        nucleoside_list,
    )
    return [
        {
            "key": step.key,
            "label": step.label,
            "description": step.description,
            "parameters": [asdict(parameter) for parameter in step.parameters],
            "unavailable_reason": step.unavailable_reason(inputs),
            **(
                {"list_label": inputs.nucleoside_list.label}
                if step is KNOWN_NUCLEOSIDES_STEP and inputs.nucleoside_list
                else {}
            ),
        }
        for step in STEPS
    ]


def _step_parameters(
    step: CurationStep, values: Mapping[str, object]
) -> dict[str, float]:
    parsed = {}
    for parameter in step.parameters:
        raw = values.get(parameter.key)
        try:
            number = float(raw)  # type: ignore[arg-type]
        except TypeError, ValueError:
            number = math.nan
        if not math.isfinite(number) or not (
            parameter.minimum <= number <= parameter.maximum
        ):
            raise ValueError(
                f"{step.label}：{parameter.label}必須介於 "
                f"{parameter.minimum:g} 與 {parameter.maximum:g}。"
            )
        parsed[parameter.key] = number
    return parsed


@dataclass(frozen=True)
class CurationEvaluation:
    """One set of decisions for preview and publication."""

    tables: Mapping[str, StepTable]
    summary: dict[str, Any]
    preview: dict[str, Any]

    def write_to(self, output_dir: Path) -> Path:
        """Publish these exact tables into a new directory, summary last."""
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, table in self.tables.items():
            write_tsv(output_dir / name, table.rows, table.columns)
        summary_path = output_dir / SUMMARY_NAME
        summary_path.write_text(
            json.dumps(self.summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return summary_path


def prepare_matrix_curation(
    *,
    source: CurationSource,
    sample_metadata: Sequence[SampleMetadata] | None,
    sample_source: str,
    steps: Mapping[str, Mapping[str, object]],
    istd_windows: Sequence[MzRtWindow] | None = None,
    istd_source: str = "",
    nucleoside_list: Path | None = None,
) -> CurationEvaluation:
    """Evaluate once without writing; preview and export consume these decisions.

    Remove supported derivative ions first, then filter the remaining features.
    Internal standards remain protected at every step. Sources are never modified.
    """
    enabled = [step for step in STEPS if step.key in steps]
    if not enabled:
        raise ValueError("請至少啟用一個整理步驟。")
    parameters = {step.key: _step_parameters(step, steps[step.key]) for step in enabled}

    header, rows, matrix_digest = read_tsv_with_header_snapshot(
        source.matrix_path, required_columns=("Mz", "RT"), encoding="utf-8-sig"
    )
    review_header, review_rows, _ = read_tsv_with_header_snapshot(
        source.review_path, encoding="utf-8-sig"
    )
    identities = matrix_review_rows(
        matrix_header=header,
        matrix_rows=rows,
        review_columns=review_header,
        review_rows=review_rows,
    )
    features = tuple(
        FeatureIdentity(
            feature_id=str(identity["feature_id"]),
            matrix_mz=float(str(identity["group_center_mz"])),
            matrix_rt=float(str(identity["group_center_rt"])),
        )
        for identity in identities
    )
    binding = bind_samples(
        header[2:], sample_metadata, missing="找不到樣本資料；請選擇樣本資料 TSV。"
    )
    needs_cells = any(step.needs_cell_evidence for step in enabled)
    inputs = _curation_inputs(
        source, binding, enabled, nucleoside_list, matrix_digest=matrix_digest
    )
    for step in enabled:
        reason = step.unavailable_reason(inputs)
        if reason:
            raise ValueError(f"{step.label}無法執行：{reason}")
    samples = binding.columns
    values = np.empty((len(rows), len(samples)), dtype=float)
    for index, row in enumerate(rows):
        for column_index, column in enumerate(samples):
            token = row[column]
            try:
                values[index, column_index] = float(token) if token.strip() else np.nan
            except ValueError as exc:
                raise ValueError(
                    f"matrix row {index + 1}, sample {column!r}: "
                    f"invalid numeric value {token!r}"
                ) from exc
    enabled_list = (
        inputs.nucleoside_list.path
        if KNOWN_NUCLEOSIDES_STEP in enabled and inputs.nucleoside_list
        else None
    )
    matrix = CurationMatrix(
        sample_binding=binding,
        values=values,
        features=features,
        nucleoside_windows=read_nucleoside_list(enabled_list) if enabled_list else (),
        cells=load_cell_evidence(source, features) if needs_cells else None,
    )
    windows = tuple(istd_windows or ())
    istd_labels = [
        match_labels(feature.matrix_mz, feature.matrix_rt, windows)
        for feature in features
    ]
    final_keep = [True] * len(features)
    results = {}
    stage_counts = {}
    position = {feature.feature_id: index for index, feature in enumerate(features)}
    folded_into: dict[int, list[FeatureIdentity]] = defaultdict(list)
    for step in enabled:
        indexes = [index for index, keep in enumerate(final_keep) if keep]
        remaining = replace(
            matrix,
            values=matrix.values[indexes],
            features=tuple(features[index] for index in indexes),
            members=tuple(tuple(folded_into[index]) for index in indexes),
            cells=tuple(matrix.cells[index] for index in indexes)
            if matrix.cells is not None
            else None,
        )
        result = step.evaluate(remaining, parameters[step.key])
        skipped = {
            column: "not_evaluated" if column.endswith("_decision") else ""
            for column in result.columns
        }
        step_rows: list[Mapping[str, object]] = [skipped] * len(features)
        keep_rows = [True] * len(features)
        for index, step_row, keep in zip(
            indexes, result.rows, result.keep, strict=True
        ):
            step_rows[index] = step_row
            keep_rows[index] = keep
            final_keep[index] = bool(istd_labels[index]) or keep
            representative = step_row.get("same_compound_representative_feature_id")
            if representative and not final_keep[index]:
                folded_into[position[str(representative)]].append(features[index])
        results[step.key] = StepResult(
            result.columns,
            tuple(step_rows),
            tuple(keep_rows),
            result.summary,
            result.tables,
        )
        stage_counts[step.key] = {
            "input_feature_count": len(indexes),
            "kept_feature_count": sum(final_keep),
            "removed_feature_count": len(indexes) - sum(final_keep),
        }

    tables: dict[str, StepTable] = {}
    for step in enabled:
        tables.update(results[step.key].tables)
    step_extras: dict[str, dict[str, Any]] = {}
    same_compound = results.get(SAME_COMPOUND_STEP.key)
    if same_compound is not None:
        group_review, empty_groups = group_outcome_review(
            same_compound,
            [feature.feature_id for feature in features],
            final_keep,
        )
        review = tables[SAME_COMPOUND_REVIEW_NAME]
        review_rows_all = (*review.rows, *group_review)
        tables[SAME_COMPOUND_REVIEW_NAME] = StepTable(review.columns, review_rows_all)
        removed_causes = Counter(
            removal_cause(str(row["same_compound_relation"]))
            for row, keep, label in zip(
                same_compound.rows, same_compound.keep, istd_labels, strict=True
            )
            if not keep and not label
        )
        step_extras[SAME_COMPOUND_STEP.key] = {
            "removed_by_cause": {
                key: removed_causes[key]
                for key, _ in REMOVAL_CAUSES
                if removed_causes[key]
            },
            "empty_group_count": empty_groups,
            "review_count": len(review_rows_all),
            "review_by_kind": dict(
                sorted(
                    Counter(str(row["review_kind"]) for row in review_rows_all).items()
                )
            ),
        }

    decision_columns = [*DECISION_KEY_COLUMNS]
    for step in enabled:
        decision_columns.extend(results[step.key].columns)
    decision_columns.extend(("decision", "decision_reason"))
    decision_rows = []
    kept_rows = []
    for index, (row, feature) in enumerate(zip(rows, features, strict=True)):
        decision: dict[str, object] = {
            "source_matrix_row_index": index + 1,
            "feature_id": feature.feature_id,
            "Mz": row["Mz"],
            "RT": row["RT"],
            "istd_label": istd_labels[index],
        }
        for step in enabled:
            decision.update(results[step.key].rows[index])
        dropped_by = [step.key for step in enabled if not results[step.key].keep[index]]
        decision["decision"] = "keep" if final_keep[index] else "drop"
        if istd_labels[index]:
            decision["decision_reason"] = "istd_protected"
        elif dropped_by:
            decision["decision_reason"] = "dropped_by:" + ",".join(dropped_by)
        else:
            decision["decision_reason"] = "kept_by_all_steps"
        decision_rows.append(decision)
        if final_keep[index]:
            kept_rows.append(row)

    export_tables = {
        CURATED_MATRIX_NAME: StepTable(tuple(header), tuple(kept_rows)),
        DECISIONS_NAME: StepTable(tuple(decision_columns), tuple(decision_rows)),
        **tables,
    }
    if binding.samples is not None:
        export_tables["sample_metadata.tsv"] = StepTable(
            ("matrix_column", *SAMPLE_METADATA_COLUMNS),
            tuple(
                {
                    "matrix_column": column,
                    "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
                    **asdict(row),
                    "excluded": "TRUE" if row.excluded else "FALSE",
                }
                for column, row in zip(samples, binding.samples, strict=True)
            ),
        )
    summary = {
        "steps": [
            {
                "key": step.key,
                "parameters": parameters[step.key],
                **results[step.key].summary,
                **step_extras.get(step.key, {}),
                **stage_counts[step.key],
            }
            for step in enabled
        ],
        "istd": {
            "source": istd_source,
            "standard_count": len(windows),
            "protected_feature_count": sum(bool(label) for label in istd_labels),
        },
        "inputs": {
            "source_dir": str(source.source_dir.resolve()),
            "matrix": str(source.matrix_path.resolve()),
            "review": str(source.review_path.resolve()),
            "explanation": (
                str(source.explanation_path.resolve())
                if needs_cells and source.explanation_path is not None
                else ""
            ),
            "sample_metadata": sample_source,
            **(
                {"nucleoside_list": str(enabled_list.resolve())} if enabled_list else {}
            ),
        },
        "feature_count": len(rows),
        "kept_feature_count": len(kept_rows),
        "sample_count": len(samples),
    }
    preview_steps = []
    protected = sum(bool(label) for label in istd_labels)
    for step in enabled:
        result = results[step.key]
        counts = stage_counts[step.key]
        item = {
            "key": step.key,
            "label": step.label,
            "input": counts["input_feature_count"],
            "kept": counts["kept_feature_count"],
            "removed": counts["removed_feature_count"],
            "protected": protected,
            "undetermined": sum(
                row.get("same_compound_relation_undetermined") == "TRUE"
                for row in result.rows
            ),
        }
        extras = step_extras.get(step.key, {})
        if "removed_by_cause" in extras:
            item["causes"] = [
                {"label": label, "count": extras["removed_by_cause"][key]}
                for key, label in REMOVAL_CAUSES
                if key in extras["removed_by_cause"]
            ]
        if step is FINITE_FRACTION_STEP:
            denominator = len(result.summary["eligible_matrix_columns"])
            threshold = parameters[step.key]["min_finite_fraction"]
            item["eligible_sample_count"] = denominator
            item["minimum_finite_count"] = sum(
                n / denominator < threshold for n in range(denominator + 1)
            )
        preview_steps.append(item)
    return CurationEvaluation(
        export_tables,
        summary,
        {
            "feature_count": len(rows),
            "kept": len(kept_rows),
            "removed": len(rows) - len(kept_rows),
            "protected": protected,
            "istd_count": len(windows),
            "steps": preview_steps,
        },
    )
