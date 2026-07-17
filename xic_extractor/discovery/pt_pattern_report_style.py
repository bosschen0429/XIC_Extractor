"""Shared HTML shell and tokens for Pt pattern diagnostic reports."""

# HTML and CSS literals stay readable as complete tags.
# ruff: noqa: E501

from __future__ import annotations

import html

from xic_extractor.tabular_io import format_diagnostic_value

DiagnosticScalar = str | int | float | bool | None


def escape(value: DiagnosticScalar) -> str:
    """Escape a diagnostic scalar for HTML output."""

    return html.escape(format_diagnostic_value(value))


def number(value: float | None, *, digits: int = 3) -> str:
    """Format a compact numeric label."""

    if value is None:
        return "n/a"
    return f"{value:.{digits}g}"


def percent(numerator: float, denominator: float) -> str:
    """Format a percentage while handling an empty denominator."""

    if denominator <= 0:
        return "n/a"
    return f"{numerator / denominator * 100:.1f}%"


def report_shell(*, title: str, meta: str, body: str) -> str:
    """Wrap a static report body in the shared responsive document shell."""

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-Hant">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            "<style>",
            REPORT_CSS,
            "</style>",
            "</head>",
            "<body>",
            '<a class="skip-link" href="#main">跳至主要內容</a>',
            f'<main id="main"><header class="report-header"><p class="eyebrow">PT PATTERN · DIAGNOSTIC REVIEW</p><h1>{escape(title)}</h1><p class="meta">{escape(meta)}</p></header>',
            body,
            '<footer><span>diagnostic_only</span><span>不開 RAW · 不改 detector · 不寫 Matrix</span></footer>',
            "</main>",
            "</body>",
            "</html>",
        ]
    )


REPORT_CSS = """
:root {
  color-scheme: light;
  --paper: #f4f6f3;
  --surface: #ffffff;
  --surface-soft: #eaf1ee;
  --ink: #17201d;
  --muted: #5f6f68;
  --line: #cbd5d0;
  --line-strong: #8ea097;
  --teal: #0f6b63;
  --teal-soft: #bcd8d2;
  --gold: #b77b18;
  --gold-soft: #ead6ad;
  --orange: #b5522c;
  --orange-soft: #edc8b8;
  --mono: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
  --sans: "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", sans-serif;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-variant-numeric: tabular-nums;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); }
main { width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 36px 0 56px; }
.skip-link { position: absolute; left: -9999px; top: 8px; }
.skip-link:focus { left: 12px; z-index: 10; background: var(--surface); color: var(--ink); padding: 8px 12px; outline: 3px solid var(--gold); }
.report-header { border-bottom: 2px solid var(--ink); padding-bottom: 18px; }
.eyebrow { margin: 0 0 10px; color: var(--teal); font-family: var(--mono); font-size: 12px; font-weight: 700; }
h1 { margin: 0; max-width: 22ch; font-size: 34px; line-height: 1.15; letter-spacing: 0; text-wrap: balance; }
.meta { margin: 9px 0 0; color: var(--muted); font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.verdict { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(260px, .75fr); gap: 28px; align-items: end; padding: 32px 0 28px; border-bottom: 1px solid var(--line); }
.verdict h2 { margin: 0 0 12px; max-width: 26ch; font-size: 28px; line-height: 1.22; letter-spacing: 0; text-wrap: balance; }
.verdict p { margin: 0; max-width: 70ch; color: var(--muted); font-size: 16px; line-height: 1.7; }
.signal { border-left: 4px solid var(--gold); padding: 10px 0 10px 16px; }
.signal strong { display: block; margin-bottom: 5px; font-size: 14px; }
.signal span { color: var(--muted); font-size: 13px; line-height: 1.55; }
.metric-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.metric { padding: 20px 18px 22px 0; }
.metric + .metric { border-left: 1px solid var(--line); padding-left: 22px; }
.metric strong { display: block; font-family: var(--mono); font-size: 29px; line-height: 1; }
.metric span { display: block; margin-top: 8px; color: var(--muted); font-size: 13px; }
.report-section { padding: 34px 0; border-bottom: 1px solid var(--line); }
.section-head { display: grid; grid-template-columns: minmax(220px, .65fr) minmax(0, 1.35fr); gap: 28px; align-items: start; margin-bottom: 22px; }
.section-head h2 { margin: 0; font-size: 21px; line-height: 1.3; letter-spacing: 0; text-wrap: balance; }
.section-head p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.65; }
.chart-shell { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px 18px 12px; overflow-x: auto; overscroll-behavior-inline: contain; }
.chart-title { margin: 0; font-size: 15px; font-weight: 650; }
.chart-subtitle { margin: 5px 0 14px; color: var(--muted); font-size: 12px; }
svg { display: block; width: 100%; min-width: 900px; height: auto; overflow: visible; }
svg text { fill: var(--muted); font-family: var(--sans); font-size: 12px; }
svg .label { fill: var(--ink); font-size: 12px; font-weight: 600; }
svg .value { fill: var(--ink); font-family: var(--mono); font-size: 11px; }
svg .grid { stroke: var(--line); stroke-width: 1; }
svg .axis { stroke: var(--line-strong); stroke-width: 1; }
svg .primary-line { stroke: var(--gold); stroke-width: 7; stroke-linecap: round; }
svg .background-line { stroke: var(--teal); stroke-width: 4; stroke-linecap: round; }
svg .primary-dot { fill: var(--gold); stroke: var(--ink); stroke-width: 1.5; }
svg .background-dot { fill: var(--surface); stroke: var(--teal); stroke-width: 2.5; }
svg .hotspot { fill: var(--orange-soft); opacity: .55; }
svg .point { fill: var(--teal); opacity: .46; }
svg .point-hot { fill: var(--orange); opacity: .62; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 12px 0 0; color: var(--muted); font-size: 12px; }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.swatch { width: 18px; height: 4px; background: var(--teal); }
.swatch.gold { background: var(--gold); height: 7px; }
.swatch.orange { background: var(--orange-soft); height: 10px; }
.takeaway { margin: 14px 0 0; padding-left: 14px; border-left: 3px solid var(--teal); color: var(--ink); font-size: 14px; line-height: 1.6; }
.equation-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); }
.equation { min-width: 0; background: var(--surface); padding: 18px; }
.equation code { display: block; font-family: var(--mono); font-size: 19px; font-weight: 700; color: var(--teal); }
.equation span { display: block; margin-top: 8px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.stage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.stage-panel { min-width: 0; }
.stage-panel h3 { margin: 0 0 12px; font-size: 15px; }
.stage-row { display: grid; grid-template-columns: 92px minmax(0, 1fr) 42px; gap: 10px; align-items: center; margin: 10px 0; }
.stage-row span { color: var(--muted); font-size: 12px; }
.stage-row strong { font-family: var(--mono); font-size: 12px; text-align: right; }
.stage-track { height: 16px; background: var(--surface-soft); }
.stage-fill { height: 100%; background: var(--teal); }
.stage-row.highlight .stage-fill { background: var(--gold); }
.stage-note { margin: 12px 0 0; color: var(--orange); font-size: 13px; font-weight: 650; }
.next-step { background: var(--ink); color: var(--surface); padding: 24px; }
.next-step h2 { margin: 0 0 10px; font-size: 21px; }
.next-step p { margin: 0; max-width: 72ch; color: #dce4e0; line-height: 1.65; }
details { margin-top: 20px; border-top: 1px solid var(--line); padding-top: 14px; }
summary { cursor: pointer; color: var(--teal); font-weight: 650; }
table { width: 100%; margin-top: 14px; border-collapse: collapse; background: var(--surface); font-size: 12px; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 650; }
td code { font-family: var(--mono); overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; padding-top: 18px; color: var(--muted); font-family: var(--mono); font-size: 11px; }
@media (max-width: 760px) {
  main { width: min(100% - 24px, 1180px); padding-top: 22px; }
  h1 { font-size: 28px; }
  .verdict, .section-head { grid-template-columns: 1fr; gap: 14px; }
  .metric-strip, .equation-grid, .stage-grid { grid-template-columns: 1fr; }
  .metric + .metric { border-left: 0; border-top: 1px solid var(--line); padding-left: 0; }
  .chart-shell { padding: 14px 10px 10px; }
  .stage-row { grid-template-columns: 80px minmax(0, 1fr) 36px; }
}
@media print {
  :root { background: #ffffff; }
  main { width: 100%; padding: 0; }
  details { display: none; }
  svg { min-width: 0; }
}
"""
