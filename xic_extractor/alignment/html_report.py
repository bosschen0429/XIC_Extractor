from __future__ import annotations

import html
from pathlib import Path

from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.production_decisions import ProductionDecisionSet


def write_alignment_review_html(
    path: Path,
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
) -> Path:
    if not isinstance(production_decisions, ProductionDecisionSet):
        raise TypeError("production_decisions must be owner-minted")
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = _status_counts(production_decisions)
    max_count = max(counts.values(), default=1)
    sample_count = len(matrix.sample_order)
    bars = "\n".join(
        _bar(label, count, max_count) for label, count in counts.items()
    )
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Alignment Review</title>
<style>
body {{
  font-family: Segoe UI, Arial, sans-serif;
  margin: 32px;
  color: #1f2933;
}}
.summary {{
  display: grid;
  grid-template-columns: repeat(3, minmax(160px, 1fr));
  gap: 12px;
  max-width: 960px;
}}
.metric {{ border: 1px solid #d7dde5; padding: 12px; border-radius: 6px; }}
.metric strong {{ display: block; font-size: 24px; }}
.chart {{ max-width: 960px; margin-top: 24px; }}
.row {{
  display: grid;
  grid-template-columns: 190px 1fr 80px;
  gap: 12px;
  align-items: center;
  margin: 10px 0;
}}
.track {{ background: #e5e7eb; height: 20px; }}
.bar {{ background: #2563eb; height: 20px; }}
.warn {{ background: #b45309; }}
</style>
</head>
<body>
<h1>Alignment Review</h1>
<section class="summary">
<div class="metric"><span>Features</span><strong>{len(matrix.clusters)}</strong></div>
<div class="metric"><span>Samples</span><strong>{sample_count}</strong></div>
<div class="metric"><span>Cells</span><strong>{len(matrix.cells)}</strong></div>
</section>
<section class="chart">
<h2>Detected / Rescued / Ambiguous</h2>
{bars}
</section>
<section>
<h2>Ownership pressure</h2>
<p>{html.escape(_ownership_pressure_text(counts))}</p>
</section>
</body>
</html>
""",
        encoding="utf-8",
    )
    return path


def _status_counts(production_decisions: ProductionDecisionSet) -> dict[str, int]:
    counts = {
        "detected": 0,
        "rescued": 0,
        "duplicate_assigned": 0,
        "ambiguous_ms1_owner": 0,
        "absent": 0,
        "unchecked": 0,
    }
    for decision in production_decisions.cells.values():
        counts[decision.raw_status] = counts.get(decision.raw_status, 0) + 1
    return counts


def _bar(label: str, count: int, max_count: int) -> str:
    width = 0 if max_count <= 0 else round(count / max_count * 100, 1)
    klass = (
        "bar warn"
        if label in {"duplicate_assigned", "ambiguous_ms1_owner"}
        else "bar"
    )
    return (
        f'<div class="row"><div>{html.escape(label)}</div>'
        f'<div class="track"><div class="{klass}" style="width:{width}%"></div></div>'
        f"<div>{count}</div></div>"
    )


def _ownership_pressure_text(counts: dict[str, int]) -> str:
    duplicate = counts.get("duplicate_assigned", 0)
    ambiguous = counts.get("ambiguous_ms1_owner", 0)
    if duplicate or ambiguous:
        return (
            f"{duplicate} duplicate-assigned cells and "
            f"{ambiguous} ambiguous owner cells need review."
        )
    return "No duplicate-assigned or ambiguous owner cells were produced."
