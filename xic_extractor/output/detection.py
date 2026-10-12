from __future__ import annotations


class MissingTargetedProductProjectionError(ValueError):
    """Raised when a product consumer lacks targeted projection authority."""


def is_accepted_row_detection(
    row: dict[str, str],
    count_no_ms2_as_detected: bool,
    *,
    require_projection: bool = False,
) -> bool:
    counted_detection = row.get("Counted Detection", "")
    if counted_detection:
        normalized = counted_detection.upper()
        if normalized in {"TRUE", "FALSE"}:
            return normalized == "TRUE"
        if require_projection:
            raise MissingTargetedProductProjectionError(
                "Counted Detection must be TRUE or FALSE in product mode"
            )
    if require_projection:
        raise MissingTargetedProductProjectionError(
            "Counted Detection is required for targeted product detection"
        )
    if _safe_float(row.get("RT", "")) is None:
        return False
    area = _safe_float(row.get("Area", ""))
    if area is None or area <= 0:
        return False
    return _is_accepted_confidence_and_nl(
        confidence=row.get("Confidence", ""),
        nl=row.get("NL", ""),
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )


def _is_accepted_confidence_and_nl(
    *,
    confidence: str,
    nl: str,
    count_no_ms2_as_detected: bool,
) -> bool:
    if confidence == "VERY_LOW":
        return False
    if nl == "NO_MS2":
        return count_no_ms2_as_detected
    if nl == "NL_FAIL":
        return False
    return nl == "" or nl == "OK" or nl.startswith("WARN_")


def _safe_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
