"""Qt-free CID-NL method editing for the local workbench."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xic_extractor.presets import (
    Preset,
    PresetError,
    PresetTag,
    apply_to_discovery,
    filter_cid_nl_recipe_presets,
    list_presets,
    load_preset,
    save_preset,
)
from xic_extractor.presets.loader import DISCOVERY_TUNING_OVERRIDE_KEYS
from xic_extractor.presets.models import SUPPORTED_COMBINE_MODES
from xic_extractor.presets.serialization import serialize_preset
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.settings_schema import RESOLVER_MODES

_DISPLAY_NAMES = {
    "dna_dr": "dR · DNA · 116.0474 Da",
    "rna_ribose": "R · RNA · 132.0423 Da",
    "rna_mer": "MeR · RNA · 146.0579 Da",
}
_STARTING_TAGS = (
    ("DNA_dR", 116.0474),
    ("RNA_ribose", 132.0423),
    ("RNA_MeR", 146.0579),
)
_NUMERIC_CONTROLS = (
    # These controls accept continuous values. HTML step is anchored at min,
    # so a fixed increment rejects valid defaults (for example 20 from 0.01).
    ("nl_tolerance_ppm", "NL 容差 (ppm)", 0.01, 1000.0, "any"),
    ("precursor_mz_tolerance_ppm", "Precursor 容差 (ppm)", 0.01, 1000.0, "any"),
    ("product_mz_tolerance_ppm", "Product 容差 (ppm)", 0.01, 1000.0, "any"),
    ("product_search_ppm", "Product 搜尋 (ppm)", 0.01, 1000.0, "any"),
    ("nl_min_intensity_ratio", "NL 最低強度比", 0.0001, 1.0, "any"),
    ("seed_rt_gap_min", "Seed RT 間隔 (min)", 0.001, 60.0, "any"),
    ("ms1_search_padding_min", "MS1 RT padding (min)", 0.001, 60.0, "any"),
    ("rt_min", "RT 下界 (min)", 0.0, None, "any"),
    ("rt_max", "RT 上界 (min)", 0.0, None, "any"),
)
_OVERRIDE_KEYS = frozenset(key for key, *_ in _NUMERIC_CONTROLS) | {"resolver_mode"}


def list_methods() -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    for key in filter_cid_nl_recipe_presets(list_presets()):
        if key not in _DISPLAY_NAMES:
            continue
        preset = load_preset(key)
        methods.append(
            {
                "key": key,
                "display_name": _DISPLAY_NAMES[key],
                "name": preset.name,
                "description": preset.description,
                "source": preset.source,
            }
        )
    return methods


def method_details(preset: str | Path) -> dict[str, Any]:
    loaded = load_preset(preset)
    overrides: dict[str, object] = dict(loaded.discovery_overrides)
    _validate_gui_method(loaded, overrides)
    settings = apply_to_discovery(loaded, explicit_tuning_overrides=overrides or None)
    draft = preset_to_payload(loaded)
    draft["overrides"] = overrides
    selected = {tag.name for tag in loaded.tags}
    for name, value in _STARTING_TAGS:
        if name not in selected:
            draft["tags"].append(
                {
                    "enabled": False,
                    "strategy": "neutral_loss",
                    "name": name,
                    "neutral_loss_da": value,
                    "pattern_id": None,
                }
            )
    effective = {key: getattr(settings, key) for key in _OVERRIDE_KEYS}
    return {
        "preset": preset_to_payload(loaded),
        "draft": draft,
        "preview": {
            "combine_mode": loaded.combine_mode,
            "tags": [
                {"name": tag.name, "neutral_loss_da": tag.value} for tag in loaded.tags
            ],
            "rt_min": settings.rt_min,
            "rt_max": settings.rt_max,
            "rt_range_mode": (
                "acquisition"
                if settings.rt_min is None and settings.rt_max is None
                else "crop"
            ),
            "nl_tolerance_ppm": settings.nl_tolerance_ppm,
            "precursor_mz_tolerance_ppm": settings.precursor_mz_tolerance_ppm,
        },
        "tuning_controls": tuning_controls(),
        "effective_values": effective,
    }


def tuning_controls() -> list[dict[str, Any]]:
    controls = [
        {
            "key": key,
            "label": label,
            "kind": "number",
            "minimum": minimum,
            **({"maximum": maximum} if maximum is not None else {}),
            "step": step,
            **({"optional": True} if key in {"rt_min", "rt_max"} else {}),
        }
        for key, label, minimum, maximum, step in _NUMERIC_CONTROLS
    ]
    controls.append(
        {
            "key": "resolver_mode",
            "label": "Resolver 模式",
            "kind": "choice",
            "choices": list(RESOLVER_MODES),
        }
    )
    return controls


def validate_method(values: dict[str, Any]) -> dict[str, Any]:
    preset, overrides = _draft(values)
    _validate_gui_method(preset, overrides)
    settings = apply_to_discovery(
        preset,
        explicit_tuning_overrides=overrides or None,
    )
    return {
        "preset": preset_to_payload(preset),
        "overrides": dict(overrides),
        "effective_values": {key: getattr(settings, key) for key in _OVERRIDE_KEYS},
    }


def save_method(values: dict[str, Any], destination: Path) -> dict[str, Any]:
    preset, overrides = _draft(values)
    _validate_gui_method(preset, overrides)
    path = Path(destination).resolve()
    saved = save_preset(preset, path, discovery_overrides=overrides)
    return {"path": str(path), "preset": preset_to_payload(saved)}


def preset_to_payload(preset: Preset) -> dict[str, Any]:
    return {
        "name": preset.name,
        "description": preset.description,
        "tags": [
            {
                "enabled": True,
                "strategy": tag.strategy,
                "name": tag.name,
                "neutral_loss_da": tag.value,
                "pattern_id": tag.pattern_id,
            }
            for tag in preset.tags
        ],
        "combine_mode": preset.combine_mode,
        "overrides": dict(preset.discovery_overrides),
        "alignment_overrides": dict(preset.alignment_overrides),
        "source": preset.source,
    }


def preset_from_payload(values: object) -> Preset:
    if not isinstance(values, dict):
        raise TypeError("preset_snapshot must be an object")
    preset, overrides = _draft(values)
    _validate_gui_method(preset, overrides)
    return Preset(
        name=preset.name,
        description=preset.description,
        tags=preset.tags,
        combine_mode=preset.combine_mode,
        discovery_overrides={**preset.discovery_overrides, **overrides},
        alignment_overrides=preset.alignment_overrides,
        source=preset.source,
    )


def _draft(values: dict[str, Any]) -> tuple[Preset, dict[str, object]]:
    if not isinstance(values, dict):
        raise TypeError("method draft must be an object")
    allowed = {
        "name",
        "description",
        "tags",
        "combine_mode",
        "overrides",
        "alignment_overrides",
        "source",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported method field: {unknown[0]}")
    name = _required_text(values.get("name"), "method name")
    description = values.get("description", "")
    if not isinstance(description, str):
        raise TypeError("method description must be text")
    combine_mode = values.get("combine_mode")
    if combine_mode not in SUPPORTED_COMBINE_MODES:
        raise ValueError(f"unsupported combine_mode: {combine_mode!r}")
    raw_tags = values.get("tags")
    if not isinstance(raw_tags, list):
        raise TypeError("method tags must be a list")
    tags: list[PresetTag] = []
    for index, raw in enumerate(raw_tags, start=1):
        if not isinstance(raw, dict):
            raise TypeError(f"tags[{index}] must be an object")
        tag_unknown = sorted(
            set(raw)
            - {"enabled", "strategy", "name", "neutral_loss_da", "value", "pattern_id"}
        )
        if tag_unknown:
            raise ValueError(f"tags[{index}] has unsupported field: {tag_unknown[0]}")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError(f"tags[{index}].enabled must be boolean")
        if not enabled:
            continue
        strategy = raw.get("strategy", "neutral_loss")
        if strategy != "neutral_loss" or raw.get("pattern_id") is not None:
            raise PresetError("GUI 僅支援純 CID-NL 方法；原方法未變更")
        value = raw.get("neutral_loss_da", raw.get("value"))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"tags[{index}] requires a neutral_loss_da number")
        tags.append(
            PresetTag(
                strategy="neutral_loss",
                name=_required_text(raw.get("name"), f"tags[{index}].name"),
                value=float(value),
            )
        )
    overrides = values.get("overrides", {})
    if not isinstance(overrides, Mapping):
        raise TypeError("method overrides must be an object")
    unknown_overrides = sorted(set(overrides) - DISCOVERY_TUNING_OVERRIDE_KEYS)
    if unknown_overrides:
        raise ValueError(f"unsupported override: {unknown_overrides[0]}")
    alignment_overrides = values.get("alignment_overrides", {})
    if not isinstance(alignment_overrides, Mapping):
        raise TypeError("alignment_overrides must be an object")
    source = values.get("source", "workbench:draft")
    if not isinstance(source, str):
        raise TypeError("method source must be text")
    return (
        Preset(
            name=name,
            description=description,
            tags=tuple(tags),
            combine_mode=combine_mode,
            discovery_overrides={},
            alignment_overrides=dict(alignment_overrides),
            source=source,
        ),
        dict(overrides),
    )


def _validate_gui_method(
    preset: Preset, overrides: Mapping[str, object] | None = None
) -> None:
    if any(
        tag.strategy != "neutral_loss" or tag.pattern_id is not None
        for tag in preset.tags
    ):
        raise PresetError("GUI 僅支援純 CID-NL 方法；原方法未變更")
    settings = apply_to_discovery(preset, explicit_tuning_overrides=overrides)
    CidNlDiscoveryMethodSpec(settings=settings)
    serialize_preset(preset, discovery_overrides=overrides)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()
