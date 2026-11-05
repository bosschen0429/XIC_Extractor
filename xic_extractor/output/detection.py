from __future__ import annotations


class MissingTargetedProductProjectionError(ValueError):
    """Raised when a product consumer lacks targeted projection authority."""


def is_accepted_row_detection(row: dict[str, str]) -> bool:
    counted = row.get("Counted Detection", "").upper()
    if counted not in {"TRUE", "FALSE"}:
        raise MissingTargetedProductProjectionError(
            "Counted Detection TRUE/FALSE is required for targeted product detection"
        )
    return counted == "TRUE"
