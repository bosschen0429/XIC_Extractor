"""Partition browser cases across CI machines without repeating a case."""

from __future__ import annotations

from argparse import ArgumentTypeError
from typing import cast

import pytest


def _parse_shard(value: str) -> tuple[int, int]:
    try:
        index, total = map(int, value.split("/"))
    except ValueError as error:
        raise ArgumentTypeError("browser shard must be INDEX/TOTAL") from error
    if not 1 <= index <= total:
        raise ArgumentTypeError("browser shard requires 1 <= INDEX <= TOTAL")
    return index, total


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--browser-shard",
        type=_parse_shard,
        metavar="INDEX/TOTAL",
        help="Run one 1-based shard of browser_e2e cases; leave other tests intact",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    shard = cast(tuple[int, int] | None, config.getoption("browser_shard"))
    if shard is None:
        return
    index, total = shard
    browser_items = sorted(
        (item for item in items if item.get_closest_marker("browser_e2e")),
        key=lambda item: item.nodeid,
    )
    deselected = [
        item
        for position, item in enumerate(browser_items)
        if position % total != index - 1
    ]
    removed = set(deselected)
    items[:] = [item for item in items if item not in removed]
    config.hook.pytest_deselected(items=deselected)
