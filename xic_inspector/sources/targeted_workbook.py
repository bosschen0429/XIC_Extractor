"""Read saved Targeted workbook facts without acquisition or reinterpretation."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from xic_extractor.diagnostics.alignment_explanation import ExplanationError
from xic_extractor.output.schema import TARGETED_WORKBOOK_LAYOUT_VERSION
from xic_extractor.output.targeted_overview import summarize_targeted_results
from xic_inspector.selection.canonical import CanonicalItem, build_canonical_ref
from xic_inspector.selection.sampler import sample_cross_sample_sha256


@dataclass(frozen=True, slots=True)
class TargetedWorkbookLocator:
    workbook_path: Path
    workbook_sha256: str


def _table(book, name: str, required: set[str]) -> list[dict[str, Any]]:
    if name not in book.sheetnames:
        raise ExplanationError(f"Targeted XLSX 缺少工作表：{name}")
    sheet = book[name]
    rows = sheet.iter_rows(values_only=True)
    headers = tuple(next(rows, ()))
    if not required.issubset(headers):
        raise ExplanationError(
            f"Targeted XLSX {name} 缺少必要欄位：{sorted(required - set(headers))}"
        )
    if any(not isinstance(header, str) for header in headers) or len(
        set(headers)
    ) != len(headers):
        raise ExplanationError(f"Targeted XLSX {name} 欄位名稱必須非空且不重複。")
    records = []
    for row_index, row in enumerate(rows, start=2):
        if any(
            value is not None and not isinstance(value, (str, int, float, bool))
            for value in row
        ):
            raise ExplanationError(
                f"Targeted XLSX {name} 第 {row_index} 列含不支援的儲存格類型。"
            )
        if any(value is not None for value in row):
            records.append(dict(zip(headers, row, strict=True)))
    return records


class TargetedWorkbookSource:
    """A self-contained saved-result source; labels are not RAW identities."""

    def __init__(self, path: Path) -> None:
        path = path.resolve()
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        self.locator = TargetedWorkbookLocator(path, digest)
        try:
            # This adapter never saves the workbook.
            book = load_workbook(path, read_only=True, data_only=True)
        except (
            BadZipFile,
            InvalidFileException,
            KeyError,
            ValueError,
            ParseError,
        ) as exc:
            raise ExplanationError(f"無法讀取 Targeted XLSX：{exc}") from exc
        try:
            metadata = [
                [row["Key"], row["Value"]]
                for row in _table(book, "Run Metadata", {"Key", "Value"})
            ]
            layout = dict(metadata).get("targeted_workbook_layout_version")
            if layout != TARGETED_WORKBOOK_LAYOUT_VERSION:
                raise ExplanationError(f"不支援的 Targeted XLSX layout：{layout}")
            self.targets = _table(
                book, "Targets", {"Label", "Role", "m/z", "RT min", "RT max"}
            )
            self.records = _table(
                book,
                "XIC Results",
                {
                    "SampleName",
                    "Target",
                    "RT",
                    "Area",
                    "PeakStart",
                    "PeakEnd",
                    "Reason",
                    "Review",
                    "Review Reason",
                    "Counted Detection",
                },
            )
            self.queue = [
                {
                    "Sample": row["SampleName"],
                    "Target": row["Target"],
                    "Status": row["Review"],
                    "Why": row["Review Reason"],
                    "Evidence": row.get("Review Evidence"),
                }
                for row in self.records
                if row["Review"]
            ]
        finally:
            book.close()
        labels = [row["Label"] for row in self.targets]
        if (
            not labels
            or any(not isinstance(v, str) or not v.strip() for v in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ExplanationError("Targeted XLSX 目標名稱必須非空且不重複。")
        if dict(metadata).get("targeted_output_schema_version") != "targeted_output":
            raise ExplanationError(
                "不支援的 Targeted XLSX：Run Metadata 缺少或不支援 "
                "targeted_output_schema_version。"
            )
        seen = set()
        for row in self.records:
            pair = (row["SampleName"], row["Target"])
            if (
                not isinstance(pair[0], str)
                or not pair[0]
                or pair[1] not in labels
                or pair in seen
            ):
                raise ExplanationError("Targeted XLSX 含無效或重複的樣本／目標結果。")
            seen.add(pair)
        if not seen:
            raise ExplanationError("Targeted XLSX 沒有可檢閱的結果。")
        if any((row["Sample"], row["Target"]) not in seen for row in self.queue):
            raise ExplanationError("Targeted XLSX ReviewQueue 無法對應樣本／目標結果。")
        self._records_by_target = defaultdict(list)
        self._queue_by_target = defaultdict(list)
        for row in self.records:
            self._records_by_target[row["Target"]].append(row)
        for row in self.queue:
            self._queue_by_target[row["Target"]].append(row)
        overview = summarize_targeted_results(self.records, self.queue)
        # Group is recorded per sample; it colours that sample in every view.
        sample_groups: dict[str, str] = {}
        for row in self.records:
            sample_groups.setdefault(row["SampleName"], str(row.get("Group") or ""))
        self._focus = {target.label: target for target in overview.targets}
        self._records_by_key = {
            (row["SampleName"], row["Target"]): row for row in self.records
        }
        self.summary = {
            "schema_version": "targeted_workbook",
            "source_kind": "targeted_workbook",
            "plot_evidence_capability": "metadata_only",
            "selection_evidence_capability": "recorded_decisions",
            "processing_attempt_id": digest,
            "method_context": metadata,
            "target_names": labels,
            "review_record_count": len(self.queue),
            "overview": asdict(overview),
            "samples": [
                {
                    "sample_key": name,
                    "sample_stem": name,
                    "group": str(group or ""),
                }
                for name, group in sample_groups.items()
            ],
        }

    def target_names_for_role(self, role: str | None) -> list[str]:
        """Project role options only from recorded Targets.Role, never labels."""
        if role is None:
            return list(cast(list[str], self.summary["target_names"]))
        return [
            target["Label"]
            for target in self.targets
            if isinstance(target["Role"], str)
            and target["Role"].strip().casefold() == role.casefold()
        ]

    def select_items(self, sample_ids: set[str], selection):
        items = []
        for target in self.targets:
            label = target["Label"]
            records = [
                r
                for r in self._records_by_target[label]
                if r["SampleName"] in sample_ids
            ]
            if not records:
                continue
            queue = [
                r for r in self._queue_by_target[label] if r["Sample"] in sample_ids
            ]
            focus = self._focus[label]
            focus_samples = [s for s in focus.focus_samples if s in sample_ids]
            attention = bool(focus_samples)
            pair = target.get("ISTD Pair")
            istd_records = {
                r["SampleName"]: {
                    key: istd.get(key)
                    for key in ("Target", "RT", "Area", "Counted Detection")
                }
                for r in records
                if pair and (istd := self._records_by_key.get((r["SampleName"], pair)))
            }
            _, ref = build_canonical_ref("target", self.locator.workbook_sha256, label)
            items.append(
                CanonicalItem(
                    kind="target",
                    attempt_id=self.locator.workbook_sha256,
                    identifier=label,
                    serialized_ref=ref,
                    sample_ids=tuple(r["SampleName"] for r in records),
                    witnesses=(),
                    stratum="attention" if attention else "rest",
                    is_attention=attention,
                    details={
                        "target": target,
                        "records": records,
                        "review_queue": queue,
                        "focus": {
                            "kind": focus.focus_kind,
                            "samples": focus_samples,
                            "counted": focus.counted,
                            "decided": focus.total - focus.count_unknown,
                        },
                        "istd_records": istd_records,
                        "review_summary": {
                            "observed_sample_count": 0,
                            "scope_sample_count": len(records),
                            "origin_kinds": [],
                        },
                    },
                )
            )
        eligible = len(items)
        if selection.strategy == "sample":
            return (
                sample_cross_sample_sha256(
                    items, limit=selection.limit, seed=selection.seed
                ),
                len(self.targets),
                eligible,
                eligible,
                0,
            )
        if selection.strategy == "attention" or selection.attention_only:
            # Lists the targets to read first; each keeps every sample, so the
            # minority rows are read against the majority they depart from.
            items = [item for item in items if item.is_attention]
        if selection.target_role is not None:
            role_targets = set(self.target_names_for_role(selection.target_role))
            items = [item for item in items if item.identifier in role_targets]
        if selection.target_ids:
            items = [item for item in items if item.identifier in selection.target_ids]
        if selection.group_ids or selection.candidate_ids:
            raise ExplanationError(
                "Targeted XLSX 不提供分組或候選 ID；請以目標名稱查詢。"
            )
        missing = 0
        for field, lower, upper in (
            ("m/z", selection.mz_min, selection.mz_max),
            ("RT", selection.rt_min, selection.rt_max),
        ):
            if lower is None and upper is None:
                continue
            matched = []
            for item in items:
                values = (
                    [item.details["target"]["m/z"]]
                    if field == "m/z"
                    else [r["RT"] for r in item.details["records"]]
                )
                numeric = [
                    v
                    for v in values
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                ]
                if field == "RT" and not numeric:
                    missing += 1
                if any(
                    (lower is None or v >= lower) and (upper is None or v <= upper)
                    for v in numeric
                ):
                    matched.append(item)
            items = matched
        return (
            items[selection.offset : selection.offset + selection.limit],
            len(self.targets),
            eligible,
            len(items),
            missing,
        )

    def load_render_items(self, items, *, capability, check_cancel):
        rendered = []
        for item in items:
            check_cancel()
            rendered.append(
                {
                    "id": item.serialized_ref,
                    "kind": "target",
                    "title": item.identifier,
                    "precursor_mz": item.details["target"]["m/z"],
                    "rt_apex_min": None,
                    "badge_label": "需先看" if item.is_attention else "已記錄",
                    "stratum": item.stratum,
                    "review_summary": item.details["review_summary"],
                    "details": item.details,
                }
            )
        return rendered
