"""Background execution for the local analysis and export interface."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from xic_workbench.workspace import WorkspaceBusy


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
                    job["output_dir"] = str(self._runner.output_dir)
                    job["available_outputs"] = [
                        {"label": label, "path": str(path)}
                        for label, path in self._runner.available_outputs
                    ]
            return {"job": job, "results": copy.deepcopy(self._results)}

    def start(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"targeted", "untargeted", "samples", "quality", "inspector"}:
            raise ValueError("不支援此工作類型。")
        with self._lock:
            if self.busy:
                raise WorkspaceBusy("工作仍在執行；請等待完成或先停止。")
            self._cancel = threading.Event()
            self._runner = None
            values = copy.deepcopy(values)
            if kind == "targeted":
                from xic_workbench.config_io import (
                    write_preferences,
                    write_settings,
                    write_targets,
                )
                from xic_workbench.targeted import TargetedRun

                write_settings(values["settings"], config_dir=self.config_dir)
                write_targets(values["targets"], config_dir=self.config_dir)
                output_dir = values.get("output_dir") or None
                if output_dir is not None:
                    write_preferences(
                        {"targeted_output_dir": output_dir}, config_dir=self.config_dir
                    )
                self._runner = TargetedRun(
                    self.config_dir,
                    output_dir=Path(output_dir) if output_dir else None,
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
                "cancellable": kind in {"targeted", "untargeted", "inspector"},
            }
            self._executor.submit(self._execute, kind, values)
            return self.state()

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
        from xic_extractor.configuration.execution_profile import (
            load_execution_profile,
        )
        from xic_workbench.discovery_config_io import write_discovery_config
        from xic_workbench.samples import apply_raw_chronology, load_samples
        from xic_workbench.untargeted import UntargetedRun, parse_request

        if (
            values.get("samples") is None
            and values.get("raw_dir")
            and values.get("dll_dir")
            and values.get("mode", "full") in {"full", "align_only"}
            and (values.get("sequence_source") or values.get("custom_roles"))
        ):
            sample_result = load_samples(
                Path(values["raw_dir"]),
                dll_dir=Path(values["dll_dir"]),
                sequence_source=Path(values["sequence_source"])
                if values.get("sequence_source")
                else None,
                custom_roles=values.get("custom_roles"),
            )
            values["samples"] = sample_result["samples"]
        elif (
            isinstance(values.get("samples"), list)
            and values.get("raw_dir")
            and values.get("dll_dir")
            and values.get("mode", "full") in {"full", "align_only"}
        ):
            values["samples"] = apply_raw_chronology(
                values["samples"],
                raw_dir=Path(values["raw_dir"]),
                dll_dir=Path(values["dll_dir"]),
            )
        if values.get("execution_profile") is None:
            profile = load_execution_profile(self.config_dir / "settings.csv")
            values["execution_profile"] = {
                "parallel_workers": profile.parallel_workers,
                "alignment_workers": profile.alignment_workers,
            }
        request = parse_request(values)
        from xic_workbench.config_io import write_dll_dir

        runner = UntargetedRun(
            request, progress=self._progress, should_cancel=self._cancel.is_set
        )
        write_dll_dir(values["dll_dir"], config_dir=self.config_dir)
        write_discovery_config(values, config_dir=self.config_dir)
        return runner

    def _run(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        if kind == "targeted":
            return self._runner.execute()
        if kind == "untargeted":
            return self._runner.execute()
        if kind == "samples":
            from xic_workbench.samples import load_samples

            return load_samples(
                Path(values["raw_dir"]),
                dll_dir=Path(values["dll_dir"]) if values.get("dll_dir") else None,
                sequence_source=Path(values["sequence_source"])
                if values.get("sequence_source")
                else None,
                custom_roles=values.get("custom_roles"),
            )
        if kind == "quality":
            from xic_extractor.feature_quality_io import export_feature_quality

            quality_result = export_feature_quality(
                matrix_path=Path(values["matrix_path"]),
                identity_path=Path(values["identity_path"]),
                sample_metadata_path=Path(values["sample_metadata_path"]),
                output_dir=Path(values["output_dir"]),
                min_finite_fraction=float(values["min_finite_fraction"]),
            )
            return {
                "output_dir": values["output_dir"],
                "manifest_path": str(quality_result),
            }
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
