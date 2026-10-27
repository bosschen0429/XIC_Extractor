"""One local review session and its bounded, cancellable curve acquisitions."""

from __future__ import annotations

import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from xic_extractor.diagnostics.explanation_review import REVIEW_SCHEMA_VERSION
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_inspector.api import (
    InspectorCancelled,
    PreparedReport,
    SelectionRequest,
    prepare_report,
    read_evidence_summary,
    resolve_source,
)
from xic_inspector.rendering.html_builder import (
    build_report_html,
    write_report_atomically,
)
from xic_inspector.sources.targeted_workbook import TargetedWorkbookLocator


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
        self._views: dict[str, Path] = {}

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
            sample_ids = tuple(sample["sample_key"] for sample in summary["samples"])
            if not sample_ids:
                raise ValueError("這份結果沒有可檢閱的樣本。")
            page = prepare_report(
                path,
                SelectionRequest(
                    strategy="query",
                    sample_ids=sample_ids,
                    limit=25,
                    review_scope="matrix"
                    if summary.get("schema_version") == REVIEW_SCHEMA_VERSION
                    else "all",
                ),
            )
            # Failed opens never displace the previously usable batch.
            self._report, self._page = path, page
            self._detail, self._job = None, None
            return self._page_response()

    def query(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "group_id",
            "target_id",
            "target_role",
            "sample_id",
            "attention_only",
            "nearest_mz",
            "mz_min",
            "mz_max",
            "rt_min",
            "rt_max",
            "offset",
            "limit",
            "review_scope",
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
                review_scope=values.get(
                    "review_scope",
                    self._page.payload["selection"].get("review_scope", "all"),
                ),
                sample_ids=(sample_id,)
                if sample_id
                else tuple(
                    sample["sample_key"]
                    for sample in self._page.payload["summary"]["samples"]
                ),
                group_ids=(group_id.strip(),) if group_id.strip() else (),
                target_ids=(target_id.strip(),) if target_id.strip() else (),
                target_role=None
                if values.get("target_role") == ""
                else values.get("target_role"),
                **{
                    key: value
                    for key, value in values.items()
                    if key
                    not in {
                        "group_id",
                        "target_id",
                        "target_role",
                        "sample_id",
                        "review_scope",
                    }
                },
            )
            page = prepare_report(
                self._report, request, expected_source=self._page.source
            )
            if (
                "overview" not in page.payload["summary"]
                and self._page.payload["summary"].get("overview", {}).get("kind")
                == "untargeted"
            ):
                page.payload["summary"]["overview"] = self._page.payload["summary"][
                    "overview"
                ]
            self._page = page
            self._detail, self._job = None, None
            return self._page_response()

    def _page_response(self) -> dict[str, Any]:
        assert self._page is not None and self._report is not None
        page = self._page
        artifact = None
        if isinstance(page.source, TargetedWorkbookLocator):
            artifact = self.analysis.register_artifact(page.source.workbook_path)
        elif isinstance(page.source, EvidenceLocator):
            artifact = self.analysis.register_artifact(page.source.matrix_path)
        response = {
            "source": str(self._report),
            "result_artifact": artifact,
            "source_kind": page.payload["summary"].get("source_kind", "evidence"),
            "target_names": page.payload["summary"].get("target_names", []),
            "target_role": page.payload["selection"].get("target_role"),
            "overview": page.payload["summary"].get("overview"),
            "review_scope": page.payload["selection"].get("review_scope", "all"),
            "all_samples": [
                {
                    "id": row["sample_key"],
                    "label": row.get("sample_stem", row["sample_key"]),
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
        selection = page.payload["selection"]
        if selection.get("nearest_mz") is not None:
            response.update(
                {
                    key: selection[key]
                    for key in ("nearest_mz", "missing_mz_count", "rt_min", "rt_max")
                }
            )
        return response

    @staticmethod
    def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
        # Translate source-owned identifiers and coordinates; never infer a group.
        source = item.get("details", {}).get("source", {})
        group_id = source.get("feature_id")
        candidate_id = (
            source.get("candidate_id") if item["kind"] == "candidate" else None
        )
        observed = sorted(
            {
                witness["sample_key"]
                for witness in item.get("witnesses", [])
                if witness.get("rt_apex_min") is not None
                and witness.get("precursor_mz") is not None
            }
        )
        target_id = None
        curve_samples = observed
        numeric_samples = [
            s["sample_id"]
            for s in item.get("samples", [])
            if isinstance(s.get("quantitative_value"), (int, float))
        ]
        if item["kind"] == "target":
            target_id = item["details"]["target"]["Label"]
            # A known Target can be inspected even when its saved result is ND.
            curve_samples = [row["SampleName"] for row in item["details"]["records"]]
        summary = {
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
            "target_id": target_id,
            "observed_sample_ids": observed,
            "curve_sample_ids": curve_samples,
            "review_summary": item.get("review_summary", {}),
            "numeric_count": len(numeric_samples),
            "numeric_sample_ids": numeric_samples,
            "unresolved_count": sum(
                any(c.get("variant") == "unresolved" for c in s.get("refs", []))
                for s in item.get("samples", [])
            ),
        }
        if "mz_distance" in item:
            summary["mz_distance"] = item["mz_distance"]
        return summary

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
            prepared = replace(self._page, payload=payload, selected_count=1)
            view_id = uuid.uuid4().hex
            self._store_view(view_id, prepared)
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
            if not any(
                description[k] for k in ("group_id", "candidate_id", "target_id")
            ):
                raise ValueError("此項目沒有可供擷取的分組或候選身份。")
            unsupported = set(samples) - set(description["curve_sample_ids"])
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
            if description["target_id"]:
                request.pop("group_id")
                request.pop("candidate_id")
                request["target_id"] = description["target_id"]
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

        bundle_path = self._folder / f"{job_id}.json"
        try:
            # Confirm the opened result still owns this request.
            prepare_report(
                source,
                SelectionRequest(
                    strategy="query",
                    limit=1,
                    sample_ids=request["sample_ids"],
                    target_ids=(request["target_id"],)
                    if request.get("target_id")
                    else (),
                    group_ids=(request["group_id"],) if request.get("group_id") else (),
                    candidate_ids=(request["candidate_id"],)
                    if request.get("candidate_id")
                    else (),
                ),
                expected_source=expected_source,
                is_cancelled=cancel.is_set,
            )
            if request.get("target_id"):
                from xic_workbench.targeted_curves import export_targeted_curves

                receipt = export_targeted_curves(
                    source,
                    bundle_path,
                    **request,
                    expected_source=expected_source,
                    is_cancelled=cancel.is_set,
                    on_progress=acquired,
                )
            else:
                receipt = export_explanation_curves(
                    source,
                    bundle_path,
                    **request,
                    is_cancelled=cancel.is_set,
                    on_progress=acquired,
                )
            prepared = prepare_report(
                bundle_path,
                SelectionRequest(sample_ids=request["sample_ids"]),
                on_progress=rendered,
                is_cancelled=cancel.is_set,
            )
            with self._lock:
                if cancel.is_set():
                    raise CurveAcquisitionCancelled("已取消曲線載入。")
                self._store_view(job_id, prepared)
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

    def _store_view(self, view_id: str, prepared: PreparedReport) -> None:
        # Session history stays on disk, not in an accumulating curve-payload cache.
        path = self._folder / f"{view_id}.view.json"
        try:
            with path.open("w", encoding="utf-8") as stream:
                json.dump(
                    prepared.render_data(), stream, ensure_ascii=False, allow_nan=False
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self._views[view_id] = path

    def _view_snapshot(self, view_id: str) -> dict[str, Any]:
        try:
            path = self._views[view_id]
        except KeyError:
            raise FileNotFoundError("檢閱頁已不存在，請重新開啟選取項目。") from None
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def view_payload(self, view_id: str) -> dict[str, Any]:
        """Read the selected owner facts captured when this view was prepared."""
        with self._lock:
            return self._view_snapshot(view_id)["payload_data"]

    def report_path(self, view_id: str) -> Path:
        """Package an immutable view as offline HTML only when requested."""
        with self._lock:
            snapshot = self._view_snapshot(view_id)
            path = self._folder / f"{view_id}.html"
            if not path.is_file():
                write_report_atomically(build_report_html(**snapshot), path)
            return path

    def close(self) -> None:
        self.analysis.close()
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._temporary.cleanup()
