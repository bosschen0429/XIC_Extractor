from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from xic_extractor import extractor
from xic_extractor.configuration.loader import load_config
from xic_extractor.extractor import RunOutput
from xic_extractor.tabular_io import write_delimited_rows

_DEFAULT_FOCUS_TARGETS = ("d3-N6-medA", "d3-5-hmdC", "8-oxo-Guo", "8-oxodG")


@dataclass(frozen=True)
class ResolverRow:
    sample_name: str
    target: str
    role: str
    detected: bool
    rt: float | None
    area: float | None
    confidence: str


@dataclass(frozen=True)
class ResolverDiffRow:
    sample_name: str
    target: str
    role: str
    issue: str
    legacy_detected: bool
    local_detected: bool
    legacy_rt: float | None
    local_rt: float | None
    rt_delta: float | None
    legacy_area: float | None
    local_area: float | None
    area_ratio_delta: float | None
    legacy_confidence: str
    local_confidence: str


@dataclass(frozen=True)
class ResolverDiffSummary:
    detected_to_nd: int = 0
    nd_to_detected: int = 0
    rt_changed: int = 0
    area_changed: int = 0
    istd_detected_losses: int = 0
    confidence_changed: int = 0


@dataclass(frozen=True)
class ResolverComparisonReport:
    summary: ResolverDiffSummary
    rows: list[ResolverDiffRow]
    focus_rows: list[ResolverDiffRow]


def compare_rows(
    legacy_output: RunOutput,
    local_output: RunOutput,
    *,
    focus_targets: set[str],
    rt_delta_threshold: float,
    area_ratio_threshold: float,
) -> ResolverComparisonReport:
    legacy_rows = collect_rows(legacy_output)
    local_rows = collect_rows(local_output)

    summary = ResolverDiffSummary()
    rows: list[ResolverDiffRow] = []
    focus_rows: list[ResolverDiffRow] = []

    for key in sorted(legacy_rows.keys() | local_rows.keys()):
        legacy = legacy_rows.get(key)
        local = local_rows.get(key)
        if legacy is None or local is None:
            continue

        detected_to_nd = legacy.detected and not local.detected
        nd_to_detected = (not legacy.detected) and local.detected
        rt_delta = _rt_delta(legacy, local)
        area_ratio_delta = _area_ratio_delta(legacy, local)
        rt_changed = rt_delta is not None and rt_delta > rt_delta_threshold
        area_changed = (
            area_ratio_delta is not None and area_ratio_delta > area_ratio_threshold
        )
        confidence_changed = legacy.confidence != local.confidence
        istd_detected_loss = legacy.role == "ISTD" and detected_to_nd

        summary = ResolverDiffSummary(
            detected_to_nd=summary.detected_to_nd + int(detected_to_nd),
            nd_to_detected=summary.nd_to_detected + int(nd_to_detected),
            rt_changed=summary.rt_changed + int(rt_changed),
            area_changed=summary.area_changed + int(area_changed),
            istd_detected_losses=summary.istd_detected_losses + int(istd_detected_loss),
            confidence_changed=summary.confidence_changed + int(confidence_changed),
        )

        issue = _primary_issue(
            detected_to_nd=detected_to_nd,
            nd_to_detected=nd_to_detected,
            rt_changed=rt_changed,
            area_changed=area_changed,
            confidence_changed=confidence_changed,
        )
        if issue is None:
            continue

        diff = ResolverDiffRow(
            sample_name=legacy.sample_name,
            target=legacy.target,
            role=legacy.role,
            issue=issue,
            legacy_detected=legacy.detected,
            local_detected=local.detected,
            legacy_rt=legacy.rt,
            local_rt=local.rt,
            rt_delta=rt_delta,
            legacy_area=legacy.area,
            local_area=local.area,
            area_ratio_delta=area_ratio_delta,
            legacy_confidence=legacy.confidence,
            local_confidence=local.confidence,
        )
        rows.append(diff)
        if diff.target in focus_targets:
            focus_rows.append(diff)

    return ResolverComparisonReport(
        summary=summary,
        rows=rows,
        focus_rows=focus_rows,
    )


def collect_rows(output: RunOutput) -> dict[tuple[str, str, str], ResolverRow]:
    rows: dict[tuple[str, str, str], ResolverRow] = {}
    for file_result in output.file_results:
        for result in file_result.extraction_results:
            peak = result.peak
            row = ResolverRow(
                sample_name=file_result.sample_name,
                target=result.target_label,
                role=result.role,
                detected=peak is not None,
                rt=peak.rt if peak is not None else None,
                area=peak.area if peak is not None else None,
                confidence=result.confidence,
            )
            rows[(row.sample_name, row.target, row.role)] = row
    return rows


def write_report_csv(output_path: Path, report: ResolverComparisonReport) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "SampleName",
        "Target",
        "Role",
        "Issue",
        "LegacyDetected",
        "LocalDetected",
        "LegacyRT",
        "LocalRT",
        "RTDelta",
        "LegacyArea",
        "LocalArea",
        "AreaRatioDelta",
        "LegacyConfidence",
        "LocalConfidence",
    ]
    rows = [
        {
            "SampleName": row.sample_name,
            "Target": row.target,
            "Role": row.role,
            "Issue": row.issue,
            "LegacyDetected": str(row.legacy_detected),
            "LocalDetected": str(row.local_detected),
            "LegacyRT": _format_optional(row.legacy_rt),
            "LocalRT": _format_optional(row.local_rt),
            "RTDelta": _format_optional(row.rt_delta),
            "LegacyArea": _format_optional(row.legacy_area),
            "LocalArea": _format_optional(row.local_area),
            "AreaRatioDelta": _format_optional(row.area_ratio_delta),
            "LegacyConfidence": row.legacy_confidence,
            "LocalConfidence": row.local_confidence,
        }
        for row in report.rows
    ]
    write_delimited_rows(
        output_path,
        rows,
        fieldnames,
        delimiter=",",
        encoding="utf-8-sig",
    )
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    base_dir = args.base_dir.resolve()
    config, targets = load_config(base_dir / "config")
    legacy_config = replace(config, resolver_mode="legacy_savgol")
    local_config = replace(config, resolver_mode="local_minimum")

    legacy_output = extractor.acquire(legacy_config, targets)
    local_output = extractor.acquire(local_config, targets)

    report = compare_rows(
        legacy_output,
        local_output,
        focus_targets=set(args.focus_target),
        rt_delta_threshold=args.rt_delta_threshold,
        area_ratio_threshold=args.area_ratio_threshold,
    )
    output_path = write_report_csv(args.output.resolve(), report)
    _print_report(output_path, report)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare legacy_savgol and local_minimum resolver outputs."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path.cwd(),
        help="Project/base directory containing config/ and output/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/resolver_compare.csv"),
        help="CSV report path.",
    )
    parser.add_argument(
        "--focus-target",
        action="append",
        default=list(_DEFAULT_FOCUS_TARGETS),
        help="Target label to highlight. Can be passed multiple times.",
    )
    parser.add_argument(
        "--rt-delta-threshold",
        type=float,
        default=0.05,
        help="Minimum absolute RT drift (min) to count as changed.",
    )
    parser.add_argument(
        "--area-ratio-threshold",
        type=float,
        default=0.20,
        help="Minimum fractional area change to count as changed.",
    )
    return parser.parse_args(argv)


def _primary_issue(
    *,
    detected_to_nd: bool,
    nd_to_detected: bool,
    rt_changed: bool,
    area_changed: bool,
    confidence_changed: bool,
) -> str | None:
    if detected_to_nd:
        return "FLIP_DETECTED_TO_ND"
    if nd_to_detected:
        return "FLIP_ND_TO_DETECTED"
    if rt_changed and area_changed:
        return "RT_AND_AREA_CHANGED"
    if rt_changed:
        return "RT_CHANGED"
    if area_changed:
        return "AREA_CHANGED"
    if confidence_changed:
        return "CONFIDENCE_CHANGED"
    return None


def _rt_delta(legacy: ResolverRow, local: ResolverRow) -> float | None:
    if legacy.rt is None or local.rt is None:
        return None
    return abs(local.rt - legacy.rt)


def _area_ratio_delta(legacy: ResolverRow, local: ResolverRow) -> float | None:
    if (
        legacy.area is None
        or local.area is None
        or legacy.area <= 0
    ):
        return None
    return abs(local.area - legacy.area) / legacy.area


def _format_optional(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"


def _print_report(output_path: Path, report: ResolverComparisonReport) -> None:
    print("legacy_savgol vs local_minimum")
    print(f"CSV report: {output_path}")
    print(f"Detected->ND: {report.summary.detected_to_nd}")
    print(f"ND->Detected: {report.summary.nd_to_detected}")
    print(f"RT changed: {report.summary.rt_changed}")
    print(f"Area changed: {report.summary.area_changed}")
    print(f"ISTD detected losses: {report.summary.istd_detected_losses}")
    print(f"Confidence changed: {report.summary.confidence_changed}")
    if report.focus_rows:
        print("Focus targets:")
        for row in report.focus_rows:
            print(
                f"  {row.sample_name} {row.target} {row.role} {row.issue}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
