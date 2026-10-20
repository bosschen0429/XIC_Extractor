from __future__ import annotations

from collections.abc import MutableMapping

ScanRetentionTimeCache = MutableMapping[int, float | None]


def cached_retention_time_for_scan(
    source: object,
    scan_number: int,
    *,
    retention_time_by_scan: ScanRetentionTimeCache | None = None,
) -> float | None:
    scan_number = int(scan_number)
    if retention_time_by_scan is not None and scan_number in retention_time_by_scan:
        return retention_time_by_scan[scan_number]

    resolver = getattr(source, "retention_time_for_scan", None)
    if not callable(resolver):
        return _cache_retention_time(retention_time_by_scan, scan_number, None)

    try:
        retention_time = float(resolver(scan_number))
    except (AttributeError, NotImplementedError):
        return _cache_retention_time(retention_time_by_scan, scan_number, None)
    return _cache_retention_time(
        retention_time_by_scan,
        scan_number,
        retention_time,
    )


def _cache_retention_time(
    retention_time_by_scan: ScanRetentionTimeCache | None,
    scan_number: int,
    retention_time: float | None,
) -> float | None:
    if retention_time_by_scan is not None:
        retention_time_by_scan[scan_number] = retention_time
    return retention_time
