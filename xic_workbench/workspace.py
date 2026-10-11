"""One local review session and its bounded, cancellable curve acquisitions."""

from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from xic_inspector.api import (
    InspectorCancelled,
    PreparedReport,
    SelectionRequest,
    generate_report,
    prepare_report,
    read_evidence_summary,
    resolve_source,
)
from xic_inspector.rendering.html_builder import write_report_atomically


class WorkspaceBusy(ValueError):
    """A running acquisition must finish or be cancelled first."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"請提供 {name}。")
    return value.strip()


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 1000:
        raise ValueError("請至少選擇一個樣本。")
    return tuple(dict.fromkeys(_text(v, "樣本 ID") for v in value))


class ReviewWorkspace:
    """Keep source selection and acquisition jobs outside the offline Inspector."""

    def __init__(
        self, *, defaults: dict[str, str] | None = None, config_dir: Path | None = None
    ) -> None:
        from xic_workbench.analysis_workspace import AnalysisWorkspace
        from xic_workbench.config_io import CONFIG_DIR

        self.defaults = dict(defaults or {})
        self.analysis = AnalysisWorkspace(config_dir or CONFIG_DIR, self.defaults)
        self._temporary = TemporaryDirectory(prefix="xic-workbench-")
        self._folder = Path(self._temporary.name)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xic-review"
        )
        self._cancel = threading.Event()
        self._report: Path | None = None
        self._page: PreparedReport | None = None
        self._job: dict[str, Any] | None = None
        self._detail: dict[str, Any] | None = None
        self._reports: dict[str, Path] = {}

    def state(self) -> dict[str, Any]:
        """Restore the current review selection and job after a page reload."""
        with self._lock:
            return copy.deepcopy(
                {
                    "page": self._page_response() if self._page is not None else None,
                    "detail": self._detail,
                    "job": self._job,
                }
            )

    def _require_idle(self) -> None:
        if self._job and self._job["status"] in {"running", "cancelling"}:
            raise WorkspaceBusy("曲線仍在載入；請等待完成或先取消。")

    def start_analysis(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            return self.analysis.jobs.start(kind, values)

    def analysis_action(self, action: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action not in {
                "open-local",
                "preferences",
                "method",
                "targeted/method",
                "istd",
                "targets/import",
            }:
                if self.analysis.jobs.busy:
                    raise WorkspaceBusy("分析仍在執行；完成或停止後才能修改設定。")
            return self.analysis.action(action, values)

    def open(self, source: Any) -> dict[str, Any]:
        path = resolve_source(
            Path(_text(source, "結果資料夾、Targeted XLSX 或 SQLite 檔案"))
        ).resolve()
        with self._lock:
            self._require_idle()
            summary = read_evidence_summary(path)
            sample_ids = tuple(
                sample["canonical_sample_identity_id"] for sample in summary["samples"]
            )
            if not sample_ids:
                raise ValueError("這份結果沒有可檢閱的樣本。")
            page = prepare_report(
                path,
                SelectionRequest(strategy="query", sample_ids=sample_ids, limit=25),
            )
            # Failed opens never displace the previously usable batch.
            self._report, self._page = path, page
            self._detail, self._job = None, None
            return self._page_response()

    def query(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "group_id",
            "target_id",
            "sample_id",
            "attention_only",
            "mz_min",
            "mz_max",
            "rt_min",
            "rt_max",
            "offset",
            "limit",
        }
        if set(values) - allowed:
            raise ValueError("查詢包含不支援的欄位。")
        with self._lock:
            self._require_idle()
            if self._report is None or self._page is None:
                raise ValueError("請先開啟一個批次。")
            group_id = values.get("group_id", "")
            if not isinstance(group_id, str):
                raise ValueError("分組 ID 必須是文字。")
            target_id = values.get("target_id", "")
            sample_id = values.get("sample_id", "")
            if not isinstance(target_id, str) or not isinstance(sample_id, str):
                raise ValueError("目標與樣本名稱必須是文字。")
            request = SelectionRequest(
                strategy="query",
                sample_ids=(sample_id,)
                if sample_id
                else tuple(
                    sample["canonical_sample_identity_id"]
                    for sample in self._page.payload["summary"]["samples"]
                ),
                group_ids=(group_id.strip(),) if group_id.strip() else (),
                target_ids=(target_id.strip(),) if target_id.strip() else (),
                **{
                    key: value
                    for key, value in values.items()
                    if key not in {"group_id", "target_id", "sample_id"}
                },
            )
            page = prepare_report(
                self._report, request, expected_source=self._page.source
            )
            self._page = page
            self._detail, self._job = None, None
            return self._page_response()

    def _page_response(self) -> dict[str, Any]:
        assert self._page is not None and self._report is not None
        page = self._page
        return {
            "source": str(self._report),
            "source_kind": page.payload["summary"].get("source_kind", "evidence"),
            "target_names": page.payload["summary"].get("target_names", []),
            "overview": page.payload["summary"].get("overview"),
            "all_samples": [
                {
                    "id": row["canonical_sample_identity_id"],
                    "label": row.get(
                        "sample_stem", row["canonical_sample_identity_id"]
                    ),
                }
                for row in page.payload["summary"]["samples"]
            ],
            "request_id": page.payload["summary"].get("processing_request_id", ""),
            "samples": page.payload["samples"],
            "run_total": page.run_total,
            "matched_count": page.matched_count,
            "selected_count": page.selected_count,
            "offset": page.payload["selection"]["offset"],
            "limit": page.payload["selection"]["limit"],
            "items": [self._item_summary(item) for item in page.payload["items"]],
        }

    @staticmethod
    def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
        # Translate source-owned identifiers and coordinates; never infer a group.
        source = item.get("details", {}).get("source", {})
        group_id = source.get("group_hypothesis_id")
        candidate_id = (
            source.get("candidate_id") if item["kind"] == "candidate" else None
        )
        observed = sorted(
            {
                witness["canonical_sample_identity_id"]
                for witness in item.get("witnesses", [])
                if witness.get("rt_apex_min") is not None
                and witness.get("precursor_mz") is not None
            }
        )
        return {
            key: item.get(key)
            for key in (
                "id",
                "kind",
                "title",
                "precursor_mz",
                "rt_apex_min",
                "badge_label",
            )
        } | {
            "group_id": group_id,
            "candidate_id": candidate_id,
            "observed_sample_ids": observed,
            "review_summary": item.get("review_summary", {}),
        }

    def _item(self, item_id: Any) -> dict[str, Any]:
        if self._page is not None:
            for item in self._page.payload["items"]:
                if item["id"] == item_id:
                    return item
        raise ValueError("選取項目已不在目前查詢頁；請重新選擇。")

    def detail(self, item_id: Any) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            item = self._item(item_id)
            assert self._page is not None
            payload = copy.deepcopy(self._page.payload)
            payload["items"] = [copy.deepcopy(item)]
            payload["selection"]["limit"] = 1
            payload["selection"]["workspace_item_id"] = item["id"]
            prepared = replace(self._page, payload=payload, selected_count=1)
            view_id = uuid.uuid4().hex
            path = self._folder / f"{view_id}.html"
            write_report_atomically(prepared.to_html(), path)
            self._reports[view_id] = path
            self._detail = {"view_id": view_id, "item": self._item_summary(item)}
            self._job = None
            return copy.deepcopy(self._detail)

    def start_curves(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"item_id", "sample_ids", "raw_dir", "dll_dir", "ppm", "padding_min"}
        if set(values) - allowed:
            raise ValueError("曲線要求包含不支援的欄位。")
        samples = _ids(values.get("sample_ids"))
        raw_dir = Path(_text(values.get("raw_dir"), "RAW 資料夾"))
        dll_dir = Path(_text(values.get("dll_dir"), "RawFileReader DLL 資料夾"))
        if not raw_dir.is_dir() or not dll_dir.is_dir():
            raise ValueError("RAW 或 DLL 資料夾不存在，請重新選擇來源位置。")
        with self._lock:
            self._require_idle()
            if self.analysis.jobs.busy:
                raise WorkspaceBusy("分析仍在執行；請完成或停止後再讀取診斷曲線。")
            item = self._item(values.get("item_id"))
            description = self._item_summary(item)
            if not description["group_id"] and not description["candidate_id"]:
                raise ValueError("此項目沒有可供擷取的分組或候選身份。")
            unsupported = set(samples) - set(description["observed_sample_ids"])
            if unsupported:
                raise ValueError("所選樣本缺少已記錄的擷取座標，無法自動取得此組曲線。")
            assert self._report is not None and self._page is not None
            if self._detail is None or self._detail["item"]["id"] != item["id"]:
                self.detail(item["id"])
            source, expected_source = self._report, self._page.source
            job_id = uuid.uuid4().hex
            self._cancel = threading.Event()
            self._job = {
                "id": job_id,
                "item_id": item["id"],
                "status": "running",
                "phase": "確認來源",
                "completed": 0,
                "total": None,
                "sample_ids": list(samples),
                "message": "正在確認來源並準備擷取。",
            }
            request = {
                "raw_dir": raw_dir,
                "dll_dir": dll_dir,
                "group_id": description["group_id"],
                "candidate_id": description["candidate_id"],
                "sample_ids": samples,
                "ppm": values.get("ppm", 5.0),
                "padding_min": values.get("padding_min", 0.5),
            }
            self._executor.submit(
                self._acquire_curves,
                job_id,
                source,
                expected_source,
                request,
                self._cancel,
            )
            return dict(self._job)

    def _acquire_curves(self, job_id, source, expected_source, request, cancel) -> None:
        def update(**values):
            with self._lock:
                assert self._job is not None
                self._job.update(values)

        try:
            from xic_workbench.acquisition import (
                CurveAcquisitionCancelled,
                export_explanation_curves,
            )
        except ImportError as exc:
            update(status="failed", phase="未完成", message=str(exc))
            return

        def acquired(completed, total, sample_id):
            update(
                phase="讀取 RAW", completed=completed, total=total, sample_id=sample_id
            )

        def rendered(progress):
            update(
                phase="準備檢閱頁", completed=progress.completed, total=progress.total
            )

        bundle_path, html_path = (
            self._folder / f"{job_id}.json",
            self._folder / f"{job_id}.html",
        )
        try:
            # Confirm the opened result still owns this request.
            prepare_report(
                source,
                SelectionRequest(
                    strategy="query",
                    limit=1,
                    sample_ids=request["sample_ids"],
                    group_ids=(request["group_id"],) if request["group_id"] else (),
                    candidate_ids=(request["candidate_id"],)
                    if request["candidate_id"]
                    else (),
                ),
                expected_source=expected_source,
                is_cancelled=cancel.is_set,
            )
            receipt = export_explanation_curves(
                source,
                bundle_path,
                **request,
                is_cancelled=cancel.is_set,
                on_progress=acquired,
            )
            generate_report(
                bundle_path,
                SelectionRequest(sample_ids=request["sample_ids"]),
                html_path,
                on_progress=rendered,
                is_cancelled=cancel.is_set,
            )
            with self._lock:
                if cancel.is_set():
                    raise CurveAcquisitionCancelled("已取消曲線載入。")
                self._reports[job_id] = html_path
                update(
                    status="succeeded",
                    phase="完成",
                    view_id=job_id,
                    message="診斷曲線已載入；當次分析紀錄與量值保持原樣。",
                    receipt=receipt,
                )
        except (CurveAcquisitionCancelled, InspectorCancelled):
            update(
                status="cancelled",
                phase="已取消",
                message="已取消；原有檢閱結果仍可使用。",
            )
        except Exception as exc:
            update(status="failed", phase="未完成", message=str(exc))

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._job is None or self._job["id"] != job_id:
                raise ValueError("找不到這次曲線工作。")
            return dict(self._job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self.job(job_id)
            assert self._job is not None
            if self._job["status"] == "running":
                self._cancel.set()
                self._job.update(status="cancelling", message="等待目前讀取結束後停止…")
            return dict(self._job)

    def report_path(self, view_id: str) -> Path:
        with self._lock:
            try:
                return self._reports[view_id]
            except KeyError:
                raise FileNotFoundError(
                    "檢閱頁已不存在，請重新開啟選取項目。"
                ) from None

    def close(self) -> None:
        self.analysis.close()
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._temporary.cleanup()
