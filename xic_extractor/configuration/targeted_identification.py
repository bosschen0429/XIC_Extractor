"""Per-target Targeted specificity: CID-NL neutral loss and Delta Mass ions.

A target is MS1 only, CID-NL, Delta Mass, or CID-NL + Delta Mass. CID-NL alone
is ranking support for the quantitation peak. When a target declares Delta Mass
ions, every declared condition must match the same peak.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Literal

from xic_extractor.configuration.delta_mass import (
    DeltaMassPatternSettings,
    DeltaMassPosition,
)
from xic_extractor.validation_primitives import require_finite_real

DELTA_MASS_LABEL = "Delta Mass"
# The quantitation ion is the declared target m/z; it does not occupy an ion row.
BASE_POSITION = "base"
# Earlier target tables named common CID-NL losses; the values are the
# packaged CID-NL presets' losses.
LEGACY_CID_NL_LOSSES = {"dR": 116.0474, "R": 132.0423, "MeR": 146.0579}


def ion_name(offset_mz: float) -> str:
    """Persisted name of an ion added without one: ASCII sign, four decimals."""
    return f"{offset_mz:+.4f}"


def format_offset(offset_mz: float) -> str:
    """Exact text of an offset in a target table cell."""
    return f"{offset_mz:+}"


@dataclass(frozen=True)
class CidNlCondition:
    neutral_loss_da: float
    kind: Literal["cid_nl"] = "cid_nl"

    @property
    def label(self) -> str:
        return f"CID-NL {self.neutral_loss_da:.4f}"


@dataclass(frozen=True)
class QuantRelativeIon:
    """A companion ion, relative to the target (quantitation) m/z."""

    label: str
    offset_mz: float
    ratio_min: float = 0.1
    ratio_max: float = 10.0
    compare_shape: bool = True

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
class DeltaMassSettings:
    """Shared by every target in one list; the pattern settings validate them."""

    mz_tolerance_ppm: float = 20.0
    apex_floor_sec: float = 1.0
    scan_interval_multiplier: float = 1.25
    shape_window_sec: float = 10.0
    min_shape_r: float = 0.9


DEFAULT_DELTA_MASS_SETTINGS = DeltaMassSettings()


@dataclass(frozen=True)
class DeltaMassCondition:
    ions: tuple[QuantRelativeIon, ...]
    settings: DeltaMassSettings = DEFAULT_DELTA_MASS_SETTINGS
    kind: Literal["delta_mass"] = "delta_mass"

    quant_position = BASE_POSITION
    label = DELTA_MASS_LABEL

    def pattern_settings(self, pattern_id: str) -> DeltaMassPatternSettings:
        positions = [
            (BASE_POSITION, 0.0, 1.0, 1.0),
            *((i.label, i.offset_mz, i.ratio_min, i.ratio_max) for i in self.ions),
        ]
        lowest = min(offset for _, offset, _, _ in positions)
        settings = self.settings
        return DeltaMassPatternSettings(
            pattern_id=pattern_id,
            method_label=DELTA_MASS_LABEL,
            positions=tuple(
                DeltaMassPosition(label, offset - lowest, low, high)
                for label, offset, low, high in sorted(positions, key=lambda p: p[1])
            ),
            anchor_positions=(BASE_POSITION,),
            discriminating_positions=tuple(
                ion.label for ion in self.ions if ion.compare_shape
            ),
            mz_tolerance_ppm=settings.mz_tolerance_ppm,
            apex_floor_sec=settings.apex_floor_sec,
            scan_interval_multiplier=settings.scan_interval_multiplier,
            shape_window_sec=settings.shape_window_sec,
            min_shape_r=settings.min_shape_r,
            shape_reference_position=BASE_POSITION,
            ratio_reference_position=BASE_POSITION,
        )


IdentificationCondition = CidNlCondition | DeltaMassCondition


@dataclass(frozen=True)
class TargetIdentification:
    """Conditions that must all match the same peak of a Delta Mass target."""

    conditions: tuple[IdentificationCondition, ...]

    @property
    def delta_mass(self) -> DeltaMassCondition:
        return next(c for c in self.conditions if isinstance(c, DeltaMassCondition))

    @property
    def cid_nl_loss_da(self) -> float | None:
        return next(
            (
                c.neutral_loss_da
                for c in self.conditions
                if isinstance(c, CidNlCondition)
            ),
            None,
        )

    @staticmethod
    def accepts(statuses) -> bool:
        return all(status == "matched" for status in statuses)


def target_cid_nl_loss(target) -> float | None:
    identification = target.identification
    if identification is not None:
        return identification.cid_nl_loss_da
    return target.neutral_loss_da


def target_cid_nl_losses(target) -> tuple[float, ...]:
    loss = target_cid_nl_loss(target)
    return () if loss is None else (loss,)


def target_specificity(target) -> str:
    """User-facing specificity with its parameters."""
    parts = []
    loss = target_cid_nl_loss(target)
    if loss is not None:
        parts.append(f"CID-NL {loss:.4f}")
    if target.identification is not None:
        offsets = ", ".join(
            ion_name(ion.offset_mz) for ion in target.identification.delta_mass.ions
        )
        parts.append(f"{DELTA_MASS_LABEL} {offsets}")
    return " + ".join(parts) or "MS1 only"


# --- Delta Mass details: one JSON-able payload for workspace, GUI and receipts ---


def delta_mass_payload(
    settings: DeltaMassSettings,
    ions_by_target: Mapping[str, tuple[QuantRelativeIon, ...]],
) -> dict:
    return {
        "settings": asdict(settings),
        "ions": {
            label: [asdict(ion) for ion in ions]
            for label, ions in ions_by_target.items()
            if ions
        },
    }


def parse_delta_mass_payload(
    value: object,
) -> tuple[DeltaMassSettings, dict[str, tuple[QuantRelativeIon, ...]]]:
    """Admit externally edited Delta Mass details once at their input boundary."""
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or set(value) - {"settings", "ions"}:
        raise ValueError("delta_mass must be an object with settings and ions")
    raw_settings = value.get("settings") or {}
    if not isinstance(raw_settings, Mapping):
        raise ValueError("delta_mass.settings must be an object")
    known = {item.name for item in fields(DeltaMassSettings)}
    if unknown := sorted(set(raw_settings) - known):
        raise ValueError(f"unsupported Delta Mass setting: {unknown[0]}")
    settings = DeltaMassSettings(**_numbers(raw_settings))
    raw_ions = value.get("ions") or {}
    if not isinstance(raw_ions, Mapping):
        raise ValueError("delta_mass.ions must map target labels to ion lists")
    ions: dict[str, tuple[QuantRelativeIon, ...]] = {}
    for label, rows in raw_ions.items():
        if not isinstance(label, str) or not isinstance(rows, list):
            raise ValueError("delta_mass.ions must map target labels to ion lists")
        parsed = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) - {
                "label",
                "offset_mz",
                "ratio_min",
                "ratio_max",
                "compare_shape",
            }:
                raise ValueError(f"{label}: unsupported Delta Mass ion fields")
            values = dict(row)
            if "label" not in values or not str(values.get("label", "")).strip():
                values["label"] = ion_name(float(values["offset_mz"]))
            parsed.append(
                QuantRelativeIon(**{**values, **_numbers(values, skip={"label"})})
            )
        ions[label] = tuple(parsed)
    return settings, ions


def _numbers(values: Mapping, *, skip=frozenset({"compare_shape"})) -> dict:
    result = {}
    for key, value in values.items():
        if key in skip or key == "compare_shape":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{key} must be a number")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{key} must be a number") from None
        if not math.isfinite(number):
            raise ValueError(f"{key} must be finite")
        result[key] = number
    return result
