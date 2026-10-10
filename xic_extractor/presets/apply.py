from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.config import AlignmentConfig
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
from xic_extractor.settings_schema import RESOLVER_MODES, normalize_resolver_mode

if TYPE_CHECKING:
    from xic_extractor.discovery.delta_mass_strategy import (
        DeltaMassPatternSettings,
        PtPatternDiscoveryStrategy,
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
OWNER_BUILD_XIC_BACKENDS = frozenset(
    {
        "raw",
        "raw-super-window",
        "ms1-index",
    }
)
_CID_NL_RECIPE_ROUTES = frozenset({"full", "discovery_only", "align_only"})
_CID_NL_RECIPE_OVERRIDE_KEYS = frozenset(DISCOVERY_TUNING_OVERRIDE_KEYS) | {
    "owner_build_xic_backend",
}


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
    alignment_config, runtime_options = apply_to_alignment(preset)
    owner_build_xic_backend = _owner_build_xic_backend_projection(
        runtime_options.get("owner_build_xic_backend", "raw")
    )
    if "owner_build_xic_backend" in overrides:
        owner_build_xic_backend = _owner_build_xic_backend_projection(
            _enum_str_setting(
                overrides,
                "owner_build_xic_backend",
                "raw",
                OWNER_BUILD_XIC_BACKENDS,
            )
        )

    return BoundCidNlRecipe(
        route=route,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
        owner_build_xic_backend=owner_build_xic_backend,
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


def _owner_build_xic_backend_projection(
    value: object,
) -> Literal["raw", "raw_superwindow", "ms1_index"]:
    text = str(value)
    if text == "raw":
        return "raw"
    if text in {"raw-super-window", "raw_superwindow"}:
        return "raw_superwindow"
    if text in {"ms1-index", "ms1_index"}:
        return "ms1_index"
    raise PresetError(f"unsupported owner_build_xic_backend: {text}")


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
    )


def resolve_pt_pattern_discovery(preset_name: str) -> PtPatternDiscoveryStrategy:
    """Load a Pt pattern preset and resolve its Discovery strategy.

    Rejects presets without a ``delta_mass`` tag; a ``neutral_loss`` tag marks
    the composite Pt pattern + CID-NL(dR) strategy.
    """
    preset = load_preset(preset_name)
    if not any(tag.strategy == "delta_mass" for tag in preset.tags):
        raise ValueError(
            f"preset {preset_name!r} is not a 'pt_pattern' preset (no "
            "'delta_mass' tag); use a Pt pattern preset such as 'pt_pattern' "
            "or 'pt_pattern_cid_nl_dr'"
        )
    from xic_extractor.discovery.delta_mass_strategy import PtPatternDiscoveryStrategy

    return PtPatternDiscoveryStrategy(
        label=preset_name,
        settings=apply_to_delta_mass_discovery(preset),
        cid_nl_composite=any(tag.strategy == "neutral_loss" for tag in preset.tags),
    )


def apply_to_delta_mass_discovery(preset: Preset) -> DeltaMassPatternSettings:
    """Map a delta_mass (Pt pattern) preset onto ``DeltaMassPatternSettings``.

    This is the delta_mass counterpart to ``apply_to_discovery()``. It never
    constructs a ``NeutralLossProfile`` -- presets handled here are Pt-pattern
    presets (``pt_pattern``) or Pt-pattern-plus-neutral-loss composites
    (``pt_pattern_cid_nl_dr``); the neutral-loss tag on a composite preset is
    intersection evidence handled downstream by the Discovery pipeline, not a
    ``NeutralLossProfile`` to build here.
    """
    if not any(tag.strategy == "delta_mass" for tag in preset.tags):
        raise PresetError(
            f"preset {preset.name!r} has no 'delta_mass' tag; "
            "use apply_to_discovery() for neutral-loss presets"
        )
    validate_delta_mass_preset_tags(
        preset.tags,
        combine_mode=preset.combine_mode,
    )

    overrides = dict(preset.discovery_overrides)
    method_label = overrides.get("method_label")
    if not isinstance(method_label, str) or not method_label.strip():
        raise PresetError(
            f"preset {preset.name!r} must set [discovery] method_label for "
            "delta_mass presets"
        )

    from xic_extractor.discovery.delta_mass_strategy import DeltaMassPatternSettings

    defaults = DeltaMassPatternSettings()
    return DeltaMassPatternSettings(
        method_label=method_label,
        mz_tolerance_ppm=_positive_float_setting(
            overrides,
            "delta_mass_mz_tolerance_ppm",
            defaults.mz_tolerance_ppm,
        ),
        rt_tolerance_sec=_positive_float_setting(
            overrides,
            "delta_mass_rt_tolerance_sec",
            defaults.rt_tolerance_sec,
        ),
        ratio_basis=_enum_str_setting(
            overrides,
            "delta_mass_ratio_basis",
            defaults.ratio_basis,
            choices=frozenset({"both"}),
        ),
    )


def apply_to_alignment(preset: Preset) -> tuple[AlignmentConfig, dict[str, object]]:
    overrides = dict(preset.alignment_overrides)
    reject_retired_alignment_runtime_options(overrides)
    owner_build_xic_backend = _enum_str_setting(
        overrides,
        "owner_build_xic_backend",
        "raw",
        OWNER_BUILD_XIC_BACKENDS,
    )
    return AlignmentConfig(), {
        "owner_build_xic_backend": owner_build_xic_backend,
    }


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


def _bool_setting(
    overrides: Mapping[str, object],
    field: str,
    default: bool,
) -> bool:
    value = overrides.get(field, default)
    if type(value) is not bool:
        raise PresetError(f"{field} must be true or false")
    return value


def _enum_str_setting(
    overrides: Mapping[str, object],
    field: str,
    default: str,
    choices: frozenset[str],
) -> str:
    value = overrides.get(field, default)
    if not isinstance(value, str) or value not in choices:
        supported = ", ".join(sorted(choices))
        raise PresetError(f"{field} must be one of: {supported}")
    return value


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
        if (
            not isinstance(resolver_mode, str)
            or normalize_resolver_mode(resolver_mode) not in RESOLVER_MODES
        ):
            modes = ", ".join(RESOLVER_MODES)
            raise PresetError(f"resolver_mode must be one of: {modes}")

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
