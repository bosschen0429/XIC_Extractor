"""Check whether CID-NL alignment evidence is ready for default activation.

This checker is a no-write preflight. It reads an alignment matrix, its stable
row identity, the current ProductionAcceptanceManifest/expected-diff bundle,
and CID-NL row provenance. It does not write ProductWriter outputs, change the
default matrix, run RAW, or grant new matrix authority.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    EXPECTED_DIFF_COLUMNS,
    build_quant_matrix_version_rows,
)
from xic_extractor.tabular_io import (  # noqa: E402
    bool_value,
    file_sha256,
    numeric_equal,
    optional_float,
    read_tsv_required,
    read_tsv_with_header,
    text_value,
)

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ALIGNMENT_DIR = (
    ROOT / "output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3"
)
DEFAULT_INPUT_QUANT_MATRIX_TSV = DEFAULT_ALIGNMENT_DIR / "alignment_matrix.tsv"
DEFAULT_INPUT_MATRIX_IDENTITY_TSV = (
    DEFAULT_ALIGNMENT_DIR / "alignment_matrix_identity.tsv"
)
DEFAULT_ALIGNMENT_REVIEW_TSV = DEFAULT_ALIGNMENT_DIR / "alignment_review.tsv"
DEFAULT_BACKFILL_CELL_EVIDENCE_TSV = (
    DEFAULT_ALIGNMENT_DIR / "alignment_backfill_cell_evidence.tsv"
)
DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV = (
    ROOT
    / "docs/superpowers/validation/quant_matrix_real_bundle/inputs/"
    / "production_acceptance_manifest.tsv"
)
DEFAULT_EXPECTED_DIFF_TSV = (
    ROOT
    / "docs/superpowers/validation/quant_matrix_real_bundle/inputs/"
    / "expected_diff.tsv"
)
DEFAULT_SUMMARY_JSON = (
    ROOT
    / "docs/superpowers/validation/cid_nl_default_activation_preflight/"
    / "cid_nl_default_activation_preflight_summary.json"
)

DEFAULT_FOCUS_SAMPLE = "TumorBC2312_DNA"
DEFAULT_FOCUS_PRECURSOR_MZ = 300.1605
DEFAULT_FOCUS_PRODUCT_MZ = 184.113
DEFAULT_PRESERVE_PRECURSOR_MZ = 301.165
DEFAULT_PRESERVE_PRODUCT_MZ = 185.116
DEFAULT_FOCUS_SOURCE_CANDIDATE_ID = (
    "TumorBC2312_DNA#19561@mz300.160635_p184.113235"
)
DEFAULT_PRESERVE_SOURCE_CANDIDATE_ID = (
    "TumorBC2312_DNA#19561@mz301.164978_p185.115845"
)
DEFAULT_TAG = "DNA_dR"
DEFAULT_RT_MIN = 22.0
DEFAULT_RT_MAX = 25.0
DEFAULT_MZ_TOLERANCE_DA = 0.02
DEFAULT_EXPECTED_FOCUS_NONBLANK_COUNT = 85

ACCEPT_DECISIONS = {"accept_basic_backfill", "accept_strict_backfill"}
PRECURSOR_PRODUCT_PATTERN = re.compile(
    r"@mz(?P<precursor>[-+]?\d+(?:\.\d+)?)_p(?P<product>[-+]?\d+(?:\.\d+)?)",
)

REQUIRED_IDENTITY_COLUMNS = (
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "source_feature_family_ids",
    "accepted_cell_count",
    "accepted_sample_count",
    "evidence_status",
)
REQUIRED_REVIEW_COLUMNS = (
    "group_hypothesis_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "identity_confidence",
    "accepted_cell_count",
    "include_in_primary_matrix",
    "row_flags",
    "consolidation_state",
)
REQUIRED_EVIDENCE_COLUMNS = (
    "feature_family_id",
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "status",
    "production_cell_status",
    "write_matrix_value",
    "include_in_primary_matrix",
    "identity_decision",
    "row_flags",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "source_candidate_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "reason",
)


@dataclass(frozen=True)
class PairExpectation:
    label: str
    precursor_mz: float
    product_mz: float
    tag: str
    expected_source_candidate_id: str | None = None
    expected_nonblank_count: int | None = None
    min_nonblank_count: int = 1
    require_high_confidence: bool = False


def evaluate_cid_nl_default_activation_preflight(
    *,
    input_quant_matrix_tsv: Path,
    input_matrix_identity_tsv: Path,
    alignment_review_tsv: Path,
    backfill_cell_evidence_tsv: Path,
    production_acceptance_manifest_tsv: Path,
    expected_diff_tsv: Path,
    focus_sample: str = DEFAULT_FOCUS_SAMPLE,
    focus_precursor_mz: float = DEFAULT_FOCUS_PRECURSOR_MZ,
    focus_product_mz: float = DEFAULT_FOCUS_PRODUCT_MZ,
    preserve_precursor_mz: float = DEFAULT_PRESERVE_PRECURSOR_MZ,
    preserve_product_mz: float = DEFAULT_PRESERVE_PRODUCT_MZ,
    expected_focus_source_candidate_id: str | None = (
        DEFAULT_FOCUS_SOURCE_CANDIDATE_ID
    ),
    expected_preserve_source_candidate_id: str | None = (
        DEFAULT_PRESERVE_SOURCE_CANDIDATE_ID
    ),
    preserve_tag: str = DEFAULT_TAG,
    mz_tolerance_da: float = DEFAULT_MZ_TOLERANCE_DA,
    rt_min: float = DEFAULT_RT_MIN,
    rt_max: float = DEFAULT_RT_MAX,
    expected_focus_nonblank_count: int | None = (
        DEFAULT_EXPECTED_FOCUS_NONBLANK_COUNT
    ),
) -> dict[str, Any]:
    matrix_header, matrix_rows = read_tsv_with_header(
        input_quant_matrix_tsv,
        required_columns=("Mz", "RT"),
    )
    identity_rows = list(
        read_tsv_required(input_matrix_identity_tsv, REQUIRED_IDENTITY_COLUMNS),
    )
    review_rows = list(read_tsv_required(alignment_review_tsv, REQUIRED_REVIEW_COLUMNS))
    manifest_rows = list(
        read_tsv_required(
            production_acceptance_manifest_tsv,
            (
                "peak_hypothesis_id",
                "sample_stem",
                "acceptance_decision",
                "write_authority",
                "matrix_write_allowed",
                "shadow_only",
            ),
        ),
    )
    expected_diff_rows = list(
        read_tsv_required(expected_diff_tsv, EXPECTED_DIFF_COLUMNS),
    )

    accepted_rows = [
        row for row in manifest_rows if _is_authorized_acceptance_row(row)
    ]
    identity_by_peak = {
        text_value(row.get("peak_hypothesis_id")): row for row in identity_rows
    }
    accepted_missing_identity = [
        {
            "peak_hypothesis_id": row.get("peak_hypothesis_id", ""),
            "sample_stem": row.get("sample_stem", ""),
        }
        for row in accepted_rows
        if text_value(row.get("peak_hypothesis_id")) not in identity_by_peak
    ]

    replay = _replay_summary(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_rows=identity_rows,
        manifest_rows=manifest_rows,
        expected_diff_rows=expected_diff_rows,
        accepted_rows=accepted_rows,
        accepted_missing_identity=accepted_missing_identity,
    )

    expectations = (
        PairExpectation(
            "focus_300_184",
            focus_precursor_mz,
            focus_product_mz,
            preserve_tag,
            expected_source_candidate_id=expected_focus_source_candidate_id,
            expected_nonblank_count=expected_focus_nonblank_count,
            require_high_confidence=True,
        ),
        PairExpectation(
            "preserve_301_185",
            preserve_precursor_mz,
            preserve_product_mz,
            preserve_tag,
            expected_source_candidate_id=expected_preserve_source_candidate_id,
        ),
    )
    target_pairs, target_problems = _target_pair_summaries(
        expectations=expectations,
        matrix_rows=matrix_rows,
        identity_rows=identity_rows,
        review_rows=review_rows,
        backfill_cell_evidence_tsv=backfill_cell_evidence_tsv,
        focus_sample=focus_sample,
        mz_tolerance_da=mz_tolerance_da,
        rt_min=rt_min,
        rt_max=rt_max,
    )

    problems: list[str] = []
    if replay["status"] != "pass":
        problems.append(
            "default_activation_replay_blocked: "
            + text_value(replay.get("first_blocking_reason")),
        )
    problems.extend(target_problems)

    target_status = "pass" if not target_problems else "fail"
    if target_problems:
        overall_status = "fail"
    elif replay["status"] == "pass":
        overall_status = "pass"
    else:
        overall_status = "blocked"

    return {
        "schema_version": "cid_nl_default_activation_preflight",
        "overall_status": overall_status,
        "default_activation_readiness": overall_status,
        "target_alignment_evidence_status": target_status,
        "readiness_label": (
            "production_candidate"
            if overall_status == "blocked" and target_status == "pass"
            else overall_status
        ),
        "product_surface_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "authority_statement": (
            "No new ProductWriter/default matrix authority is granted. "
            "The current rule-selected Backfill membership must replay cleanly "
            "before "
            "any default activation can claim the CID-NL 300.1605 row."
        ),
        "focus_sample": focus_sample,
        "rt_window_min": rt_min,
        "rt_window_max": rt_max,
        "mz_tolerance_da": mz_tolerance_da,
        "artifacts": _artifact_summaries(
            input_quant_matrix_tsv=input_quant_matrix_tsv,
            input_matrix_identity_tsv=input_matrix_identity_tsv,
            alignment_review_tsv=alignment_review_tsv,
            backfill_cell_evidence_tsv=backfill_cell_evidence_tsv,
            production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
            expected_diff_tsv=expected_diff_tsv,
        ),
        "replay": replay,
        "target_pairs": target_pairs,
        "problems": problems,
    }


def write_summary(summary_json: Path, payload: Mapping[str, Any]) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _replay_summary(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, str]],
    identity_rows: Sequence[Mapping[str, str]],
    manifest_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
    accepted_rows: Sequence[Mapping[str, str]],
    accepted_missing_identity: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    try:
        build_quant_matrix_version_rows(
            matrix_header=matrix_header,
            input_quant_matrix_rows=matrix_rows,
            input_matrix_identity_rows=identity_rows,
            production_acceptance_rows=manifest_rows,
            expected_diff_rows=expected_diff_rows,
        )
    except ValueError as exc:
        blocking_reasons.append(str(exc))

    return {
        "status": "pass" if not blocking_reasons else "blocked",
        "accepted_authority_cell_count": len(accepted_rows),
        "expected_diff_row_count": len(expected_diff_rows),
        "accepted_peak_hypothesis_id_count": len(
            {text_value(row.get("peak_hypothesis_id")) for row in accepted_rows},
        ),
        "accepted_missing_identity_count": len(accepted_missing_identity),
        "first_missing_identity_rows": list(accepted_missing_identity[:10]),
        "first_blocking_reason": blocking_reasons[0] if blocking_reasons else "",
        "blocking_reasons": blocking_reasons,
    }


def _target_pair_summaries(
    *,
    expectations: Sequence[PairExpectation],
    matrix_rows: Sequence[Mapping[str, str]],
    identity_rows: Sequence[Mapping[str, str]],
    review_rows: Sequence[Mapping[str, str]],
    backfill_cell_evidence_tsv: Path,
    focus_sample: str,
    mz_tolerance_da: float,
    rt_min: float,
    rt_max: float,
) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    summaries: dict[str, Any] = {}
    expected_by_peak: dict[str, PairExpectation] = {}

    for expectation in expectations:
        identity_hits = _identity_hits(
            identity_rows,
            expectation=expectation,
            mz_tolerance_da=mz_tolerance_da,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        summary: dict[str, Any] = {
            "target_precursor_mz": expectation.precursor_mz,
            "target_product_mz": expectation.product_mz,
            "target_tag": expectation.tag,
            "identity_hit_count": len(identity_hits),
            "status": "pass",
        }
        if len(identity_hits) != 1:
            problems.append(
                f"{expectation.label}: expected exactly one matrix identity row, "
                f"observed {len(identity_hits)}",
            )
            summary["status"] = "fail"
            summaries[expectation.label] = summary
            continue

        identity = identity_hits[0]
        peak_hypothesis_id = text_value(identity.get("peak_hypothesis_id"))
        matrix_row = _matrix_row_for_identity(matrix_rows, identity)
        matrix_nonblank_count = _matrix_nonblank_count(matrix_row)
        focus_matrix_value = text_value(matrix_row.get(focus_sample))
        review_hits = _review_hits(
            review_rows,
            expectation=expectation,
            peak_hypothesis_id=peak_hypothesis_id,
            mz_tolerance_da=mz_tolerance_da,
            rt_min=rt_min,
            rt_max=rt_max,
        )

        pair_problems = _target_pair_problem_list(
            expectation=expectation,
            matrix_nonblank_count=matrix_nonblank_count,
            review_hits=review_hits,
        )
        problems.extend(f"{expectation.label}: {problem}" for problem in pair_problems)
        summary.update(
            {
                "peak_hypothesis_id": peak_hypothesis_id,
                "matrix_row_index": identity.get("matrix_row_index", ""),
                "matrix_mz": identity.get("Mz", ""),
                "matrix_rt": identity.get("RT", ""),
                "matrix_nonblank_count": matrix_nonblank_count,
                "focus_sample_matrix_value": focus_matrix_value,
                "identity": {
                    "row_identity_basis": identity.get("row_identity_basis", ""),
                    "source_feature_family_ids": identity.get(
                        "source_feature_family_ids",
                        "",
                    ),
                    "source_feature_family_count": identity.get(
                        "source_feature_family_count",
                        "",
                    ),
                    "accepted_cell_count": identity.get("accepted_cell_count", ""),
                    "accepted_sample_count": identity.get("accepted_sample_count", ""),
                    "evidence_status": identity.get("evidence_status", ""),
                    "projection_status": identity.get("projection_status", ""),
                },
                "review_hit_count": len(review_hits),
                "review": _review_summary(review_hits[0]) if review_hits else {},
                "status": "fail" if pair_problems else "pass",
            },
        )
        summaries[expectation.label] = summary
        expected_by_peak[peak_hypothesis_id] = expectation

    provenance = _target_provenance_summaries(
        backfill_cell_evidence_tsv,
        expected_by_peak=expected_by_peak,
        target_pair_summaries=summaries,
        focus_sample=focus_sample,
        mz_tolerance_da=mz_tolerance_da,
    )
    for label, provenance_summary in provenance.items():
        summaries[label]["provenance"] = provenance_summary
        provenance_problems = _provenance_problem_list(
            label=label,
            expectation=provenance_summary["expectation"],
            summary=summaries[label],
            provenance=provenance_summary,
            focus_sample=focus_sample,
            mz_tolerance_da=mz_tolerance_da,
        )
        if provenance_problems:
            summaries[label]["status"] = "fail"
            problems.extend(provenance_problems)
    return summaries, problems


def _target_pair_problem_list(
    *,
    expectation: PairExpectation,
    matrix_nonblank_count: int,
    review_hits: Sequence[Mapping[str, str]],
) -> list[str]:
    problems: list[str] = []
    if expectation.expected_nonblank_count is not None and (
        matrix_nonblank_count != expectation.expected_nonblank_count
    ):
        problems.append(
            "matrix nonblank count mismatch: "
            f"expected {expectation.expected_nonblank_count}, "
            f"observed {matrix_nonblank_count}",
        )
    if matrix_nonblank_count < expectation.min_nonblank_count:
        problems.append(
            "matrix row is not preserved: "
            f"observed {matrix_nonblank_count} nonblank cells",
        )
    if len(review_hits) != 1:
        problems.append(
            f"expected exactly one primary review row, observed {len(review_hits)}",
        )
        return problems
    review = review_hits[0]
    if text_value(review.get("neutral_loss_tag")) != expectation.tag:
        problems.append(
            "review row tag mismatch: "
            f"expected {expectation.tag}, "
            f"observed {review.get('neutral_loss_tag', '')}",
        )
    if text_value(review.get("include_in_primary_matrix")) != "TRUE":
        problems.append("review row is not included in primary matrix")
    if expectation.require_high_confidence and (
        text_value(review.get("identity_confidence")) != "high"
    ):
        problems.append(
            "review row identity confidence mismatch: "
            f"expected high, observed {review.get('identity_confidence', '')}",
        )
    return problems


def _provenance_problem_list(
    *,
    label: str,
    expectation: PairExpectation,
    summary: Mapping[str, Any],
    provenance: Mapping[str, Any],
    focus_sample: str,
    mz_tolerance_da: float,
) -> list[str]:
    problems: list[str] = []
    sample_row = provenance.get("focus_sample_row") or {}
    sample_row_count = provenance.get("focus_sample_row_count", 0)
    if sample_row_count != 1:
        return [
            f"{label}: expected exactly one provenance row for {focus_sample}, "
            f"observed {sample_row_count}",
        ]
    if not sample_row:
        return [f"{label}: missing provenance row for {focus_sample}"]
    peak_hypothesis_id = text_value(summary.get("peak_hypothesis_id"))
    for field in ("feature_family_id", "group_hypothesis_id", "public_family_id"):
        if text_value(sample_row.get(field)) != peak_hypothesis_id:
            problems.append(
                f"{label}: provenance {field} does not match target "
                f"{peak_hypothesis_id}",
            )
    if text_value(sample_row.get("neutral_loss_tag")) != expectation.tag:
        problems.append(
            f"{label}: provenance tag mismatch for {focus_sample}: "
            f"expected {expectation.tag}, "
            f"observed {sample_row.get('neutral_loss_tag', '')}",
        )
    if text_value(sample_row.get("status")) != "detected":
        problems.append(
            f"{label}: provenance status is not detected for {focus_sample}: "
            f"{sample_row.get('status', '')}",
        )
    if text_value(sample_row.get("production_cell_status")) != "detected":
        problems.append(
            f"{label}: production cell status is not detected for {focus_sample}: "
            f"{sample_row.get('production_cell_status', '')}",
        )
    if text_value(sample_row.get("write_matrix_value")) != "TRUE":
        problems.append(f"{label}: provenance does not write matrix value")
    if text_value(sample_row.get("include_in_primary_matrix")) != "TRUE":
        problems.append(f"{label}: provenance is not included in primary matrix")
    if not numeric_equal(
        sample_row.get("primary_matrix_area"),
        summary.get("focus_sample_matrix_value"),
    ):
        problems.append(
            f"{label}: primary_matrix_area does not match matrix value for "
            f"{focus_sample}",
        )
    source_candidate_id = text_value(sample_row.get("source_candidate_id"))
    if expectation.expected_source_candidate_id:
        if source_candidate_id != expectation.expected_source_candidate_id:
            problems.append(
                f"{label}: source_candidate_id does not match expected source",
            )
    elif not source_candidate_id.startswith(f"{focus_sample}#"):
        problems.append(
            f"{label}: source_candidate_id sample prefix does not match "
            f"{focus_sample}",
        )
    precursor_mz, product_mz = _source_candidate_precursor_product(
        source_candidate_id,
    )
    if precursor_mz is None or abs(precursor_mz - expectation.precursor_mz) > (
        mz_tolerance_da
    ):
        problems.append(
            f"{label}: source_candidate_id precursor does not match target",
        )
    if product_mz is None or abs(product_mz - expectation.product_mz) > (
        mz_tolerance_da
    ):
        problems.append(f"{label}: source_candidate_id product does not match target")
    return problems


def _target_provenance_summaries(
    path: Path,
    *,
    expected_by_peak: Mapping[str, PairExpectation],
    target_pair_summaries: Mapping[str, Mapping[str, Any]],
    focus_sample: str,
    mz_tolerance_da: float,
) -> dict[str, dict[str, Any]]:
    summaries = {
        expectation.label: {
            "expectation": expectation,
            "evidence_row_count": 0,
            "writable_primary_row_count": 0,
            "focus_sample_row_count": 0,
            "focus_sample_row": {},
        }
        for expectation in expected_by_peak.values()
    }
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = [
            column
            for column in REQUIRED_EVIDENCE_COLUMNS
            if column not in tuple(reader.fieldnames or ())
        ]
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
        for row in reader:
            peak_id = text_value(row.get("group_hypothesis_id"))
            expectation = expected_by_peak.get(peak_id)
            if expectation is None:
                continue
            summary = summaries[expectation.label]
            summary["evidence_row_count"] += 1
            if (
                text_value(row.get("write_matrix_value")) == "TRUE"
                and text_value(row.get("include_in_primary_matrix")) == "TRUE"
                and text_value(row.get("primary_matrix_area"))
            ):
                summary["writable_primary_row_count"] += 1
            if text_value(row.get("sample_stem")) == focus_sample:
                summary["focus_sample_row_count"] += 1
                if summary["focus_sample_row"]:
                    continue
                summary["focus_sample_row"] = {
                    "feature_family_id": row.get("feature_family_id", ""),
                    "group_hypothesis_id": row.get("group_hypothesis_id", ""),
                    "public_family_id": row.get("public_family_id", ""),
                    "sample_stem": row.get("sample_stem", ""),
                    "status": row.get("status", ""),
                    "production_cell_status": row.get(
                        "production_cell_status",
                        "",
                    ),
                    "write_matrix_value": row.get("write_matrix_value", ""),
                    "include_in_primary_matrix": row.get(
                        "include_in_primary_matrix",
                        "",
                    ),
                    "identity_decision": row.get("identity_decision", ""),
                    "row_flags": row.get("row_flags", ""),
                    "primary_matrix_area": row.get("primary_matrix_area", ""),
                    "primary_matrix_area_source": row.get(
                        "primary_matrix_area_source",
                        "",
                    ),
                    "source_candidate_id": row.get("source_candidate_id", ""),
                    "neutral_loss_tag": row.get("neutral_loss_tag", ""),
                    "family_center_mz": row.get("family_center_mz", ""),
                    "family_center_rt": row.get("family_center_rt", ""),
                    "reason": row.get("reason", ""),
                }
    result: dict[str, dict[str, Any]] = {}
    for label, summary in summaries.items():
        expectation = summary["expectation"]
        public_summary = {
            key: value for key, value in summary.items() if key != "expectation"
        }
        public_summary["target_precursor_mz"] = expectation.precursor_mz
        public_summary["target_product_mz"] = expectation.product_mz
        public_summary["target_tag"] = expectation.tag
        result[label] = {
            **public_summary,
            "expectation": expectation,
        }
    return result


def _identity_hits(
    rows: Sequence[Mapping[str, str]],
    *,
    expectation: PairExpectation,
    mz_tolerance_da: float,
    rt_min: float,
    rt_max: float,
) -> list[Mapping[str, str]]:
    hits = []
    for row in rows:
        mz = optional_float(row.get("Mz"))
        rt = optional_float(row.get("RT"))
        if mz is None or rt is None:
            continue
        if abs(mz - expectation.precursor_mz) <= mz_tolerance_da and (
            rt_min <= rt <= rt_max
        ):
            hits.append(row)
    return hits


def _review_hits(
    rows: Sequence[Mapping[str, str]],
    *,
    expectation: PairExpectation,
    peak_hypothesis_id: str,
    mz_tolerance_da: float,
    rt_min: float,
    rt_max: float,
) -> list[Mapping[str, str]]:
    hits = []
    for row in rows:
        if text_value(row.get("group_hypothesis_id")) != peak_hypothesis_id:
            continue
        center_mz = optional_float(row.get("family_center_mz"))
        center_rt = optional_float(row.get("family_center_rt"))
        product_mz = optional_float(row.get("family_product_mz"))
        if center_mz is None or center_rt is None or product_mz is None:
            continue
        if (
            abs(center_mz - expectation.precursor_mz) <= mz_tolerance_da
            and abs(product_mz - expectation.product_mz) <= mz_tolerance_da
            and rt_min <= center_rt <= rt_max
            and text_value(row.get("include_in_primary_matrix")) == "TRUE"
        ):
            hits.append(row)
    return hits


def _matrix_row_for_identity(
    rows: Sequence[Mapping[str, str]],
    identity: Mapping[str, str],
) -> Mapping[str, str]:
    row_index = int(text_value(identity.get("matrix_row_index")))
    if row_index < 1 or row_index > len(rows):
        raise ValueError(f"invalid matrix_row_index: {row_index}")
    return rows[row_index - 1]


def _matrix_nonblank_count(row: Mapping[str, str]) -> int:
    return sum(1 for key, value in row.items() if key not in {"Mz", "RT"} and value)


def _review_summary(row: Mapping[str, str]) -> dict[str, str]:
    return {
        "group_hypothesis_id": row.get("group_hypothesis_id", ""),
        "neutral_loss_tag": row.get("neutral_loss_tag", ""),
        "family_center_mz": row.get("family_center_mz", ""),
        "family_center_rt": row.get("family_center_rt", ""),
        "family_product_mz": row.get("family_product_mz", ""),
        "identity_confidence": row.get("identity_confidence", ""),
        "accepted_cell_count": row.get("accepted_cell_count", ""),
        "row_flags": row.get("row_flags", ""),
        "consolidation_state": row.get("consolidation_state", ""),
        "include_in_primary_matrix": row.get("include_in_primary_matrix", ""),
    }


def _source_candidate_precursor_product(
    value: object,
) -> tuple[float | None, float | None]:
    match = PRECURSOR_PRODUCT_PATTERN.search(text_value(value))
    if match is None:
        return None, None
    return (
        optional_float(match.group("precursor")),
        optional_float(match.group("product")),
    )


def _is_authorized_acceptance_row(row: Mapping[str, str]) -> bool:
    return (
        text_value(row.get("acceptance_decision")) in ACCEPT_DECISIONS
        and bool_value(row.get("write_authority")) is True
        and bool_value(row.get("matrix_write_allowed")) is True
        and bool_value(row.get("shadow_only")) is False
    )


def _artifact_summaries(**paths: Path) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for label, path in paths.items():
        summaries[label] = {
            "path": _relative_or_absolute(path),
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
    return summaries


def _relative_or_absolute(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _json_ready(payload: Any) -> Any:
    if isinstance(payload, PairExpectation):
        return {
            "label": payload.label,
            "precursor_mz": payload.precursor_mz,
            "product_mz": payload.product_mz,
            "tag": payload.tag,
            "expected_source_candidate_id": payload.expected_source_candidate_id,
            "expected_nonblank_count": payload.expected_nonblank_count,
            "min_nonblank_count": payload.min_nonblank_count,
            "require_high_confidence": payload.require_high_confidence,
        }
    if isinstance(payload, Mapping):
        return {str(key): _json_ready(value) for key, value in payload.items()}
    if isinstance(payload, list | tuple):
        return [_json_ready(value) for value in payload]
    return payload


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-quant-matrix-tsv",
        type=Path,
        default=DEFAULT_INPUT_QUANT_MATRIX_TSV,
    )
    parser.add_argument(
        "--input-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_INPUT_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument(
        "--alignment-review-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_REVIEW_TSV,
    )
    parser.add_argument(
        "--backfill-cell-evidence-tsv",
        type=Path,
        default=DEFAULT_BACKFILL_CELL_EVIDENCE_TSV,
    )
    parser.add_argument(
        "--production-acceptance-manifest-tsv",
        type=Path,
        default=DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV,
    )
    parser.add_argument(
        "--expected-diff-tsv",
        type=Path,
        default=DEFAULT_EXPECTED_DIFF_TSV,
    )
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--focus-sample", default=DEFAULT_FOCUS_SAMPLE)
    parser.add_argument(
        "--focus-precursor-mz",
        type=float,
        default=DEFAULT_FOCUS_PRECURSOR_MZ,
    )
    parser.add_argument(
        "--focus-product-mz",
        type=float,
        default=DEFAULT_FOCUS_PRODUCT_MZ,
    )
    parser.add_argument(
        "--preserve-precursor-mz",
        type=float,
        default=DEFAULT_PRESERVE_PRECURSOR_MZ,
    )
    parser.add_argument(
        "--preserve-product-mz",
        type=float,
        default=DEFAULT_PRESERVE_PRODUCT_MZ,
    )
    parser.add_argument(
        "--expected-focus-source-candidate-id",
        default=DEFAULT_FOCUS_SOURCE_CANDIDATE_ID,
    )
    parser.add_argument(
        "--expected-preserve-source-candidate-id",
        default=DEFAULT_PRESERVE_SOURCE_CANDIDATE_ID,
    )
    parser.add_argument("--preserve-tag", default=DEFAULT_TAG)
    parser.add_argument("--rt-min", type=float, default=DEFAULT_RT_MIN)
    parser.add_argument("--rt-max", type=float, default=DEFAULT_RT_MAX)
    parser.add_argument(
        "--mz-tolerance-da",
        type=float,
        default=DEFAULT_MZ_TOLERANCE_DA,
    )
    parser.add_argument(
        "--expected-focus-nonblank-count",
        type=int,
        default=DEFAULT_EXPECTED_FOCUS_NONBLANK_COUNT,
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Return non-zero if the preflight is blocked or failed.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = evaluate_cid_nl_default_activation_preflight(
        input_quant_matrix_tsv=args.input_quant_matrix_tsv,
        input_matrix_identity_tsv=args.input_matrix_identity_tsv,
        alignment_review_tsv=args.alignment_review_tsv,
        backfill_cell_evidence_tsv=args.backfill_cell_evidence_tsv,
        production_acceptance_manifest_tsv=args.production_acceptance_manifest_tsv,
        expected_diff_tsv=args.expected_diff_tsv,
        focus_sample=args.focus_sample,
        focus_precursor_mz=args.focus_precursor_mz,
        focus_product_mz=args.focus_product_mz,
        preserve_precursor_mz=args.preserve_precursor_mz,
        preserve_product_mz=args.preserve_product_mz,
        expected_focus_source_candidate_id=args.expected_focus_source_candidate_id,
        expected_preserve_source_candidate_id=(
            args.expected_preserve_source_candidate_id
        ),
        preserve_tag=args.preserve_tag,
        mz_tolerance_da=args.mz_tolerance_da,
        rt_min=args.rt_min,
        rt_max=args.rt_max,
        expected_focus_nonblank_count=args.expected_focus_nonblank_count,
    )
    write_summary(args.summary_json, _json_ready(payload))
    print(f"cid_nl_default_activation_preflight_summary: {args.summary_json}")
    print(f"cid_nl_default_activation_preflight_status: {payload['overall_status']}")
    if args.require_pass and payload["overall_status"] != "pass":
        for problem in payload["problems"]:
            print(f"cid_nl_default_activation_preflight_problem: {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
