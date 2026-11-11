"""Targeted Delta Mass details as worksheet rows: one row per target ion.

The target row's ``delta_mass_mz`` cell declares which ions a target has; this
table adds each ion's name, ratio range and shape comparison. List-shared
settings are ``delta_mass_*`` keys of the Method sheet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import DeltaMassSettings

DELTA_MASS_SHEET = "Delta Mass"
DELTA_MASS_HEADERS = (
    "Target",
    "Position",
    "Delta m/z",
    "Ratio Min",
    "Ratio Max",
    "Compare Shape",
)
_ION_FIELDS = (
    ("Position", "label"),
    ("Delta m/z", "offset_mz"),
    ("Ratio Min", "ratio_min"),
    ("Ratio Max", "ratio_max"),
    ("Compare Shape", "compare_shape"),
)
DELTA_MASS_SETTING_KEYS = {
    f"delta_mass_{item.name}": item.name for item in fields(DeltaMassSettings)
}


def delta_mass_rows(payload: Mapping) -> list[dict[str, object]]:
    return [
        {"Target": target, **{column: ion[key] for column, key in _ION_FIELDS}}
        for target, ions in payload.get("ions", {}).items()
        for ion in ions
    ]


def delta_mass_setting_rows(payload: Mapping) -> list[dict[str, object]]:
    settings = payload.get("settings", {})
    return [
        {
            "key": key,
            "value": settings[name],
            "description": "Delta Mass, shared by every target",
        }
        for key, name in DELTA_MASS_SETTING_KEYS.items()
        if name in settings
    ]


def parse_delta_mass_rows(
    rows: Sequence[Mapping[str, object]], settings: Mapping[str, object]
) -> dict:
    """Rebuild the details payload; target admission owns semantic validation."""
    ions: dict[str, list[dict[str, object]]] = {}
    for number, row in enumerate(rows, start=2):
        target = _text(row.get("Target"))
        if not target or not _text(row.get("Delta m/z")):
            raise ConfigError(
                f"{DELTA_MASS_SHEET} row {number}: Target and Delta m/z are required"
            )
        ion: dict[str, object] = {}
        for column, key in _ION_FIELDS:
            text = _text(row.get(column))
            if not text:
                continue
            if key == "label":
                ion[key] = text
            elif key == "compare_shape":
                if text.lower() not in {"true", "false"}:
                    raise ConfigError(
                        f"{DELTA_MASS_SHEET} row {number}: "
                        "Compare Shape must be TRUE or FALSE"
                    )
                ion[key] = text.lower() == "true"
            else:
                ion[key] = _number(text, number)
        ions.setdefault(target, []).append(ion)
    return {
        "settings": {
            DELTA_MASS_SETTING_KEYS[key]: _number(_text(value), 0)
            for key, value in settings.items()
        },
        "ions": ions,
    }


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _number(text: str, row_number: int) -> float:
    try:
        return float(text)
    except ValueError:
        raise ConfigError(
            f"{DELTA_MASS_SHEET} row {row_number}: {text!r} is not a number"
        ) from None
