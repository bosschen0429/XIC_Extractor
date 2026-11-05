"""Verify the actual pytest shard interface, not a second test registry."""

import re

import pytest

pytest_plugins = ("pytester",)
PLUGIN = ("-p", "tools.testing.test_sharding")


@pytest.fixture
def case_suite(pytester: pytest.Pytester) -> pytest.Pytester:
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


def test_shards_cover_each_selected_case_once_despite_collection_order(case_suite):
    seen = []
    for shard in ("1/2", "2/2"):
        result = case_suite.runpytest_subprocess(
            *PLUGIN, "--test-shard", shard, "-m", "browser_e2e", "-v"
        )
        result.assert_outcomes(passed=2, deselected=3)
        seen.append(
            set(re.findall(r"test_cases.py::(test_\w+) PASSED", result.stdout.str()))
        )
    assert seen == [{"test_a", "test_c"}, {"test_b", "test_d"}]


def test_no_shard_keeps_all_case_suite(case_suite):
    result = case_suite.runpytest_subprocess(*PLUGIN, "-q")
    result.assert_outcomes(passed=5)


def test_sharding_applies_to_unmarked_cases_too(case_suite):
    result = case_suite.runpytest_subprocess(*PLUGIN, "--test-shard", "1/2", "-q")
    result.assert_outcomes(passed=3, deselected=2)


@pytest.mark.parametrize("shard", ["0/2", "3/2", "1/0", "invalid"])
def test_invalid_shard_fails_instead_of_silently_passing(case_suite, shard):
    result = case_suite.runpytest_subprocess(*PLUGIN, "--test-shard", shard, "-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "test shard" in result.stderr.str()
