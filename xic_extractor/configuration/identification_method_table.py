"""Identification methods as a flat worksheet table: one row per condition ion.

A CID-NL condition is one row. A Delta Mass condition has one row per ion
position; its condition-level settings repeat on each of those rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.configuration.targeted_identification import (
    CidNlCondition,
    IdentificationMethod,
    parse_identification_methods,
)

METHOD_TABLE_SHEET = "Identification Methods"
METHOD_TABLE_HEADERS = (
    "Method",
    "Combine",
    "m/z Tolerance ppm",
    "Condition",
    "Kind",
    "CID-NL Tag",
    "Position",
    "Delta m/z",
    "Ratio Min",
    "Ratio Max",
    "Compare Shape",
    "Apex Floor sec",
    "Scan Interval Multiplier",
    "Shape Window sec",
    "Min Shape r",
)
_REQUIRED = ("Method", "Combine", "m/z Tolerance ppm", "Condition", "Kind")
_METHOD_FIELDS = (
    ("Combine", "combine_mode"),
    ("m/z Tolerance ppm", "mz_tolerance_ppm"),
)
_DELTA_FIELDS = (
    ("Apex Floor sec", "apex_floor_sec"),
    ("Scan Interval Multiplier", "scan_interval_multiplier"),
    ("Shape Window sec", "shape_window_sec"),
    ("Min Shape r", "min_shape_r"),
)
_POSITION_FIELDS = (
    ("Position", "label"),
    ("Delta m/z", "offset_mz"),
    ("Ratio Min", "ratio_min"),
    ("Ratio Max", "ratio_max"),
    ("Compare Shape", "compare_shape"),
)
_NUMBERS = frozenset(
    {
        "mz_tolerance_ppm",
        "offset_mz",
        "ratio_min",
        "ratio_max",
        "apex_floor_sec",
        "scan_interval_multiplier",
        "shape_window_sec",
        "min_shape_r",
    }
)


def identification_method_rows(
    methods: Mapping[str, IdentificationMethod],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, method in methods.items():
        common = {
            "Method": name,
            "Combine": method.combine_mode,
            "m/z Tolerance ppm": method.mz_tolerance_ppm,
        }
        for number, condition in enumerate(method.conditions, start=1):
            head = {**common, "Condition": number, "Kind": condition.kind}
            if isinstance(condition, CidNlCondition):
                rows.append({**head, "CID-NL Tag": condition.tag})
                continue
            shared = {
                column: getattr(condition, field) for column, field in _DELTA_FIELDS
            }
            for position in condition.positions:
                rows.append(
                    {
                        **head,
                        **{
                            column: getattr(position, field)
                            for column, field in _POSITION_FIELDS
                        },
                        **shared,
                    }
                )
    return rows


def parse_identification_method_rows(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, IdentificationMethod]:
    """Rebuild method definitions; the method model owns semantic validation."""
    grouped: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for row_number, row in enumerate(rows, start=2):
        if missing := [key for key in _REQUIRED if _text(row.get(key)) == ""]:
            raise ValueError(f"row {row_number}: {', '.join(missing)} is required")
        grouped.setdefault(_text(row["Method"]), []).append((row_number, dict(row)))
    payload: dict[str, dict[str, object]] = {}
    for name, method_rows in grouped.items():
        method = _shared(name, method_rows, _METHOD_FIELDS)
        by_condition: dict[int, list[tuple[int, dict[str, object]]]] = {}
        for row_number, row in method_rows:
            number = _value("number", row["Condition"], row_number)
            if number != int(number) or number < 1:
                raise ValueError(f"row {row_number}: Condition must be 1, 2, 3, ...")
            by_condition.setdefault(int(number), []).append((row_number, row))
        if sorted(by_condition) != list(range(1, len(by_condition) + 1)):
            raise ValueError(f"{name}: Condition numbers must be 1, 2, 3, ...")
        conditions: list[dict[str, object]] = []
        for number in sorted(by_condition):
            condition_rows = by_condition[number]
            kinds = {_text(row["Kind"]) for _, row in condition_rows}
            if len(kinds) != 1:
                raise ValueError(f"{name}: condition {number} mixes Kind values")
            kind = kinds.pop()
            if kind == "cid_nl":
                if len(condition_rows) != 1:
                    raise ValueError(f"{name}: CID-NL condition {number} needs one row")
                conditions.append(
                    {"kind": kind, "tag": _text(condition_rows[0][1].get("CID-NL Tag"))}
                )
            elif kind == "delta_mass":
                conditions.append(
                    {
                        "kind": kind,
                        **_shared(
                            f"{name} condition {number}", condition_rows, _DELTA_FIELDS
                        ),
                        "positions": [
                            _fields(row, row_number, _POSITION_FIELDS)
                            for row_number, row in condition_rows
                        ],
                    }
                )
            else:
                raise ValueError(f"{name}: Kind must be cid_nl or delta_mass")
        payload[name] = {**method, "conditions": conditions}
    return parse_identification_methods(payload)


def _shared(owner, rows, columns) -> dict[str, object]:
    first_number, first = rows[0]
    values = _fields(first, first_number, columns)
    for row_number, row in rows[1:]:
        if _fields(row, row_number, columns) != values:
            raise ValueError(
                f"{owner}: rows disagree on {', '.join(c for c, _ in columns)}"
            )
    return values


def _fields(row, row_number, columns) -> dict[str, object]:
    values: dict[str, object] = {}
    for column, field in columns:
        text = _text(row.get(column))
        if text == "":
            continue
        if field == "compare_shape":
            values[field] = _value("bool", row[column], row_number)
        elif field in _NUMBERS:
            values[field] = _value("number", row[column], row_number)
        else:
            values[field] = text
    return values


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _value(kind: str, value: object, row_number: int):
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = _text(value).lower()
        if text in {"true", "false"}:
            return text == "true"
        raise ValueError(f"row {row_number}: Compare Shape must be TRUE or FALSE")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        return float(_text(value))
    except ValueError:
        raise ValueError(f"row {row_number}: {value!r} is not a number") from None
