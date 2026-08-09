"""Represent machine-bound and user-facing Alignment Matrix numbers."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def canonical_mz_text(value: object) -> str:
    """Return the shortest text that round-trips a finite machine m/z value."""

    numeric = _finite_float(value, field_name="Mz")
    return "" if numeric is None else repr(numeric)


def public_matrix_mz_text(value: object) -> str:
    """Render m/z for the human-facing Matrix without becoming identity."""

    canonical = canonical_mz_text(value)
    return "" if canonical == "" else f"{float(canonical):.4f}"


def public_matrix_area_text(value: object) -> str:
    """Render a finite Matrix area with six significant scientific digits."""

    numeric = _finite_float(value, field_name="Matrix area")
    return "" if numeric is None else f"{numeric:.5e}"


def project_public_matrix_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    sample_columns: Sequence[str],
) -> Iterable[dict[str, object]]:
    """Project machine rows onto the public ``Mz,RT,*samples`` surface."""

    for row in rows:
        yield {
            "Mz": public_matrix_mz_text(row.get("Mz", "")),
            "RT": row.get("RT", ""),
            **{
                sample: public_matrix_area_text(row.get(sample, ""))
                for sample in sample_columns
            },
        }


def matrix_mz_binding_matches(display_mz: object, canonical_mz: object) -> bool:
    """Accept the current display projection or an exact legacy ``.6g`` pair."""

    display_text = str(display_mz).strip()
    canonical_input = str(canonical_mz).strip()
    try:
        canonical_text = canonical_mz_text(canonical_input)
    except ValueError:
        return False
    if not display_text or not canonical_text:
        return False
    if display_text == public_matrix_mz_text(canonical_text):
        return True
    legacy_text = f"{float(canonical_text):.6g}"
    return canonical_input == legacy_text and display_text == legacy_text


def _finite_float(value: object, *, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        numeric = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    return numeric if math.isfinite(numeric) else None
