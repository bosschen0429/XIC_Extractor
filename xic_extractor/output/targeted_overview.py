"""A reading guide to saved Targeted results, not a new detection policy."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median

from xic_extractor.output.result_labels import needs_look

# A detection outcome shared by at most this share of a target's recorded
# decisions is the unusual one: it is where a wrong peak or a missed peak hides.
MINORITY_SHARE = 0.2


@dataclass(frozen=True)
class TargetOverview:
    label: str
    role: str
    total: int
    counted: int
    count_unknown: int
    needs_look: int
    focus_kind: str
    focus_samples: tuple[str, ...]
    median_area: float | None
    rt_min: float | None
    rt_max: float | None


@dataclass(frozen=True)
class TargetedOverview:
    sample_count: int
    result_count: int
    counted_count: int
    count_unknown: int
    needs_look_count: int
    focus_count: int
    focus_target_count: int
    minority_share: float
    analyte_counted: int
    analyte_total: int
    istd_counted: int
    istd_total: int
    targets: tuple[TargetOverview, ...]


def rows_needing_look(
    rows: Sequence[Mapping[str, object]], istd_targets: Collection[str]
) -> list[Mapping[str, object]]:
    """The results whose saved labels ask for a look (`result_labels`)."""
    return [
        row
        for row in rows
        if needs_look(
            row.get("依據"),
            row.get("疑點"),
            is_istd=str(row.get("Target")) in istd_targets,
        )
    ]


def summarize_targeted_results(
    rows: Sequence[Mapping[str, object]],
    look_rows: Sequence[Mapping[str, object]],
) -> TargetedOverview:
    """Count recorded decisions once; never infer missing decisions from values."""
    by_target: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    look_by_target: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_target[str(row["Target"])].append(row)
    for row in look_rows:
        look_by_target[str(row["Target"])].append(row)
    targets = []
    for label, records in by_target.items():
        counted = [row for row in records if _counted(row) == "TRUE"]
        areas = [_number(row.get("Area")) for row in counted]
        rts = [_number(row.get("RT")) for row in counted]
        numeric_areas = [value for value in areas if value is not None]
        numeric_rts = [value for value in rts if value is not None]
        look = look_by_target[label]
        focus_kind, focus_samples = _focus(records, look)
        targets.append(
            TargetOverview(
                label=label,
                role=str(records[0].get("Role") or ""),
                total=len(records),
                counted=len(counted),
                count_unknown=sum(
                    _counted(row) not in {"TRUE", "FALSE"} for row in records
                ),
                needs_look=len(look),
                focus_kind=focus_kind,
                focus_samples=focus_samples,
                median_area=median(numeric_areas) if numeric_areas else None,
                rt_min=min(numeric_rts) if numeric_rts else None,
                rt_max=max(numeric_rts) if numeric_rts else None,
            )
        )
    # Unusual outcomes come first; result order is otherwise kept.
    targets.sort(key=lambda target: not target.focus_samples)
    return TargetedOverview(
        sample_count=len({row["SampleName"] for row in rows}),
        result_count=len(rows),
        counted_count=sum(target.counted for target in targets),
        count_unknown=sum(target.count_unknown for target in targets),
        needs_look_count=len(look_rows),
        focus_count=sum(len(target.focus_samples) for target in targets),
        focus_target_count=sum(bool(target.focus_samples) for target in targets),
        minority_share=MINORITY_SHARE,
        analyte_counted=sum(t.counted for t in targets if t.role != "ISTD"),
        analyte_total=sum(t.total for t in targets if t.role != "ISTD"),
        istd_counted=sum(t.counted for t in targets if t.role == "ISTD"),
        istd_total=sum(t.total for t in targets if t.role == "ISTD"),
        targets=tuple(targets),
    )


def focus_worklist(
    overview: TargetedOverview,
    result_rows: Sequence[Mapping[str, object]],
    diagnostics: list[dict[str, str]],
) -> list[dict[str, object]]:
    """List the results the reader should look at first, in summary order.

    Each row shows its saved labels; a minority outcome is listed for its
    outcome, not given a new flag. A RAW file that failed before any result
    was delivered stays visible here as `RAW 失敗`; its message is on
    `診斷事件`.
    """
    records = {(str(r["SampleName"]), str(r["Target"])): r for r in result_rows}
    sample_order = {key[0]: index for index, key in reversed(list(enumerate(records)))}
    worklist: list[dict[str, object]] = []
    for target in overview.targets:
        for sample in sorted(
            target.focus_samples, key=lambda s: sample_order.get(s, len(sample_order))
        ):
            record = records[(sample, target.label)]
            worklist.append(
                {
                    "Sample": sample,
                    "Target": target.label,
                    "Outcome": "已計入" if _counted(record) == "TRUE" else "未計入",
                    "依據": record.get("依據", ""),
                    "疑點": record.get("疑點", ""),
                    "RT": record.get("RT", ""),
                    "Area": record.get("Area", ""),
                }
            )
    orphans = dict.fromkeys(
        (str(event.get("SampleName", "")), str(event.get("Target", "")))
        for event in diagnostics
        if event.get("Issue") == "FILE_ERROR"
    )
    worklist.extend(
        {"Sample": sample, "Target": label, "依據": "RAW 失敗"}
        for sample, label in orphans
        if (sample, label) not in records
    )
    return worklist


def _focus(
    records: Sequence[Mapping[str, object]],
    look: Sequence[Mapping[str, object]],
) -> tuple[str, tuple[str, ...]]:
    """Return the samples whose saved outcome departs from the target's majority.

    An outcome every sample shares is the batch fact and needs no focus.
    Without a clear majority, the results whose labels need a look come first.
    """
    counted = [str(r["SampleName"]) for r in records if _counted(r) == "TRUE"]
    missed = [str(r["SampleName"]) for r in records if _counted(r) == "FALSE"]
    decided = len(counted) + len(missed)
    if not counted or not missed:
        return "uniform", ()
    if len(counted) <= MINORITY_SHARE * decided:
        return "minority_counted", tuple(counted)
    if len(missed) <= MINORITY_SHARE * decided:
        return "minority_not_counted", tuple(missed)
    return "mixed", tuple(dict.fromkeys(str(row["SampleName"]) for row in look))


def _counted(row: Mapping[str, object]) -> str:
    return str(row.get("Counted Detection", "")).strip().upper()


def _number(value: object) -> float | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if isfinite(number) else None
