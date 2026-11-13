"""One local review session and its curve views.

Saved curves are shown at once; a RAW read runs as one cancellable job, and any
new review action cancels it.
"""

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

from xic_extractor.alignment_explanation.errors import (
    ExplanationError,
    ExplanationUnavailableError,
)
from xic_extractor.alignment_explanation.reader import (
    ReviewCurves,
    read_review_curves,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_extractor.matrix_curation.published import (
    PublishedCuration,
    is_curation,
    latest_curation,
    read_published_curation,
)
from xic_inspector.api import (
    InspectorCancelled,
    PreparedReport,
    SelectionRequest,
    prepare_curve_view,
    prepare_report,
    read_evidence_summary,
    resolve_source,
)
from xic_inspector.rendering.html_builder import (
    build_report_html,
    write_report_atomically,
)
from xic_inspector.sources.targeted_workbook import TargetedWorkbookLocator
from xic_workbench.analysis_workspace import AnalysisWorkspace
from xic_workbench.errors import WorkspaceBusy as WorkspaceBusy


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

    def __init__(self, analysis: AnalysisWorkspace) -> None:
        self.defaults = analysis.defaults
        self.analysis = analysis
        self._temporary = TemporaryDirectory(prefix="xic-workbench-")
        self._folder = Path(self._temporary.name)
        self._lock = threading.RLock()
        self._closing = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xic-review"
        )
        self._cancel = threading.Event()
        self._report: Path | None = None
        # Targeted RAW preparation is reused while one workbook stays open.
        self._targeted_raws: dict[Path, Any] = {}
        self._page: PreparedReport | None = None
        # The curation the open Untargeted result follows, fixed at open.
        self._curation: PublishedCuration | None = None
        # Curve locations of the opened detail item, read once per selection.
        self._curves: ReviewCurves | None = None
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

    def _require_open(self) -> None:
        if self._closing:
            raise WorkspaceBusy("工作區正在結束，請重新開啟。")

    def _replace_job(self) -> None:
        """A new review action replaces the curve job; a running read stops."""
        self._cancel.set()
        self._job = None

    @property
    def closing(self) -> bool:
        with self._lock:
            return self._closing

    def request_shutdown(self, *, preserve_unseen_completion: bool = False) -> bool:
        with self._lock:
            if (
                self._closing
                or (self._job and self._job["status"] in {"running", "cancelling"})
                or self.analysis.jobs.busy
                or (
                    preserve_unseen_completion
                    and self.analysis.jobs.has_unseen_completion
                )
            ):
                return False
            self._closing = True
            return True

    def start_analysis(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            started = self.analysis.jobs.start(kind, values)
            self._replace_job()
            return started

    def analysis_action(self, action: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action not in {
                "open-local",
                "preferences",
                "method",
                "targeted/method",
                "istd",
                "targets/import",
                "thermo-reader/status",
                "paths/validate",
            }:
                if self.analysis.jobs.busy:
                    raise WorkspaceBusy("分析仍在執行；完成或停止後才能修改設定。")
            return self.analysis.action(action, values)

    @staticmethod
    def evidence_summary(source: str) -> dict[str, Any]:
        return read_evidence_summary(resolve_source(Path(source)))

    def preview_curation(self, values: dict[str, Any]) -> dict[str, Any]:
        # Preview is read-only: do not hold the mutable workspace lock.
        return self.analysis.curation.preview_curation(self.analysis.config_dir, values)

    def open(self, source: Any) -> dict[str, Any]:
        given = Path(_text(source, "結果資料夾、Targeted XLSX 或 SQLite 檔案"))
        curation = None
        if given.is_dir() and is_curation(given):
            # An opened curation keeps its own original result and decisions.
            curation = read_published_curation(given)
            if not curation.source_dir.is_dir():
                raise ValueError(f"找不到這次整理的原始結果：{curation.source_dir}")
            given = curation.source_dir
        path = resolve_source(given).resolve()
        if curation is None and path.suffix.lower() == ".sqlite":
            folder = latest_curation(path.parent)
            curation = read_published_curation(folder) if folder else None
        with self._lock:
            self._require_open()
            try:
                summary = read_evidence_summary(path)
            except ExplanationUnavailableError:
                raise
            except ExplanationError as exc:
                # The technical reason travels as detail, not as the headline.
                rerun = "Alignment" if path.suffix.lower() == ".sqlite" else "分析"
                raise ValueError(
                    "無法讀取這份結果：可能由舊版本產生或檔案已損壞，"
                    f"請用目前版本重新執行 {rerun}。"
                ) from exc
            sample_ids = tuple(sample["sample_key"] for sample in summary["samples"])
            if not sample_ids:
                raise ValueError("這份結果沒有可檢閱的樣本。")
            reviewed = summary.get("retention") == "review"
            # A full audit is browsed as published, without Matrix scopes.
            curation = curation if reviewed else None
            page = prepare_report(
                path,
                SelectionRequest(
                    strategy="query",
                    sample_ids=sample_ids,
                    limit=25,
                    review_scope="matrix" if reviewed else "all",
                    curation_removed_ids=_removed_ids(curation),
                ),
            )
            # Failed opens never displace the previously usable batch.
            self._report, self._page, self._curation = path, page, curation
            self._targeted_raws = {}
            self._replace_job()
            self._detail, self._curves = None, None
            if isinstance(page.source, EvidenceLocator):
                # Load the curve preparation code before the first selection.
                self._executor.submit(_load_review_curves)
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
            self._require_open()
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
                curation_removed_ids=_removed_ids(self._curation),
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
            self._replace_job()
            self._detail, self._curves = None, None
            return self._page_response()

    def _page_response(self) -> dict[str, Any]:
        assert self._page is not None and self._report is not None
        page = self._page
        artifact = None
        curatable = False
        if isinstance(page.source, TargetedWorkbookLocator):
            artifact = self.analysis.register_artifact(page.source.workbook_path)
        elif isinstance(page.source, EvidenceLocator):
            artifact = self.analysis.register_artifact(
                self._curation.matrix_path
                if self._curation
                else page.source.matrix_path
            )
            curatable = _curatable(page.source.matrix_path.parent)
        response = {
            "source": str(self._report),
            "curation": {
                "name": self._curation.folder.name,
                "path": str(self._curation.folder),
            }
            if self._curation
            else None,
            "result_artifact": artifact,
            # The result page opens matrix curation only where it can run.
            "curatable": curatable,
            "source_kind": page.payload["summary"].get("source_kind", "evidence"),
            "target_names": page.payload["summary"].get("target_names", []),
            "target_role": page.payload["selection"].get("target_role"),
            "overview": page.payload["summary"].get("overview"),
            "review_scope": page.payload["selection"].get("review_scope", "all"),
            "all_samples": [
                {
                    "id": row["sample_key"],
                    "label": row.get("sample_stem", row["sample_key"]),
                    **({"group": row["group"]} if "group" in row else {}),
                }
                for row in page.payload["summary"]["samples"]
            ],
            "samples": page.payload["samples"],
            "run_total": page.run_total,
            "matched_count": page.matched_count,
            "selected_count": page.selected_count,
            "offset": page.payload["selection"]["offset"],
            "limit": page.payload["selection"]["limit"],
            "items": [
                self._item_summary(item, page.source, self._curation)
                for item in page.payload["items"]
            ],
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
    def _item_summary(
        item: dict[str, Any],
        source: object,
        curation: PublishedCuration | None,
    ) -> dict[str, Any]:
        # Translate source-owned identifiers and coordinates; never infer a group.
        # An Alignment evidence item's ref ends with its row or candidate ID.
        identifier = (
            json.loads(item["id"])[-1] if isinstance(source, EvidenceLocator) else None
        )
        group_id = identifier if item["kind"] == "row" else None
        candidate_id = identifier if item["kind"] == "candidate" else None
        observed = sorted(
            {
                witness["sample_key"]
                for witness in item.get("witnesses", [])
                if witness.get("rt_apex_min") is not None
                and witness.get("precursor_mz") is not None
            }
        )
        target_id = None
        # A group with any observed apex can be read for every member: samples
        # without their own apex are acquired at the group's position.
        curve_samples = (
            sorted(s["sample_id"] for s in item.get("samples", []))
            if group_id and observed
            else observed
        )
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
            "unresolved_count": item.get("group_counts", {}).get("unresolved", 0),
        }
        if "mz_distance" in item:
            summary["mz_distance"] = item["mz_distance"]
        if curation and group_id in curation.removed:
            summary["curation_reason"] = curation.removed[group_id]
        return summary

    def _item(self, item_id: Any) -> dict[str, Any]:
        if self._page is not None:
            for item in self._page.payload["items"]:
                if item["id"] == item_id:
                    return item
        raise ValueError("選取項目已不在目前查詢頁；請重新選擇。")

    def detail(self, item_id: Any) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            item = self._item(item_id)
            assert self._page is not None and self._report is not None
            self._replace_job()
            payload = copy.deepcopy(self._page.payload)
            payload["items"] = [copy.deepcopy(item)]
            payload["selection"]["limit"] = 1
            prepared = replace(self._page, payload=payload, selected_count=1)
            view_id = uuid.uuid4().hex
            self._store_view(view_id, prepared)
            summary = self._item_summary(item, self._page.source, None)
            self._curves = None
            if summary["group_id"] or summary["candidate_id"]:
                curves = read_review_curves(
                    self._report,
                    feature_id=summary["group_id"],
                    candidate_id=summary["candidate_id"],
                )
                self._curves = curves
                # Samples with a location can be read; saved ones show at once.
                summary["curve_sample_ids"] = sorted(
                    {location.sample_key for location in curves.locations}
                )
                summary["saved_curve_sample_ids"] = sorted(
                    {
                        location.sample_key
                        for location in curves.locations
                        if location.curve is not None
                    }
                )
            self._detail = {"view_id": view_id, "item": summary}
            return copy.deepcopy(self._detail)

    def start_curves(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"item_id", "sample_ids", "raw_dir", "dll_dir", "padding_min"}
        if set(values) - allowed:
            raise ValueError("曲線要求包含不支援的欄位。")
        samples = _ids(values.get("sample_ids"))
        with self._lock:
            self._require_open()
            if self._detail is None or self._detail["item"]["id"] != values.get(
                "item_id"
            ):
                self.detail(values.get("item_id"))
            assert (
                self._detail is not None
                and self._report is not None
                and self._page is not None
            )
            description = self._detail["item"]
            if not any(
                description[k] for k in ("group_id", "candidate_id", "target_id")
            ):
                raise ValueError("此項目沒有可供擷取的分組或候選身份。")
            if set(samples) - set(description["curve_sample_ids"]):
                raise ValueError("所選樣本缺少已記錄的擷取座標，無法自動取得此組曲線。")
            self._replace_job()
            curves = self._curves
            raw_needed = curves is None or curves.needs_raw(samples)
            raw_dir = dll_dir = None
            if raw_needed:
                raw_dir = Path(_text(values.get("raw_dir"), "RAW 資料夾"))
                dll_dir = Path(_text(values.get("dll_dir"), "RawFileReader DLL 資料夾"))
                if not raw_dir.is_dir() or not dll_dir.is_dir():
                    raise ValueError("RAW 或 DLL 資料夾不存在，請重新選擇來源位置。")
                if self.analysis.jobs.busy:
                    raise WorkspaceBusy("分析仍在執行；請完成或停止後再讀取 RAW 曲線。")
            job_id = uuid.uuid4().hex
            self._cancel = cancel = threading.Event()
            self._job = {
                "id": job_id,
                "item_id": description["id"],
                "status": "running",
                "phase": "讀取 RAW" if raw_needed else "準備曲線",
                "completed": 0,
                "total": None,
                "sample_ids": list(samples),
                "message": "正在讀取所選樣本的 RAW。"
                if raw_needed
                else "正在顯示保存的曲線。",
            }
            request = {
                "report": self._report,
                "source": self._page.source,
                "description": description,
                "review_item": copy.deepcopy(self._item(description["id"])),
                "sample_catalog": copy.deepcopy(self._page.payload["samples"]),
                "curves": curves,
                "samples": samples,
                "raw_dir": raw_dir,
                "dll_dir": dll_dir,
                "padding_min": values.get("padding_min", 0.5),
            }
        if raw_needed:
            self._executor.submit(self._read_curves, job_id, request, cancel)
        else:
            # Saved curves need no RAW: show them within this request.
            self._read_curves(job_id, request, cancel)
        return self.job(job_id)

    def _read_curves(
        self, job_id: str, request: dict[str, Any], cancel: threading.Event
    ) -> None:
        def update(**values: Any) -> None:
            with self._lock:
                # A replaced job's worker no longer reports.
                if self._job is not None and self._job["id"] == job_id:
                    self._job.update(values)

        def progress(completed: int, total: int, sample: str) -> None:
            update(phase="讀取 RAW", completed=completed, total=total, sample_id=sample)

        try:
            from xic_workbench.review_curves import CurveAcquisitionCancelled
        except ImportError as exc:
            update(status="failed", phase="未完成", message=str(exc))
            return
        description = request["description"]
        try:
            unusable: list[str] = []
            sample_groups: dict[str, str] = {}
            if description["target_id"]:
                from xic_workbench.targeted_curves import export_targeted_curves

                bundle_path = self._folder / f"{job_id}.json"
                export_targeted_curves(
                    request["report"],
                    bundle_path,
                    raw_dir=request["raw_dir"],
                    dll_dir=request["dll_dir"],
                    target_id=description["target_id"],
                    sample_ids=request["samples"],
                    padding_min=request["padding_min"],
                    expected_source=request["source"],
                    is_cancelled=cancel.is_set,
                    on_progress=progress,
                    verified_raws=self._targeted_raws,
                )
                prepared = prepare_report(
                    bundle_path,
                    SelectionRequest(sample_ids=request["samples"]),
                    is_cancelled=cancel.is_set,
                )
            else:
                from xic_workbench.review_curves import (
                    RunSettings,
                    review_curve_bundle,
                )
                from xic_workbench.samples import review_sample_groups

                curves = request["curves"]
                bundle, unusable = review_curve_bundle(
                    request["report"],
                    curves,
                    key=description["group_id"] or description["candidate_id"],
                    sample_ids=request["samples"],
                    raw_dir=request["raw_dir"],
                    dll_dir=request["dll_dir"],
                    padding_min=request["padding_min"],
                    is_cancelled=cancel.is_set,
                    on_progress=progress,
                )
                prepared = prepare_curve_view(
                    bundle, request["samples"], is_cancelled=cancel.is_set
                )
                # Curves and the saved result are one review, including in the
                # offline export. Loading fewer curves does not shrink its cells.
                curve_item = prepared.payload["items"][0]
                prepared.payload["items"] = [
                    {
                        **request["review_item"],
                        "curves": curve_item["curves"],
                        "witnesses": curve_item["witnesses"],
                        "review_rt_window": curve_item["review_rt_window"],
                    }
                ]
                prepared.payload["samples"] = request["sample_catalog"]
                # Sample roles are confirmed per RAW folder.
                folder = request["raw_dir"] or RunSettings(request["report"]).raw_dir
                if folder is not None:
                    sample_groups = review_sample_groups(
                        self.analysis.config_dir,
                        folder,
                        [
                            {
                                "sample_key": location.sample_key,
                                "sample_stem": location.sample_stem,
                            }
                            for location in curves.locations
                        ],
                    )
            with self._lock:
                if cancel.is_set():
                    raise CurveAcquisitionCancelled("已取消曲線載入。")
                self._store_view(job_id, prepared, sample_groups=sample_groups)
                update(
                    status="succeeded",
                    phase="完成",
                    view_id=job_id,
                    message="曲線已載入；分析結果與量值保持原樣。",
                    unusable_samples=unusable,
                )
        except CurveAcquisitionCancelled, InspectorCancelled:
            update(
                status="cancelled",
                phase="已取消",
                message="已取消；原有檢閱結果仍可使用。",
            )
        except ExplanationError as exc:
            update(status="failed", phase="未完成", message=str(exc))
        except Exception as exc:
            update(
                status="failed",
                phase="未完成",
                message="讀取曲線時發生錯誤。",
                detail=f"{type(exc).__name__}: {exc}",
            )

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
                self._job.update(status="cancelling", message="正在停止讀取…")
            return dict(self._job)

    def _store_view(
        self,
        view_id: str,
        prepared: PreparedReport,
        *,
        sample_groups: dict[str, str] | None = None,
    ) -> None:
        # Session history stays on disk, not in an accumulating curve-payload cache.
        path = self._folder / f"{view_id}.view.json"
        snapshot = prepared.render_data()
        if sample_groups:
            # Freeze display annotations with this view, separately from source
            # owner facts. Offline rendering does not consume this context.
            snapshot["sample_groups"] = sample_groups
        try:
            with path.open("w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False)
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
        """Read captured owner facts with the interactive view's display groups."""
        with self._lock:
            snapshot = self._view_snapshot(view_id)
            payload = snapshot["payload_data"]
            if "sample_groups" in snapshot:
                payload["sample_groups"] = snapshot["sample_groups"]
            return payload

    def report_path(self, view_id: str) -> Path:
        """Package an immutable view as offline HTML only when requested."""
        with self._lock:
            snapshot = self._view_snapshot(view_id)
            snapshot.pop("sample_groups", None)
            path = self._folder / f"{view_id}.html"
            if not path.is_file():
                write_report_atomically(build_report_html(**snapshot), path)
            return path

    def close(self) -> None:
        self.analysis.close()
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._temporary.cleanup()


def _removed_ids(curation: PublishedCuration | None) -> tuple[str, ...]:
    return tuple(sorted(curation.removed)) if curation else ()


def _load_review_curves() -> None:
    import xic_workbench.review_curves  # noqa: F401


def _curatable(folder: Path) -> bool:
    from xic_extractor.matrix_curation.source import locate_curation_source

    try:
        locate_curation_source(folder)
    except ValueError:
        return False
    return True
