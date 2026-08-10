from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.detected_features import DeltaMassPatternSettings
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.presets.loader import (
    DISCOVERY_TUNING_OVERRIDE_KEYS,
    validate_delta_mass_preset_tags,
)
from xic_extractor.presets.models import Preset, PresetError, PresetTag
from xic_extractor.presets.recipe_models import (
    BoundCidNlRecipe,
    CidNlRecipeRoute,
)
from xic_extractor.settings_schema import RESOLVER_MODES

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
STANDARD_PEAK_PUBLICATION_MODES = frozenset(
    {
        "matrix-only",
        "review-gallery",
        "deep-audit",
    }
)
BACKFILL_EXPANSION_PRODUCTIZATION_MODES = frozenset(
    {
        "off",
        "clean-target-selective",
    }
)
OWNER_BUILD_XIC_BACKENDS = frozenset(
    {
        "raw",
        "raw-super-window",
        "ms1-index",
    }
)
_CID_NL_RECIPE_ROUTES = frozenset(
    {"full", "discovery_only", "align_only", "bounded_mode_track"}
)
_CID_NL_RECIPE_OVERRIDE_KEYS = (
    frozenset(DISCOVERY_TUNING_OVERRIDE_KEYS)
    | {
        "standard_peak_backfill_publication_mode",
        "owner_build_xic_backend",
    }
)


def bind_cid_nl_recipe(
    preset: Preset,
    route: CidNlRecipeRoute,
    *,
    recipe_overrides: Mapping[str, object] | None = None,
) -> BoundCidNlRecipe:
    """Bind one CID-NL preset to an existing V1 execution route."""

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
    if route == "bounded_mode_track" and len(
        discovery_settings.neutral_loss_profiles
    ) != 1:
        raise ValueError("ModeTrack runner requires a single-tag CID-NL preset")

    alignment_config, runtime_options = apply_to_alignment(preset)
    standard_peak_backfill = bool(
        runtime_options.get("standard_peak_backfill", False)
    )
    publication_mode = cast(
        Literal["matrix-only", "review-gallery", "deep-audit"],
        runtime_options.get("standard_peak_backfill_publication_mode", "deep-audit"),
    )
    write_gallery = bool(
        runtime_options.get("standard_peak_backfill_write_gallery", True)
    )
    publication_override = overrides.get(
        "standard_peak_backfill_publication_mode"
    )
    if publication_override is not None:
        if not standard_peak_backfill:
            raise PresetError(
                "--standard-peak-backfill-publication-mode requires a preset "
                "that enables standard_peak_backfill"
            )
        publication_mode = cast(
            Literal["matrix-only", "review-gallery", "deep-audit"],
            _publication_mode_setting(
                {"standard_peak_backfill_publication_mode": publication_override},
                legacy_write_gallery=write_gallery,
            ),
        )
        write_gallery = publication_mode in {"review-gallery", "deep-audit"}

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

    min_shape_r = float(
        cast(
            int | float,
            runtime_options.get("standard_peak_backfill_min_shape_r", 0.95),
        )
    )
    return BoundCidNlRecipe(
        route=route,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
        standard_peak_backfill=standard_peak_backfill,
        standard_peak_backfill_chunk_size=int(
            cast(
                int,
                runtime_options.get("standard_peak_backfill_chunk_size", 120),
            )
        ),
        standard_peak_backfill_publication_mode=publication_mode,
        standard_peak_backfill_write_gallery=write_gallery,
        standard_peak_backfill_reuse_existing=bool(
            runtime_options.get("standard_peak_backfill_reuse_existing", False)
        ),
        standard_peak_backfill_min_shape_r=min_shape_r,
        owner_build_xic_backend=owner_build_xic_backend,
        backfill_expansion_productization=cast(
            Literal["off", "clean-target-selective"],
            runtime_options.get("backfill_expansion_productization", "off"),
        ),
        backfill_expansion_reuse_existing_raw_overlay=bool(
            runtime_options.get(
                "backfill_expansion_reuse_existing_raw_overlay",
                False,
            )
        ),
        backfill_expansion_reuse_existing_shift_aware=bool(
            runtime_options.get(
                "backfill_expansion_reuse_existing_shift_aware",
                False,
            )
        ),
        backfill_expansion_render_shift_aware_images=bool(
            runtime_options.get(
                "backfill_expansion_render_shift_aware_images",
                False,
            )
        ),
        backfill_expansion_min_shape_r=float(
            cast(
                int | float,
                runtime_options.get("backfill_expansion_min_shape_r", min_shape_r),
            )
        ),
        product_ready_publication_check=(
            preset.source == "builtin:dna_dr_product_ready"
        ),
    )


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
        rt_min=_float_setting(tuning, "rt_min", baseline.rt_min),
        rt_max=_float_setting(tuning, "rt_max", baseline.rt_max),
        resolver_mode=_str_setting(tuning, "resolver_mode", baseline.resolver_mode),
    )


def apply_to_delta_mass_discovery(preset: Preset) -> DeltaMassPatternSettings:
    """Map a delta_mass (Pt pattern) preset onto ``DeltaMassPatternSettings``.

    This is the delta_mass counterpart to ``apply_to_discovery()``. It never
    constructs a ``NeutralLossProfile`` -- presets handled here are Pt-pattern
    presets (``pt_pattern``) or Pt-pattern-plus-neutral-loss composites
    (``pt_pattern_cid_nl_dr``); the neutral-loss tag on a composite preset is
    intersection evidence handled downstream (Task 5's pipeline), not a
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
    standard_peak = _bool_setting(overrides, "standard_peak_backfill", False)
    chunk_size = _positive_int_setting(
        overrides,
        "standard_peak_backfill_chunk_size",
        120,
    )
    write_gallery = _bool_setting(
        overrides,
        "standard_peak_backfill_write_gallery",
        True,
    )
    publication_mode = _publication_mode_setting(
        overrides,
        legacy_write_gallery=write_gallery,
    )
    write_gallery = publication_mode in {"review-gallery", "deep-audit"}
    reuse_existing = _bool_setting(
        overrides,
        "standard_peak_backfill_reuse_existing",
        False,
    )
    min_shape_r = _range_float_setting(
        overrides,
        "standard_peak_backfill_min_shape_r",
        0.95,
        minimum=0.0,
        maximum=1.0,
    )
    owner_build_xic_backend = _enum_str_setting(
        overrides,
        "owner_build_xic_backend",
        "raw",
        OWNER_BUILD_XIC_BACKENDS,
    )
    backfill_expansion_productization = _enum_str_setting(
        overrides,
        "backfill_expansion_productization",
        "off",
        BACKFILL_EXPANSION_PRODUCTIZATION_MODES,
    )
    backfill_expansion_reuse_existing_raw_overlay = _bool_setting(
        overrides,
        "backfill_expansion_reuse_existing_raw_overlay",
        False,
    )
    backfill_expansion_reuse_existing_shift_aware = _bool_setting(
        overrides,
        "backfill_expansion_reuse_existing_shift_aware",
        False,
    )
    backfill_expansion_render_shift_aware_images = _bool_setting(
        overrides,
        "backfill_expansion_render_shift_aware_images",
        False,
    )
    backfill_expansion_min_shape_r = _range_float_setting(
        overrides,
        "backfill_expansion_min_shape_r",
        min_shape_r,
        minimum=0.0,
        maximum=1.0,
    )
    if backfill_expansion_productization != "off" and not standard_peak:
        raise PresetError(
            "backfill_expansion_productization requires standard_peak_backfill"
        )
    if not standard_peak:
        return AlignmentConfig(), {"standard_peak_backfill": False}

    return AlignmentConfig(), {
        "standard_peak_backfill": True,
        "standard_peak_backfill_chunk_size": chunk_size,
        "standard_peak_backfill_publication_mode": publication_mode,
        "standard_peak_backfill_write_gallery": write_gallery,
        "standard_peak_backfill_reuse_existing": reuse_existing,
        "standard_peak_backfill_min_shape_r": min_shape_r,
        "owner_build_xic_backend": owner_build_xic_backend,
        "backfill_expansion_productization": backfill_expansion_productization,
        "backfill_expansion_reuse_existing_raw_overlay": (
            backfill_expansion_reuse_existing_raw_overlay
        ),
        "backfill_expansion_reuse_existing_shift_aware": (
            backfill_expansion_reuse_existing_shift_aware
        ),
        "backfill_expansion_render_shift_aware_images": (
            backfill_expansion_render_shift_aware_images
        ),
        "backfill_expansion_min_shape_r": backfill_expansion_min_shape_r,
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


def _publication_mode_setting(
    overrides: Mapping[str, object],
    *,
    legacy_write_gallery: bool,
) -> str:
    value = overrides.get("standard_peak_backfill_publication_mode")
    if value is None:
        return "deep-audit" if legacy_write_gallery else "matrix-only"
    if not isinstance(value, str) or value not in STANDARD_PEAK_PUBLICATION_MODES:
        modes = ", ".join(sorted(STANDARD_PEAK_PUBLICATION_MODES))
        raise PresetError(
            "standard_peak_backfill_publication_mode must be one of: "
            f"{modes}"
        )
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


def _positive_int_setting(
    overrides: Mapping[str, object],
    field: str,
    default: int,
) -> int:
    value = overrides.get(field, default)
    if type(value) is not int or value < 1:
        raise PresetError(f"{field} must be an integer >= 1")
    return value


def _range_float_setting(
    overrides: Mapping[str, object],
    field: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = overrides.get(field, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not minimum <= float(value) <= maximum
    ):
        raise PresetError(
            f"{field} must be a finite number between {minimum} and {maximum}",
        )
    return float(value)


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
        if field in tuning and not _is_rt_bound(tuning[field]):
            raise PresetError(f"{field} must be finite and >= 0")

    if "resolver_mode" in tuning:
        resolver_mode = tuning["resolver_mode"]
        if not isinstance(resolver_mode, str) or resolver_mode not in RESOLVER_MODES:
            modes = ", ".join(RESOLVER_MODES)
            raise PresetError(f"resolver_mode must be one of: {modes}")

    rt_min = _float_setting(tuning, "rt_min", baseline.rt_min)
    rt_max = _float_setting(tuning, "rt_max", baseline.rt_max)
    if rt_min > rt_max:
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
