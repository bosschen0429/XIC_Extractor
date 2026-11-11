"""Qt-free Untargeted execution adapter for the local workbench."""

from __future__ import annotations

import csv
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from xic_extractor.applications.untargeted.application import run_untargeted_application
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
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
from xic_extractor.discovery.delta_mass_pipeline import run_delta_mass_discovery
from xic_extractor.discovery.models import DiscoveryBatchOutputs
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.presets import (
    BoundCidNlRecipe,
    Preset,
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.presets.delta_mass import resolve_delta_mass_discovery
from xic_extractor.raw_sequence import read_analysis_set
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadata,
)
from xic_extractor.tabular_io import write_tsv
from xic_workbench.methods import preset_from_payload
from xic_workbench.samples import from_payload as samples_from_payload


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    preset: str
    tuning_overrides: dict[str, Any]
    raw_dir: Path | None
    dll_dir: Path
    output_dir: Path
    targets_csv: Path | None = None
    sample_metadata: tuple[SampleMetadata, ...] | None = None
    preset_snapshot: Preset | None = None
    execution_profile: ExecutionProfile = field(default_factory=ExecutionProfile)


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
        "preset",
        "overrides",
        "raw_dir",
        "dll_dir",
        "output_dir",
        "targets_csv",
        "sequence_source",
        "samples",
        "preset_snapshot",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported Untargeted request field: {unknown[0]}")
    preset = _text(values.get("preset", "dna_dr"), "preset")
    overrides = values.get("overrides", {})
    if not isinstance(overrides, dict):
        raise TypeError("overrides must be an object")
    snapshot_value = values.get("preset_snapshot")
    snapshot = (
        preset_from_payload(snapshot_value) if snapshot_value is not None else None
    )
    bound_preset = snapshot or load_preset(preset)
    if _is_delta_mass(bound_preset):
        resolve_delta_mass_discovery(bound_preset, label=preset)
    else:
        bind_cid_nl_recipe(bound_preset, "full", recipe_overrides=overrides)

    return DiscoveryRequest(
        preset=preset,
        tuning_overrides=dict(overrides),
        raw_dir=_optional_path(values.get("raw_dir")),
        dll_dir=Path(_text(values.get("dll_dir"), "DLL directory")),
        output_dir=Path(_text(values.get("output_dir"), "output directory")),
        targets_csv=_optional_path(values.get("targets_csv")),
        sample_metadata=samples_from_payload(values.get("samples")),
        preset_snapshot=bound_preset,
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
        if request.targets_csv is not None:
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
        self.samples: tuple[SampleMetadata, ...] = ()

    def execute(self) -> dict[str, Any]:
        self._raise_if_cancelled("Untargeted run cancelled before start")
        delta_mass = self.request.preset_snapshot is not None and _is_delta_mass(
            self.request.preset_snapshot
        )
        summary = self._run_delta_mass() if delta_mass else self._run_full()
        summary["published_paths"] = [
            str(path) for path in _ordered_existing_paths(self.available_outputs)
        ]
        summary["modules"] = (
            {"delta_mass": "completed"}
            if delta_mass
            else {"alignment": "completed", "backfill": "enabled"}
        )
        summary["samples"] = [asdict(row) for row in self.samples]
        return summary

    def _run_full(self) -> dict[str, Any]:
        request = self.request
        self.stage = "讀取 RAW 資料來源"
        raw_paths = self._prepare_samples(request.raw_dir)
        if request.raw_dir is None or not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        total_steps = len(raw_paths) + 1
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
        index_path = batch_outputs.batch_index_csv
        self._retain_discovery_outputs(batch_outputs, index_path)
        self._raise_if_cancelled("Untargeted run cancelled after Discovery")
        self.progress(0, 0, "Discovery 完成；準備跨樣本對齊與 Backfill")
        outputs = self._run_alignment(index_path=index_path, raw_dir=request.raw_dir)
        self.progress(total_steps, total_steps, "完成")
        summary = self._discovery_summary(index_path)
        summary.update(_alignment_summary(outputs))
        return summary

    def _run_delta_mass(self) -> dict[str, Any]:
        """Delta Mass Discovery only: diagnostic candidates, no Alignment or matrix.

        A composite with CID-NL dR first runs dR Discovery on the same RAW and
        joins its peaks, as the command-line route does in two steps.
        """
        request = self.request
        assert request.preset_snapshot is not None
        self.stage = "讀取 RAW 資料來源"
        raw_paths = self._prepare_samples(request.raw_dir)
        if request.raw_dir is None or not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        strategy = resolve_delta_mass_discovery(
            request.preset_snapshot, label=request.preset
        )
        peaks_csv = None
        if strategy.cid_nl_composite:
            peaks_csv = self._run_cid_nl_dr(raw_paths)
            self._raise_if_cancelled("Untargeted run cancelled after CID-NL dR")
        self.stage = "Delta Mass Discovery"
        self.progress(0, 0, "Delta Mass Discovery…")
        outputs = run_delta_mass_discovery(
            raw_dir=request.raw_dir,
            dll_dir=request.dll_dir,
            output_dir=request.output_dir,
            strategy=strategy,
            cid_nl_peaks_csv=peaks_csv,
            raw_paths=raw_paths,
            should_cancel=self.should_cancel,
            progress_callback=lambda done, total, sample: self.progress(
                done, total, f"Delta Mass Discovery: {sample}"
            ),
        )
        self._raise_if_cancelled("Untargeted run cancelled after Delta Mass")
        self.available_outputs += (
            ("Delta Mass 候選表", outputs.candidates_csv),
            ("Delta Mass 離子明細", outputs.positions_csv),
            ("Delta Mass 執行紀錄", outputs.sidecar_json),
        )
        self.progress(1, 1, "完成")
        with outputs.candidates_csv.open(encoding="utf-8", newline="") as handle:
            statuses = [row.get("status") for row in csv.DictReader(handle)]
        return {
            "output_dir": str(request.output_dir),
            "sample_count": len(raw_paths),
            "delta_mass": {
                "candidates_csv": str(outputs.candidates_csv),
                "candidate_count": statuses.count("candidate"),
                "cid_nl_peaks_csv": str(peaks_csv) if peaks_csv else None,
            },
            "alignment_outputs": {},
            "matrix_tsv": None,
        }

    def _run_cid_nl_dr(self, raw_paths: tuple[Path, ...]) -> Path:
        """dR Discovery for the composite's base join; peaks merged into one table."""
        request = self.request
        self.stage = "CID-NL dR Discovery"
        settings = bind_cid_nl_recipe(load_preset("dna_dr"), "full").discovery_settings
        directory = request.output_dir / "cid_nl_dR"
        outputs = run_discovery_batch(
            raw_paths,
            output_dir=directory,
            settings=settings,
            dll_dir=request.dll_dir,
            run_spec=compile_discovery_processing_run_spec(
                raw_paths, discovery_settings=settings
            ),
            max_workers=min(len(raw_paths), request.execution_profile.parallel_workers),
            should_cancel=self.should_cancel,
            progress_callback=lambda done, total, sample: self.progress(
                done, total, f"CID-NL dR Discovery: {sample}"
            ),
        )
        merged = directory / "discovery_peaks.csv"
        header: list[str] | None = None
        with merged.open("w", encoding="utf-8", newline="") as target:
            writer = None
            for item in outputs.per_sample:
                with Path(item.peaks_csv).open(encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if writer is None:
                        header = list(reader.fieldnames or ())
                        writer = csv.DictWriter(target, fieldnames=header)
                        writer.writeheader()
                    elif list(reader.fieldnames or ()) != header:
                        raise ValueError(f"{item.peaks_csv}: peak table columns differ")
                    writer.writerows(reader)
        self._retain_discovery_outputs(outputs, outputs.batch_index_csv)
        self.available_outputs += (("CID-NL dR 峰表（合併）", merged),)
        return merged

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
                alignment_config=recipe.alignment_config,
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
                    emit_alignment_explanation=True,
                ),
            ),
            UntargetedExecutionContext(
                should_cancel=self.should_cancel,
                progress_callback=self._alignment_progress,
            ),
        )
        self.available_outputs += _alignment_available_outputs(outputs)
        self.stage = "Alignment 結果摘要"
        return outputs

    def _alignment_progress(self, event: Mapping[str, object]) -> None:
        labels = {
            "mass_recalibration": "確認 RAW 質量校正",
            "known_istd_reference": "量測內標 RT 參考",
            "alignment_inputs": "準備跨樣本觀察資料",
            "partition_start": "讀取 RAW 與搜尋缺格（Backfill）",
            "partition_compose": "整合樣本定量證據",
            "partition_released": "已處理工作區塊",
            "final_grouping": "跨樣本特徵分組",
            "global_closure": "統整所有樣本與分組",
            "batch_completed": "對齊與 Backfill 完成",
            "publish_matrix": "寫入 Matrix 與檢閱資料",
        }
        label = labels.get(str(event.get("stage")))
        if label is None:
            return
        self.stage = "Alignment 與 Backfill"
        completed, total = event.get("completed"), event.get("total")
        if isinstance(completed, int) and isinstance(total, int) and total:
            label += f" · {completed}/{total}"
        self.progress(0, 0, label)

    def _retain_discovery_outputs(
        self, outputs: DiscoveryBatchOutputs, index_path: Path
    ) -> None:
        found: list[tuple[str, Path]] = []
        if index_path.is_file():
            found.append(("Discovery batch index", index_path))
        for item in outputs.per_sample:
            for label, path in (
                ("Discovery 峰表", item.peaks_csv),
                ("Discovery 事件表", item.events_csv),
            ):
                if Path(path).is_file():
                    found.append((label, Path(path)))
        self.available_outputs += tuple(found)

    def _alignment_runtime(self) -> BoundCidNlRecipe:
        if self._recipe_binding is None:
            preset = self.request.preset_snapshot or load_preset(self.request.preset)
            self._recipe_binding = bind_cid_nl_recipe(
                preset,
                "full",
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
            mass_recalibration=settings.mass_recalibration,
        )

    def _prepare_samples(self, raw_dir: Path | None) -> tuple[Path, ...]:
        if raw_dir is None or not raw_dir.is_dir():
            return ()
        analysis_set = read_analysis_set(
            raw_dir,
            self.request.dll_dir,
            self.request.sample_metadata,
            analysis="untargeted",
        )
        self.samples = analysis_set.sample_metadata
        sample_path = self.output_dir / "sample_metadata.tsv"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        write_tsv(
            sample_path,
            [
                {"schema_version": SAMPLE_METADATA_SCHEMA_VERSION, **asdict(row)}
                for row in self.samples
            ],
            SAMPLE_METADATA_COLUMNS,
        )
        self.available_outputs += (("本次分析的樣本資訊", sample_path),)
        return tuple(sorted(analysis_set.raw_paths, key=lambda path: path.name.lower()))

    def _discovery_summary(self, index_path: Path) -> dict[str, Any]:
        counts, sample_count = _read_batch_counts(index_path)
        return {
            "output_dir": str(self.request.output_dir),
            "sample_count": sample_count,
            "peak_counts": counts,
            "discovery_batch_index": str(index_path),
            "alignment_outputs": {},
            "matrix_tsv": None,
        }

    def _raise_if_cancelled(self, message: str) -> None:
        if self.should_cancel():
            self.cancellation_reason = message
            raise InterruptedError(message)


def _is_delta_mass(preset: Preset) -> bool:
    return any(tag.strategy == "delta_mass" for tag in preset.tags)


def _alignment_summary(outputs: AlignmentRunOutputs) -> dict[str, Any]:
    from xic_inspector.api import read_evidence_summary
    from xic_inspector.sources.untargeted_overview import review_overview

    overview = None
    if outputs.evidence is not None:
        path = Path(outputs.evidence.report_path)
        overview, _rows = review_overview(path, read_evidence_summary(path))
    alignment_outputs = {
        name: str(path)
        for name, path in (
            ("review_tsv", outputs.review_tsv),
            ("matrix_tsv", outputs.matrix_tsv),
            ("cells_tsv", outputs.cells_tsv),
            ("run_metadata_json", outputs.run_metadata_json),
        )
        if path is not None
    }
    return {
        "overview": overview,
        "alignment_outputs": alignment_outputs,
        "matrix_tsv": alignment_outputs.get("matrix_tsv"),
        "run_metadata_json": alignment_outputs.get("run_metadata_json"),
        "evidence": outputs.evidence,
    }


def _alignment_available_outputs(
    outputs: AlignmentRunOutputs,
) -> tuple[tuple[str, Path], ...]:
    published = (
        ("review_tsv", outputs.review_tsv),
        ("matrix_tsv", outputs.matrix_tsv),
        ("backfill_cell_evidence_tsv", outputs.backfill_cell_evidence_tsv),
        ("cells_tsv", outputs.cells_tsv),
        ("status_matrix_tsv", outputs.status_matrix_tsv),
        ("run_metadata_json", outputs.run_metadata_json),
    )
    found = [
        (f"Alignment {name}", Path(path))
        for name, path in published
        if path is not None and Path(path).is_file()
    ]
    if outputs.evidence is not None and Path(outputs.evidence.report_path).is_file():
        found.append(("Alignment review evidence", Path(outputs.evidence.report_path)))
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
    totals = {"total": 0, "admitted": 0}
    sample_count = 0
    with index_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sample_count += 1
            totals["total"] += _int_csv_value(row, "peak_count")
            totals["admitted"] += _int_csv_value(row, "admitted_peak_count")
    return totals, sample_count


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
