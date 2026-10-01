from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.schema import (
    DIAGNOSTIC_HEADERS,
    LONG_HEADERS,
    SCORE_BREAKDOWN_HEADERS,
    TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION,
    TARGETED_LONG_CSV_SCHEMA_VERSION,
    TARGETED_OUTPUT_SCHEMA_VERSION,
    TARGETED_SCORE_BREAKDOWN_CSV_SCHEMA_VERSION,
)

METHOD_MANIFEST_SCHEMA_VERSION = "method_manifest"
METHOD_MANIFEST_FILENAME = "method_manifest.json"
ARTIFACT_REPLAY_POLICY_SCHEMA_VERSION = "artifact_replay_policy"
WORKBOOK_NORMALIZED_METADATA_KEYS: tuple[str, ...] = (
    "elapsed",
    "elapsed_seconds",
    "generated_at",
    "method_manifest_path",
    "method_manifest_sha256",
    "output_dir",
    "output_path",
    "output_workbook",
    "runtime",
    "runtime_seconds",
    "workbook_path",
)


@dataclass(frozen=True)
class MethodManifestContext:
    entrypoint: str = "xic_extractor.extractor.run"
    argv: tuple[str, ...] | None = None
    base_dir: Path | None = None
    config_dir: Path | None = None
    settings_overrides: Mapping[str, str] | None = None
    output_mode: str | None = None


@dataclass(frozen=True)
class MethodManifestReplayRequest:
    manifest_path: Path
    base_dir: Path
    config_dir: Path
    settings_overrides: dict[str, str]
    output_mode: str
    parallel_mode: str
    parallel_workers: int


class MethodManifestError(ValueError):
    """Raised when a method manifest cannot support replay."""


def method_manifest_path(config: ExtractionConfig) -> Path:
    return config.output_csv.with_name(METHOD_MANIFEST_FILENAME)


def write_method_manifest(
    config: ExtractionConfig,
    targets: Sequence[Target],
    *,
    context: MethodManifestContext | None = None,
    destination: Path | None = None,
    artifact_sources: Mapping[Path, Path] | None = None,
) -> Path:
    path = destination or method_manifest_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_method_manifest(
        config,
        targets,
        context=context,
        artifact_sources=artifact_sources,
    )
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def build_method_manifest(
    config: ExtractionConfig,
    targets: Sequence[Target],
    *,
    context: MethodManifestContext | None = None,
    artifact_sources: Mapping[Path, Path] | None = None,
) -> dict[str, Any]:
    context = context or MethodManifestContext()
    output_dir = config.output_csv.parent
    config_dir = _config_dir(config, context)
    settings_path = config_dir / "settings.csv"
    targets_path = config_dir / "targets.csv"
    manifest_path = method_manifest_path(config)

    return {
        "schema_version": METHOD_MANIFEST_SCHEMA_VERSION,
        "generated_at": _utc_now_text(),
        "app_version": _app_version(),
        "run_kind": "targeted_extraction",
        "invocation": {
            "entrypoint": context.entrypoint,
            "argv": list(context.argv) if context.argv is not None else None,
            "base_dir": _path_text(context.base_dir),
            "config_dir": _path_text(config_dir),
            "settings_overrides": dict(context.settings_overrides or {}),
            "output_mode": context.output_mode,
        },
        "input_artifacts": {
            "settings_csv": _artifact(settings_path, "settings", output_dir),
            "targets_csv": _artifact(targets_path, "targets", output_dir),
            "raw_dir": _artifact(config.data_dir, "raw_dir", output_dir),
            "dll_dir": _artifact(config.dll_dir, "dll_dir", output_dir),
            "injection_order_source": _artifact(
                config.injection_order_source,
                "sample_metadata",
                output_dir,
            ),
            "rt_prior_library": _artifact(
                config.rt_prior_library_path,
                "rt_prior",
                output_dir,
            ),
            "target_pair_rt_calibration": _artifact(
                config.target_pair_rt_calibration_path,
                "target_pair_rt_calibration",
                output_dir,
            ),
            "expected_diff_approval_registry": _artifact(
                config.model_selection_expected_diff_approval_registry,
                "expected_diff",
                output_dir,
            ),
            "targeted_ms1_shape_identity_support_tsv": _artifact(
                config.targeted_ms1_shape_identity_support_tsv,
                "targeted_ms1_shape_identity_support",
                output_dir,
            ),
        },
        "config_fragments": {
            "config_hash": {
                "value": config.config_hash,
                "scope": "targets_csv + settings_csv_effective_bytes",
                "is_full_method_hash": False,
            },
            "target_config_hash": {
                "value": config.target_config_hash,
                "scope": "targets_csv_bytes",
                "is_full_method_hash": False,
            },
        },
        "method_settings": _method_settings(config),
        "target_summary": _target_summary(targets),
        "output_schema": _output_schema(),
        "artifact_replay_policy": _artifact_replay_policy(),
        "runtime": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "backend": config.parallel_mode,
            "parallel_workers": config.parallel_workers,
        },
        "output_artifacts": {
            "output_csv": _output_artifact(
                config.output_csv,
                output_dir,
                artifact_sources,
            ),
            "long_csv": _output_artifact(
                config.output_csv.with_name("xic_results_long.csv"),
                output_dir,
                artifact_sources,
            ),
            "diagnostics_csv": _output_artifact(
                config.diagnostics_csv,
                output_dir,
                artifact_sources,
            ),
            "score_breakdown_csv": _output_artifact(
                config.output_csv.with_name("xic_score_breakdown.csv"),
                output_dir,
                artifact_sources,
            ),
            "method_manifest_json": _artifact(
                manifest_path,
                "output",
                output_dir,
                force_exists=True,
                include_sha256=False,
            ),
        },
        "replay_status": {
            "capability": "manifest_driven_cli_replay",
            "exact_replay_ready": False,
            "blockers": _replay_blockers(context),
        },
    }


def artifact_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_method_manifest_for_replay(path: Path) -> MethodManifestReplayRequest:
    manifest_path = path.expanduser().resolve()
    if not manifest_path.is_file():
        raise MethodManifestError(f"{manifest_path}: method manifest not found")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MethodManifestError(
            f"{manifest_path}: method manifest is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MethodManifestError(f"{manifest_path}: method manifest must be an object")
    _validate_replay_header(payload, manifest_path)

    invocation = _required_mapping(payload, "invocation", manifest_path)
    config_dir = _required_path(invocation, "config_dir", manifest_path)
    if not config_dir.is_dir():
        raise MethodManifestError(f"{config_dir}: replay config_dir is not a directory")
    base_dir = (
        _optional_path(invocation, "base_dir", manifest_path) or config_dir.parent
    )
    output_mode = _required_text(invocation, "output_mode", manifest_path)
    if output_mode not in {"excel", "csv_only"}:
        raise MethodManifestError(
            f"{manifest_path}: invocation.output_mode must be 'excel' or 'csv_only'"
        )
    settings_overrides = _settings_overrides(invocation, manifest_path)
    method_settings = _required_mapping(payload, "method_settings", manifest_path)
    parallel_mode = _required_text(method_settings, "parallel_mode", manifest_path)
    parallel_workers = _required_int(method_settings, "parallel_workers", manifest_path)
    if parallel_mode not in {"serial", "process"}:
        raise MethodManifestError(
            f"{manifest_path}: method_settings.parallel_mode is unsupported"
        )
    if parallel_workers < 1:
        raise MethodManifestError(
            f"{manifest_path}: method_settings.parallel_workers must be >= 1"
        )

    input_artifacts = _required_mapping(payload, "input_artifacts", manifest_path)
    settings_artifact_path = _validate_required_file_artifact(
        input_artifacts,
        "settings_csv",
        manifest_path,
    )
    targets_artifact_path = _validate_required_file_artifact(
        input_artifacts,
        "targets_csv",
        manifest_path,
    )
    _validate_config_artifact_path(
        settings_artifact_path,
        config_dir / "settings.csv",
        "settings_csv",
        manifest_path,
    )
    _validate_config_artifact_path(
        targets_artifact_path,
        config_dir / "targets.csv",
        "targets_csv",
        manifest_path,
    )
    _validate_required_directory_artifact(input_artifacts, "raw_dir", manifest_path)
    _validate_required_directory_artifact(input_artifacts, "dll_dir", manifest_path)
    for artifact_id in (
        "injection_order_source",
        "rt_prior_library",
        "target_pair_rt_calibration",
        "expected_diff_approval_registry",
        "targeted_ms1_shape_identity_support_tsv",
    ):
        _validate_optional_file_artifact(input_artifacts, artifact_id, manifest_path)

    return MethodManifestReplayRequest(
        manifest_path=manifest_path,
        base_dir=base_dir,
        config_dir=config_dir,
        settings_overrides=settings_overrides,
        output_mode=output_mode,
        parallel_mode=parallel_mode,
        parallel_workers=parallel_workers,
    )


def _app_version() -> str:
    try:
        return metadata.version("xic-extractor")
    except metadata.PackageNotFoundError:
        return "unknown"


def _validate_replay_header(
    payload: Mapping[str, object],
    manifest_path: Path,
) -> None:
    if payload.get("schema_version") != METHOD_MANIFEST_SCHEMA_VERSION:
        raise MethodManifestError(
            f"{manifest_path}: unsupported method manifest schema_version"
        )
    if payload.get("run_kind") != "targeted_extraction":
        raise MethodManifestError(
            f"{manifest_path}: only targeted_extraction manifests can be replayed"
        )


def _required_mapping(
    payload: Mapping[str, object],
    key: str,
    manifest_path: Path,
) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise MethodManifestError(f"{manifest_path}: {key} must be an object")
    return value


def _required_path(
    payload: Mapping[str, object],
    key: str,
    manifest_path: Path,
) -> Path:
    path_text = _required_text(payload, key, manifest_path)
    return _resolve_manifest_path(path_text, manifest_path)


def _optional_path(
    payload: Mapping[str, object],
    key: str,
    manifest_path: Path,
) -> Path | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise MethodManifestError(f"{manifest_path}: {key} must be a string")
    return _resolve_manifest_path(value, manifest_path)


def _required_text(
    payload: Mapping[str, object],
    key: str,
    manifest_path: Path,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MethodManifestError(f"{manifest_path}: {key} must be a non-empty string")
    return value


def _required_int(
    payload: Mapping[str, object],
    key: str,
    manifest_path: Path,
) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise MethodManifestError(f"{manifest_path}: {key} must be an integer")
    return value


def _settings_overrides(
    invocation: Mapping[str, object],
    manifest_path: Path,
) -> dict[str, str]:
    value = invocation.get("settings_overrides") or {}
    if not isinstance(value, Mapping):
        raise MethodManifestError(
            f"{manifest_path}: invocation.settings_overrides must be an object"
        )
    overrides: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise MethodManifestError(
                f"{manifest_path}: invocation.settings_overrides must map strings"
            )
        overrides[key] = item
    return overrides


def _resolve_manifest_path(path_text: str, manifest_path: Path) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (manifest_path.parent / path).resolve()


def _validate_required_file_artifact(
    artifacts: Mapping[str, object],
    artifact_id: str,
    manifest_path: Path,
) -> Path:
    artifact = _artifact_descriptor(artifacts, artifact_id, manifest_path)
    path = _artifact_required_path(artifact, artifact_id, manifest_path)
    if not path.is_file():
        raise MethodManifestError(
            f"{path}: replay artifact {artifact_id} is not a file"
        )
    expected_hash = artifact.get("sha256")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id}.sha256 is required"
        )
    actual_hash = artifact_sha256(path)
    if actual_hash != expected_hash:
        raise MethodManifestError(
            f"{path}: replay artifact {artifact_id} sha256 mismatch"
        )
    return path


def _validate_config_artifact_path(
    artifact_path: Path,
    expected_path: Path,
    artifact_id: str,
    manifest_path: Path,
) -> None:
    expected = expected_path.expanduser().resolve()
    if artifact_path != expected:
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id}.path must match "
            f"invocation.config_dir/{expected_path.name}"
        )


def _validate_required_directory_artifact(
    artifacts: Mapping[str, object],
    artifact_id: str,
    manifest_path: Path,
) -> None:
    artifact = _artifact_descriptor(artifacts, artifact_id, manifest_path)
    path = _artifact_required_path(artifact, artifact_id, manifest_path)
    if not path.is_dir():
        raise MethodManifestError(
            f"{path}: replay artifact {artifact_id} is not a directory"
        )


def _validate_optional_file_artifact(
    artifacts: Mapping[str, object],
    artifact_id: str,
    manifest_path: Path,
) -> None:
    artifact = artifacts.get(artifact_id)
    if artifact is None:
        return
    if not isinstance(artifact, Mapping):
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id} must be an object"
        )
    if artifact.get("path") in (None, "") and artifact.get("exists") is False:
        return
    _validate_required_file_artifact(artifacts, artifact_id, manifest_path)


def _artifact_descriptor(
    artifacts: Mapping[str, object],
    artifact_id: str,
    manifest_path: Path,
) -> Mapping[str, object]:
    value = artifacts.get(artifact_id)
    if not isinstance(value, Mapping):
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id} must be an object"
        )
    if value.get("exists") is not True:
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id}.exists must be true"
        )
    return value


def _artifact_required_path(
    artifact: Mapping[str, object],
    artifact_id: str,
    manifest_path: Path,
) -> Path:
    value = artifact.get("path")
    if not isinstance(value, str) or not value:
        raise MethodManifestError(
            f"{manifest_path}: input_artifacts.{artifact_id}.path is required"
        )
    return _resolve_manifest_path(value, manifest_path)


def _config_dir(config: ExtractionConfig, context: MethodManifestContext) -> Path:
    if context.config_dir is not None:
        return context.config_dir
    if context.base_dir is not None:
        return context.base_dir / "config"
    return config.output_csv.parent.parent / "config"


def _artifact(
    path: Path | None,
    scope: str,
    output_dir: Path,
    *,
    force_exists: bool | None = None,
    include_sha256: bool = True,
    source_path: Path | None = None,
    fallback_to_path: bool = True,
) -> dict[str, object]:
    if path is None:
        return {
            "path": None,
            "exists": False,
            "sha256": None,
            "scope": scope,
        }
    evidence_path = source_path
    if evidence_path is None and fallback_to_path:
        evidence_path = path
    exists = (
        evidence_path is not None and evidence_path.exists()
        if force_exists is None
        else force_exists
    )
    sha256 = (
        artifact_sha256(evidence_path)
        if include_sha256 and evidence_path is not None
        else None
    )
    return {
        "path": _artifact_path_text(path, output_dir),
        "exists": exists,
        "sha256": sha256,
        "scope": scope,
    }


def _output_artifact(
    path: Path,
    output_dir: Path,
    sources: Mapping[Path, Path] | None,
) -> dict[str, object]:
    return _artifact(
        path,
        "output",
        output_dir,
        source_path=_artifact_source(path, sources),
        fallback_to_path=sources is None,
    )


def _artifact_source(
    path: Path,
    sources: Mapping[Path, Path] | None,
) -> Path | None:
    if sources is None:
        return None
    resolved = path.resolve()
    for final_path, source_path in sources.items():
        if final_path.resolve() == resolved:
            return source_path
    return None


def _artifact_path_text(path: Path, output_dir: Path) -> str:
    try:
        return Path(path).resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _path_text(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _method_settings(config: ExtractionConfig) -> dict[str, object]:
    return {
        "signal_processing_method": config.signal_processing_method,
        "signal_processing_window_points": config.signal_processing_window_points,
        "ms1_morphology_smoothing_window_points": (
            config.ms1_morphology_smoothing_window_points
        ),
        "ms2_precursor_tol_da": config.ms2_precursor_tol_da,
        "nl_min_intensity_ratio": config.nl_min_intensity_ratio,
        "count_no_ms2_as_detected": config.count_no_ms2_as_detected,
        "targeted_ms1_shape_identity_activation_policy": (
            config.targeted_ms1_shape_identity_activation_policy
        ),
        "parallel_mode": config.parallel_mode,
        "parallel_workers": config.parallel_workers,
        "emit_score_breakdown": config.emit_score_breakdown,
        "emit_review_report": config.emit_review_report,
        "keep_intermediate_csv": config.keep_intermediate_csv,
    }


def _target_summary(targets: Sequence[Target]) -> dict[str, object]:
    analyte_count = sum(1 for target in targets if not target.is_istd)
    istd_count = sum(1 for target in targets if target.is_istd)
    return {
        "target_count": len(targets),
        "analyte_count": analyte_count,
        "istd_count": istd_count,
        "sample_applicability_values": sorted(
            {target.sample_applicability for target in targets}
        ),
        "isotope_label_type_values": sorted(
            {target.isotope_label_type for target in targets}
        ),
    }


def _output_schema() -> dict[str, object]:
    return {
        "targeted_output_schema_version": TARGETED_OUTPUT_SCHEMA_VERSION,
        "xic_results_long_csv": {
            "schema_version": TARGETED_LONG_CSV_SCHEMA_VERSION,
            "headers": list(LONG_HEADERS),
        },
        "xic_diagnostics_csv": {
            "schema_version": TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION,
            "headers": list(DIAGNOSTIC_HEADERS),
        },
        "xic_score_breakdown_csv": {
            "schema_version": TARGETED_SCORE_BREAKDOWN_CSV_SCHEMA_VERSION,
            "headers": list(SCORE_BREAKDOWN_HEADERS),
        },
    }


def _artifact_replay_policy() -> dict[str, object]:
    return {
        "schema_version": ARTIFACT_REPLAY_POLICY_SCHEMA_VERSION,
        "exact_artifacts": [
            "output_csv",
            "long_csv",
            "diagnostics_csv",
            "score_breakdown_csv",
        ],
        "normalized_compare_artifacts": {
            "timestamped_workbook": {
                "comparison": "scripts.compare_workbooks",
                "ignored_run_metadata_keys": list(WORKBOOK_NORMALIZED_METADATA_KEYS),
                "reason": (
                    "CLI Excel output uses a timestamped workbook filename; "
                    "workbook replay parity is verified by normalized sheet "
                    "comparison, not byte hash."
                ),
            }
        },
        "provenance_only_artifacts": {
            "method_manifest_json": {
                "reason": (
                    "The manifest records the run and naturally changes between "
                    "initial and replay executions."
                )
            }
        },
        "full_byte_exact_replay_ready": False,
    }


def _replay_blockers(context: MethodManifestContext) -> list[str]:
    blockers = ["timestamped_workbook_uses_normalized_compare_policy"]
    if context.output_mode not in {"excel", "csv_only"}:
        blockers.insert(0, "output_mode_not_recorded")
    return blockers
