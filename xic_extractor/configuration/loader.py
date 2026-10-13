import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from xic_extractor.configuration.csv_io import (
    read_settings,
    write_config_snapshot,
)
from xic_extractor.configuration.hashing import (
    _compute_config_hash,
    compute_target_config_hash,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.configuration.settings import (
    build_extraction_config,
    resolve_settings,
)
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    mint_target_list_snapshot,
)
from xic_extractor.configuration.targeted_method_files import (
    apply_targeted_method,
    load_targeted_method,
)
from xic_extractor.configuration.targets import _read_targets, parse_target_rows
from xic_extractor.configuration.workspace_state import (
    read_workspace,
    workspace_settings,
)

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


def default_output_dir(config_dir: Path) -> Path:
    """Run artifacts go beside the config folder unless a caller chooses."""
    return Path(config_dir).parent / "output"


def prepare_targeted_config(
    config_dir: Path,
    *,
    method_path: Path | None = None,
    settings_overrides: Mapping[str, str] | None = None,
    output_dir: Path | None = None,
) -> PreparedTargetedConfig:
    """Freeze actual Targeted execution inputs, optionally binding a portable method.

    The caller owns the returned workspace until execution finishes or is cancelled.
    Resource-bound portable cases are rejected rather than silently downgraded.
    """
    config_dir = Path(config_dir).resolve()
    state = read_workspace(config_dir)
    raw = workspace_settings(state)
    overrides = dict(settings_overrides or {})
    if (
        overrides.get("data_dir")
        and Path(overrides["data_dir"]).resolve()
        != Path(raw.get("data_dir", "")).resolve()
        and raw.get("injection_order_source", "").strip()
        and "injection_order_source" not in overrides
    ):
        raise ConfigError(
            "更換 RAW 批次不能沿用原注射順序；請以 --injection-order-source "
            "明確提供本批次的順序資料，或清空本批次設定中的 injection_order_source。"
        )
    method = load_targeted_method(method_path) if method_path is not None else None
    values = (
        apply_targeted_method(raw, method, overrides=overrides)
        if method is not None
        else {**raw, **overrides}
    )
    target_rows = method.target_rows if method is not None else state["targets"]
    targets = method.targets if method is not None else parse_target_rows(target_rows)
    order_source = values.get("injection_order_source", "").strip()
    output_dir = (
        Path(output_dir) if output_dir is not None else default_output_dir(config_dir)
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
        settings_path = receipt_dir / "settings.csv"
        targets_path = receipt_dir / "targets.csv"
        values, parsed, warnings = resolve_settings(values, settings_path=settings_path)
        for warning in warnings:
            LOGGER.warning(warning)
        write_config_snapshot(receipt_dir, values, target_rows)
        output_dir.mkdir(parents=True, exist_ok=True)
        config = build_extraction_config(
            parsed,
            values,
            settings_path,
            output_dir,
            _compute_config_hash(targets_path, settings_path, settings_overrides=None),
        )
        config = replace(
            config, target_config_hash=compute_target_config_hash(targets_path)
        )
        # The already admitted targets stay authoritative; the private CSVs are
        # receipts, not a second input boundary to reparse during the same run.
        return PreparedTargetedConfig(config, targets, receipt_dir, workspace)
    except BaseException:
        workspace.cleanup()
        raise


def load_target_list_snapshot(
    config_dir: Path,
    *,
    identity_namespace: str,
) -> TargetListSnapshot:
    """Load and owner-mint the exact target-list snapshot for one run."""

    targets_path = config_dir / "targets.csv"
    return mint_target_list_snapshot(
        tuple(_read_targets(targets_path)),
        identity_namespace=identity_namespace,
    )


def load_config_snapshot(
    config_dir: Path,
    *,
    settings_overrides: dict[str, str] | None = None,
    output_dir: Path | None = None,
) -> tuple[ExtractionConfig, list[Target]]:
    """Read explicit machine-generated CSV receipts; never discover user inputs."""
    settings_path = config_dir / "settings.csv"
    targets_path = config_dir / "targets.csv"

    raw_settings = read_settings(settings_path)
    try:
        settings, parsed, warnings = resolve_settings(
            raw_settings, settings_overrides, settings_path=settings_path
        )
    except ConfigError as exc:
        raise ConfigError(f"{settings_path}: {exc}") from exc
    for warning in warnings:
        LOGGER.warning(warning)

    output_dir = (
        Path(output_dir) if output_dir is not None else default_output_dir(config_dir)
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
