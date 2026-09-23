"""Build a manual calibration pack for shift-aware same-pattern evidence."""

from __future__ import annotations

import argparse
import html
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.diagnostic_io import (
    optional_float,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "shift_aware_backfill_calibration_pack"
DEFAULT_STANDARD_PEAK_MIN_SHAPE_R = 0.95

PACK_COLUMNS = (
    "schema_version",
    "review_rank",
    "feature_family_id",
    "machine_shift_aware_call",
    "manual_same_peak_call",
    "manual_standard_peak_call",
    "manual_backfill_authority_call",
    "manual_notes",
    "nonref_source_families",
    "nonref_group_count",
    "min_shape_r_after_best_shift",
    "max_shape_r_after_best_shift",
    "max_abs_shift_sec",
    "product_behavior_state",
    "evidence_authority_state",
    "reconciliation_class",
    "detected_cell_count",
    "rescued_cell_count",
    "top_support_component",
    "top_blocker",
    "missing_evidence",
    "family_verdict",
    "shape_supported_fraction",
    "absolute_own_max_shape_supported_fraction",
    "absolute_trace_apex_cluster_fraction",
    "family_context_png_path",
    "shift_best_alignment_png_path",
    "shift_best_summary_tsv_path",
    "reconciliation_gallery_path",
)

_SHIFT_SUMMARY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "nonref_source_families",
    "nonref_group_count",
    "min_shape_r_after_best_shift",
    "max_shape_r_after_best_shift",
    "max_abs_shift_sec",
)
_SHIFT_SOURCE_FAMILY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "source_family",
    "is_reference",
    "shift_to_reference_sec",
    "shape_similarity_to_reference_after_group_shift",
)
_RECONCILIATION_GROUP_REQUIRED_COLUMNS = (
    "feature_family_id",
    "product_behavior_state",
    "evidence_authority_state",
    "reconciliation_class",
    "detected_cell_count",
    "rescued_cell_count",
    "top_support_component",
    "top_blocker",
    "missing_evidence",
)
_OVERLAY_SUMMARY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_verdict",
    "png_path",
    "shape_supported_fraction",
    "absolute_own_max_shape_supported_fraction",
    "absolute_trace_apex_cluster_fraction",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        shift_rows = None
        if args.shift_aware_summary_dir is not None:
            shift_rows = collect_shift_aware_family_summary_rows(
                sorted(
                    args.shift_aware_summary_dir.glob(args.shift_aware_summary_pattern),
                ),
            )
        if shift_rows is None:
            rows = build_calibration_rows(
                shift_aware_summary_tsv=args.shift_aware_summary_tsv,
                reconciliation_groups_tsv=args.reconciliation_groups_tsv,
                overlay_batch_summary_tsv=args.overlay_batch_summary_tsv,
                shift_aware_output_dir=args.shift_aware_output_dir,
                reconciliation_gallery_html=args.reconciliation_gallery_html,
                min_shape_r=args.min_shape_r,
                include_all=args.include_all,
            )
        else:
            rows = build_calibration_rows_from_shift_rows(
                shift_rows=shift_rows,
                reconciliation_groups_tsv=args.reconciliation_groups_tsv,
                overlay_batch_summary_tsv=args.overlay_batch_summary_tsv,
                shift_aware_output_dir=args.shift_aware_output_dir,
                reconciliation_gallery_html=args.reconciliation_gallery_html,
                min_shape_r=args.min_shape_r,
                include_all=args.include_all,
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = None
        if shift_rows is not None:
            summary_path = args.output_dir / "shift_aware_family_best_shift_summary.tsv"
            write_tsv(summary_path, shift_rows, _SHIFT_SUMMARY_REQUIRED_COLUMNS)
        tsv_path = args.output_dir / "shift_aware_backfill_calibration_pack.tsv"
        html_path = args.output_dir / "shift_aware_backfill_calibration_pack.html"
        write_tsv(tsv_path, rows, PACK_COLUMNS)
        html_path.write_text(_render_html(rows, html_path=html_path), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"shift-aware calibration pack TSV: {tsv_path}")
    print(f"shift-aware calibration pack HTML: {html_path}")
    if summary_path is not None:
        print(f"shift-aware family summary TSV: {summary_path}")
    return 0


def build_calibration_rows(
    *,
    shift_aware_summary_tsv: Path,
    reconciliation_groups_tsv: Path,
    overlay_batch_summary_tsv: Path,
    shift_aware_output_dir: Path,
    reconciliation_gallery_html: Path | None = None,
    min_shape_r: float = DEFAULT_STANDARD_PEAK_MIN_SHAPE_R,
    include_all: bool = False,
) -> list[dict[str, str]]:
    shift_rows = read_tsv_required(
        shift_aware_summary_tsv,
        _SHIFT_SUMMARY_REQUIRED_COLUMNS,
    )
    return build_calibration_rows_from_shift_rows(
        shift_rows=shift_rows,
        reconciliation_groups_tsv=reconciliation_groups_tsv,
        overlay_batch_summary_tsv=overlay_batch_summary_tsv,
        shift_aware_output_dir=shift_aware_output_dir,
        reconciliation_gallery_html=reconciliation_gallery_html,
        min_shape_r=min_shape_r,
        include_all=include_all,
    )


def build_calibration_rows_from_shift_rows(
    *,
    shift_rows: Sequence[Mapping[str, str]],
    reconciliation_groups_tsv: Path,
    overlay_batch_summary_tsv: Path,
    shift_aware_output_dir: Path,
    reconciliation_gallery_html: Path | None = None,
    min_shape_r: float = DEFAULT_STANDARD_PEAK_MIN_SHAPE_R,
    include_all: bool = False,
) -> list[dict[str, str]]:
    group_rows = read_tsv_required(
        reconciliation_groups_tsv,
        _RECONCILIATION_GROUP_REQUIRED_COLUMNS,
    )
    overlay_rows = read_tsv_required(
        overlay_batch_summary_tsv,
        _OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )

    groups_by_family = _first_by_family(group_rows)
    overlays_by_family = _first_by_family(overlay_rows)
    selected: list[dict[str, str]] = []
    for row in shift_rows:
        family = text_value(row.get("feature_family_id"))
        group_row = groups_by_family.get(family, {})
        overlay_row = overlays_by_family.get(family, {})
        shift_supported = _shift_aware_min_supported(row, min_shape_r=min_shape_r)
        overlay_supported = _overlay_reference_supported(
            overlay_row,
            min_shape_r=min_shape_r,
        )
        if not include_all and not (shift_supported or overlay_supported):
            continue
        selected.append(
            _calibration_row(
                shift_row=row,
                group_row=group_row,
                overlay_row=overlay_row,
                review_rank=len(selected) + 1,
                min_shape_r=min_shape_r,
                shift_aware_output_dir=shift_aware_output_dir,
                reconciliation_gallery_html=reconciliation_gallery_html,
            ),
        )
    selected.sort(
        key=lambda row: (
            -float(row["min_shape_r_after_best_shift"]),
            row["feature_family_id"],
        ),
    )
    for rank, row in enumerate(selected, start=1):
        row["review_rank"] = str(rank)
    return selected


def collect_shift_aware_family_summary_rows(
    source_family_summary_paths: Sequence[Path],
) -> list[dict[str, str]]:
    by_family: dict[str, _ShiftAwareFamilySummaryAccumulator] = {}
    for path in source_family_summary_paths:
        for row in read_tsv_required(path, _SHIFT_SOURCE_FAMILY_REQUIRED_COLUMNS):
            family = text_value(row.get("feature_family_id"))
            if not family:
                continue
            if text_value(row.get("is_reference")).upper() == "TRUE":
                continue
            by_family.setdefault(
                family,
                _ShiftAwareFamilySummaryAccumulator(),
            ).add(row)

    summaries: list[dict[str, str]] = []
    for family, summary in sorted(by_family.items()):
        if not summary.shape_values:
            continue
        summaries.append(
            {
                "feature_family_id": family,
                "nonref_source_families": ";".join(summary.source_families),
                "nonref_group_count": str(summary.nonref_count),
                "min_shape_r_after_best_shift": (
                    f"{min(summary.shape_values):.4f}"
                ),
                "max_shape_r_after_best_shift": (
                    f"{max(summary.shape_values):.4f}"
                ),
                "max_abs_shift_sec": (
                    f"{max(summary.shift_values):.2f}"
                    if summary.shift_values
                    else ""
                ),
            },
        )
    return summaries


@dataclass
class _ShiftAwareFamilySummaryAccumulator:
    source_families: list[str] = field(default_factory=list)
    nonref_count: int = 0
    shape_values: list[float] = field(default_factory=list)
    shift_values: list[float] = field(default_factory=list)

    def add(self, row: Mapping[str, str]) -> None:
        self.source_families.append(text_value(row.get("source_family")))
        self.nonref_count += 1
        shape_value = optional_float(
            row.get("shape_similarity_to_reference_after_group_shift"),
        )
        if shape_value is not None:
            self.shape_values.append(shape_value)
        shift_value = optional_float(row.get("shift_to_reference_sec"))
        if shift_value is not None:
            self.shift_values.append(abs(shift_value))


def _calibration_row(
    *,
    shift_row: Mapping[str, str],
    group_row: Mapping[str, str],
    overlay_row: Mapping[str, str],
    review_rank: int,
    min_shape_r: float,
    shift_aware_output_dir: Path,
    reconciliation_gallery_html: Path | None,
) -> dict[str, str]:
    family = text_value(shift_row.get("feature_family_id"))
    min_r_text = text_value(shift_row.get("min_shape_r_after_best_shift"))
    machine_call = (
        "shift_aware_same_pattern_support_review_only"
        if _shift_aware_min_supported(shift_row, min_shape_r=min_shape_r)
        or _overlay_reference_supported(overlay_row, min_shape_r=min_shape_r)
        else "shift_aware_same_pattern_review_required"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "review_rank": str(review_rank),
        "feature_family_id": family,
        "machine_shift_aware_call": machine_call,
        "manual_same_peak_call": "",
        "manual_standard_peak_call": "",
        "manual_backfill_authority_call": "",
        "manual_notes": "",
        "nonref_source_families": text_value(shift_row.get("nonref_source_families")),
        "nonref_group_count": text_value(shift_row.get("nonref_group_count")),
        "min_shape_r_after_best_shift": min_r_text,
        "max_shape_r_after_best_shift": text_value(
            shift_row.get("max_shape_r_after_best_shift"),
        ),
        "max_abs_shift_sec": text_value(shift_row.get("max_abs_shift_sec")),
        "product_behavior_state": text_value(group_row.get("product_behavior_state")),
        "evidence_authority_state": text_value(
            group_row.get("evidence_authority_state"),
        ),
        "reconciliation_class": text_value(group_row.get("reconciliation_class")),
        "detected_cell_count": text_value(group_row.get("detected_cell_count")),
        "rescued_cell_count": text_value(group_row.get("rescued_cell_count")),
        "top_support_component": text_value(group_row.get("top_support_component")),
        "top_blocker": text_value(group_row.get("top_blocker")),
        "missing_evidence": text_value(group_row.get("missing_evidence")),
        "family_verdict": text_value(overlay_row.get("family_verdict")),
        "shape_supported_fraction": text_value(
            overlay_row.get("shape_supported_fraction"),
        ),
        "absolute_own_max_shape_supported_fraction": text_value(
            overlay_row.get("absolute_own_max_shape_supported_fraction"),
        ),
        "absolute_trace_apex_cluster_fraction": text_value(
            overlay_row.get("absolute_trace_apex_cluster_fraction"),
        ),
        "family_context_png_path": text_value(overlay_row.get("png_path")),
        "shift_best_alignment_png_path": _find_shift_best_png(
            shift_aware_output_dir,
            family,
        ),
        "shift_best_summary_tsv_path": _find_shift_best_summary_tsv(
            shift_aware_output_dir,
            family,
        ),
        "reconciliation_gallery_path": (
            str(reconciliation_gallery_html) if reconciliation_gallery_html else ""
        ),
    }


def _shift_aware_min_supported(
    row: Mapping[str, str],
    *,
    min_shape_r: float,
) -> bool:
    min_r = optional_float(row.get("min_shape_r_after_best_shift"))
    return min_r is not None and min_r >= min_shape_r


def _overlay_reference_supported(
    row: Mapping[str, str],
    *,
    min_shape_r: float,
) -> bool:
    if text_value(row.get("status")) != "success":
        return False
    if text_value(row.get("family_verdict")) != "ms1_shape_supports_family_backfill":
        return False
    shape_fraction = optional_float(row.get("shape_supported_fraction"))
    if shape_fraction is None or shape_fraction < min_shape_r:
        return False
    if _positive_int(row.get("detected_count")) <= 0:
        return False
    if _positive_int(row.get("backfilled_count")) <= 0:
        return False
    if _positive_int(row.get("global_apex_interference_count")) != 0:
        return False
    return True


def _positive_int(value: object) -> int:
    try:
        parsed = int(float(text_value(value)))
    except ValueError:
        return -1
    return parsed if parsed >= 0 else -1


def _first_by_family(rows: Sequence[Mapping[str, str]]) -> dict[str, Mapping[str, str]]:
    by_family: dict[str, Mapping[str, str]] = {}
    for row in rows:
        family = text_value(row.get("feature_family_id"))
        if family and family not in by_family:
            by_family[family] = row
    return by_family


def _find_shift_best_png(output_dir: Path, family: str) -> str:
    return _first_glob_path(output_dir, f"*{_family_file_token(family)}*")


def _find_shift_best_summary_tsv(output_dir: Path, family: str) -> str:
    return _first_glob_path(
        output_dir,
        f"*{_family_file_token(family)}*_source_family_best_shift_summary.tsv",
    )


def _family_file_token(family: str) -> str:
    return text_value(family).lower()


def _first_glob_path(output_dir: Path, pattern: str) -> str:
    if not output_dir.exists():
        return ""
    matches = sorted(
        path
        for path in output_dir.glob(pattern)
        if path.name.endswith(
            (
                "_source_family_best_shift_alignment.png",
                "_source_family_best_shift_summary.tsv",
            ),
        )
    )
    return str(matches[0]) if matches else ""


def _render_html(rows: Sequence[Mapping[str, str]], *, html_path: Path) -> str:
    cards = "\n".join(_render_card(row, html_path=html_path) for row in rows)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shift-aware backfill calibration pack</title>
<style>
:root {{
  color-scheme: light;
  --ink: #17212b;
  --muted: #62717e;
  --line: #d8e1e8;
  --panel: #ffffff;
  --wash: #f5f8fa;
  --accent: #08758f;
  --accent-soft: #dff3f7;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", Arial, sans-serif;
  color: var(--ink);
  background: var(--wash);
}}
main {{
  max-width: 1320px;
  margin: 0 auto;
  padding: 24px;
}}
header {{
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  margin-bottom: 18px;
}}
h1 {{
  font-size: 26px;
  margin: 0 0 6px;
}}
.lede {{
  color: var(--muted);
  margin: 0;
  max-width: 820px;
  line-height: 1.5;
}}
.count {{
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 10px 14px;
  font-weight: 700;
  white-space: nowrap;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin: 14px 0;
  overflow: hidden;
}}
.card-head {{
  display: grid;
  grid-template-columns: minmax(170px, 1fr) repeat(4, minmax(120px, auto));
  gap: 12px;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}}
.family {{
  font-size: 20px;
  font-weight: 800;
}}
.metric {{
  text-align: center;
  line-height: 1.25;
}}
.metric small {{
  display: block;
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .04em;
}}
.pill {{
  display: inline-flex;
  justify-content: center;
  border: 1px solid #8bd0dc;
  background: var(--accent-soft);
  color: #063f4c;
  border-radius: 999px;
  padding: 6px 10px;
  font-weight: 700;
}}
.body {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  padding: 14px 16px 16px;
}}
.facts {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}}
.fact {{
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  min-height: 56px;
}}
.fact small {{
  display: block;
  color: var(--muted);
  margin-bottom: 4px;
}}
.manual {{
  border-left: 4px solid var(--accent);
  padding: 10px 12px;
  background: #fbfdfe;
  border-radius: 6px;
}}
.manual strong {{
  display: block;
  margin-bottom: 6px;
}}
.media {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}}
figure {{
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}}
figcaption {{
  padding: 8px 10px;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  font-size: 12px;
}}
img {{
  display: block;
  width: 100%;
  height: 280px;
  object-fit: contain;
  background: #fff;
}}
a {{ color: #075e75; font-weight: 700; }}
.links {{
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}}
@media (max-width: 980px) {{
  .card-head, .body, .media {{ grid-template-columns: 1fr; }}
  .metric {{ text-align: left; }}
}}
</style>
</head>
<body>
<main>
<header>
<div>
<h1>Shift-aware same-pattern calibration</h1>
<p class="lede">
這份 pack 只用來校準人工與機器判斷。機器欄位代表平移後的 MS1 pattern similarity；
人工欄位留在 TSV，等你判斷 same peak / standard peak / 是否足以授權 backfill。
</p>
</div>
<div class="count">{len(rows)} cases</div>
</header>
{cards}
</main>
</body>
</html>
"""


def _render_card(row: Mapping[str, str], *, html_path: Path) -> str:
    family = text_value(row.get("feature_family_id"))
    machine_call = _escape(row.get("machine_shift_aware_call"))
    detected_backfilled = (
        f"{text_value(row.get('detected_cell_count'))} / "
        f"{text_value(row.get('rescued_cell_count'))}"
    )
    best_shift_link = _path_link(
        row.get("shift_best_summary_tsv_path"),
        "best-shift TSV",
        html_path,
    )
    gallery_link = _path_link(
        row.get("reconciliation_gallery_path"),
        "reconciliation gallery",
        html_path,
    )
    family_png = _image_html(
        row.get("family_context_png_path"),
        caption="Family context",
        html_path=html_path,
    )
    shift_png = _image_html(
        row.get("shift_best_alignment_png_path"),
        caption="Best-shift source-family pattern",
        html_path=html_path,
    )
    title_html = (
        f'<div><div class="family">{_escape(family)}</div>'
        f'<span class="pill">{machine_call}</span></div>'
    )
    return f"""
<section class="card">
  <div class="card-head">
    {title_html}
    {_metric("min r", row.get("min_shape_r_after_best_shift"))}
    {_metric("max r", row.get("max_shape_r_after_best_shift"))}
    {_metric("max shift", f"{text_value(row.get('max_abs_shift_sec'))} sec")}
    {_metric("non-ref groups", row.get("nonref_group_count"))}
  </div>
  <div class="body">
    <div>
      <div class="facts">
        {_fact("source families", row.get("nonref_source_families"))}
        {_fact("product", row.get("product_behavior_state"))}
        {_fact("evidence", row.get("evidence_authority_state"))}
        {_fact("class", row.get("reconciliation_class"))}
        {_fact("detected / backfilled", detected_backfilled)}
        {_fact("family verdict", row.get("family_verdict"))}
      </div>
      <div class="manual">
        <strong>人工要填的欄位在 TSV</strong>
        manual_same_peak_call、manual_standard_peak_call、manual_backfill_authority_call、manual_notes
      </div>
      <div class="links">
        {best_shift_link}
        {gallery_link}
      </div>
    </div>
    <div class="media">
      {family_png}
      {shift_png}
    </div>
  </div>
</section>
"""


def _metric(label: str, value: object) -> str:
    return (
        '<div class="metric">'
        f"<small>{_escape(label)}</small>"
        f"<strong>{_escape(value)}</strong>"
        "</div>"
    )


def _fact(label: str, value: object) -> str:
    return (
        '<div class="fact">'
        f"<small>{_escape(label)}</small>"
        f"<strong>{_escape(value)}</strong>"
        "</div>"
    )


def _image_html(path_value: object, *, caption: str, html_path: Path) -> str:
    path_text = text_value(path_value)
    if not path_text:
        return (
            "<figure>"
            f"<figcaption>{_escape(caption)}</figcaption>"
            '<div style="padding:24px;color:#62717e">not available</div>'
            "</figure>"
        )
    href = _relative_href(path_text, html_path)
    return (
        "<figure>"
        f"<figcaption>{_escape(caption)}</figcaption>"
        f'<a href="{_attr(href)}"><img src="{_attr(href)}" alt="{_attr(caption)}"></a>'
        "</figure>"
    )


def _path_link(path_value: object, label: str, html_path: Path) -> str:
    path_text = text_value(path_value)
    if not path_text:
        return ""
    href = _relative_href(path_text, html_path)
    return f'<a href="{_attr(href)}">{_escape(label)}</a>'


def _relative_href(path_text: str, html_path: Path) -> str:
    path = Path(path_text)
    try:
        return path.resolve().relative_to(html_path.parent.resolve()).as_posix()
    except ValueError:
        try:
            return Path(
                "..",
                path.resolve().relative_to(html_path.parent.parent.resolve()),
            ).as_posix()
        except ValueError:
            return path_text.replace("\\", "/")


def _escape(value: object) -> str:
    return html.escape(text_value(value))


def _attr(value: object) -> str:
    return html.escape(text_value(value), quote=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--shift-aware-summary-tsv", type=Path)
    source.add_argument(
        "--shift-aware-summary-dir",
        type=Path,
        help=(
            "Directory containing *_source_family_best_shift_summary.tsv files; "
            "the tool will aggregate them into a per-family summary."
        ),
    )
    parser.add_argument(
        "--shift-aware-summary-pattern",
        default="*_source_family_best_shift_summary.tsv",
        help="Glob pattern used with --shift-aware-summary-dir.",
    )
    parser.add_argument("--reconciliation-groups-tsv", type=Path, required=True)
    parser.add_argument("--overlay-batch-summary-tsv", type=Path, required=True)
    parser.add_argument("--shift-aware-output-dir", type=Path, required=True)
    parser.add_argument("--reconciliation-gallery-html", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--min-shape-r",
        type=float,
        default=DEFAULT_STANDARD_PEAK_MIN_SHAPE_R,
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include rows below --min-shape-r as review-required controls.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
