import csv
import json
from collections import Counter
from pathlib import Path

from tools.diagnostics import low_ms1_assessable_coverage_audit as audit
from tools.diagnostics import low_ms1_coverage_review_classifier as classifier


def test_classifies_rt_window_or_multiseed_shift(tmp_path: Path) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_SHIFT",
                prefix="fam_shift",
                rt_min="10.0",
                rt_max="11.0",
                global_assessable_fraction="1.0",
                selected_in_window_fraction="0.333333",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_SHIFT", event_cluster_count="5")],
    )
    _write_trace_summary(
        overlay_dir / "fam_shift_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="10.2", trace_max="200"),
            _trace_row("S2", apex_rt="11.4", trace_max="180"),
            _trace_row("S3", apex_rt="11.5", trace_max="170"),
        ],
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
    )

    summary = result["summary"][0]
    assert summary["root_cause_bucket"] == "rt_window_or_multiseed_shift"
    assert summary["selected_apex_outside_window_count"] == 2
    assert summary["recommended_next_action"] == (
        "run_seed_aware_or_rt_warped_overlay_before_gate_change"
    )
    queue = result["selected_apex_overlay_queue"]
    assert len(queue) == 1
    assert queue[0]["feature_family_id"] == "FAM_SHIFT"
    assert queue[0]["suggested_rt_min"] == 9.85
    assert queue[0]["suggested_rt_max"] == 11.85
    assert queue[0]["suggested_output_prefix"] == (
        "fam_shift_selected_apex_window_overlay"
    )


def test_classifies_single_center_xic_not_supported(tmp_path: Path) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_ZERO",
                prefix="fam_zero",
                rt_min="14.0",
                rt_max="15.0",
                global_assessable_fraction="0.25",
                selected_in_window_fraction="1.0",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_ZERO", event_cluster_count="5")],
    )
    _write_trace_summary(
        overlay_dir / "fam_zero_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="14.1", trace_max="0"),
            _trace_row("S2", apex_rt="14.2", trace_max="0"),
            _trace_row("S3", apex_rt="14.3", trace_max="0"),
            _trace_row("S4", apex_rt="14.4", trace_max="100"),
        ],
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
    )

    summary = result["summary"][0]
    assert summary["root_cause_bucket"] == "single_center_xic_not_supported"
    assert summary["zero_trace_inside_window_count"] == 3
    assert summary["assessable_fraction"] == 0.25
    assert summary["trace_recomputed_assessable_fraction"] == 0.25
    assert summary["assessable_fraction_delta"] == 0.0
    assert (
        "primary backfill support is not established"
        in summary["production_interpretation"]
    )


def test_outputs_rows_and_markdown(tmp_path: Path) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    output_dir = tmp_path / "out"
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_ZERO",
                prefix="fam_zero",
                rt_min="14.0",
                rt_max="15.0",
                global_assessable_fraction="0.5",
                selected_in_window_fraction="1.0",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_ZERO", event_cluster_count="5")],
    )
    _write_trace_summary(
        overlay_dir / "fam_zero_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="14.1", trace_max="0"),
            _trace_row("S2", apex_rt="14.2", trace_max="100"),
        ],
    )

    code = audit.main(
        [
            "--review-candidates-tsv",
            str(review_tsv),
            "--alignment-dir",
            str(alignment_dir),
            "--overlay-dir",
            str(overlay_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert (output_dir / "low_ms1_assessable_coverage_summary.tsv").is_file()
    assert (output_dir / "low_ms1_assessable_coverage_rows.tsv").is_file()
    assert (
        output_dir / "low_ms1_assessable_coverage_selected_apex_overlay_queue.tsv"
    ).is_file()
    markdown = (output_dir / "low_ms1_assessable_coverage.md").read_text(
        encoding="utf-8",
    )
    assert "`FAM_ZERO`" in markdown


def test_output_artifacts_pin_schema_order_and_key_values(tmp_path: Path) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    output_dir = tmp_path / "out"
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_SHIFT",
                prefix="fam_shift",
                rt_min="10.0",
                rt_max="11.0",
                global_assessable_fraction="1.0",
                selected_in_window_fraction="0.333333",
            ),
            _candidate_row(
                "FAM_SEEDCTX",
                prefix="fam_seedctx",
                rt_min="14.0",
                rt_max="15.0",
                global_assessable_fraction="0.5",
                selected_in_window_fraction="1.0",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [
            _alignment_row("FAM_SHIFT", event_cluster_count="5"),
            _alignment_row("FAM_SEEDCTX", event_cluster_count="5"),
        ],
    )
    _write_trace_summary(
        overlay_dir / "fam_shift_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="10.2", trace_max="200"),
            _trace_row("S2", apex_rt="11.4", trace_max="180"),
        ],
    )
    _write_trace_summary(
        overlay_dir / "fam_seedctx_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="14.1", trace_max="0"),
            _trace_row("S2", apex_rt="14.4", trace_max="100"),
        ],
    )
    seed_audit_tsv = tmp_path / "alignment_owner_backfill_seed_audit.tsv"
    _write_backfill_seed_audit(
        seed_audit_tsv,
        [
            _seed_audit_row("FAM_SEEDCTX", "S1", seed_rt="14.1", delta_sec="0"),
            _seed_audit_row("FAM_SEEDCTX", "S2", seed_rt="14.45", delta_sec="90"),
        ],
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
        backfill_seed_audit_tsv=seed_audit_tsv,
    )
    audit.write_outputs(output_dir, result)

    summary_rows = _read_tsv(output_dir / "low_ms1_assessable_coverage_summary.tsv")
    assert summary_rows[0]["feature_family_id"] == "FAM_SHIFT"
    assert summary_rows[0]["root_cause_bucket"] == "rt_window_or_multiseed_shift"
    assert summary_rows[1]["feature_family_id"] == "FAM_SEEDCTX"
    assert summary_rows[1]["root_cause_bucket"] == "seed_aware_overlay_required"

    rows_path = output_dir / "low_ms1_assessable_coverage_rows.tsv"
    assert _read_header(rows_path) == list(audit._detail_fields())

    selected_queue = _read_tsv(
        output_dir / "low_ms1_assessable_coverage_selected_apex_overlay_queue.tsv",
    )
    assert _read_header(
        output_dir / "low_ms1_assessable_coverage_selected_apex_overlay_queue.tsv",
    ) == list(audit._apex_aware_queue_fields())
    assert [row["feature_family_id"] for row in selected_queue] == ["FAM_SHIFT"]
    assert selected_queue[0]["suggested_rt_min"] == "9.85"
    assert selected_queue[0]["suggested_rt_max"] == "11.75"

    seed_queue_path = output_dir / "low_ms1_assessable_coverage_seed_overlay_queue.tsv"
    seed_queue = _read_tsv(seed_queue_path)
    assert _read_header(seed_queue_path) == list(audit._seed_aware_queue_fields())
    assert [row["backfill_seed_rt"] for row in seed_queue] == ["14.1", "14.45"]
    assert seed_queue[1]["suggested_output_prefix"] == "fam_seedctx_seed2_overlay"

    payload = json.loads(
        (output_dir / "low_ms1_assessable_coverage.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["thresholds"]["assessable_fraction_min"] == 0.7
    assert [row["feature_family_id"] for row in payload["summary"]] == [
        "FAM_SHIFT",
        "FAM_SEEDCTX",
    ]

    markdown = (output_dir / "low_ms1_assessable_coverage.md").read_text(
        encoding="utf-8",
    )
    assert "Selected-apex overlay queue: `1`" in markdown
    assert "Seed-aware overlay queue: `2`" in markdown


def test_joins_detected_seed_evidence_from_discovery_dir(tmp_path: Path) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    discovery_dir = tmp_path / "discovery"
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_SEED",
                prefix="fam_seed",
                rt_min="10.0",
                rt_max="11.0",
                global_assessable_fraction="0.6",
                selected_in_window_fraction="0.5",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_SEED", event_cluster_count="5")],
    )
    _write_trace_summary(
        overlay_dir / "fam_seed_trace_summary.tsv",
        [
            _trace_row(
                "SeedA",
                status="detected",
                apex_rt="10.2",
                trace_max="200",
                source_candidate_id="SeedA#1",
            ),
            _trace_row(
                "SeedB",
                status="detected",
                apex_rt="11.4",
                trace_max="180",
                source_candidate_id="SeedB#2",
            ),
        ],
    )
    _write_discovery_candidate(
        discovery_dir,
        "SeedA",
        "SeedA#1",
        evidence_score="55",
        seed_event_count="1",
        nl_ppm="12.5",
    )
    _write_discovery_candidate(
        discovery_dir,
        "SeedB",
        "SeedB#2",
        evidence_score="70",
        seed_event_count="2",
        nl_ppm="3.0",
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
        discovery_dir=discovery_dir,
    )

    summary = result["summary"][0]
    assert summary["detected_seed_candidate_count"] == 2
    assert summary["detected_seed_joined_count"] == 2
    assert summary["min_seed_evidence_score"] == 55
    assert summary["min_seed_event_count"] == 1
    assert summary["max_abs_nl_ppm"] == 12.5
    assert summary["seed_evidence_bucket"] == "weak_detected_seed_evidence"


def test_joins_backfill_seed_audit_and_writes_seed_overlay_queue(
    tmp_path: Path,
) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    seed_audit_tsv = tmp_path / "alignment_owner_backfill_seed_audit.tsv"
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_SEEDCTX",
                prefix="fam_seedctx",
                rt_min="14.0",
                rt_max="15.0",
                global_assessable_fraction="0.5",
                selected_in_window_fraction="1.0",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_SEEDCTX", event_cluster_count="5")],
    )
    _write_trace_summary(
        overlay_dir / "fam_seedctx_trace_summary.tsv",
        [
            _trace_row("S1", apex_rt="14.1", trace_max="0"),
            _trace_row("S2", apex_rt="14.4", trace_max="100"),
            _trace_row("S3", apex_rt="14.5", trace_max="100"),
        ],
    )
    _write_backfill_seed_audit(
        seed_audit_tsv,
        [
            _seed_audit_row("FAM_SEEDCTX", "S1", seed_rt="14.1", delta_sec="0"),
            _seed_audit_row("FAM_SEEDCTX", "S2", seed_rt="14.45", delta_sec="-3"),
            _seed_audit_row("FAM_SEEDCTX", "S3", seed_rt="14.45", delta_sec="90"),
        ],
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
        backfill_seed_audit_tsv=seed_audit_tsv,
    )

    summary = result["summary"][0]
    assert summary["backfill_seed_row_count"] == 3
    assert summary["backfill_seed_group_count"] == 2
    assert round(summary["backfill_seed_rt_span"], 3) == 0.35
    assert summary["seed_apex_far_count"] == 1
    assert summary["seed_context_bucket"] == (
        "multi_seed_family_center_overlay_incomplete"
    )
    assert summary["root_cause_bucket"] == "seed_aware_overlay_required"
    assert summary["recommended_next_action"] == (
        "run_seed_aware_overlay_before_gate_change"
    )
    assert "rt=14.45" in summary["backfill_seed_rt_distribution"]
    assert result["rows"][1]["backfill_seed_rt"] == "14.45"
    queue = result["seed_overlay_queue"]
    assert len(queue) == 2
    assert queue[0]["feature_family_id"] == "FAM_SEEDCTX"
    assert queue[0]["backfill_seed_rt"] == "14.45"
    assert queue[0]["suggested_rt_min"] == "11.45"
    assert queue[0]["suggested_output_prefix"] == "fam_seedctx_seed1_overlay"


def test_seed_group_order_is_shared_by_distribution_and_overlay_rows() -> None:
    grouped = Counter(
        {
            ("301.0", "14.00", "11.00", "17.00", "10.0"): 2,
            ("300.0", "14.45", "11.45", "17.45", "10.0"): 2,
            ("299.0", "14.10", "11.10", "17.10", "10.0"): 1,
        },
    )

    seed_groups = classifier._sorted_seed_groups(grouped)
    distribution = classifier._format_seed_distribution(seed_groups)
    rows = classifier._seed_overlay_rows(seed_groups)

    assert [key[0] for key, _count in seed_groups] == ["300.0", "301.0", "299.0"]
    assert distribution.split(";")[0].startswith("mz=300.0,rt=14.45")
    assert [row["backfill_seed_mz"] for row in rows] == ["300.0", "301.0", "299.0"]
    assert [row["seed_index"] for row in rows] == [1, 2, 3]


def test_missing_required_columns_fail_clearly(tmp_path: Path) -> None:
    review_tsv = tmp_path / "review.tsv"
    review_tsv.write_text("feature_family_id\nFAM001\n", encoding="utf-8")

    code = audit.main(
        [
            "--review-candidates-tsv",
            str(review_tsv),
            "--alignment-dir",
            str(tmp_path / "alignment"),
            "--overlay-dir",
            str(tmp_path / "overlay"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert code == 2


def test_source_candidate_id_is_only_required_for_discovery_join(
    tmp_path: Path,
) -> None:
    review_tsv, alignment_dir, overlay_dir = _fixture_paths(tmp_path)
    _write_candidates(
        review_tsv,
        [
            _candidate_row(
                "FAM_COMPAT",
                prefix="fam_compat",
                rt_min="14.0",
                rt_max="15.0",
                global_assessable_fraction="0.25",
                selected_in_window_fraction="1.0",
            ),
        ],
    )
    _write_alignment_review(
        alignment_dir,
        [_alignment_row("FAM_COMPAT", event_cluster_count="5")],
    )
    trace_row = _trace_row("S1", apex_rt="14.1", trace_max="0")
    trace_row.pop("source_candidate_id")
    _write_tsv(
        overlay_dir / "fam_compat_trace_summary.tsv",
        [trace_row],
        tuple(
            column
            for column in audit.TRACE_REQUIRED_COLUMNS
            if column != "source_candidate_id"
        ),
    )

    result = audit.build_audit(
        review_candidates_tsv=review_tsv,
        alignment_dir=alignment_dir,
        overlay_dir=overlay_dir,
    )

    assert result["summary"][0]["seed_evidence_bucket"] == "not_provided"


def _fixture_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    review_tsv = tmp_path / "review.tsv"
    alignment_dir = tmp_path / "alignment"
    overlay_dir = tmp_path / "overlay"
    alignment_dir.mkdir()
    overlay_dir.mkdir()
    return review_tsv, alignment_dir, overlay_dir


def _candidate_row(
    family_id: str,
    *,
    prefix: str,
    rt_min: str,
    rt_max: str,
    global_assessable_fraction: str = "0.4",
    selected_in_window_fraction: str = "0.5",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "family_center_mz": "300.0",
        "family_center_rt": "14.5",
        "suggested_rt_min": rt_min,
        "suggested_rt_max": rt_max,
        "suggested_output_prefix": prefix,
        "detected_count": "5",
        "accepted_backfill_count": "80",
        "detected_backfilled_count": "85",
        "global_apex_assessable_fraction": global_assessable_fraction,
        "selected_apex_in_trace_window_fraction": selected_in_window_fraction,
        "review_classification": "low_ms1_assessable_coverage_review",
    }


def _alignment_row(
    family_id: str,
    *,
    event_cluster_count: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "event_cluster_count": event_cluster_count,
        "event_member_count": event_cluster_count,
        "identity_decision": "production_family",
        "primary_evidence": "owner_complete_link",
        "row_flags": "rescue_heavy",
        "reason": "anchor family; MS1 backfilled",
    }


def _trace_row(
    sample: str,
    *,
    status: str = "backfilled",
    apex_rt: str,
    trace_max: str,
    source_candidate_id: str = "",
) -> dict[str, str]:
    return {
        "sample_stem": sample,
        "status": status,
        "cell_area": "1000",
        "cell_height": "100",
        "cell_apex_rt": apex_rt,
        "trace_max_intensity": trace_max,
        "trace_apex_rt": apex_rt if trace_max != "0" else "",
        "global_trace_apex_delta_min": "0.0" if trace_max != "0" else "",
        "local_window_to_global_max_ratio": "1.0" if trace_max != "0" else "",
        "region_shadow_verdict": "split_supported",
        "source_candidate_id": source_candidate_id,
    }


def _write_candidates(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(path, rows, audit.REVIEW_REQUIRED_COLUMNS)


def _write_alignment_review(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path / "alignment_review.tsv",
        rows,
        audit.ALIGNMENT_REVIEW_REQUIRED_COLUMNS,
    )


def _write_trace_summary(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        rows,
        audit.TRACE_REQUIRED_COLUMNS + audit.TRACE_DISCOVERY_JOIN_COLUMNS,
    )


def _seed_audit_row(
    family_id: str,
    sample_stem: str,
    *,
    seed_rt: str,
    delta_sec: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "backfill_seed_mz": "300.0",
        "backfill_seed_rt": seed_rt,
        "backfill_request_rt_min": str(float(seed_rt) - 3.0),
        "backfill_request_rt_max": str(float(seed_rt) + 3.0),
        "backfill_request_ppm": "20",
        "backfill_apex_delta_sec": delta_sec,
    }


def _write_backfill_seed_audit(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(path, rows, audit.BACKFILL_SEED_AUDIT_REQUIRED_COLUMNS)


def _write_discovery_candidate(
    discovery_dir: Path,
    sample_stem: str,
    candidate_id: str,
    *,
    evidence_score: str,
    seed_event_count: str,
    nl_ppm: str,
) -> None:
    sample_dir = discovery_dir / sample_stem
    sample_dir.mkdir(parents=True)
    _write_tsv(
        sample_dir / "discovery_candidates.csv",
        [
            {
                "candidate_id": candidate_id,
                "precursor_mz": "500.0",
                "product_mz": "384.0",
                "observed_neutral_loss_da": "116.0",
                "evidence_score": evidence_score,
                "seed_event_count": seed_event_count,
                "neutral_loss_mass_error_ppm": nl_ppm,
            },
        ],
        audit.DISCOVERY_REQUIRED_COLUMNS,
        delimiter=",",
    )


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
    *,
    delimiter: str = "\t",
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter=delimiter, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle, delimiter="\t"))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
