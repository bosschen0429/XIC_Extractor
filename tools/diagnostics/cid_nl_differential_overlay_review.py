"""Produce RAW-backed source/successor scientific diagnostic artifacts."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.diagnostics.family_ms1_overlay_evidence import _gaussian_smooth_values
from tools.diagnostics.family_ms1_overlay_rendering_styles import (
    PLOT_GAUSSIAN_SMOOTH_POINTS,
)
from xic_extractor.tabular_io import read_tsv_required, text_value, write_tsv
from xic_extractor.xic_models import XICTrace

DEFAULT_DIFFERENTIAL_REVIEW_TSV = Path(
    "output/validation/cid_nl_default_activation_gallery_review/"
    "cid_nl_discovery_identity_differential_review.tsv",
)
DEFAULT_DECISIONS_TSV = Path(
    "output/validation/cid_nl_default_activation_successor_authority_contract/"
    "successor_authority_decisions.tsv",
)
DEFAULT_OUTPUT_DIR = Path(
    "output/validation/cid_nl_default_activation_gallery_review/"
    "differential_overlays",
)
RAW_ROOT = Path("C:/Xcalibur/data/20260106_CSMU_NAA_Tissue_R")
DLL_DIR = Path("C:/Xcalibur/system/programs")
SCHEMA_VERSION = "cid_nl_differential_overlay_review"
PPM = 10.0
RT_HALF_WINDOW_MIN = 1.5

DIFFERENTIAL_COLUMNS = (
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "transition_key",
    "sample_count",
    "write_authorized_count",
    "no_write_detected_baseline_preserved_count",
    "no_write_omitted_count",
    "source_mz",
    "source_rt",
    "source_product_mz",
    "source_neutral_loss_tag",
    "source_identity_decision",
    "source_accepted_cell_count",
    "successor_mz",
    "successor_rt",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "successor_identity_decision",
    "successor_accepted_cell_count",
    "mz_delta",
    "rt_delta",
    "feature_inclusion_gate",
    "identity_authority_gate",
    "source_successor_relationship",
    "transition_type",
    "differential_overlay_readiness",
    "review_note",
)
DECISION_COLUMNS = (
    "old_peak_hypothesis_id",
    "sample_stem",
    "successor_peak_hypothesis_id",
    "successor_decision",
    "write_authority",
    "matrix_write_allowed",
    "matrix_effect",
    "human_explanation",
    "input_resolution_status",
    "candidate_new_peak_hypothesis_ids",
    "candidate_baseline_values",
    "accepted_quant_value",
)
QUEUE_COLUMNS = (
    "review_rank",
    "transition_key",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "sample_count",
    "write_authorized_count",
    "no_write_detected_baseline_preserved_count",
    "source_mz",
    "source_rt",
    "source_rt_min",
    "source_rt_max",
    "successor_mz",
    "successor_rt",
    "successor_rt_min",
    "successor_rt_max",
    "mz_delta",
    "rt_delta",
    "feature_inclusion_gate",
    "identity_authority_gate",
    "source_successor_relationship",
)
SUMMARY_COLUMNS = (
    *QUEUE_COLUMNS,
    "status",
    "visual_successor_ms1_state",
    "visual_source_successor_relationship_state",
    "png_path",
    "trace_data_json",
    "source_trace_max_median",
    "successor_trace_max_median",
    "successor_to_source_median_max_ratio",
    "source_nonzero_fraction",
    "successor_nonzero_fraction",
    "missing_raw_count",
    "failure_reason",
)


@dataclass(frozen=True)
class SampleDecision:
    sample_stem: str
    successor_decision: str
    matrix_effect: str
    accepted_quant_value: float | None
    human_explanation: str


@dataclass(frozen=True)
class TransitionReview:
    rank: int
    source_id: str
    successor_id: str
    transition_key: str
    sample_count: int
    write_authorized_count: int
    preserve_count: int
    source_mz: float
    source_rt: float
    source_product_mz: str
    source_identity_decision: str
    source_accepted_cell_count: str
    successor_mz: float
    successor_rt: float
    successor_product_mz: str
    successor_identity_decision: str
    successor_accepted_cell_count: str
    mz_delta: float
    rt_delta: float
    feature_inclusion_gate: str
    identity_authority_gate: str
    source_successor_relationship: str
    sample_decisions: tuple[SampleDecision, ...]


@dataclass(frozen=True)
class TracePair:
    sample_stem: str
    successor_decision: str
    source_trace: XICTrace
    successor_trace: XICTrace
    missing_raw: bool = False
    failure_reason: str = ""


def write_differential_evidence(
    *,
    differential_review_tsv: Path,
    decisions_tsv: Path,
    output_dir: Path,
    raw_dir: Path,
    dll_dir: Path,
    limit: int | None = None,
    rt_half_window_min: float = RT_HALF_WINDOW_MIN,
    ppm: float = PPM,
    require_pass: bool = False,
) -> dict[str, object]:
    transitions = build_transition_reviews(
        differential_review_tsv=differential_review_tsv,
        decisions_tsv=decisions_tsv,
        limit=limit,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_tsv = output_dir / "cid_nl_differential_overlay_review_queue.tsv"
    queue_rows = [_queue_row(row, rt_half_window_min) for row in transitions]
    write_tsv(queue_tsv, queue_rows, QUEUE_COLUMNS)
    trace_pairs_by_transition = extract_transition_trace_pairs(
        transitions,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        rt_half_window_min=rt_half_window_min,
        ppm=ppm,
    )
    summary_rows = []
    for transition in transitions:
        pairs = trace_pairs_by_transition[transition.transition_key]
        prefix = (
            f"{transition.rank:03d}_{transition.source_id.lower()}"
            f"_to_{transition.successor_id.lower()}"
        )
        png_path = output_dir / f"{prefix}.png"
        trace_json = output_dir / f"{prefix}_trace_data.json"
        render_transition_overlay(
            transition,
            pairs,
            png_path=png_path,
            rt_half_window_min=rt_half_window_min,
        )
        write_transition_trace_json(
            transition,
            pairs,
            trace_json,
            rt_half_window_min=rt_half_window_min,
            ppm=ppm,
        )
        summary_row = _summary_row(
            transition,
            pairs,
            png_path=png_path,
            trace_json=trace_json,
            rt_half_window_min=rt_half_window_min,
        )
        summary_rows.append(summary_row)
        if transition.rank % 10 == 0:
            print(
                "paired differential overlays rendered: "
                f"{transition.rank}/{len(transitions)}",
            )

    summary_tsv = output_dir / "cid_nl_differential_overlay_review_summary.tsv"
    write_tsv(summary_tsv, summary_rows, SUMMARY_COLUMNS, lineterminator="\n")
    failures = [row for row in summary_rows if row["status"] != "success"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only",
        "overall_status": "pass" if not failures else "failed",
        "transition_count": len(transitions),
        "rendered_transition_count": len(summary_rows) - len(failures),
        "failed_transition_count": len(failures),
        "queue_tsv": str(queue_tsv),
        "summary_tsv": str(summary_tsv),
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "review_frame": "feature_inclusion_then_identity_authority",
        "feature_inclusion_gate": "diagnostic_only_successor_ms1_feature_context",
        "identity_authority_gate": "not_granted_requires_expected_diff",
        "source_successor_not_mutually_exclusive": True,
        "authority_statement": (
            "Differential overlays are human-review evidence only; they do "
            "not grant ProductWriter or default matrix authority."
        ),
    }
    summary_json = output_dir / "cid_nl_differential_overlay_review_summary.json"
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if require_pass and failures:
        raise ValueError(f"differential overlay review has {len(failures)} failures")
    return payload


def build_transition_reviews(
    *,
    differential_review_tsv: Path,
    decisions_tsv: Path,
    limit: int | None = None,
) -> tuple[TransitionReview, ...]:
    differential_rows = read_tsv_required(differential_review_tsv, DIFFERENTIAL_COLUMNS)
    decisions = read_tsv_required(decisions_tsv, DECISION_COLUMNS)
    decisions_by_transition: dict[
        tuple[str, str],
        list[SampleDecision],
    ] = defaultdict(list)
    for row in decisions:
        successor = text_value(row.get("successor_peak_hypothesis_id"))
        if not successor:
            continue
        decisions_by_transition[(row["old_peak_hypothesis_id"], successor)].append(
            SampleDecision(
                sample_stem=row["sample_stem"],
                successor_decision=row["successor_decision"],
                matrix_effect=row["matrix_effect"],
                accepted_quant_value=_optional_float(row.get("accepted_quant_value")),
                human_explanation=row["human_explanation"],
            )
        )
    ready_rows = [
        row
        for row in differential_rows
        if row["differential_overlay_readiness"] == "ready_for_paired_overlay"
    ]
    ready_rows.sort(key=_transition_priority_key)
    if limit is not None:
        ready_rows = ready_rows[:limit]
    transitions: list[TransitionReview] = []
    for rank, row in enumerate(ready_rows, start=1):
        source = row["source_peak_hypothesis_id"]
        successor = row["successor_peak_hypothesis_id"]
        sample_decisions = tuple(
            sorted(
                decisions_by_transition[(source, successor)],
                key=lambda item: (
                    item.successor_decision != "write_authorized",
                    item.sample_stem,
                ),
            )
        )
        if not sample_decisions:
            raise ValueError(f"{source}->{successor}: no decision samples")
        transitions.append(
            TransitionReview(
                rank=rank,
                source_id=source,
                successor_id=successor,
                transition_key=row["transition_key"],
                sample_count=_required_int(row, "sample_count"),
                write_authorized_count=_required_int(row, "write_authorized_count"),
                preserve_count=_required_int(
                    row,
                    "no_write_detected_baseline_preserved_count",
                ),
                source_mz=_required_float(row, "source_mz"),
                source_rt=_required_float(row, "source_rt"),
                source_product_mz=row["source_product_mz"],
                source_identity_decision=row["source_identity_decision"],
                source_accepted_cell_count=row["source_accepted_cell_count"],
                successor_mz=_required_float(row, "successor_mz"),
                successor_rt=_required_float(row, "successor_rt"),
                successor_product_mz=row["successor_product_mz"],
                successor_identity_decision=row["successor_identity_decision"],
                successor_accepted_cell_count=row["successor_accepted_cell_count"],
                mz_delta=_required_float(row, "mz_delta"),
                rt_delta=_required_float(row, "rt_delta"),
                feature_inclusion_gate=row["feature_inclusion_gate"],
                identity_authority_gate=row["identity_authority_gate"],
                source_successor_relationship=row["source_successor_relationship"],
                sample_decisions=sample_decisions,
            )
        )
    return tuple(transitions)


def extract_transition_trace_pairs(
    transitions: Sequence[TransitionReview],
    *,
    raw_dir: Path,
    dll_dir: Path,
    rt_half_window_min: float,
    ppm: float,
) -> dict[str, tuple[TracePair, ...]]:
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.xic_models import XICRequest

    request_items_by_sample: dict[
        str,
        list[tuple[str, str, XICRequest]],
    ] = defaultdict(list)
    for transition in transitions:
        source_rt_min, source_rt_max = _rt_window(
            transition.source_rt,
            rt_half_window_min,
        )
        successor_rt_min, successor_rt_max = _rt_window(
            transition.successor_rt,
            rt_half_window_min,
        )
        for sample in transition.sample_decisions:
            request_items_by_sample[sample.sample_stem].append(
                (
                    transition.transition_key,
                    f"{sample.sample_stem}:source",
                    XICRequest(
                        mz=transition.source_mz,
                        rt_min=source_rt_min,
                        rt_max=source_rt_max,
                        ppm_tol=ppm,
                    ),
                )
            )
            request_items_by_sample[sample.sample_stem].append(
                (
                    transition.transition_key,
                    f"{sample.sample_stem}:successor",
                    XICRequest(
                        mz=transition.successor_mz,
                        rt_min=successor_rt_min,
                        rt_max=successor_rt_max,
                        ppm_tol=ppm,
                    ),
                )
            )

    traces: dict[tuple[str, str], XICTrace] = {}
    missing_raw: set[str] = set()
    total_samples = len(request_items_by_sample)
    for index, (sample, items) in enumerate(
        sorted(request_items_by_sample.items()),
        start=1,
    ):
        raw_path = raw_dir / f"{sample}.raw"
        if not raw_path.is_file():
            missing_raw.add(sample)
            continue
        with open_raw(raw_path, dll_dir) as raw:
            extracted = tuple(raw.extract_xic_many(tuple(item[2] for item in items)))
        for (transition_key, side_key, _request), trace in zip(items, extracted):
            traces[(transition_key, side_key)] = trace
        if index % 10 == 0:
            print(f"paired differential RAW extraction: sample {index}/{total_samples}")

    pairs_by_transition: dict[str, list[TracePair]] = defaultdict(list)
    for transition in transitions:
        for sample in transition.sample_decisions:
            source_key = (transition.transition_key, f"{sample.sample_stem}:source")
            successor_key = (
                transition.transition_key,
                f"{sample.sample_stem}:successor",
            )
            if sample.sample_stem in missing_raw:
                pairs_by_transition[transition.transition_key].append(
                    TracePair(
                        sample_stem=sample.sample_stem,
                        successor_decision=sample.successor_decision,
                        source_trace=XICTrace.empty(),
                        successor_trace=XICTrace.empty(),
                        missing_raw=True,
                        failure_reason="missing_raw",
                    )
                )
            else:
                pairs_by_transition[transition.transition_key].append(
                    TracePair(
                        sample_stem=sample.sample_stem,
                        successor_decision=sample.successor_decision,
                        source_trace=traces[source_key],
                        successor_trace=traces[successor_key],
                    )
                )
    return {key: tuple(value) for key, value in pairs_by_transition.items()}


def render_transition_overlay(
    transition: TransitionReview,
    pairs: Sequence[TracePair],
    *,
    png_path: Path,
    rt_half_window_min: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(13.5, 7.6), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=[3.0, 1.25])
    source_ax = fig.add_subplot(grid[0, 0])
    successor_ax = fig.add_subplot(grid[0, 1])
    scatter_ax = fig.add_subplot(grid[1, 0])
    note_ax = fig.add_subplot(grid[1, 1])
    y_max = max(
        1.0,
        *(_trace_max(pair.source_trace) for pair in pairs),
        *(_trace_max(pair.successor_trace) for pair in pairs),
    )
    source_window = _rt_window(transition.source_rt, rt_half_window_min)
    successor_window = _rt_window(transition.successor_rt, rt_half_window_min)
    _plot_trace_panel(
        source_ax,
        pairs,
        side="source",
        title=(
            f"Source hypothesis {transition.source_id} "
            f"m/z {transition.source_mz:.6g} RT {transition.source_rt:.4g}"
        ),
        center_rt=transition.source_rt,
        rt_window=source_window,
        y_max=y_max,
    )
    _plot_trace_panel(
        successor_ax,
        pairs,
        side="successor",
        title=(
            f"Successor hypothesis {transition.successor_id} "
            f"m/z {transition.successor_mz:.6g} RT {transition.successor_rt:.4g}"
        ),
        center_rt=transition.successor_rt,
        rt_window=successor_window,
        y_max=y_max,
    )
    _plot_intensity_scatter(scatter_ax, pairs)
    _plot_note_panel(note_ax, transition, pairs)
    fig.suptitle(
        (
            f"CID-NL feature inclusion / identity relationship review "
            f"{transition.transition_key} | "
            f"Candidate {transition.write_authorized_count} / "
            f"Existing {transition.preserve_count}"
        ),
        fontsize=13,
        fontweight="bold",
    )
    fig.savefig(png_path, dpi=150, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)


def write_transition_trace_json(
    transition: TransitionReview,
    pairs: Sequence[TracePair],
    path: Path,
    *,
    rt_half_window_min: float,
    ppm: float,
) -> None:
    source_window = _rt_window(transition.source_rt, rt_half_window_min)
    successor_window = _rt_window(transition.successor_rt, rt_half_window_min)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "transition_key": transition.transition_key,
        "source_peak_hypothesis_id": transition.source_id,
        "successor_peak_hypothesis_id": transition.successor_id,
        "ppm": ppm,
        "source": {
            "mz": transition.source_mz,
            "rt": transition.source_rt,
            "rt_min": source_window[0],
            "rt_max": source_window[1],
            "product_mz": transition.source_product_mz,
            "identity_decision": transition.source_identity_decision,
            "accepted_cell_count": transition.source_accepted_cell_count,
        },
        "successor": {
            "mz": transition.successor_mz,
            "rt": transition.successor_rt,
            "rt_min": successor_window[0],
            "rt_max": successor_window[1],
            "product_mz": transition.successor_product_mz,
            "identity_decision": transition.successor_identity_decision,
            "accepted_cell_count": transition.successor_accepted_cell_count,
        },
        "counts": {
            "sample_count": transition.sample_count,
            "write_authorized_count": transition.write_authorized_count,
            "no_write_detected_baseline_preserved_count": transition.preserve_count,
        },
        "review_gates": {
            "feature_inclusion_gate": transition.feature_inclusion_gate,
            "identity_authority_gate": transition.identity_authority_gate,
            "source_successor_relationship": transition.source_successor_relationship,
        },
        "traces": [
            {
                "sample_stem": pair.sample_stem,
                "successor_decision": pair.successor_decision,
                "missing_raw": pair.missing_raw,
                "failure_reason": pair.failure_reason,
                "source_trace_max_intensity": _raw_trace_max(pair.source_trace),
                "successor_trace_max_intensity": _raw_trace_max(pair.successor_trace),
                "source_gaussian15_trace_max_intensity": _trace_max(pair.source_trace),
                "successor_gaussian15_trace_max_intensity": _trace_max(
                    pair.successor_trace
                ),
                "source_rt": tuple(float(value) for value in pair.source_trace.rt),
                "source_intensity": tuple(
                    float(value) for value in pair.source_trace.intensity
                ),
                "successor_rt": tuple(
                    float(value) for value in pair.successor_trace.rt
                ),
                "successor_intensity": tuple(
                    float(value) for value in pair.successor_trace.intensity
                ),
            }
            for pair in pairs
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _plot_trace_panel(
    ax: Any,
    pairs: Sequence[TracePair],
    *,
    side: str,
    title: str,
    center_rt: float,
    rt_window: tuple[float, float],
    y_max: float,
) -> None:
    for pair in pairs:
        trace = pair.source_trace if side == "source" else pair.successor_trace
        if trace.rt.size == 0:
            continue
        color, alpha, width = _decision_style(pair.successor_decision)
        ax.plot(
            trace.rt,
            _gaussian15_intensity(trace),
            color=color,
            alpha=alpha,
            lw=width,
        )
    ax.axvline(center_rt, color="black", lw=1.1, ls="--", alpha=0.65)
    ax.set_xlim(rt_window[0], rt_window[1])
    ax.set_ylim(0, y_max * 1.05)
    ax.set_title(title)
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Gaussian15-smoothed intensity")
    ax.grid(alpha=0.16)


def _plot_intensity_scatter(ax: Any, pairs: Sequence[TracePair]) -> None:
    values = []
    for pair in pairs:
        source_max = _trace_max(pair.source_trace)
        successor_max = _trace_max(pair.successor_trace)
        values.append((source_max, successor_max))
        color, alpha, _width = _decision_style(pair.successor_decision)
        ax.scatter(
            source_max + 1.0,
            successor_max + 1.0,
            color=color,
            alpha=alpha,
            s=28,
        )
    max_value = max((max(left, right) for left, right in values), default=1.0) + 1.0
    ax.plot([1.0, max_value], [1.0, max_value], color="0.45", lw=1, ls="--")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("source-hypothesis Gaussian15 max + 1")
    ax.set_ylabel("successor-hypothesis\nGaussian15 max + 1")
    ax.set_title("same-sample feature/identity relationship context")
    ax.grid(alpha=0.16)


def _plot_note_panel(
    ax: Any,
    transition: TransitionReview,
    pairs: Sequence[TracePair],
) -> None:
    ax.axis("off")
    ratio = _successor_to_source_median_ratio(pairs)
    lines = [
        "Diagnostic-only feature-inclusion / identity review.",
        "This figure does not write matrix values.",
        "Gate A: successor MS1 support can justify feature inclusion.",
        "Gate B: replacement/merge/dedupe needs a separate expected diff.",
        "A source peak does not invalidate successor feature inclusion.",
        "Different source/successor m/z values may be expected.",
        "",
        f"Feature inclusion gate: {transition.feature_inclusion_gate}",
        f"Identity authority gate: {transition.identity_authority_gate}",
        f"Relationship: {transition.source_successor_relationship}",
        f"Source identity: {transition.source_identity_decision}",
        f"Source accepted cells: {transition.source_accepted_cell_count}",
        f"Successor identity: {transition.successor_identity_decision}",
        f"Successor accepted cells: {transition.successor_accepted_cell_count}",
        f"m/z delta: {transition.mz_delta:.6g}",
        f"RT delta: {transition.rt_delta:.6g} min",
        f"median Gaussian15 successor/source max ratio: {_format_float(ratio)}",
    ]
    ax.text(
        0.02,
        0.98,
        "\n".join(lines),
        va="top",
        ha="left",
        fontsize=9.2,
        family="monospace",
    )


def _decision_style(decision: str) -> tuple[str, float, float]:
    if decision == "write_authorized":
        return "#0072B2", 0.70, 1.0
    if decision == "no_write_detected_baseline_preserved":
        return "#D55E00", 0.62, 1.0
    return "0.55", 0.35, 0.8


def _queue_row(
    transition: TransitionReview,
    rt_half_window_min: float,
) -> dict[str, object]:
    source_min, source_max = _rt_window(transition.source_rt, rt_half_window_min)
    successor_min, successor_max = _rt_window(
        transition.successor_rt,
        rt_half_window_min,
    )
    return {
        "review_rank": transition.rank,
        "transition_key": transition.transition_key,
        "source_peak_hypothesis_id": transition.source_id,
        "successor_peak_hypothesis_id": transition.successor_id,
        "sample_count": transition.sample_count,
        "write_authorized_count": transition.write_authorized_count,
        "no_write_detected_baseline_preserved_count": transition.preserve_count,
        "source_mz": _format_float(transition.source_mz),
        "source_rt": _format_float(transition.source_rt),
        "source_rt_min": _format_float(source_min),
        "source_rt_max": _format_float(source_max),
        "successor_mz": _format_float(transition.successor_mz),
        "successor_rt": _format_float(transition.successor_rt),
        "successor_rt_min": _format_float(successor_min),
        "successor_rt_max": _format_float(successor_max),
        "mz_delta": _format_float(transition.mz_delta),
        "rt_delta": _format_float(transition.rt_delta),
        "feature_inclusion_gate": transition.feature_inclusion_gate,
        "identity_authority_gate": transition.identity_authority_gate,
        "source_successor_relationship": transition.source_successor_relationship,
    }


def _summary_row(
    transition: TransitionReview,
    pairs: Sequence[TracePair],
    *,
    png_path: Path,
    trace_json: Path,
    rt_half_window_min: float,
) -> dict[str, object]:
    missing = [pair for pair in pairs if pair.missing_raw]
    return {
        **_queue_row(transition, rt_half_window_min),
        "status": "success" if not missing else "failed",
        "visual_successor_ms1_state": _visual_successor_ms1_state(pairs),
        "visual_source_successor_relationship_state": (
            _visual_source_successor_relationship_state(pairs)
        ),
        "png_path": str(png_path),
        "trace_data_json": str(trace_json),
        "source_trace_max_median": _format_float(
            _median(_trace_max(pair.source_trace) for pair in pairs),
        ),
        "successor_trace_max_median": _format_float(
            _median(_trace_max(pair.successor_trace) for pair in pairs),
        ),
        "successor_to_source_median_max_ratio": _format_float(
            _successor_to_source_median_ratio(pairs),
        ),
        "source_nonzero_fraction": _format_float(
            _nonzero_fraction(_trace_max(pair.source_trace) for pair in pairs),
        ),
        "successor_nonzero_fraction": _format_float(
            _nonzero_fraction(_trace_max(pair.successor_trace) for pair in pairs),
        ),
        "missing_raw_count": len(missing),
        "failure_reason": ";".join(
            f"{pair.sample_stem}:{pair.failure_reason}" for pair in missing
        ),
    }


def _transition_priority_key(row: Mapping[str, str]) -> tuple[float, float, float, str]:
    return (
        -_required_int(row, "write_authorized_count"),
        -_required_int(row, "sample_count"),
        -abs(_required_float(row, "rt_delta")),
        row["transition_key"],
    )


def _required_int(row: Mapping[str, str], key: str) -> int:
    try:
        return int(float(row[key]))
    except (KeyError, ValueError) as exc:
        label = row.get("transition_key", "<row>")
        raise ValueError(f"{label}: invalid {key}") from exc


def _required_float(row: Mapping[str, str], key: str) -> float:
    value = _optional_float(row.get(key))
    if value is None:
        raise ValueError(f"{row.get('transition_key', '<row>')}: invalid {key}")
    return value


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(text_value(value))
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _rt_window(center: float, half_window: float) -> tuple[float, float]:
    return max(0.0, center - half_window), center + half_window


def _raw_trace_max(trace: XICTrace) -> float:
    if trace.intensity.size == 0:
        return 0.0
    return float(trace.intensity.max())


def _trace_max(trace: XICTrace) -> float:
    intensity = _gaussian15_intensity(trace)
    if intensity.size == 0:
        return 0.0
    return float(intensity.max())


def _gaussian15_intensity(trace: XICTrace) -> Any:
    import numpy as np

    if trace.intensity.size == 0:
        return trace.intensity
    return np.asarray(
        _gaussian_smooth_values(
            trace.intensity,
            points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        ),
        dtype=float,
    )


def _successor_to_source_median_ratio(pairs: Sequence[TracePair]) -> float | None:
    source = _median(_trace_max(pair.source_trace) for pair in pairs)
    successor = _median(_trace_max(pair.successor_trace) for pair in pairs)
    if source is None or source <= 0 or successor is None:
        return None
    return successor / source


def _visual_successor_ms1_state(pairs: Sequence[TracePair]) -> str:
    if any(pair.missing_raw for pair in pairs):
        return "not_assessable_missing_raw"
    fraction = _nonzero_fraction(_trace_max(pair.successor_trace) for pair in pairs)
    if fraction is None:
        return "not_assessable_no_trace"
    if fraction > 0:
        return "successor_ms1_trace_present"
    return "successor_ms1_trace_not_observed"


def _visual_source_successor_relationship_state(pairs: Sequence[TracePair]) -> str:
    if any(pair.missing_raw for pair in pairs):
        return "not_assessable_missing_raw"
    source_fraction = _nonzero_fraction(_trace_max(pair.source_trace) for pair in pairs)
    successor_fraction = _nonzero_fraction(
        _trace_max(pair.successor_trace) for pair in pairs
    )
    if source_fraction is None or successor_fraction is None:
        return "not_assessable_no_trace"
    source_present = source_fraction > 0
    successor_present = successor_fraction > 0
    if source_present and successor_present:
        return "coexisting_or_identity_unresolved"
    if successor_present:
        return "successor_only_feature_context"
    if source_present:
        return "source_only_context"
    return "no_visual_ms1_support"


def _median(values: Sequence[float] | Any) -> float | None:
    items = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not items:
        return None
    middle = len(items) // 2
    if len(items) % 2:
        return items[middle]
    return (items[middle - 1] + items[middle]) / 2.0


def _nonzero_fraction(values: Sequence[float] | Any) -> float | None:
    items = [float(value) for value in values if math.isfinite(float(value))]
    if not items:
        return None
    return sum(value > 0 for value in items) / len(items)


def _format_float(value: object) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return ""
    return f"{parsed:.6g}"
