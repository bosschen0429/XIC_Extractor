from pathlib import Path

import pytest

from scripts import check_phase_naming_boundary as boundary


def test_current_domain_holds_the_boundary() -> None:
    assert boundary.check_phase_naming_boundary() == []


def test_only_the_domain_package_is_in_scope() -> None:
    assert boundary.in_scope("xic_extractor/alignment/matrix.py")
    # Unlike the lane-fixture checker, diagnostics are NOT exempt: naming code
    # after the sprint that produced it has no justification anywhere.
    assert boundary.in_scope("xic_extractor/diagnostics/backfill_gallery.py")
    for path in ("tests/test_m5_successor_verdict.py", "scripts/build_x.py",
                 "tools/diagnostics/INDEX.md", "docs/product/backfill.md"):
        assert not boundary.in_scope(path)


@pytest.mark.parametrize(
    "text",
    [
        "_package1_schema",
        "MULTI_SEED_REQUIRES_PHASE2",
        "multi_seed_requires_phase2",
        'key = "package2:alignment-method"',
        "class M5SuccessorVerdictReceipt:",
        'VERSION = "p7-backfill-scope-v1"',
        "issue256_quantitative_projection_authority_v1.json",
        # The snake_case milestone spelling, invisible until `m\d+_` was added.
        "_M5_PACKET_SCHEMA",
        "_m5_git_bytes",
        'SCHEMA = "m9_mode_track_source_v2"',
    ],
)
def test_pattern_matches_phase_names_including_infixes(text: str) -> None:
    assert boundary.PHASE_NAMING_PATTERN.search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # Evidence grades are real domain vocabulary, not phase naming.
        "tier2_apex_rt",
        "tier3_width_only_sample_count",
        "min_non_seed_tier12_identity_samples",
        # Ordinary CamelCase that a case-insensitive M-rule would catch.
        "atom1Abc",
        "form2Bx",
        # Same guard in snake_case: the m-digit segment may not follow a letter.
        "atom1_value",
        "form2_row",
        # Schema versions are legitimate.
        "schema_v1",
        "PRODUCER_VERSION = 'raw_trace_reread_v0_1'",
    ],
)
def test_pattern_leaves_domain_vocabulary_alone(text: str) -> None:
    assert boundary.PHASE_NAMING_PATTERN.search(text) is None


def test_scan_counts_lines_not_matches(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "sample.py"
    # Two matches on one line must count once, or budgets drift from reality.
    target.write_text("a = 1\npackage1 = package2\n", encoding="utf-8")
    monkeypatch.setattr(boundary, "ROOT", tmp_path)

    hits = boundary.scan(["sample.py"])

    assert hits == {"sample.py": [(2, "package1 = package2")]}


def test_new_unbudgeted_file_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        boundary, "scan",
        lambda: {"xic_extractor/alignment/new.py": [(3, "phase4_gate = True")]},
    )
    monkeypatch.setattr(boundary, "KNOWN_PHASE_NAMES", {})

    problems = boundary.check_phase_naming_boundary()

    assert len(problems) == 1
    assert "development-phase name" in problems[0]


def test_growth_past_budget_fails(monkeypatch) -> None:
    path = "xic_extractor/alignment/existing.py"
    monkeypatch.setattr(
        boundary, "scan",
        lambda: {path: [(1, "package1"), (2, "package2"), (3, "phase1")]},
    )
    monkeypatch.setattr(boundary, "KNOWN_PHASE_NAMES", {path: (2, "#293")})

    problems = boundary.check_phase_naming_boundary()

    assert len(problems) == 1
    assert "grew past its budget" in problems[0]


def test_budget_must_ratchet_down(monkeypatch) -> None:
    path = "xic_extractor/alignment/existing.py"
    monkeypatch.setattr(boundary, "scan", lambda: {path: [(1, "package1")]})
    monkeypatch.setattr(boundary, "KNOWN_PHASE_NAMES", {path: (5, "#293")})

    problems = boundary.check_phase_naming_boundary()

    assert len(problems) == 1
    assert "Lower it to 1" in problems[0]


def test_fully_cleaned_file_must_lose_its_entry(monkeypatch) -> None:
    monkeypatch.setattr(boundary, "scan", dict)
    monkeypatch.setattr(
        boundary, "KNOWN_PHASE_NAMES", {"xic_extractor/a.py": (1, "#293")}
    )

    problems = boundary.check_phase_naming_boundary()

    assert len(problems) == 1
    assert "Delete this KNOWN_PHASE_NAMES entry" in problems[0]
