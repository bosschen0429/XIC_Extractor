"""Verify the actual pytest shard interface, not a second test registry."""

import re

import pytest

pytest_plugins = ("pytester",)
PLUGIN = ("-p", "tools.testing.browser_sharding")


@pytest.fixture
def browser_cases(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeini("[pytest]\nmarkers = browser_e2e: browser case\n")
    pytester.makepyfile(
        test_cases="""
import pytest

@pytest.mark.browser_e2e
def test_d(): pass
@pytest.mark.browser_e2e
def test_b(): pass
@pytest.mark.browser_e2e
def test_c(): pass
@pytest.mark.browser_e2e
def test_a(): pass
def test_other(): pass
"""
    )
    return pytester


def test_browser_shards_cover_each_case_once_despite_collection_order(browser_cases):
    seen = []
    for shard in ("1/2", "2/2"):
        result = browser_cases.runpytest_subprocess(
            *PLUGIN, "--browser-shard", shard, "-m", "browser_e2e", "-v"
        )
        result.assert_outcomes(passed=2, deselected=3)
        seen.append(
            set(re.findall(r"test_cases.py::(test_\w+) PASSED", result.stdout.str()))
        )
    assert seen == [{"test_a", "test_c"}, {"test_b", "test_d"}]


def test_no_shard_keeps_all_browser_cases(browser_cases):
    result = browser_cases.runpytest_subprocess(*PLUGIN, "-m", "browser_e2e", "-q")
    result.assert_outcomes(passed=4, deselected=1)


def test_shard_does_not_drop_ordinary_tests(browser_cases):
    result = browser_cases.runpytest_subprocess(*PLUGIN, "--browser-shard", "1/2", "-q")
    result.assert_outcomes(passed=3, deselected=2)


@pytest.mark.parametrize("shard", ["0/2", "3/2", "1/0", "invalid"])
def test_invalid_shard_fails_instead_of_silently_passing(browser_cases, shard):
    result = browser_cases.runpytest_subprocess(*PLUGIN, "--browser-shard", shard, "-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "browser shard" in result.stderr.str()
