from __future__ import annotations


def feature_id_label(ordinal: int) -> str:
    """Report-scoped label of the ``ordinal``-th written group (1-based)."""

    return f"F{ordinal:06d}"
