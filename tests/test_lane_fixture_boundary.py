from pathlib import Path

import pytest

from scripts import check_lane_fixture_boundary as boundary


def test_current_domain_holds_the_boundary() -> None:
    assert boundary.check_lane_fixture_boundary() == []


def test_diagnostics_are_out_of_scope_but_the_rest_of_the_domain_is_not() -> None:
    assert not boundary.in_scope("xic_extractor/diagnostics/backfill_raw85_overlay.py")
    assert boundary.in_scope("xic_extractor/peak_detection/model_selection.py")
    assert boundary.in_scope("xic_extractor/alignment/matrix.py")


def test_evidence_layers_are_out_of_scope() -> None:
    # Validation evidence is supposed to name the lane it pinned. Sweeping it
    # into this check would turn a boundary rule into a deletion campaign.
    for path in (
        "tests/test_run_alignment.py",
        "scripts/check_cid_nl_85raw_universe_closure.py",
        "tools/diagnostics/family_ms1_overlay_batch.py",
        "docs/product/backfill.md",
    ):
        assert not boundary.in_scope(path)


@pytest.mark.parametrize(
    "line",
    [
        "    linewidth=0.85,",
        "    DEFAULT_SHIFTED_PEARSON_MIN = 0.85",
        "    raw_reader.open(path)",
        "    expected = 85",
        "    raw8bit_decode(payload)",
    ],
)
def test_pattern_ignores_unrelated_numbers_and_words(line: str) -> None:
    assert boundary.LANE_FIXTURE_PATTERN.search(line) is None


@pytest.mark.parametrize(
    "line", ["x = '8raw'", "# 85RAW gate", "raw85 lane", "2raw set"]
)
def test_pattern_matches_lane_fixture_names(line: str) -> None:
    assert boundary.LANE_FIXTURE_PATTERN.search(line) is not None


def test_scan_reports_line_numbers(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "sample.py"
    target.write_text('a = 1\nb = "8raw"\nc = 3\n', encoding="utf-8")
    monkeypatch.setattr(boundary, "ROOT", tmp_path)

    hits = boundary.scan(["sample.py"])

    assert hits == {"sample.py": [(2, 'b = "8raw"')]}


def test_new_unbudgeted_file_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        boundary, "scan",
        lambda: {"xic_extractor/alignment/brand_new.py": [(7, 'lane = "85raw"')]},
    )
    monkeypatch.setattr(boundary, "KNOWN_LEAKAGE", {})

    problems = boundary.check_lane_fixture_boundary()

    assert len(problems) == 1
    assert "brand_new.py" in problems[0]
    assert "any dataset" in problems[0]


def test_growth_past_budget_fails(monkeypatch) -> None:
    path = "xic_extractor/alignment/existing.py"
    monkeypatch.setattr(
        boundary, "scan",
        lambda: {path: [(1, "8raw"), (2, "8raw"), (3, "85raw")]},
    )
    monkeypatch.setattr(boundary, "KNOWN_LEAKAGE", {path: (2, "issue-1")})

    problems = boundary.check_lane_fixture_boundary()

    assert len(problems) == 1
    assert "grew past its budget" in problems[0]
    assert "3 > 2" in problems[0]


def test_budget_must_ratchet_down_when_leaks_are_removed(monkeypatch) -> None:
    path = "xic_extractor/alignment/existing.py"
    monkeypatch.setattr(boundary, "scan", lambda: {path: [(1, "8raw")]})
    monkeypatch.setattr(boundary, "KNOWN_LEAKAGE", {path: (3, "issue-1")})

    problems = boundary.check_lane_fixture_boundary()

    assert len(problems) == 1
    assert "Lower it to 1" in problems[0]


def test_fully_cleaned_file_must_lose_its_entry(monkeypatch) -> None:
    path = "xic_extractor/alignment/cleaned.py"
    monkeypatch.setattr(boundary, "scan", dict)
    monkeypatch.setattr(boundary, "KNOWN_LEAKAGE", {path: (1, "issue-1")})

    problems = boundary.check_lane_fixture_boundary()

    assert len(problems) == 1
    assert "Delete this KNOWN_LEAKAGE entry" in problems[0]


def test_budgeted_file_at_exactly_its_budget_passes(monkeypatch) -> None:
    path = "xic_extractor/alignment/existing.py"
    monkeypatch.setattr(boundary, "scan", lambda: {path: [(1, "8raw"), (2, "85raw")]})
    monkeypatch.setattr(boundary, "KNOWN_LEAKAGE", {path: (2, "issue-1")})

    assert boundary.check_lane_fixture_boundary() == []
