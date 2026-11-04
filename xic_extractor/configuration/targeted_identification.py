"""Reusable Targeted identification methods, independent of targets and I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from xic_extractor.configuration.delta_mass import (
    DeltaMassPatternSettings,
    DeltaMassPosition,
)
from xic_extractor.validation_primitives import require_finite_real

BUILTIN_TARGET_METHODS = ("ms1", "cid:dR", "cid:R", "cid:MeR")


@dataclass(frozen=True)
class CidNlCondition:
    tag: str
    kind: Literal["cid_nl"] = "cid_nl"


@dataclass(frozen=True)
class QuantRelativeIon:
    label: str
    offset_mz: float
    ratio_min: float = 0.1
    ratio_max: float = 10.0
    compare_shape: bool = False

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("position requires a non-empty label")
        for key in ("offset_mz", "ratio_min", "ratio_max"):
            require_finite_real(getattr(self, key), name=key)
        if not 0 < self.ratio_min <= self.ratio_max:
            raise ValueError("reference ratios must be positive and ordered")
        if not isinstance(self.compare_shape, bool):
            raise ValueError("compare_shape must be true or false")


@dataclass(frozen=True)
class DeltaMassCondition:
    positions: tuple[QuantRelativeIon, ...]
    label: str = "Delta Mass"
    apex_floor_sec: float = 1.0
    scan_interval_multiplier: float = 1.25
    shape_window_sec: float = 10.0
    min_shape_r: float = 0.9
    kind: Literal["delta_mass"] = "delta_mass"

    @property
    def quant_position(self):
        return next(p.label for p in self.positions if p.offset_mz == 0)

    def pattern_settings(self, ppm: float, method_name: str, index: int):
        lowest = min(p.offset_mz for p in self.positions)
        return DeltaMassPatternSettings(
            pattern_id=f"{method_name}:{index}",
            method_label=self.label,
            positions=tuple(
                DeltaMassPosition(
                    p.label,
                    p.offset_mz - lowest,
                    p.ratio_min,
                    p.ratio_max,
                )
                for p in sorted(self.positions, key=lambda p: p.offset_mz)
            ),
            anchor_positions=(self.quant_position,),
            discriminating_positions=tuple(
                p.label for p in self.positions if p.compare_shape
            ),
            mz_tolerance_ppm=ppm,
            apex_floor_sec=self.apex_floor_sec,
            scan_interval_multiplier=self.scan_interval_multiplier,
            shape_window_sec=self.shape_window_sec,
            min_shape_r=self.min_shape_r,
            shape_reference_position=self.quant_position,
            ratio_reference_position=self.quant_position,
        )


IdentificationCondition = CidNlCondition | DeltaMassCondition


@dataclass(frozen=True)
class IdentificationMethod:
    name: str
    conditions: tuple[IdentificationCondition, ...]
    combine_mode: Literal["all", "any"] = "all"
    mz_tolerance_ppm: float = 20.0

    def accepts(self, statuses) -> bool:
        if not self.conditions:
            return True
        matched = tuple(status == "matched" for status in statuses)
        return all(matched) if self.combine_mode == "all" else any(matched)


def parse_identification_methods(values: Mapping) -> dict[str, IdentificationMethod]:
    """Admit externally edited method definitions once at their input boundary."""
    if not isinstance(values, Mapping):
        raise ValueError("identification_methods must be a named method object")
    methods = {}
    for name, value in values.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or name in BUILTIN_TARGET_METHODS
        ):
            raise ValueError(
                "method names must be nonempty, trimmed and not built-in names"
            )
        if not isinstance(value, Mapping) or set(value) - {
            "conditions",
            "combine_mode",
            "mz_tolerance_ppm",
        }:
            raise ValueError(f"{name}: unsupported method fields")
        try:
            mode = value.get("combine_mode", "all")
            if mode not in ("all", "any"):
                raise ValueError("combine_mode must be all or any")
            ppm = value.get("mz_tolerance_ppm", 20.0)
            require_finite_real(ppm, name="mz_tolerance_ppm")
            if ppm <= 0:
                raise ValueError("mz_tolerance_ppm must be positive")
            raw_conditions = value.get("conditions", [])
            if not isinstance(raw_conditions, list):
                raise ValueError("conditions must be a list")
            conditions: list[IdentificationCondition] = []
            for raw in raw_conditions:
                if not isinstance(raw, Mapping):
                    raise ValueError("each condition must be an object")
                if raw.get("kind") == "cid_nl":
                    if set(raw) != {"kind", "tag"} or raw["tag"] not in (
                        "dR",
                        "R",
                        "MeR",
                    ):
                        raise ValueError("CID-NL requires dR, R or MeR")
                    conditions.append(CidNlCondition(tag=raw["tag"]))
                elif raw.get("kind") == "delta_mass":
                    fields = {**raw}
                    fields.pop("kind")
                    positions = tuple(
                        QuantRelativeIon(**p) for p in fields.pop("positions")
                    )
                    labels = [p.label for p in positions]
                    offsets = [p.offset_mz for p in positions]
                    if (
                        len(positions) < 2
                        or len(set(labels)) != len(labels)
                        or len(set(offsets)) != len(offsets)
                        or offsets.count(0) != 1
                    ):
                        raise ValueError(
                            "Delta Mass needs distinct positions, including "
                            "exactly one quantitation ion at zero"
                        )
                    condition = DeltaMassCondition(positions=positions, **fields)
                    condition.pattern_settings(ppm, name, len(conditions))
                    conditions.append(condition)
                else:
                    raise ValueError(
                        "supported conditions are cid_nl and delta_mass; "
                        "HCD-PI is not implemented"
                    )
            methods[name] = IdentificationMethod(name, tuple(conditions), mode, ppm)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name}: invalid identification method: {exc}") from exc
    return methods


def identification_methods_payload(methods: Mapping[str, IdentificationMethod]) -> dict:
    result = {}
    for name, method in methods.items():
        value = asdict(method)
        value.pop("name")
        value["conditions"] = list(value["conditions"])
        for condition in value["conditions"]:
            if condition["kind"] == "delta_mass":
                condition["positions"] = list(condition["positions"])
        result[name] = value
    return result


def target_cid_nl_tags(target) -> tuple[str, ...]:
    if target.identification_method is not None:
        return tuple(
            dict.fromkeys(
                c.tag
                for c in target.identification_method.conditions
                if isinstance(c, CidNlCondition)
            )
        )
    return (target.cid_nl_tag,) if target.neutral_loss_da is not None else ()
