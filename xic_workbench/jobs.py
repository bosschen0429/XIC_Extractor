"""Background execution for the local analysis and export interface."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from xic_workbench.errors import WorkspaceBusy


def new_run_folder(root: Path, kind: str, raw_dir: str) -> Path:
    """Give each analysis its own folder under the chosen location.

    Earlier results are never overwritten; the name sorts by start time and
    says which analysis of which RAW folder produced it.
    """
    name = Path(raw_dir.strip().rstrip("\\/")).name if raw_dir.strip() else ""
    parts = (time.strftime("%Y%m%d_%H%M%S"), kind, name)
    base = "_".join(part for part in parts if part)
    root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        folder = root / (base if attempt == 1 else f"{base}_{attempt}")
        try:
            folder.mkdir()
        except FileExistsError:
            attempt += 1
            continue
        return folder


class AnalysisJobs:
    """Retain observable job state while application owners execute and publish."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xic-analysis"
        )
        self._current: dict[str, Any] | None = None
        self._cancel = threading.Event()
        self._runner: Any = None
        self._results: dict[str, dict[str, Any]] = {}
        self._observed_completion: str | None = None

    @property
    def has_unseen_completion(self) -> bool:
        with self._lock:
            return bool(
                self._current
                and not self.busy
                and self._current["job_id"] != self._observed_completion
            )

    def acknowledge_completion(self, job_id: str) -> None:
        with self._lock:
            if self._current and self._current["job_id"] == job_id and not self.busy:
                self._observed_completion = job_id

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(
                self._current and self._current["status"] in {"running", "cancelling"}
            )

    def state(self) -> dict[str, Any]:
        with self._lock:
            job = copy.deepcopy(self._current)
            if job is not None:
                end = job.get("finished_at", time.time())
                job["elapsed_seconds"] = max(0, end - job["started_at"])
                if self._runner is not None:
                    job["stage"] = self._runner.stage
                    if job["kind"] == "targeted":
                        job["sample_summary"] = copy.deepcopy(
                            self._runner.sample_summary
                        )
                    job["output_dir"] = str(self._runner.output_dir)
                    job["available_outputs"] = [
                        {"label": label, "path": str(path)}
                        for label, path in self._runner.available_outputs
                    ]
            return {"job": job, "results": copy.deepcopy(self._results)}

    def start(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"targeted", "untargeted", "samples", "curation", "inspector"}:
            raise ValueError("不支援此工作類型。")
        with self._lock:
            if self.busy:
                raise WorkspaceBusy("工作仍在執行；請等待完成或先停止。")
            self._cancel = threading.Event()
            self._runner = None
            values = copy.deepcopy(values)
            if kind == "targeted":
                from xic_extractor.configuration.loader import default_output_dir
                from xic_workbench.config_io import (
                    write_preferences,
                    write_work,
                )
                from xic_workbench.targeted import TargetedRun

                write_work(
                    values["settings"],
                    values["targets"],
                    config_dir=self.config_dir,
                    identification_methods=values.get("identification_methods"),
                )
                output_root = str(values.get("output_dir") or "").strip()
                if output_root:
                    write_preferences(
                        {"targeted_output_dir": output_root}, config_dir=self.config_dir
                    )
                from xic_workbench.samples import sample_annotations_path

                data_dir = str(values["settings"].get("data_dir") or "").strip()
                root = (
                    Path(output_root)
                    if output_root
                    else default_output_dir(self.config_dir)
                )
                output_dir = new_run_folder(root, kind, data_dir)
                annotations = (
                    sample_annotations_path(self.config_dir, Path(data_dir))
                    if data_dir
                    else None
                )
                self._runner = TargetedRun(
                    self.config_dir,
                    output_dir=output_dir,
                    sample_annotations=annotations
                    if annotations is not None and annotations.is_file()
                    else None,
                    progress=self._progress,
                    should_cancel=self._cancel.is_set,
                )
            elif kind == "untargeted":
                self._runner = self._prepare_untargeted(values)
            self._current = {
                "job_id": uuid.uuid4().hex,
                "kind": kind,
                "status": "running",
                "stage": "準備執行",
                "completed": 0,
                "total": 0,
                "message": "",
                "started_at": time.time(),
                "available_outputs": [],
                "cancellable": kind
                in {"targeted", "untargeted", "inspector", "samples"},
            }
            if kind == "samples":
                # Both flows read samples; the job card follows the asking page.
                self._current["page"] = values.get("page") or "untargeted"
            self._executor.submit(self._execute, kind, values)
            return self.state()

    def remember_samples(
        self, raw_dir: str, rows: list[dict[str, Any]], overview: dict[str, Any]
    ) -> None:
        """Keep the restorable sample result in step with confirmed edits."""
        with self._lock:
            cached = self._results.get("samples")
            if cached and cached.get("input_paths", {}).get("raw_dir") == raw_dir:
                cached["samples"] = copy.deepcopy(rows)
                cached["overview"] = copy.deepcopy(overview)
                cached["summary"]["annotation_source"] = "confirmed"

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._current is None or self._current["job_id"] != job_id:
                raise ValueError("這個工作已不是目前工作。")
            if self.busy:
                if not self._current["cancellable"]:
                    raise ValueError("此匯出需等待目前寫入完成。")
                self._cancel.set()
                self._current["status"] = "cancelling"
            return self.state()

    def _progress(self, completed: int, total: int, message: str) -> None:
        with self._lock:
            assert self._current is not None
            self._current.update(completed=completed, total=total, message=message)

    def _execute(self, kind: str, values: dict[str, Any]) -> None:
        try:
            result = self._run(kind, values)
            with self._lock:
                assert self._current is not None
                self._current.update(
                    status="succeeded", result=result, finished_at=time.time()
                )
                self._results[kind] = copy.deepcopy(result)
        except InterruptedError as exc:
            with self._lock:
                assert self._current is not None
                self._current.update(
                    status="cancelled",
                    error=str(exc) or "工作已停止。",
                    finished_at=time.time(),
                )
        except Exception as exc:
            with self._lock:
                assert self._current is not None
                self._current.update(
                    status="failed",
                    error=str(exc) or type(exc).__name__,
                    finished_at=time.time(),
                )

    def _prepare_untargeted(self, values: dict[str, Any]) -> Any:
        from xic_workbench.samples import (
            apply_raw_chronology,
            read_batch_samples,
            sample_annotations_path,
        )
        from xic_workbench.untargeted import UntargetedRun, parse_request
        from xic_workbench.untargeted_config import write_untargeted_config

        if values.get("raw_dir") and values.get("dll_dir"):
            raw_dir = Path(values["raw_dir"])
            # A folder's confirmed metadata decides the run, whatever rows the
            # page still holds; without annotations the alignment owner resolves
            # default metadata.
            if sample_annotations_path(self.config_dir, raw_dir).is_file() or (
                values.get("samples") is None and values.get("sequence_source")
            ):
                values["samples"] = read_batch_samples(
                    self.config_dir,
                    raw_dir,
                    dll_dir=Path(values["dll_dir"]),
                    sequence_source=Path(values["sequence_source"])
                    if values.get("sequence_source")
                    else None,
                )["samples"]
            elif isinstance(values.get("samples"), list):
                values["samples"] = apply_raw_chronology(
                    values["samples"], raw_dir=raw_dir, dll_dir=Path(values["dll_dir"])
                )
        from xic_extractor.configuration.loader import default_output_dir

        # The saved setting stays the location; this run writes a new folder in it.
        output_root = str(values.get("output_dir") or "").strip()
        root = Path(output_root) if output_root else default_output_dir(self.config_dir)
        run_dir = new_run_folder(root, "untargeted", str(values.get("raw_dir") or ""))
        try:
            request = parse_request({**values, "output_dir": str(run_dir)})
        except Exception:
            run_dir.rmdir()
            raise
        from xic_workbench.config_io import write_dll_dir

        runner = UntargetedRun(
            request, progress=self._progress, should_cancel=self._cancel.is_set
        )
        write_dll_dir(values["dll_dir"], config_dir=self.config_dir)
        write_untargeted_config(values, config_dir=self.config_dir)
        return runner

    def _run(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        if kind in {"targeted", "untargeted"}:
            result = self._runner.execute()
            inputs = values["settings"] if kind == "targeted" else values
            # Bind review acquisition to this run, not the next editable batch.
            result["review_paths"] = {
                "raw_dir": str(
                    inputs.get("data_dir" if kind == "targeted" else "raw_dir") or ""
                ),
                "dll_dir": str(inputs.get("dll_dir") or ""),
                "sequence_source": str(
                    inputs.get(
                        "injection_order_source"
                        if kind == "targeted"
                        else "sequence_source"
                    )
                    or ""
                ),
            }
            return result
        if kind == "samples":
            from xic_workbench.samples import read_batch_samples

            result = read_batch_samples(
                self.config_dir,
                Path(values["raw_dir"]),
                dll_dir=Path(values["dll_dir"]) if values.get("dll_dir") else None,
                sequence_source=Path(values["sequence_source"])
                if values.get("sequence_source")
                else None,
                import_sequence=bool(values.get("import_sequence")),
                read_headers=bool(values.get("dll_dir")),
                progress=self._progress,
                should_cancel=self._cancel.is_set,
            )
            result["input_paths"] = {
                key: str(values.get(key) or "")
                for key in ("raw_dir", "dll_dir", "sequence_source")
            }
            result["page"] = values.get("page") or "untargeted"
            return result
        if kind == "curation":
            from xic_workbench.matrix_curation import run_curation

            return run_curation(self.config_dir, values)
        from dataclasses import asdict

        from xic_extractor.evidence_artifacts import EvidenceLocator
        from xic_inspector.api import (
            InspectorCancelled,
            SelectionRequest,
            generate_report,
        )

        expected_source = self._results.get("untargeted", {}).get("evidence")
        if not isinstance(expected_source, EvidenceLocator):
            raise ValueError("Inspector 匯出需要目前已成功發布的 Untargeted 證據。")
        try:
            report_result = generate_report(
                Path(values["source"]),
                SelectionRequest(**values["selection"]),
                Path(values["output"]),
                expected_source=expected_source,
                is_cancelled=self._cancel.is_set,
                on_progress=lambda event: self._progress(
                    event.completed, event.total or 0, event.stage
                ),
            )
        except InspectorCancelled as exc:
            raise InterruptedError(str(exc)) from exc
        return {**asdict(report_result), "output": str(values["output"])}

    def close(self) -> None:
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
