import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from xic_extractor.configuration.csv_io import (
    _config_input_path,
    _parse_settings_csv_bytes,
    _read_settings,
)
from xic_extractor.configuration.hashing import (
    _compute_config_hash,
    compute_target_config_hash,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.configuration.settings import (
    build_extraction_config,
    reject_retired_rt_reference_inputs,
    resolve_settings,
)
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    mint_target_list_snapshot,
)
from xic_extractor.configuration.targeted_method_files import (
    _write_csv,
    _write_method_targets,
    apply_targeted_method,
    load_targeted_method,
)
from xic_extractor.configuration.targets import _read_targets

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedTargetedConfig:
    """Admitted configuration with inputs frozen only for this run's lifetime."""

    config: ExtractionConfig
    targets: tuple[Target, ...]
    input_config_dir: Path
    _workspace: TemporaryDirectory | None = field(
        default=None, repr=False, compare=False
    )

    def cleanup(self) -> None:
        if self._workspace is not None:
            self._workspace.cleanup()


def prepare_targeted_config(
    config_dir: Path,
    *,
    method_dir: Path | None = None,
    settings_overrides: Mapping[str, str] | None = None,
    output_dir: Path | None = None,
) -> PreparedTargetedConfig:
    """Freeze actual Targeted execution inputs, optionally binding a portable method.

    The caller owns the returned workspace until execution finishes or is cancelled.
    Resource-bound portable cases are rejected rather than silently downgraded.
    """
    config_dir = Path(config_dir).resolve()
    settings_path = _config_input_path(config_dir, "settings")
    if not settings_path.is_file():
        raise ConfigError(f"{settings_path}: file is missing")
    settings_bytes = settings_path.read_bytes()
    raw = _parse_settings_csv_bytes(settings_bytes, settings_path)
    overrides = dict(settings_overrides or {})
    reject_retired_rt_reference_inputs({**raw, **overrides})
    if (
        method_dir is not None
        and overrides.get("data_dir")
        and Path(overrides["data_dir"]).resolve()
        != Path(raw.get("data_dir", "")).resolve()
        and raw.get("injection_order_source", "").strip()
        and "injection_order_source" not in overrides
    ):
        raise ConfigError(
            "更換 RAW 批次不能沿用原注射順序；請以 --injection-order-source "
            "明確提供本批次的順序資料，或清空本批次設定中的 injection_order_source。"
        )
    method = load_targeted_method(method_dir) if method_dir is not None else None
    values = (
        apply_targeted_method(raw, method, overrides=overrides)
        if method is not None
        else {**raw, **overrides}
    )
    order_source = values.get("injection_order_source", "").strip()
    targets_bytes = (
        None
        if method is not None
        else _config_input_path(config_dir, "targets").read_bytes()
    )
    output_dir = (
        Path(output_dir) if output_dir is not None else config_dir.parent / "output"
    ).resolve()
    workspace = TemporaryDirectory(prefix="xic-targeted-inputs-")
    receipt_dir = Path(workspace.name)
    try:
        if order_source:
            source = Path(order_source).expanduser()
            if not source.is_file():
                raise ConfigError(f"{source}: 本批次注射順序資料不存在；未啟動分析。")
            frozen_source = receipt_dir / f"injection_order{source.suffix}"
            frozen_source.write_bytes(source.read_bytes())
            values["injection_order_source"] = str(frozen_source)
        if method is not None or overrides or order_source:
            _write_csv(
                receipt_dir / "settings.csv",
                ("key", "value"),
                [{"key": key, "value": value} for key, value in values.items()],
            )
        else:
            (receipt_dir / "settings.csv").write_bytes(settings_bytes)
        if method is not None:
            _write_method_targets(method, receipt_dir / "targets.csv")
        else:
            assert targets_bytes is not None
            (receipt_dir / "targets.csv").write_bytes(targets_bytes)
        config, targets = load_config(receipt_dir, output_dir=output_dir)
        return PreparedTargetedConfig(config, tuple(targets), receipt_dir, workspace)
    except BaseException:
        workspace.cleanup()
        raise


def load_target_list_snapshot(
    config_dir: Path,
    *,
    identity_namespace: str,
) -> TargetListSnapshot:
    """Load and owner-mint the exact target-list snapshot for one run."""

    targets_path = _config_input_path(config_dir, "targets")
    return mint_target_list_snapshot(
        tuple(_read_targets(targets_path)),
        identity_namespace=identity_namespace,
    )


def load_config(
    config_dir: Path,
    *,
    settings_overrides: dict[str, str] | None = None,
    output_dir: Path | None = None,
) -> tuple[ExtractionConfig, list[Target]]:
    """Load active settings; output_dir only controls run artifact placement."""
    settings_path = _config_input_path(config_dir, "settings")
    targets_path = _config_input_path(config_dir, "targets")

    raw_settings = _read_settings(settings_path)
    try:
        settings, parsed, warnings = resolve_settings(
            raw_settings, settings_overrides, settings_path=settings_path
        )
    except ConfigError as exc:
        raise ConfigError(f"{settings_path}: {exc}") from exc
    for warning in warnings:
        LOGGER.warning(warning)

    output_dir = (
        Path(output_dir) if output_dir is not None else config_dir.parent / "output"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config_hash = _compute_config_hash(
        targets_path,
        settings_path,
        settings_overrides=settings_overrides,
    )
    config = build_extraction_config(
        parsed, settings, settings_path, output_dir, config_hash
    )
    targets = _read_targets(targets_path)
    target_config_hash = compute_target_config_hash(targets_path)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "target_config_hash": target_config_hash,
        }
    )
    return config, targets
