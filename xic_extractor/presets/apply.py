from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.mass_recalibration import resolve_mass_recalibration
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.presets.loader import (
    DISCOVERY_TUNING_OVERRIDE_KEYS,
    load_preset,
    reject_retired_alignment_runtime_options,
    validate_delta_mass_preset_tags,
)
from xic_extractor.presets.models import Preset, PresetError, PresetTag
from xic_extractor.presets.recipe_models import (
    BoundCidNlRecipe,
    CidNlRecipeRoute,
)
from xic_extractor.settings_schema import RESOLVER_MODES

if TYPE_CHECKING:
    from xic_extractor.configuration.delta_mass import DeltaMassPatternSettings
    from xic_extractor.discovery.delta_mass_strategy import (
        DeltaMassDiscoveryStrategy,
    )

_POSITIVE_FLOAT_FIELDS = frozenset(
    {
        "nl_tolerance_ppm",
        "precursor_mz_tolerance_ppm",
        "product_mz_tolerance_ppm",
        "product_search_ppm",
        "ms2_precursor_tol_da",
        "nl_min_intensity_ratio",
        "seed_rt_gap_min",
        "ms1_search_padding_min",
    }
)
_RT_FIELDS = frozenset({"rt_min", "rt_max"})
_CID_NL_RECIPE_ROUTES = frozenset({"full", "discovery_only", "align_only"})
_CID_NL_RECIPE_OVERRIDE_KEYS = DISCOVERY_TUNING_OVERRIDE_KEYS


def bind_cid_nl_recipe(
    preset: Preset,
    route: CidNlRecipeRoute,
    *,
    recipe_overrides: Mapping[str, object] | None = None,
) -> BoundCidNlRecipe:
    """Bind one CID-NL preset to an existing execution route."""

    if route not in _CID_NL_RECIPE_ROUTES:
        supported = ", ".join(sorted(_CID_NL_RECIPE_ROUTES))
        raise PresetError(f"CID-NL recipe route must be one of: {supported}")

    overrides = dict(recipe_overrides or {})
    unknown = sorted(set(overrides) - _CID_NL_RECIPE_OVERRIDE_KEYS)
    if unknown:
        raise PresetError(f"unknown CID-NL recipe override: {unknown[0]}")

    discovery_overrides = {
        key: value
        for key, value in overrides.items()
        if key in DISCOVERY_TUNING_OVERRIDE_KEYS
    }
    discovery_settings = apply_to_discovery(
        preset,
        explicit_tuning_overrides=discovery_overrides or None,
    )
    alignment_config = apply_to_alignment(preset)
    return BoundCidNlRecipe(
        route=route,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
    )


def filter_cid_nl_recipe_presets(
    preset_names: Iterable[str],
    *,
    route: CidNlRecipeRoute = "discovery_only",
) -> tuple[str, ...]:
    """Return only presets accepted by the package-owned CID-NL binder."""

    accepted: list[str] = []
    for preset_name in preset_names:
        if not isinstance(preset_name, str) or not preset_name:
            raise TypeError("preset_names must contain non-empty text")
        preset = load_preset(preset_name)
        try:
            bind_cid_nl_recipe(preset, route)
        except (PresetError, ValueError):
            continue
        accepted.append(preset_name)
    return tuple(accepted)


def apply_to_discovery(
    preset: Preset,
    *,
    explicit_tuning_overrides: Mapping[str, object] | None = None,
) -> DiscoverySettings:
    if any(tag.strategy == "delta_mass" for tag in preset.tags):
        raise PresetError(
            f"preset {preset.name!r} contains a 'delta_mass' tag and is not a "
            "neutral-loss preset; use apply_to_delta_mass_discovery() instead"
        )

    neutral_loss_profiles = tuple(
        NeutralLossProfile(tag=tag.name, neutral_loss_da=_neutral_loss_value(tag))
        for tag in preset.tags
    )
    baseline = DiscoverySettings(
        neutral_loss_profiles=neutral_loss_profiles,
        selected_tag_names=tuple(tag.name for tag in preset.tags),
        tag_combine_mode=preset.combine_mode,
    )

    tuning = dict(preset.discovery_overrides)
    if explicit_tuning_overrides is not None:
        _reject_unknown_explicit_tuning(explicit_tuning_overrides)
        tuning.update(explicit_tuning_overrides)

    _validate_discovery_tuning(tuning, baseline=baseline)
    return DiscoverySettings(
        neutral_loss_profiles=neutral_loss_profiles,
        selected_tag_names=baseline.selected_tag_names,
        tag_combine_mode=baseline.tag_combine_mode,
        nl_tolerance_ppm=_float_setting(
            tuning,
            "nl_tolerance_ppm",
            baseline.nl_tolerance_ppm,
        ),
        precursor_mz_tolerance_ppm=_float_setting(
            tuning,
            "precursor_mz_tolerance_ppm",
            baseline.precursor_mz_tolerance_ppm,
        ),
        product_mz_tolerance_ppm=_float_setting(
            tuning,
            "product_mz_tolerance_ppm",
            baseline.product_mz_tolerance_ppm,
        ),
        product_search_ppm=_float_setting(
            tuning,
            "product_search_ppm",
            baseline.product_search_ppm,
        ),
        ms2_precursor_tol_da=_float_setting(
            tuning,
            "ms2_precursor_tol_da",
            baseline.ms2_precursor_tol_da,
        ),
        nl_min_intensity_ratio=_float_setting(
            tuning,
            "nl_min_intensity_ratio",
            baseline.nl_min_intensity_ratio,
        ),
        seed_rt_gap_min=_float_setting(
            tuning,
            "seed_rt_gap_min",
            baseline.seed_rt_gap_min,
        ),
        ms1_search_padding_min=_float_setting(
            tuning,
            "ms1_search_padding_min",
            baseline.ms1_search_padding_min,
        ),
        rt_min=_optional_float_setting(tuning, "rt_min", baseline.rt_min),
        rt_max=_optional_float_setting(tuning, "rt_max", baseline.rt_max),
        resolver_mode=_str_setting(tuning, "resolver_mode", baseline.resolver_mode),
        mass_recalibration=resolve_mass_recalibration(
            _str_setting(
                tuning, "mass_recalibration", baseline.mass_recalibration.value
            )
        ),
    )


def resolve_delta_mass_discovery(preset_name: str) -> DeltaMassDiscoveryStrategy:
    """Resolve a preset into the method and detector settings for one run."""
    from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy

    preset = load_preset(preset_name)
    settings = apply_to_delta_mass_discovery(preset)
    detector = preset.discovery_overrides.get("detector", {})
    if not isinstance(detector, Mapping):
        raise PresetError("discovery.detector must be a table")
    unknown = set(detector) - {"smoothing_window_sec"}
    if unknown:
        raise PresetError(f"unknown delta-mass detector setting: {sorted(unknown)[0]}")
    smoothing = _positive_float_setting(detector, "smoothing_window_sec", 6.6)
    return DeltaMassDiscoveryStrategy(
        label=preset_name,
        settings=settings,
        cid_nl_composite=any(tag.strategy == "neutral_loss" for tag in preset.tags),
        smoothing_window_sec=smoothing,
    )


def apply_to_delta_mass_discovery(preset: Preset) -> DeltaMassPatternSettings:
    """Admit preset positions and roles, without embedding a chemical pattern."""
    from xic_extractor.configuration.delta_mass import (
        DeltaMassPatternSettings,
        DeltaMassPosition,
    )

    validate_delta_mass_preset_tags(preset.tags, combine_mode=preset.combine_mode)
    overrides = preset.discovery_overrides
    raw_positions = overrides.get("positions")
    if not isinstance(raw_positions, list):
        raise PresetError("delta-mass discovery requires positions")
    positions = []
    for raw in raw_positions:
        if not isinstance(raw, Mapping) or set(raw) != {
            "label",
            "delta_da",
            "ratio_min",
            "ratio_max",
        }:
            raise PresetError(
                "each position requires label, delta_da, ratio_min, ratio_max"
            )
        positions.append(
            DeltaMassPosition(
                label=_str_setting(raw, "label", ""),
                delta_da=cast(float, raw["delta_da"]),
                ratio_min=cast(float, raw["ratio_min"]),
                ratio_max=cast(float, raw["ratio_max"]),
            )
        )
    roles = {}
    for key in (
        "anchor_positions",
        "discriminating_positions",
        "base_support_positions",
    ):
        value = overrides.get(key, [] if key == "base_support_positions" else None)
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise PresetError(f"{key} must be an array of labels")
        roles[key] = tuple(value)
    tag = next(t for t in preset.tags if t.strategy == "delta_mass")
    assert tag.pattern_id is not None
    return DeltaMassPatternSettings(
        pattern_id=tag.pattern_id,
        method_label=_str_setting(overrides, "method_label", tag.pattern_id),
        positions=tuple(positions),
        anchor_positions=roles["anchor_positions"],
        discriminating_positions=roles["discriminating_positions"],
        mz_tolerance_ppm=_positive_float_setting(
            overrides, "delta_mass_mz_tolerance_ppm", 20
        ),
        apex_floor_sec=_positive_float_setting(overrides, "apex_floor_sec", 1),
        scan_interval_multiplier=_positive_float_setting(
            overrides, "scan_interval_multiplier", 1.25
        ),
        shape_window_sec=_positive_float_setting(overrides, "shape_window_sec", 10),
        min_shape_r=cast(float, overrides.get("min_shape_r", 0.9)),
        lowest_position=_str_setting(overrides, "lowest_position", ""),
        base_support_positions=roles["base_support_positions"],
        min_base_support=cast(float, overrides.get("min_base_support", 0.25)),
        background_inner_sec=_positive_float_setting(
            overrides, "background_inner_sec", 20
        ),
        background_outer_sec=_positive_float_setting(
            overrides, "background_outer_sec", 30
        ),
        min_height_above_background=_positive_float_setting(
            overrides, "min_height_above_background", 25_000
        ),
        min_background_sigma=_positive_float_setting(
            overrides, "min_background_sigma", 5
        ),
    )


def apply_to_alignment(preset: Preset) -> AlignmentConfig:
    overrides = dict(preset.alignment_overrides)
    reject_retired_alignment_runtime_options(overrides)
    if overrides:
        raise PresetError(f"unsupported alignment setting: {next(iter(overrides))}")
    return AlignmentConfig()


def _neutral_loss_value(tag: PresetTag) -> float:
    """Return a neutral-loss tag's numeric value.

    Only reachable from ``apply_to_discovery()`` after it has already
    rejected any ``delta_mass`` tag, so every remaining tag is
    ``strategy == "neutral_loss"``, for which the loader (``_parse_neutral_loss_tag``)
    guarantees ``value`` is a finite float, never ``None``.
    """
    if tag.value is None:
        raise PresetError(f"tag {tag.name!r} is missing a neutral-loss value")
    return tag.value


def _reject_unknown_explicit_tuning(overrides: Mapping[str, object]) -> None:
    unknown = sorted(set(overrides) - DISCOVERY_TUNING_OVERRIDE_KEYS)
    if unknown:
        raise PresetError(f"unknown explicit discovery tuning override: {unknown[0]}")


def _float_setting(
    tuning: Mapping[str, object],
    field: str,
    default: float,
) -> float:
    value = tuning.get(field, default)
    return float(cast(int | float, value))


def _optional_float_setting(
    tuning: Mapping[str, object], field: str, default: float | None
) -> float | None:
    value = tuning.get(field, default)
    return None if value is None else float(cast(int | float, value))


def _positive_float_setting(
    overrides: Mapping[str, object],
    field: str,
    default: float,
) -> float:
    value = overrides.get(field, default)
    if not _is_positive_float(value):
        raise PresetError(f"{field} must be finite and positive")
    return float(cast(int | float, value))


def _str_setting(
    tuning: Mapping[str, object],
    field: str,
    default: str,
) -> str:
    return cast(str, tuning.get(field, default))


def _validate_discovery_tuning(
    tuning: Mapping[str, object],
    *,
    baseline: DiscoverySettings,
) -> None:
    unknown = sorted(set(tuning) - DISCOVERY_TUNING_OVERRIDE_KEYS)
    if unknown:
        raise PresetError(f"unknown discovery tuning override: {unknown[0]}")

    for field in sorted(_POSITIVE_FLOAT_FIELDS):
        if field in tuning and not _is_positive_float(tuning[field]):
            raise PresetError(f"{field} must be finite and positive")

    for field in sorted(_RT_FIELDS):
        if (
            field in tuning
            and tuning[field] is not None
            and not _is_rt_bound(tuning[field])
        ):
            raise PresetError(f"{field} must be finite and >= 0 or None")

    if "resolver_mode" in tuning:
        resolver_mode = tuning["resolver_mode"]
        if not isinstance(resolver_mode, str) or resolver_mode not in RESOLVER_MODES:
            modes = ", ".join(RESOLVER_MODES)
            raise PresetError(f"resolver_mode must be one of: {modes}")

    if "mass_recalibration" in tuning:
        try:
            resolve_mass_recalibration(tuning["mass_recalibration"])
        except ValueError as exc:
            raise PresetError(str(exc)) from None

    rt_min = _optional_float_setting(tuning, "rt_min", baseline.rt_min)
    rt_max = _optional_float_setting(tuning, "rt_max", baseline.rt_max)
    if rt_min is not None and rt_max is not None and rt_min > rt_max:
        raise PresetError("rt_min must be <= rt_max")


def _is_positive_float(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value > 0
    )


def _is_rt_bound(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value >= 0
    )
