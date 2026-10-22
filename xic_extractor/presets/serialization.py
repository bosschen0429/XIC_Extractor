"""Serialization of package-owned presets to the existing TOML format."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.presets.apply import (
    apply_to_alignment,
    apply_to_delta_mass_discovery,
    apply_to_discovery,
)
from xic_extractor.presets.loader import (
    ALIGNMENT_RUNTIME_OVERRIDE_KEYS,
    DELTA_MASS_DISCOVERY_OVERRIDE_KEYS,
    DISCOVERY_TUNING_OVERRIDE_KEYS,
    _parse_toml,
    load_preset,
    reject_retired_alignment_runtime_options,
)
from xic_extractor.presets.models import Preset, PresetError
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec

_NL_DISCOVERY_FIELDS = (
    "nl_tolerance_ppm",
    "precursor_mz_tolerance_ppm",
    "product_mz_tolerance_ppm",
    "product_search_ppm",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "seed_rt_gap_min",
    "ms1_search_padding_min",
    "rt_min",
    "rt_max",
    "resolver_mode",
    "mass_recalibration",
)


def serialize_preset(
    preset: Preset,
    *,
    discovery_overrides: Mapping[str, object] | None = None,
) -> str:
    """Return a validated, full-effective TOML representation of ``preset``.

    The TOML shape is deliberately the same closed shape accepted by
    :func:`load_preset`. Existing apply functions remain the semantic owners of
    effective values and validation.
    """

    if not isinstance(preset, Preset):
        raise TypeError("preset must be a Preset")

    merged_discovery = _merge_discovery_overrides(preset, discovery_overrides)
    effective_preset = replace(
        preset,
        discovery_overrides=merged_discovery,
        alignment_overrides=_validated_alignment_overrides(preset),
    )
    _validate_discovery_keys(effective_preset)

    has_delta_mass = any(tag.strategy == "delta_mass" for tag in effective_preset.tags)
    if has_delta_mass:
        delta_mass_settings = apply_to_delta_mass_discovery(effective_preset)
        effective = asdict(delta_mass_settings)
        effective.pop("pattern_id")
        effective["delta_mass_mz_tolerance_ppm"] = effective.pop("mz_tolerance_ppm")
        effective["detector"] = effective_preset.discovery_overrides.get("detector", {})
        discovery_values: Mapping[str, object] = effective
        discovery_fields: tuple[str, ...] = tuple(effective)
    else:
        discovery_settings = apply_to_discovery(effective_preset)
        recalibration = discovery_settings.mass_recalibration
        # Human presets omit only the new default; explicit none is rollback.
        discovery_fields = tuple(
            field
            for field in _NL_DISCOVERY_FIELDS
            if field != "mass_recalibration"
            or recalibration is not MassRecalibration.EASY_IC_PROBE
        )
        discovery_values = {
            field: (
                recalibration.value
                if field == "mass_recalibration"
                else getattr(discovery_settings, field)
            )
            for field in discovery_fields
        }

    _, runtime_options = apply_to_alignment(effective_preset)
    rendered = _render_preset(
        effective_preset,
        discovery_values=discovery_values,
        discovery_fields=discovery_fields,
        owner_build_xic_backend=runtime_options["owner_build_xic_backend"],
    )
    raw = rendered.encode("utf-8")

    # Validate the actual bytes through the existing parser and semantic owners
    # before returning them or allowing a destination replacement.
    parsed = _parse_toml(raw, source="<serialized preset>")
    _validate_loaded_preset(parsed)
    return rendered


def save_preset(
    preset: Preset,
    path: Path,
    *,
    discovery_overrides: Mapping[str, object] | None = None,
) -> Preset:
    """Atomically write a validated preset and return the loader-derived value."""

    destination = Path(path)
    rendered = serialize_preset(
        preset,
        discovery_overrides=discovery_overrides,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return load_preset(destination)


def _merge_discovery_overrides(
    preset: Preset,
    explicit: Mapping[str, object] | None,
) -> dict[str, object]:
    existing = _require_mapping(preset.discovery_overrides, "discovery_overrides")
    merged = dict(existing)
    if explicit is not None:
        merged.update(_require_mapping(explicit, "discovery_overrides"))
    return merged


def _validated_alignment_overrides(preset: Preset) -> dict[str, object]:
    overrides = _require_mapping(preset.alignment_overrides, "alignment_overrides")
    reject_retired_alignment_runtime_options(overrides)
    unknown = sorted(set(overrides) - ALIGNMENT_RUNTIME_OVERRIDE_KEYS)
    if unknown:
        raise PresetError(f"unknown alignment override: {unknown[0]}")
    return dict(overrides)


def _validate_discovery_keys(preset: Preset) -> None:
    allowed = (
        DELTA_MASS_DISCOVERY_OVERRIDE_KEYS
        if any(tag.strategy == "delta_mass" for tag in preset.tags)
        else DISCOVERY_TUNING_OVERRIDE_KEYS
    )
    unknown = sorted(set(preset.discovery_overrides) - allowed)
    if unknown:
        raise PresetError(f"unknown discovery tuning override: {unknown[0]}")


def _validate_loaded_preset(preset: Preset) -> None:
    if any(tag.strategy == "delta_mass" for tag in preset.tags):
        apply_to_delta_mass_discovery(preset)
    else:
        CidNlDiscoveryMethodSpec(settings=apply_to_discovery(preset))
    apply_to_alignment(preset)


def _render_preset(
    preset: Preset,
    *,
    discovery_values: Mapping[str, object],
    discovery_fields: tuple[str, ...],
    owner_build_xic_backend: object,
) -> str:
    lines = [
        f"name = {_toml_string(preset.name, field='name')}",
        f"description = {_toml_string(preset.description, field='description')}",
        f"combine_mode = {_toml_string(preset.combine_mode, field='combine_mode')}",
    ]
    for index, tag in enumerate(preset.tags, start=1):
        lines.extend(("", "[[tag]]"))
        lines.append(
            f"strategy = {_toml_string(tag.strategy, field=f'tag #{index}.strategy')}"
        )
        lines.append(f"name = {_toml_string(tag.name, field=f'tag #{index}.name')}")
        if tag.value is not None:
            lines.append(
                f"value = {_toml_scalar(tag.value, field=f'tag #{index}.value')}"
            )
        if tag.pattern_id is not None:
            lines.append(
                "pattern_id = "
                + _toml_string(
                    tag.pattern_id,
                    field=f"tag #{index}.pattern_id",
                )
            )

    lines.append("")
    lines.append("[discovery]")
    for field in discovery_fields:
        if field in {"rt_min", "rt_max"} and discovery_values[field] is None:
            continue
        lines.append(f"{field} = {_toml_value(discovery_values[field], field=field)}")
    lines.extend(
        (
            "",
            "[alignment]",
            "owner_build_xic_backend = "
            + _toml_string(
                owner_build_xic_backend,
                field="owner_build_xic_backend",
            ),
            "",
        )
    )
    return "\n".join(lines)


def _require_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise PresetError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise PresetError(f"{field} keys must be strings")
    return dict(value)


def _toml_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise PresetError(f"{field} must be a string")
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_scalar(value: object, *, field: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PresetError(f"{field} must be finite")
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value, field=field)
    raise PresetError(f"{field} must be a TOML scalar")


def _toml_value(value: object, *, field: str) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item, field=field) for item in value) + "]"
    if isinstance(value, Mapping):
        return (
            "{ "
            + ", ".join(
                f"{_toml_string(key, field=field)} = {_toml_value(item, field=field)}"
                for key, item in value.items()
            )
            + " }"
        )
    return _toml_scalar(value, field=field)
