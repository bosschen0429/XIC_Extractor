"""Target benchmark rendering for the reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_indices import (
    _target_benchmark_context_sort_key,
)
from .backfill_reconciliation_gallery_models import (
    TargetBenchmarkContext,
)


def _compact_counts_text(counts: Mapping[str, int]) -> str:
    return " · ".join(f"{key} {value}" for key, value in counts.items() if value)


def _target_benchmark_summary_text(
    contexts: Sequence[TargetBenchmarkContext],
    counts: Mapping[str, int],
    input_artifacts: object,
) -> str:
    if contexts:
        return "context only · " + (_compact_counts_text(counts) or "matched")
    if _target_benchmark_supplied(input_artifacts):
        return "context only · no matched target family"
    return "not supplied"


def _target_benchmark_panel_html(
    contexts: Sequence[TargetBenchmarkContext],
    input_artifacts: object,
    counts: Mapping[str, int],
) -> list[str]:
    if not contexts and not _target_benchmark_supplied(input_artifacts):
        return []
    summary = _target_benchmark_summary_text(contexts, counts, input_artifacts)
    return [
        '<details class="provenance-panel target-benchmark-panel" open>',
        f"<summary>Target benchmark · {_escape(summary)}</summary>",
        _target_benchmark_contexts_html(contexts, input_artifacts),
        "</details>",
    ]


def _family_target_summary_html(
    contexts: Sequence[TargetBenchmarkContext],
) -> str:
    if not contexts:
        return ""
    labels = [
        f"{context.target_label} {context.status}".strip()
        for context in sorted(contexts, key=_target_benchmark_context_sort_key)
    ]
    label = "target context " + " / ".join(labels[:2])
    if len(labels) > 2:
        label += f" / +{len(labels) - 2}"
    return f'<span class="target-status">{_escape(label)}</span>'


def _target_benchmark_compact_summary(
    contexts: Sequence[TargetBenchmarkContext],
    input_artifacts: object,
) -> str:
    if contexts:
        if len(contexts) == 1:
            context = contexts[0]
            label = context.target_label or "target"
            status = context.status or "UNKNOWN"
            coverage = _target_coverage_text(context)
            return f"{label} {status} · {coverage}"
        counts = Counter(context.status or "UNKNOWN" for context in contexts)
        return "matched targets: " + ", ".join(
            f"{status}={count}" for status, count in sorted(counts.items())
        )
    if _target_benchmark_supplied(input_artifacts):
        return "benchmark not matched to this family"
    return "not supplied"


def _target_benchmark_contexts_html(
    contexts: Sequence[TargetBenchmarkContext],
    input_artifacts: object,
) -> str:
    if not contexts:
        if _target_benchmark_supplied(input_artifacts):
            return (
                '<p class="chain-note">'
                "targeted benchmark summary 已提供，但這個 family 沒有對到 "
                "selected/primary target feature；可視為 benchmark context miss，"
                "不是 production identity decision。</p>"
            )
        return (
            '<p class="chain-note">'
            "No targeted benchmark summary supplied for this gallery run.</p>"
        )
    rows = "".join(
        "<tr>"
        f"<td>{_escape(context.target_label)}</td>"
        f"<td>{_escape(context.role)}</td>"
        f"<td>{_badge(context.status or 'UNKNOWN')}</td>"
        f"<td>{_escape(_target_coverage_text(context))}</td>"
        f"<td>{_escape(context.selected_feature_id or 'none')}</td>"
        f"<td>{_escape(';'.join(context.failure_modes) or 'none')}</td>"
        f"<td>{_escape(context.note or 'none')}</td>"
        "</tr>"
        for context in sorted(contexts, key=_target_benchmark_context_sort_key)
    )
    return (
        '<p class="chain-note">'
        "benchmark context only；target benchmark 可作驗收/定位 target context，"
        "但不會改 product identity 或 backfill decision。</p>"
        '<div class="target-benchmark-table-wrap">'
        '<table class="target-benchmark-table">'
        "<thead><tr>"
        '<th scope="col">target</th>'
        '<th scope="col">role</th>'
        '<th scope="col">status</th>'
        '<th scope="col">coverage</th>'
        '<th scope="col">selected family</th>'
        '<th scope="col">failure modes</th>'
        '<th scope="col">note</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _target_coverage_text(context: TargetBenchmarkContext) -> str:
    parts = []
    if context.untargeted_positive_count or context.targeted_positive_count:
        parts.append(
            f"untargeted {context.untargeted_positive_count or '?'}"
            f"/targeted {context.targeted_positive_count or '?'}",
        )
    if context.coverage_minimum:
        parts.append(f"min {context.coverage_minimum}")
    return " · ".join(parts) or "not supplied"


def _target_benchmark_supplied(input_artifacts: object) -> bool:
    return isinstance(input_artifacts, Mapping) and bool(
        input_artifacts.get("targeted_istd_benchmark_summary_tsv"),
    )
