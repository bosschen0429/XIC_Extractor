from xic_extractor.alignment.scan_retention_times import (
    cached_retention_time_for_scan,
)


def test_cached_retention_time_for_scan_reuses_cached_values() -> None:
    class Source:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def retention_time_for_scan(self, scan_number: int) -> float:
            self.calls.append(scan_number)
            return scan_number / 100.0

    source = Source()
    cache: dict[int, float | None] = {}

    assert (
        cached_retention_time_for_scan(
            source,
            812,
            retention_time_by_scan=cache,
        )
        == 8.12
    )
    assert (
        cached_retention_time_for_scan(
            source,
            812,
            retention_time_by_scan=cache,
        )
        == 8.12
    )
    assert source.calls == [812]
    assert cache == {812: 8.12}


def test_cached_retention_time_for_scan_caches_missing_resolver() -> None:
    cache: dict[int, float | None] = {}

    assert (
        cached_retention_time_for_scan(
            object(),
            812,
            retention_time_by_scan=cache,
        )
        is None
    )
    assert cache == {812: None}
