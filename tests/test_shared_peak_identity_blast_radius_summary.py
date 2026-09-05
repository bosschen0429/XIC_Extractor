from __future__ import annotations

import csv
from pathlib import Path

import pytest

from xic_extractor.alignment.shared_peak_identity_explanation.blast_radius import (
    _cell_facts_from_row,
    build_blast_radius_summary,
    build_class_profiles,
)
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    BLAST_RADIUS_SUMMARY_COLUMNS,
)


def test_summary_counts_scopes_and_excludes_context_rows(tmp_path: Path) -> None:
    class_profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "pass",
                "machine_too_conservative_low_opportunity",
                "absent",
            ),
            _explanation(
                "FAM001|__family_context__",
                "FAM001",
                "__family_context__",
                "not_applicable",
                "delta_mass_related_context_only",
                "not_applicable",
            ),
        ],
        [
            _evidence(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "absent",
                intensity_status="low_but_visible",
                dda_opportunity_status="low_intensity_stochastic_not_observed",
            )
        ],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [
            _cell("FAM001", "Seed", "absent", reason="no local MS1 owner"),
            _cell("FAM001", "Other", "absent", reason="no local MS1 owner"),
            _cell("FAM002", "Other", "absent", reason="no local MS1 owner"),
        ],
    )
    _write_cells(
        full_cohort,
        [_cell("FAM001", "Other85", "absent", reason="no local MS1 owner")],
    )

    rows = build_blast_radius_summary(
        class_profiles=class_profiles,
        manifest_rows=_manifest_rows(
            current=True,
            fixture_cohort=fixture_cohort,
            full_cohort=full_cohort,
        ),
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    assert all(tuple(row) == BLAST_RADIUS_SUMMARY_COLUMNS for row in rows)
    by_key = {
        (row["evidence_gap_class"], row["scope"], row["artifact_id"]): row
        for row in rows
    }
    seed = by_key[
        (
            "machine_too_conservative_low_opportunity",
            "seed",
            "slice0_explanations",
        )
    ]
    assert seed["seed_count"] == "1"
    assert seed["context_row_count"] == "0"
    assert seed["all_available_row_count"] == "1"
    assert seed["compatible_row_count"] == "1"

    non_seed = by_key[
        (
            "machine_too_conservative_low_opportunity",
            "non_seed_same_family",
            "combined_alignment_cells",
        )
    ]
    assert non_seed["seed_count"] == "1"
    assert non_seed["non_seed_same_family_count"] == "2"
    assert non_seed["assessed_row_count"] == "2"
    assert non_seed["all_available_row_count"] == "2"
    assert non_seed["compatible_row_count"] == "2"

    context = by_key[
        (
            "delta_mass_related_context_only",
            "overall",
            "combined_alignment_cells",
        )
    ]
    assert context["seed_count"] == "0"
    assert context["context_row_count"] == "1"
    assert context["assessed_row_count"] == "0"
    assert context["overfit_risk"] == "none"


def test_summary_counts_missing_columns_and_ambiguous_machine_matches(
    tmp_path: Path,
) -> None:
    class_profiles = build_class_profiles(
        [
            _explanation(
                "FAM010|Seed",
                "FAM010",
                "Seed",
                "fail",
                "machine_too_permissive_rt_pattern_conflict",
                "rescued",
            )
        ],
        [
            _evidence(
                "FAM010|Seed",
                "FAM010",
                "Seed",
                "rescued",
                rt_context_status="conflicting",
                pattern_conflict_status="rt_pattern_conflict",
            )
        ],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [
            _cell("FAM010", "A", "rescued", reason="ambiguous_ms1_owner"),
            _cell("FAM010", "A", "rescued", reason="duplicate MS1 peak claim"),
        ],
    )
    _write_cells(
        full_cohort,
        [_cell("FAM010", "B", "rescued", reason="rt pattern conflict")],
        fieldnames=("feature_family_id", "sample_stem", "status"),
    )

    rows = build_blast_radius_summary(
        class_profiles=class_profiles,
        manifest_rows=_manifest_rows(
            current=True,
            fixture_cohort=fixture_cohort,
            full_cohort=full_cohort,
        ),
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    eight = _row(
        rows,
        "machine_too_permissive_rt_pattern_conflict",
        "all_available_fixture_cohort",
    )
    assert eight["assessed_row_count"] == "2"
    assert eight["ambiguous_machine_match_count"] == "2"
    assert eight["ambiguous_fraction"] == "1.000000"

    eightyfive = _row(
        rows,
        "machine_too_permissive_rt_pattern_conflict",
        "all_available_full_cohort",
    )
    assert eightyfive["assessed_row_count"] == "1"
    assert eightyfive["all_available_row_count"] == "0"
    assert eightyfive["unavailable_field_count"] == "1"
    assert eightyfive["overfit_risk"] == "unassessed"


def test_summary_risk_table_high_medium_and_low(tmp_path: Path) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM100|Seed",
                "FAM100",
                "Seed",
                "pass",
                "machine_too_conservative_shape_or_pattern_unmodeled",
                "rescued",
            ),
            _explanation(
                "FAM200|Seed",
                "FAM200",
                "Seed",
                "pass",
                "machine_too_conservative_low_opportunity",
                "absent",
            ),
            _explanation(
                "FAM300|Seed",
                "FAM300",
                "Seed",
                "fail",
                "machine_too_permissive_rt_pattern_conflict",
                "rescued",
            ),
        ],
        [
            _evidence("FAM100|Seed", "FAM100", "Seed", "rescued"),
            _evidence(
                "FAM200|Seed",
                "FAM200",
                "Seed",
                "absent",
                intensity_status="low_but_visible",
                dda_opportunity_status="low_intensity_stochastic_not_observed",
            ),
            _evidence(
                "FAM300|Seed",
                "FAM300",
                "Seed",
                "rescued",
                rt_context_status="conflicting",
                pattern_conflict_status="rt_pattern_conflict",
            ),
        ],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [
            *[
                _cell("FAM999", f"low8_{index}", "rescued", reason="supported")
                for index in range(25)
            ],
            *[
                _cell("FAM888", f"high8_{index}", "absent", reason="no peak")
                for index in range(25)
            ],
            _cell("FAM777", "medium8", "rescued", reason="rt pattern conflict"),
        ],
    )
    _write_cells(
        full_cohort,
        [
            *[
                _cell("FAM999", f"low85_{index}", "rescued", reason="supported")
                for index in range(25)
            ],
            *[
                _cell("FAM888", f"high85_{index}", "absent", reason="no peak")
                for index in range(25)
            ],
        ],
    )

    rows = build_blast_radius_summary(
        class_profiles=profiles,
        manifest_rows=_manifest_rows(
            current=True,
            fixture_cohort=fixture_cohort,
            full_cohort=full_cohort,
        ),
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    low = _row(
        rows,
        "machine_too_conservative_shape_or_pattern_unmodeled",
        "overall",
    )
    assert low["assessed_row_count"] == "101"
    assert low["compatible_row_count"] == "51"
    assert low["overfit_risk"] == "low"

    high = _row(rows, "machine_too_conservative_low_opportunity", "overall")
    assert high["compatible_row_count"] == "0"
    assert high["overfit_risk"] == "high"

    medium = _row(rows, "machine_too_permissive_rt_pattern_conflict", "overall")
    assert medium["assessed_row_count"] == "101"
    assert medium["compatible_row_count"] == "1"
    assert medium["overfit_risk"] == "medium"


def test_summary_marks_unpinned_required_surfaces_unassessed(tmp_path: Path) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "pass",
                "machine_too_conservative_shape_or_pattern_unmodeled",
                "rescued",
            )
        ],
        [_evidence("FAM001|Seed", "FAM001", "Seed", "rescued")],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [_cell("FAM001", f"S{index}", "rescued") for index in range(50)],
    )
    _write_cells(full_cohort, [_cell("FAM001", "S85", "rescued")])

    rows = build_blast_radius_summary(
        class_profiles=profiles,
        manifest_rows=_manifest_rows(
            current=False,
            fixture_cohort=fixture_cohort,
            full_cohort=full_cohort,
        ),
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    overall = _row(
        rows,
        "machine_too_conservative_shape_or_pattern_unmodeled",
        "overall",
    )
    assert overall["compatible_row_count"] == "51"
    assert overall["overfit_risk"] == "unassessed"


def test_summary_requires_current_review_and_cell_surfaces(tmp_path: Path) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "pass",
                "machine_too_conservative_shape_or_pattern_unmodeled",
                "rescued",
            )
        ],
        [_evidence("FAM001|Seed", "FAM001", "Seed", "rescued")],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [_cell("FAM001", f"S{index}", "rescued") for index in range(25)],
    )
    _write_cells(
        full_cohort,
        [_cell("FAM001", f"S85_{index}", "rescued") for index in range(25)],
    )

    manifest_rows = _manifest_rows(
        current=True,
        fixture_cohort=fixture_cohort,
        full_cohort=full_cohort,
    )
    for row in manifest_rows:
        if row["artifact_id"] == "full_cohort_alignment_review":
            row["artifact_status"] = "present_stale_hash_mismatch"

    rows = build_blast_radius_summary(
        class_profiles=profiles,
        manifest_rows=manifest_rows,
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    overall = _row(
        rows,
        "machine_too_conservative_shape_or_pattern_unmodeled",
        "overall",
    )
    assert overall["compatible_row_count"] == "50"
    assert overall["overfit_risk"] == "unassessed"


def test_summary_scans_each_alignment_cells_file_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "fail",
                "machine_too_permissive_rt_pattern_conflict",
                "rescued",
            )
        ],
        [
            _evidence(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "rescued",
                rt_context_status="conflicting",
                pattern_conflict_status="rt_pattern_conflict",
            )
        ],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(fixture_cohort, [_cell("FAM001", "A", "rescued")])
    _write_cells(
        full_cohort,
        [
            _cell("FAM001", "B", "rescued", reason="rt pattern conflict"),
            _cell("FAM001", "B", "rescued", reason="rt pattern conflict"),
        ],
    )
    target = full_cohort / "alignment_cells.tsv"
    open_count = 0
    original_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal open_count
        if self == target:
            open_count += 1
            if open_count > 1:
                raise AssertionError("alignment_cells.tsv was opened more than once")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    rows = build_blast_radius_summary(
        class_profiles=profiles,
        manifest_rows=_manifest_rows(
            current=True,
            fixture_cohort=fixture_cohort,
            full_cohort=full_cohort,
        ),
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    eightyfive = _row(
        rows,
        "machine_too_permissive_rt_pattern_conflict",
        "all_available_full_cohort",
    )
    assert open_count == 1
    assert eightyfive["ambiguous_machine_match_count"] == "2"
    assert eightyfive["compatible_row_count"] == "0"


def test_summary_marks_review_surface_missing_fields_unassessed(
    tmp_path: Path,
) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "pass",
                "machine_too_conservative_shape_or_pattern_unmodeled",
                "rescued",
            )
        ],
        [_evidence("FAM001|Seed", "FAM001", "Seed", "rescued")],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    _write_cells(
        fixture_cohort,
        [_cell("FAM001", f"S{index}", "rescued") for index in range(25)],
    )
    _write_cells(
        full_cohort,
        [_cell("FAM001", f"S85_{index}", "rescued") for index in range(25)],
    )
    manifest_rows = _manifest_rows(
        current=True,
        fixture_cohort=fixture_cohort,
        full_cohort=full_cohort,
    )
    for row in manifest_rows:
        if row["artifact_id"] == "fixture_cohort_alignment_review":
            row["missing_required_fields"] = "row_flags"

    rows = build_blast_radius_summary(
        class_profiles=profiles,
        manifest_rows=manifest_rows,
        fixture_cohort_run_dir=fixture_cohort,
        full_cohort_run_dir=full_cohort,
    )

    overall = _row(
        rows,
        "machine_too_conservative_shape_or_pattern_unmodeled",
        "overall",
    )
    assert overall["compatible_row_count"] == "50"
    assert overall["overfit_risk"] == "unassessed"


def test_summary_rejects_manifest_path_that_does_not_match_input_run(
    tmp_path: Path,
) -> None:
    profiles = build_class_profiles(
        [
            _explanation(
                "FAM001|Seed",
                "FAM001",
                "Seed",
                "pass",
                "machine_too_conservative_shape_or_pattern_unmodeled",
                "rescued",
            )
        ],
        [_evidence("FAM001|Seed", "FAM001", "Seed", "rescued")],
    )
    fixture_cohort = tmp_path / "fixture_cohort"
    full_cohort = tmp_path / "full_cohort"
    stale_run = tmp_path / "stale_full_cohort"
    _write_cells(fixture_cohort, [_cell("FAM001", "S1", "rescued")])
    _write_cells(full_cohort, [_cell("FAM001", "S2", "rescued")])
    stale_run.mkdir()
    manifest_rows = _manifest_rows(
        current=True,
        fixture_cohort=fixture_cohort,
        full_cohort=full_cohort,
    )
    for row in manifest_rows:
        if row["artifact_id"] == "full_cohort_alignment_cells":
            row["artifact_path"] = str(stale_run / "alignment_cells.tsv")

    with pytest.raises(
        ValueError, match="full_cohort_alignment_cells.*stale_full_cohort"
    ):
        build_blast_radius_summary(
            class_profiles=profiles,
            manifest_rows=manifest_rows,
            fixture_cohort_run_dir=fixture_cohort,
            full_cohort_run_dir=full_cohort,
        )


def test_cell_facts_drop_extra_payload_before_pending_state() -> None:
    row = _cell("FAM001", "PayloadSample", "rescued", reason="rt pattern conflict")
    row["large_payload"] = "x" * 100_000
    row["unused_extra_status"] = "ambiguous_ms1_owner"

    facts = _cell_facts_from_row(row)

    assert facts.feature_family_id == "FAM001"
    assert facts.sample_key == "FAM001|PayloadSample"
    assert facts.status == "rescued"
    assert "rt_pattern_conflict" in facts.tokens
    assert "large_payload" not in facts.tokens
    assert "unused_extra_status" not in facts.tokens
    assert not hasattr(facts, "row")
    assert not hasattr(facts, "large_payload")


def _explanation(
    oracle_row_id: str,
    family: str,
    sample: str,
    manual_label: str,
    evidence_gap_class: str,
    machine_current_label: str,
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "feature_family_id": family,
        "sample_id": sample,
        "manual_label": manual_label,
        "manual_label_source": "direct_eic_ms2_review",
        "manual_scope": "family_level_context"
        if sample.startswith("__")
        else "reviewed_cell",
        "manual_reason_tags": "",
        "machine_current_label": machine_current_label,
        "machine_match_status": "not_applicable"
        if sample.startswith("__")
        else "single_match",
        "machine_blockers": "",
        "evidence_gap_class": evidence_gap_class,
    }


def _evidence(
    oracle_row_id: str,
    family: str,
    sample: str,
    machine_current_label: str,
    *,
    intensity_status: str = "sufficient",
    dda_opportunity_status: str = "observed",
    rt_context_status: str = "supportive",
    pattern_conflict_status: str = "none",
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "feature_family_id": family,
        "sample_id": sample,
        "source_role": "rescued_cell",
        "machine_current_label": machine_current_label,
        "machine_blockers": "",
        "rt_context_status": rt_context_status,
        "pattern_conflict_status": pattern_conflict_status,
        "intensity_status": intensity_status,
        "dda_opportunity_status": dda_opportunity_status,
    }


def _cell(
    family: str,
    sample: str,
    status: str,
    *,
    reason: str = "supported",
) -> dict[str, str]:
    return {
        "feature_family_id": family,
        "sample_stem": sample,
        "status": status,
        "apex_rt": "1.0",
        "peak_start_rt": "0.9",
        "peak_end_rt": "1.1",
        "rt_delta_sec": "0.0",
        "trace_quality": "clean",
        "scan_support_score": "1.0",
        "reason": reason,
    }


def _write_cells(
    run_dir: Path,
    rows: list[dict[str, str]],
    *,
    fieldnames: tuple[str, ...] = (
        "feature_family_id",
        "sample_stem",
        "status",
        "apex_rt",
        "peak_start_rt",
        "peak_end_rt",
        "rt_delta_sec",
        "trace_quality",
        "scan_support_score",
        "reason",
    ),
) -> None:
    run_dir.mkdir()
    _write_tsv(run_dir / "alignment_cells.tsv", fieldnames, rows)
    _write_tsv(
        run_dir / "alignment_review.tsv",
        ("feature_family_id", "identity_decision", "identity_reason", "row_flags"),
        [
            {
                "feature_family_id": rows[0]["feature_family_id"] if rows else "FAM",
                "identity_decision": "review",
                "identity_reason": "context",
                "row_flags": "",
            }
        ],
    )


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )


def _manifest_rows(
    *,
    current: bool,
    fixture_cohort: Path,
    full_cohort: Path,
) -> list[dict[str, str]]:
    status = "present_current" if current else "present_hash_unpinned"
    return [
        {
            "artifact_id": "fixture_cohort_alignment_review",
            "artifact_role": "alignment_review",
            "artifact_path": str(fixture_cohort / "alignment_review.tsv"),
            "artifact_status": status,
            "missing_required_fields": "",
        },
        {
            "artifact_id": "fixture_cohort_alignment_cells",
            "artifact_role": "alignment_cells",
            "artifact_path": str(fixture_cohort / "alignment_cells.tsv"),
            "artifact_status": status,
            "missing_required_fields": "",
        },
        {
            "artifact_id": "full_cohort_alignment_review",
            "artifact_role": "alignment_review",
            "artifact_path": str(full_cohort / "alignment_review.tsv"),
            "artifact_status": status,
            "missing_required_fields": "",
        },
        {
            "artifact_id": "full_cohort_alignment_cells",
            "artifact_role": "alignment_cells",
            "artifact_path": str(full_cohort / "alignment_cells.tsv"),
            "artifact_status": status,
            "missing_required_fields": "",
        },
    ]


def _row(
    rows: tuple[dict[str, str], ...],
    evidence_gap_class: str,
    scope: str,
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["evidence_gap_class"] == evidence_gap_class and row["scope"] == scope
    ]
    assert len(matches) == 1
    return matches[0]
