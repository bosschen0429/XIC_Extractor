"""Qt-free Untargeted execution adapter for the local workbench."""

from __future__ import annotations

import csv
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from xic_extractor.applications.untargeted.application import run_untargeted_application
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    RetainedAlignmentOptions,
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.configuration.execution_profile import (
    DEFAULT_ALIGNMENT_WORKER_BACKEND,
    ExecutionProfile,
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.pipeline import run_discovery, run_discovery_batch
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.presets import (
    BoundCidNlRecipe,
    Preset,
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.raw_reader import read_analysis_set
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.methods import preset_from_payload
from xic_workbench.samples import from_payload as samples_from_payload

DiscoveryMode = Literal["full", "discovery_only", "align_only"]
_MODES = frozenset({"full", "discovery_only", "align_only"})
_SUMMARY_ALIGNMENT_ATTRS = (
    "review_tsv",
    "matrix_tsv",
    "matrix_identity_tsv",
    "cells_tsv",
    "run_metadata_json",
)
_PUBLISHED_OUTPUT_ATTRS = (
    "review_tsv",
    "matrix_tsv",
    "matrix_identity_tsv",
    "owner_backfill_peak_fact_provenance_tsv",
    "backfill_cell_evidence_tsv",
    "cells_tsv",
    "backfill_seed_audit_tsv",
    "status_matrix_tsv",
    "run_metadata_json",
)


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    mode: DiscoveryMode
    preset: str
    tuning_overrides: dict[str, Any]
    raw_dir: Path | None
    raw_file: Path | None
    dll_dir: Path
    output_dir: Path
    discovery_batch_index: Path | None
    targets_csv: Path | None = None
    sample_metadata: tuple[SampleMetadata, ...] | None = None
    preset_snapshot: Preset | None = None
    execution_profile: ExecutionProfile = field(default_factory=ExecutionProfile)
    alignment_enabled: bool = True
    backfill_enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("alignment_enabled", "backfill_enabled"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if self.mode == "align_only" and not self.alignment_enabled:
            raise ValueError("align_only requires alignment_enabled")


def parse_request(values: dict[str, Any]) -> DiscoveryRequest:
    """Compile JSON workbench state into one validated immutable request.

    ``preset_snapshot`` accepts
    ``xic_workbench.methods.method_details(...)["draft"]``; when present,
    execution binds those displayed method bytes instead of re-reading the
    preset locator.
    """

    if not isinstance(values, dict):
        raise TypeError("Untargeted request must be an object")
    allowed = {
        "mode",
        "preset",
        "overrides",
        "raw_dir",
        "raw_file",
        "dll_dir",
        "output_dir",
        "discovery_batch_index",
        "targets_csv",
        "sequence_source",
        "samples",
        "preset_snapshot",
        "execution_profile",
        "alignment_enabled",
        "backfill_enabled",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported Untargeted request field: {unknown[0]}")
    mode = values.get("mode", "full")
    if mode not in _MODES:
        raise ValueError(f"unsupported Untargeted mode: {mode!r}")
    preset = _text(values.get("preset", "dna_dr"), "preset")
    overrides = values.get("overrides", {})
    if not isinstance(overrides, dict):
        raise TypeError("overrides must be an object")
    snapshot_value = values.get("preset_snapshot")
    snapshot = (
        preset_from_payload(snapshot_value) if snapshot_value is not None else None
    )
    bound_preset = snapshot or load_preset(preset)
    bind_cid_nl_recipe(bound_preset, mode, recipe_overrides=overrides)

    profile_value = values.get("execution_profile")
    if profile_value is None:
        profile = ExecutionProfile()
    elif isinstance(profile_value, dict):
        profile_unknown = sorted(
            set(profile_value) - {"parallel_workers", "alignment_workers"}
        )
        if profile_unknown:
            raise ValueError(
                f"unsupported execution_profile field: {profile_unknown[0]}"
            )
        profile = ExecutionProfile(**profile_value)
    else:
        raise TypeError("execution_profile must be an object")

    return DiscoveryRequest(
        mode=mode,
        preset=preset,
        tuning_overrides=dict(overrides),
        raw_dir=_optional_path(values.get("raw_dir")),
        raw_file=_optional_path(values.get("raw_file")),
        dll_dir=Path(_text(values.get("dll_dir"), "DLL directory")),
        output_dir=Path(_text(values.get("output_dir"), "output directory")),
        discovery_batch_index=_optional_path(values.get("discovery_batch_index")),
        targets_csv=_optional_path(values.get("targets_csv")),
        sample_metadata=samples_from_payload(values.get("samples")),
        preset_snapshot=bound_preset,
        execution_profile=profile,
        alignment_enabled=values.get("alignment_enabled", True),
        backfill_enabled=values.get("backfill_enabled", True),
    )


class UntargetedRun:
    """Execute one compiled request while reporting durable stage state."""

    def __init__(
        self,
        request: DiscoveryRequest,
        *,
        progress: Callable[[int, int, str], None],
        should_cancel: Callable[[], bool],
    ) -> None:
        # Persisted scientific inputs must not be re-read from an active locator
        # after dispatch. RAW acquisition remains live and is not a byte snapshot.
        if request.targets_csv is not None and not (
            request.mode == "full" and not request.alignment_enabled
        ):
            receipt_dir = request.output_dir / ".run_inputs" / uuid.uuid4().hex
            contents = request.targets_csv.read_bytes()
            receipt_dir.mkdir(parents=True)
            targets_path = receipt_dir / request.targets_csv.name
            targets_path.write_bytes(contents)
            request = replace(request, targets_csv=targets_path)
        self.request = request
        self.progress = progress
        self.should_cancel = should_cancel
        self.stage = "準備 Untargeted 執行"
        self.output_dir = request.output_dir
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Untargeted 執行已停止。"
        self._recipe_binding: BoundCidNlRecipe | None = None

    def execute(self) -> dict[str, Any]:
        self._raise_if_cancelled("Untargeted run cancelled before start")
        if self.request.mode == "full":
            summary = self._run_full()
        elif self.request.mode == "discovery_only":
            summary = self._run_discovery_only()
        else:
            summary = self._run_align_only()
        summary["published_paths"] = [
            str(path) for path in _ordered_existing_paths(self.available_outputs)
        ]
        alignment_run = (
            self.request.mode != "discovery_only" and self.request.alignment_enabled
        )
        summary["modules"] = {
            "alignment": "completed" if alignment_run else "not_run",
            "backfill": "enabled"
            if alignment_run and self.request.backfill_enabled
            else "not_run",
        }
        return summary

    def _run_full(self) -> dict[str, Any]:
        request = self.request
        self.stage = "讀取 RAW 資料來源"
        raw_paths = self._raw_paths(request.raw_dir)
        if request.raw_dir is None or not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        total_steps = len(raw_paths) + int(request.alignment_enabled)
        self.progress(0, total_steps, "Discovery...")
        self.stage = "Discovery 設定"
        settings = self._build_settings()
        run_spec = compile_discovery_processing_run_spec(
            raw_paths, discovery_settings=settings
        )
        self.stage = "Discovery 擷取與結果發布"
        batch_outputs = run_discovery_batch(
            raw_paths,
            output_dir=request.output_dir,
            settings=settings,
            dll_dir=request.dll_dir,
            run_spec=run_spec,
            max_workers=min(len(raw_paths), request.execution_profile.parallel_workers),
            should_cancel=self.should_cancel,
            progress_callback=lambda done, _total, sample: self.progress(
                done, total_steps, f"Discovery: {sample}"
            ),
        )
        index_path = Path(
            getattr(
                batch_outputs,
                "batch_index_csv",
                request.output_dir / "discovery_batch_index.csv",
            )
        )
        self._retain_discovery_outputs(batch_outputs, index_path)
        self._raise_if_cancelled("Untargeted run cancelled after Discovery")
        if not request.alignment_enabled:
            self.progress(total_steps, total_steps, "完成（未執行 Alignment）")
            return self._discovery_summary(index_path)
        self.progress(len(raw_paths), total_steps, "Alignment...")
        outputs = self._run_alignment(index_path=index_path, raw_dir=request.raw_dir)
        self.progress(total_steps, total_steps, "完成")
        summary = self._discovery_summary(index_path)
        summary.update(_alignment_summary(outputs))
        return summary

    def _run_discovery_only(self) -> dict[str, Any]:
        request = self.request
        if request.raw_file is None:
            raise ValueError("discovery-only 需要單一 RAW 檔")
        self.progress(0, 1, "Discovery...")
        self.stage = "Discovery 設定"
        settings = self._build_settings()
        run_spec = compile_discovery_processing_run_spec(
            (request.raw_file,), discovery_settings=settings
        )
        self.stage = "Discovery 擷取與結果發布"
        outputs = run_discovery(
            request.raw_file,
            output_dir=request.output_dir,
            settings=settings,
            dll_dir=request.dll_dir,
            run_spec=run_spec,
            should_cancel=self.should_cancel,
        )
        self.available_outputs = tuple(
            (label, Path(path))
            for label, path in (
                ("Discovery 候選", outputs.candidates_csv),
                ("Discovery review", outputs.review_csv),
            )
            if Path(path).is_file()
        )
        self._raise_if_cancelled("Untargeted run cancelled after Discovery")
        self.stage = "Discovery 結果摘要"
        self.progress(1, 1, "完成")
        return {
            "mode": "discovery_only",
            "output_dir": str(request.output_dir),
            "sample_count": 1,
            "candidate_counts": _read_review_counts(Path(outputs.review_csv)),
            "discovery_candidates_csv": str(outputs.candidates_csv),
            "discovery_review_csv": str(outputs.review_csv),
            "discovery_batch_index": None,
            "alignment_outputs": {},
            "matrix_tsv": None,
            "evidence": None,
        }

    def _run_align_only(self) -> dict[str, Any]:
        request = self.request
        self.stage = "讀取 Discovery batch index"
        index_path = request.discovery_batch_index
        if index_path is None:
            raise ValueError("align-only 需要既有的 discovery_batch_index.csv")
        if request.raw_dir is None:
            raise ValueError("align-only 需要 raw_dir 來定位 RAW 檔案")
        counts, sample_count = _read_batch_counts(index_path)
        self.progress(0, 1, "Alignment...")
        outputs = self._run_alignment(index_path=index_path, raw_dir=request.raw_dir)
        self.progress(1, 1, "完成")
        summary: dict[str, Any] = {
            "mode": "align_only",
            "output_dir": str(request.output_dir),
            "sample_count": sample_count,
            "candidate_counts": counts,
            "discovery_candidates_csv": None,
            "discovery_review_csv": None,
            "discovery_batch_index": str(index_path),
            "alignment_outputs": {},
            "matrix_tsv": None,
        }
        summary.update(_alignment_summary(outputs))
        return summary

    def _run_alignment(self, *, index_path: Path, raw_dir: Path) -> AlignmentRunOutputs:
        request = self.request
        self.stage = "Alignment 設定"
        recipe = self._alignment_runtime()
        self._raise_if_cancelled("Untargeted run cancelled before Alignment")
        self.stage = "Alignment 分析與 Matrix 發布"
        outputs = run_untargeted_application(
            UntargetedRequest(
                discovery_batch_index=index_path,
                raw_dir=raw_dir,
                dll_dir=request.dll_dir,
                output_dir=request.output_dir,
                alignment_config=replace(
                    recipe.alignment_config, backfill_enabled=request.backfill_enabled
                ),
                peak_config=self._alignment_peak_config(recipe.discovery_settings),
                discovery_settings=recipe.discovery_settings,
                sample_metadata=request.sample_metadata,
                targets_csv=request.targets_csv,
                execution=UntargetedExecutionPolicy(
                    raw_workers=request.execution_profile.alignment_workers,
                    raw_worker_backend=DEFAULT_ALIGNMENT_WORKER_BACKEND,
                ),
                publication=UntargetedPublicationPolicy(
                    output_level="machine",
                    emit_cid_nl_explanation=True,
                ),
                legacy=RetainedAlignmentOptions(
                    owner_build_xic_backend=recipe.owner_build_xic_backend,
                    audit_evidence_mode="none",
                ),
            ),
            UntargetedExecutionContext(should_cancel=self.should_cancel),
        )
        self.available_outputs += _alignment_available_outputs(outputs)
        self.stage = "Alignment 結果摘要"
        return outputs

    def _retain_discovery_outputs(self, outputs: Any, index_path: Path) -> None:
        found: list[tuple[str, Path]] = []
        if index_path.is_file():
            found.append(("Discovery batch index", index_path))
        for item in getattr(outputs, "per_sample", ()):
            for label, attr in (
                ("Discovery 候選", "candidates_csv"),
                ("Discovery review", "review_csv"),
                ("Discovery physical facts", "physical_facts_json"),
            ):
                value = getattr(item, attr, None)
                if value is not None and Path(value).is_file():
                    found.append((label, Path(value)))
        self.available_outputs = tuple(found)

    def _alignment_runtime(self) -> BoundCidNlRecipe:
        if self._recipe_binding is None:
            preset = self.request.preset_snapshot or load_preset(self.request.preset)
            self._recipe_binding = bind_cid_nl_recipe(
                preset,
                self.request.mode,
                recipe_overrides=self.request.tuning_overrides,
            )
        return self._recipe_binding

    def _build_settings(self) -> DiscoverySettings:
        return self._alignment_runtime().discovery_settings

    def _alignment_peak_config(self, settings: DiscoverySettings) -> ExtractionConfig:
        return self._peak_config(
            self.request.raw_dir or self.request.output_dir,
            settings,
        )

    def _peak_config(
        self, data_dir: Path, settings: DiscoverySettings
    ) -> ExtractionConfig:
        return ExtractionConfig(
            data_dir=data_dir,
            dll_dir=self.request.dll_dir,
            output_csv=self.request.output_dir / "xic_results.csv",
            diagnostics_csv=self.request.output_dir / "xic_diagnostics.csv",
            smooth_window=15,
            smooth_polyorder=3,
            ms1_morphology_smoothing_window_points=(
                DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points
            ),
            peak_rel_height=0.95,
            peak_min_prominence_ratio=0.10,
            ms2_precursor_tol_da=DEFAULT_CID_NL_MASS_POLICY.ms2_precursor_tol_da,
            nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
            resolver_mode=settings.resolver_mode,
        )

    def _raw_paths(self, raw_dir: Path | None) -> tuple[Path, ...]:
        if raw_dir is None or not raw_dir.is_dir():
            return ()
        analysis_set = read_analysis_set(
            raw_dir,
            self.request.dll_dir,
            self.request.sample_metadata,
        )
        return tuple(sorted(analysis_set.raw_paths, key=lambda path: path.name.lower()))

    def _discovery_summary(self, index_path: Path) -> dict[str, Any]:
        counts, sample_count = _read_batch_counts(index_path)
        return {
            "mode": self.request.mode,
            "output_dir": str(self.request.output_dir),
            "sample_count": sample_count,
            "candidate_counts": counts,
            "discovery_candidates_csv": None,
            "discovery_review_csv": None,
            "discovery_batch_index": str(index_path),
            "alignment_outputs": {},
            "matrix_tsv": None,
        }

    def _raise_if_cancelled(self, message: str) -> None:
        if self.should_cancel():
            self.cancellation_reason = message
            raise InterruptedError(message)


def _alignment_summary(outputs: Any) -> dict[str, Any]:
    alignment_outputs = {
        attr: str(value)
        for attr in _SUMMARY_ALIGNMENT_ATTRS
        if (value := getattr(outputs, attr, None)) is not None
    }
    matrix = getattr(outputs, "matrix_tsv", None)
    receipt = getattr(outputs, "owner_build_route_receipt", None)
    return {
        "alignment_outputs": alignment_outputs,
        "matrix_tsv": str(matrix) if matrix is not None else None,
        "run_metadata_json": alignment_outputs.get("run_metadata_json"),
        "evidence": getattr(outputs, "evidence", None),
        "owner_build_route": receipt.as_dict() if receipt is not None else None,
    }


def _alignment_available_outputs(outputs: Any) -> tuple[tuple[str, Path], ...]:
    found: list[tuple[str, Path]] = []
    for attr in _PUBLISHED_OUTPUT_ATTRS:
        value = getattr(outputs, attr, None)
        if value is not None and Path(value).is_file():
            found.append((f"Alignment {attr}", Path(value)))
    evidence = getattr(outputs, "evidence", None)
    report_path = getattr(evidence, "report_path", None)
    if report_path is not None and Path(report_path).is_file():
        found.append(("Alignment review evidence", Path(report_path)))
    return tuple(found)


def _ordered_existing_paths(
    outputs: tuple[tuple[str, Path], ...],
) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    for _label, path in outputs:
        candidate = Path(path)
        if candidate.is_file() and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)
    return tuple(ordered)


def _read_batch_counts(index_path: Path) -> tuple[dict[str, int] | None, int]:
    if not index_path.exists():
        return None, 0
    totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
    sample_count = 0
    with index_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sample_count += 1
            totals["HIGH"] += _int_csv_value(row, "high_count")
            totals["MEDIUM"] += _int_csv_value(row, "medium_count")
            totals["LOW"] += _int_csv_value(row, "low_count")
            totals["total"] += _int_csv_value(row, "candidate_count")
    return totals, sample_count


def _read_review_counts(review_path: Path) -> dict[str, int] | None:
    if not review_path.exists():
        return None
    totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
    with review_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            priority = (row.get("review_priority") or "").upper()
            if priority in {"HIGH", "MEDIUM", "LOW"}:
                totals[priority] += 1
            totals["total"] += 1
    return totals


def _int_csv_value(row: Mapping[str, str | None], key: str) -> int:
    value = row.get(key)
    if value is None or value == "":
        return 0
    return int(value)


def _optional_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, Path)):
        raise TypeError("path values must be text")
    text = str(value).strip()
    return Path(text) if text else None


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"please provide {label}")
    return value.strip()
