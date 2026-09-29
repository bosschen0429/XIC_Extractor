import csv
from dataclasses import fields
from pathlib import Path

from xic_extractor.discovery.csv_writer import (
    _format_csv_value,
    write_discovery_candidates_csv,
)
from xic_extractor.discovery.evidence_config import DEFAULT_EVIDENCE_PROFILE
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_COLUMNS,
    DISCOVERY_CANDIDATE_REVIEW_COLUMNS,
    DISCOVERY_PROVENANCE_COLUMNS,
    DISCOVERY_REVIEW_COLUMNS,
    DiscoveryCandidate,
    DiscoverySeed,
    DiscoverySeedGroup,
    DiscoverySettings,
    NeutralLossProfile,
)

EXPECTED_CANDIDATE_REVIEW_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "candidate_id",
    "feature_family_id",
    "feature_family_size",
    "precursor_mz",
    "product_mz",
    "observed_neutral_loss_da",
    "best_seed_rt",
    "seed_event_count",
    "ms1_peak_found",
    "ms1_apex_rt",
    "ms1_area",
    "ms2_product_max_intensity",
    "reason",
)

EXPECTED_SUCCESSOR_COLUMNS = (
    "discovery_candidate_state",
    "ms1_feature_row_id",
)

EXPECTED_PROVENANCE_COLUMNS = (
    "raw_file",
    "sample_stem",
    "best_ms2_scan_id",
    "seed_scan_ids",
    "neutral_loss_tag",
    "configured_neutral_loss_da",
    "neutral_loss_mass_error_ppm",
    "neutral_loss_error_basis",
    "precursor_mz_basis",
    "scan_precursor_mz",
    "scan_precursor_delta_da",
    "max_scan_precursor_abs_delta_da",
    "rt_seed_min",
    "rt_seed_max",
    "ms1_search_rt_min",
    "ms1_search_rt_max",
    "ms1_seed_delta_min",
    "ms1_peak_rt_start",
    "ms1_peak_rt_end",
    "ms1_height",
    "ms1_apex_mz",
    "ms1_trace_quality",
    "ms1_scan_support_score",
    "selected_tag_count",
    "matched_tag_count",
    "matched_tag_names",
    "primary_tag_name",
    "tag_combine_mode",
    "tag_intersection_status",
    "tag_evidence_json",
)

def _candidate(
    *,
    review_priority: str = "MEDIUM",
    evidence_score: int = 50,
    evidence_tier: str = "C",
    ms2_support: str = "moderate",
    ms1_support: str = "moderate",
    rt_alignment: str = "aligned",
    candidate_id: str = "candidate-1",
    sample_stem: str = "Sample_1",
    reason: str = "neutral loss seed",
    raw_file: Path = Path("C:/data/Sample_1.raw"),
    best_seed_rt: float = 7.83,
    seed_event_count: int = 1,
    ms1_area: float | None = 88765.4,
    ms2_product_max_intensity: float = 12000.0,
    ms1_peak_found: bool = True,
    seed_scan_ids: tuple[int, ...] = (6095,),
    feature_family_id: str = "Sample_1@F0001",
    feature_family_size: int = 1,
    ms1_scan_support_score: float | None = None,
    discovery_candidate_state: str = "ms1_feature_nl_supported",
    ms1_feature_row_id: str = "Sample_1|DNA_dR|258.108512|7.841234",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        review_priority=review_priority,  # type: ignore[arg-type]
        evidence_score=evidence_score,
        evidence_tier=evidence_tier,
        ms2_support=ms2_support,
        ms1_support=ms1_support,
        rt_alignment=rt_alignment,
        discovery_candidate_state=discovery_candidate_state,  # type: ignore[arg-type]
        ms1_feature_row_id=ms1_feature_row_id,
        candidate_id=candidate_id,
        feature_family_id=feature_family_id,
        feature_family_size=feature_family_size,
        precursor_mz=258.108512345,
        product_mz=142.061112345,
        observed_neutral_loss_da=116.0474,
        best_seed_rt=best_seed_rt,
        seed_event_count=seed_event_count,
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=7.841234,
        ms1_area=ms1_area,
        ms2_product_max_intensity=ms2_product_max_intensity,
        reason=reason,
        raw_file=raw_file,
        sample_stem=sample_stem,
        best_ms2_scan_id=6095,
        seed_scan_ids=seed_scan_ids,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.584321,
        neutral_loss_error_basis="measured_scan_precursor_product",
        precursor_mz_basis="scan_precursor",
        scan_precursor_mz=258.108512345,
        scan_precursor_delta_da=0.0,
        max_scan_precursor_abs_delta_da=0.0,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.011234,
        ms1_peak_rt_start=7.701234,
        ms1_peak_rt_end=7.981234,
        ms1_height=4500.5,
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=ms1_scan_support_score,
    )

def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))

def test_discovery_review_columns_alias_stays_compatible() -> None:
    assert DISCOVERY_REVIEW_COLUMNS == EXPECTED_CANDIDATE_REVIEW_COLUMNS

def test_discovery_candidate_review_columns_are_named_explicitly() -> None:
    assert DISCOVERY_CANDIDATE_REVIEW_COLUMNS == EXPECTED_CANDIDATE_REVIEW_COLUMNS
    assert DISCOVERY_REVIEW_COLUMNS == DISCOVERY_CANDIDATE_REVIEW_COLUMNS

def test_discovery_provenance_columns_are_stable_csv_contract() -> None:
    assert DISCOVERY_PROVENANCE_COLUMNS == EXPECTED_PROVENANCE_COLUMNS

def test_discovery_candidate_columns_start_with_review_columns() -> None:
    review_column_count = len(DISCOVERY_REVIEW_COLUMNS)
    successor_column_count = len(EXPECTED_SUCCESSOR_COLUMNS)
    assert DISCOVERY_CANDIDATE_COLUMNS[:review_column_count] == DISCOVERY_REVIEW_COLUMNS
    assert (
        DISCOVERY_CANDIDATE_COLUMNS[
            review_column_count : review_column_count + successor_column_count
        ]
        == EXPECTED_SUCCESSOR_COLUMNS
    )
    assert (
        DISCOVERY_CANDIDATE_COLUMNS[review_column_count + successor_column_count :-1]
        == DISCOVERY_PROVENANCE_COLUMNS
    )
    assert DISCOVERY_CANDIDATE_COLUMNS[-1] == "is_provisional"

def test_discovery_candidate_has_optional_scan_support_score() -> None:
    field_names = {field.name for field in fields(DiscoveryCandidate)}

    assert "ms1_scan_support_score" in field_names
    assert _candidate().ms1_scan_support_score is None

def test_discovery_candidate_has_successor_state_and_ms1_feature_identity() -> None:
    field_names = {field.name for field in fields(DiscoveryCandidate)}

    assert "discovery_candidate_state" in field_names
    assert "ms1_feature_row_id" in field_names
    assert _candidate().discovery_candidate_state == "ms1_feature_nl_supported"
    assert _candidate().ms1_feature_row_id == (
        "Sample_1|DNA_dR|258.108512|7.841234"
    )

def test_tag_evidence_json_is_last_provenance_column() -> None:
    review_column_count = len(DISCOVERY_REVIEW_COLUMNS)
    successor_column_count = len(EXPECTED_SUCCESSOR_COLUMNS)
    assert DISCOVERY_CANDIDATE_COLUMNS[:review_column_count] == DISCOVERY_REVIEW_COLUMNS
    assert (
        DISCOVERY_CANDIDATE_COLUMNS[
            review_column_count : review_column_count + successor_column_count
        ]
        == EXPECTED_SUCCESSOR_COLUMNS
    )
    assert DISCOVERY_PROVENANCE_COLUMNS[-1] == "tag_evidence_json"
    assert DISCOVERY_PROVENANCE_COLUMNS[
        DISCOVERY_PROVENANCE_COLUMNS.index("neutral_loss_mass_error_ppm") + 1:
        DISCOVERY_PROVENANCE_COLUMNS.index("rt_seed_min")
    ] == (
        "neutral_loss_error_basis",
        "precursor_mz_basis",
        "scan_precursor_mz",
        "scan_precursor_delta_da",
        "max_scan_precursor_abs_delta_da",
    )

def test_write_discovery_candidates_csv_creates_parent_and_writes_header(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "nested" / "discovery_candidates.csv"

    result_path = write_discovery_candidates_csv(output_path, [_candidate()])

    assert result_path == output_path
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == list(DISCOVERY_CANDIDATE_COLUMNS)

def test_write_discovery_candidates_csv_writes_header_for_empty_candidates(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"

    write_discovery_candidates_csv(output_path, [])

    with output_path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [list(DISCOVERY_CANDIDATE_COLUMNS)]

def test_write_discovery_candidates_csv_sorts_review_queue_rows(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidates = [
        _candidate(
            review_priority="LOW",
            candidate_id="low",
            seed_event_count=99,
            ms2_product_max_intensity=99999.0,
            ms1_area=99999.0,
            best_seed_rt=1.0,
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=60,
            evidence_tier="B",
            candidate_id="high-low-count",
            seed_event_count=2,
            ms2_product_max_intensity=99999.0,
            ms1_area=99999.0,
            best_seed_rt=1.0,
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=80,
            evidence_tier="A",
            candidate_id="high-count-intensity",
            seed_event_count=3,
            ms2_product_max_intensity=20000.0,
            ms1_area=1.0,
            best_seed_rt=2.0,
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=80,
            evidence_tier="A",
            candidate_id="high-count-intensity-area-early",
            seed_event_count=3,
            ms2_product_max_intensity=30000.0,
            ms1_area=100.0,
            best_seed_rt=4.0,
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=80,
            evidence_tier="A",
            candidate_id="high-count-intensity-area-none",
            seed_event_count=3,
            ms2_product_max_intensity=30000.0,
            ms1_area=None,
            best_seed_rt=1.0,
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=80,
            evidence_tier="A",
            candidate_id="high-count-intensity-area-late",
            seed_event_count=3,
            ms2_product_max_intensity=30000.0,
            ms1_area=100.0,
            best_seed_rt=5.0,
        ),
        _candidate(review_priority="MEDIUM", candidate_id="medium"),
    ]

    write_discovery_candidates_csv(output_path, candidates)

    assert [row["candidate_id"] for row in _read_csv(output_path)] == [
        "high-count-intensity-area-early",
        "high-count-intensity-area-late",
        "high-count-intensity-area-none",
        "high-count-intensity",
        "high-low-count",
        "medium",
        "low",
    ]

def test_write_discovery_candidates_csv_sorts_by_evidence_score_within_priority(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidates = [
        _candidate(
            review_priority="MEDIUM",
            evidence_score=40,
            evidence_tier="C",
            candidate_id="medium-c",
            seed_event_count=9,
            ms2_product_max_intensity=999999.0,
        ),
        _candidate(
            review_priority="MEDIUM",
            evidence_score=75,
            evidence_tier="B",
            candidate_id="medium-b",
            seed_event_count=1,
            ms2_product_max_intensity=1.0,
        ),
        _candidate(
            review_priority="LOW",
            evidence_score=95,
            evidence_tier="A",
            candidate_id="low-a",
        ),
    ]

    write_discovery_candidates_csv(output_path, candidates)

    assert [row["candidate_id"] for row in _read_csv(output_path)] == [
        "medium-b",
        "medium-c",
        "low-a",
    ]

def test_write_discovery_candidates_csv_keeps_feature_families_together(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidates = [
        _candidate(
            candidate_id="family-a-lower-intensity",
            feature_family_id="Sample_1@F0001",
            feature_family_size=2,
            ms2_product_max_intensity=1000.0,
        ),
        _candidate(
            candidate_id="family-b-between",
            feature_family_id="Sample_1@F0002",
            feature_family_size=1,
            ms2_product_max_intensity=9000.0,
        ),
        _candidate(
            candidate_id="family-a-higher-intensity",
            feature_family_id="Sample_1@F0001",
            feature_family_size=2,
            ms2_product_max_intensity=8000.0,
        ),
    ]

    write_discovery_candidates_csv(output_path, candidates)

    assert [row["candidate_id"] for row in _read_csv(output_path)] == [
        "family-a-higher-intensity",
        "family-a-lower-intensity",
        "family-b-between",
    ]

def test_write_discovery_candidates_csv_formats_review_values_stably(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidate = _candidate(ms1_peak_found=False, ms1_area=None)

    write_discovery_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["precursor_mz"] == "258.108512345"
    assert row["evidence_score"] == "50"
    assert row["evidence_tier"] == "C"
    assert row["ms2_support"] == "moderate"
    assert row["ms1_support"] == "moderate"
    assert row["rt_alignment"] == "aligned"
    assert row["discovery_candidate_state"] == "ms1_feature_nl_supported"
    assert row["ms1_feature_row_id"] == "Sample_1|DNA_dR|258.108512|7.841234"
    assert row["feature_family_id"] == "Sample_1@F0001"
    assert row["feature_family_size"] == "1"
    assert row["product_mz"] == "142.061112345"
    assert row["observed_neutral_loss_da"] == "116.0474"
    assert row["best_seed_rt"] == "7.83"
    assert row["neutral_loss_mass_error_ppm"] == "2.584321"
    assert row["neutral_loss_error_basis"] == "measured_scan_precursor_product"
    assert row["precursor_mz_basis"] == "scan_precursor"
    assert row["scan_precursor_mz"] == "258.108512345"
    assert row["scan_precursor_delta_da"] == "0"
    assert row["max_scan_precursor_abs_delta_da"] == "0"
    assert row["ms1_area"] == ""
    assert row["ms2_product_max_intensity"] == "12000"
    assert row["ms1_peak_found"] == "FALSE"
    assert row["ms1_apex_rt"] == "7.841234"
    assert row["raw_file"] == "C:\\data\\Sample_1.raw"

def test_write_discovery_candidates_csv_serializes_seed_scan_ids(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidate = _candidate(seed_scan_ids=(6095, 6100, 6112))

    write_discovery_candidates_csv(output_path, [candidate])

    assert _read_csv(output_path)[0]["seed_scan_ids"] == "6095;6100;6112"

def test_seed_scan_ids_formatter_does_not_join_string_characters() -> None:
    assert _format_csv_value("seed_scan_ids", (6095, 6100, 6112)) == "6095;6100;6112"
    assert _format_csv_value("seed_scan_ids", "6095;6100;6112") == "6095;6100;6112"

def test_write_discovery_candidates_csv_uses_csv_escaping(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidate = _candidate(
        sample_stem='Sample, "quoted"',
        reason='neutral loss, requires "review"',
    )

    write_discovery_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["sample_stem"] == 'Sample, "quoted"'
    assert row["reason"] == 'neutral loss, requires "review"'

def test_write_discovery_candidates_csv_escapes_excel_formula_strings(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidate = _candidate(
        raw_file=Path("=cmd.raw"),
        sample_stem="+sample",
        reason="@review",
    )

    write_discovery_candidates_csv(output_path, [candidate])

    row = _read_csv(output_path)[0]
    assert row["raw_file"] == "'=cmd.raw"
    assert row["sample_stem"] == "'+sample"
    assert row["reason"] == "'@review"

def test_discovery_settings_require_neutral_loss_profile() -> None:
    profile = NeutralLossProfile("DNA_dR", 116.0474)

    settings = DiscoverySettings(neutral_loss_profile=profile)

    assert settings.neutral_loss_profile == profile
    assert settings.nl_tolerance_ppm == 20.0
    assert settings.precursor_mz_tolerance_ppm == 20.0
    assert settings.product_mz_tolerance_ppm == 20.0
    assert settings.product_search_ppm == 50.0
    assert settings.nl_min_intensity_ratio == 0.01
    assert settings.seed_rt_gap_min == 0.20
    assert settings.ms1_search_padding_min == 0.35
    assert settings.rt_min == 0.0
    assert settings.rt_max == 999.0
    assert settings.resolver_mode == "local_minimum"

def test_discovery_settings_default_evidence_profile() -> None:
    profile = NeutralLossProfile("DNA_dR", 116.0474)

    settings = DiscoverySettings(neutral_loss_profile=profile)

    assert settings.evidence_profile == DEFAULT_EVIDENCE_PROFILE
    assert settings.evidence_profile.weights == DEFAULT_EVIDENCE_PROFILE.weights
    assert (
        settings.evidence_profile.thresholds
        == DEFAULT_EVIDENCE_PROFILE.thresholds
    )

def test_discovery_seed_preserves_full_ms2_seed_contract() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")

    seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        scan_number=6095,
        rt=7.83,
        precursor_mz=258.1085,
        product_mz=142.0611,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0471,
        observed_loss_error_ppm=2.58,
    )

    assert seed.raw_file == raw_file
    assert seed.sample_stem == "TumorBC2312_DNA"
    assert seed.scan_number == 6095
    assert seed.rt == 7.83
    assert seed.precursor_mz == 258.1085
    assert seed.product_mz == 142.0611
    assert seed.product_intensity == 12000.0
    assert seed.neutral_loss_tag == "DNA_dR"
    assert seed.configured_neutral_loss_da == 116.0474
    assert seed.observed_neutral_loss_da == 116.0471
    assert seed.observed_loss_error_ppm == 2.58

def test_discovery_seed_group_preserves_group_contract_without_best_seed() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")
    seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        scan_number=6095,
        rt=7.83,
        precursor_mz=258.1085,
        product_mz=142.0611,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0471,
        observed_loss_error_ppm=2.58,
    )

    group = DiscoverySeedGroup(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        seeds=(seed,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        precursor_mz=258.1085,
        product_mz=142.0611,
        observed_neutral_loss_da=116.0471,
        neutral_loss_mass_error_ppm=2.58,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
    )

    assert tuple(field.name for field in fields(DiscoverySeedGroup)) == (
        "raw_file",
        "sample_stem",
        "seeds",
        "neutral_loss_tag",
        "configured_neutral_loss_da",
        "precursor_mz",
        "product_mz",
        "observed_neutral_loss_da",
        "neutral_loss_mass_error_ppm",
        "rt_seed_min",
        "rt_seed_max",
        "matched_tag_names",
        "tag_evidence_json",
        "precursor_mz_basis",
        "neutral_loss_error_basis",
        "scan_precursor_mz",
        "scan_precursor_delta_da",
        "max_scan_precursor_abs_delta_da",
    )
    assert group.raw_file == raw_file
    assert group.sample_stem == "TumorBC2312_DNA"
    assert group.seeds == (seed,)
    assert group.neutral_loss_tag == "DNA_dR"
    assert group.configured_neutral_loss_da == 116.0474
    assert group.precursor_mz == 258.1085
    assert group.product_mz == 142.0611
    assert group.observed_neutral_loss_da == 116.0471
    assert group.neutral_loss_mass_error_ppm == 2.58
    assert group.rt_seed_min == 7.80
    assert group.rt_seed_max == 7.86
    assert group.matched_tag_names == ()
    assert group.tag_evidence_json == "{}"
    assert group.precursor_mz_basis == "scan_precursor"
    assert group.neutral_loss_error_basis == "measured_scan_precursor_product"
    assert group.scan_precursor_mz is None
    assert group.scan_precursor_delta_da is None
    assert group.max_scan_precursor_abs_delta_da is None

def test_discovery_candidate_from_values_builds_row_identity_candidate_id() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")
    best_seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        scan_number=6095,
        rt=7.83,
        precursor_mz=258.1085,
        product_mz=142.0611,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0471,
        observed_loss_error_ppm=2.58,
    )

    candidate = DiscoveryCandidate.from_values(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        precursor_mz=258.1085,
        product_mz=142.0611,
        observed_neutral_loss_da=116.0471,
        best_seed=best_seed,
        seed_scan_ids=(6095,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.58,
        neutral_loss_error_basis="configured_loss_inferred_precursor",
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=300.2028,
        scan_precursor_delta_da=0.0423,
        max_scan_precursor_abs_delta_da=0.0423,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=7.70,
        ms1_peak_rt_end=7.98,
        ms1_height=4500.0,
        ms1_trace_quality="GOOD",
        seed_event_count=1,
        ms1_peak_found=True,
        ms1_apex_rt=7.84,
        ms1_area=88765.4,
        ms2_product_max_intensity=12000.0,
        review_priority="HIGH",
        reason="neutral loss seed",
    )

    assert candidate.candidate_id == "TumorBC2312_DNA#6095@mz258.1085_p142.0611"
    assert candidate.review_priority == "HIGH"
    assert candidate.reason == "neutral loss seed"
    assert candidate.seed_scan_ids == (6095,)
    assert candidate.raw_file == raw_file
    assert candidate.sample_stem == "TumorBC2312_DNA"
    assert candidate.best_ms2_scan_id == 6095
    assert candidate.best_seed_rt == 7.83
    assert candidate.precursor_mz == 258.1085
    assert candidate.product_mz == 142.0611
    assert candidate.observed_neutral_loss_da == 116.0471
    assert candidate.seed_event_count == 1
    assert candidate.ms1_peak_found is True
    assert candidate.neutral_loss_tag == "DNA_dR"
    assert candidate.configured_neutral_loss_da == 116.0474
    assert candidate.neutral_loss_mass_error_ppm == 2.58
    assert candidate.neutral_loss_error_basis == "configured_loss_inferred_precursor"
    assert candidate.precursor_mz_basis == "product_plus_neutral_loss"
    assert candidate.discovery_candidate_state == "ms1_feature_nl_rescued"
    assert candidate.ms1_feature_row_id == "TumorBC2312_DNA|DNA_dR|258.1085|7.84"
    assert "6095" not in candidate.ms1_feature_row_id
    assert candidate.scan_precursor_mz == 300.2028
    assert candidate.scan_precursor_delta_da == 0.0423
    assert candidate.max_scan_precursor_abs_delta_da == 0.0423
    assert candidate.rt_seed_min == 7.80
    assert candidate.rt_seed_max == 7.86
    assert candidate.ms1_search_rt_min == 7.60
    assert candidate.ms1_search_rt_max == 8.06
    assert candidate.ms1_seed_delta_min == 0.01
    assert candidate.ms1_peak_rt_start == 7.70
    assert candidate.ms1_peak_rt_end == 7.98
    assert candidate.ms1_apex_rt == 7.84
    assert candidate.ms1_area == 88765.4
    assert candidate.ms1_height == 4500.0
    assert candidate.ms1_trace_quality == "GOOD"
    assert candidate.ms2_product_max_intensity == 12000.0

def test_discovery_candidate_from_values_assigns_supported_state() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")
    best_seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        scan_number=6095,
        rt=7.83,
        precursor_mz=258.1085,
        product_mz=142.0611,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0471,
        observed_loss_error_ppm=2.58,
    )

    candidate = DiscoveryCandidate.from_values(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        precursor_mz=258.1085,
        product_mz=142.0611,
        observed_neutral_loss_da=116.0471,
        best_seed=best_seed,
        seed_scan_ids=(6095,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.58,
        precursor_mz_basis="scan_precursor",
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=7.70,
        ms1_peak_rt_end=7.98,
        ms1_height=4500.0,
        ms1_trace_quality="GOOD",
        seed_event_count=1,
        ms1_peak_found=True,
        ms1_apex_rt=7.84,
        ms1_area=88765.4,
        ms2_product_max_intensity=12000.0,
        review_priority="HIGH",
        reason="neutral loss seed",
    )

    assert candidate.discovery_candidate_state == "ms1_feature_nl_supported"
    assert candidate.ms1_feature_row_id == "TumorBC2312_DNA|DNA_dR|258.1085|7.84"

def test_from_values_makes_orphan_review_only_without_ms1_peak() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")
    best_seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        scan_number=6095,
        rt=7.83,
        precursor_mz=258.1085,
        product_mz=142.0611,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0471,
        observed_loss_error_ppm=2.58,
    )

    candidate = DiscoveryCandidate.from_values(
        raw_file=raw_file,
        sample_stem="TumorBC2312_DNA",
        precursor_mz=258.1085,
        product_mz=142.0611,
        observed_neutral_loss_da=116.0471,
        best_seed=best_seed,
        seed_scan_ids=(6095,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.58,
        precursor_mz_basis="product_plus_neutral_loss",
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=None,
        ms1_peak_rt_start=None,
        ms1_peak_rt_end=None,
        ms1_height=None,
        ms1_trace_quality="missing",
        seed_event_count=1,
        ms1_peak_found=False,
        ms1_apex_rt=None,
        ms1_area=None,
        ms2_product_max_intensity=12000.0,
        review_priority="LOW",
        reason="neutral loss seed",
    )

    assert candidate.discovery_candidate_state == "review_only_orphan_nl"
    assert candidate.ms1_feature_row_id == ""

def test_candidate_id_distinguishes_same_scan_precursor_product_hypotheses() -> None:
    raw_file = Path("C:/data/TumorBC2312_DNA.raw")

    def _build_candidate(
        precursor_mz: float,
        product_mz: float,
        *,
        scan_number: int = 19561,
        apex_rt: float = 23.3417,
    ) -> DiscoveryCandidate:
        seed = DiscoverySeed(
            raw_file=raw_file,
            sample_stem="TumorBC2312_DNA",
            scan_number=scan_number,
            rt=apex_rt,
            precursor_mz=301.164978,
            product_mz=185.115845,
            product_intensity=367660.1,
            neutral_loss_tag="DNA_dR",
            configured_neutral_loss_da=116.0474,
            observed_neutral_loss_da=116.0474,
            observed_loss_error_ppm=0.0,
        )
        return DiscoveryCandidate.from_values(
            raw_file=raw_file,
            sample_stem="TumorBC2312_DNA",
            precursor_mz=precursor_mz,
            product_mz=product_mz,
            observed_neutral_loss_da=116.0474,
            best_seed=seed,
            seed_scan_ids=(scan_number,),
            neutral_loss_tag="DNA_dR",
            configured_neutral_loss_da=116.0474,
            neutral_loss_mass_error_ppm=0.0,
            rt_seed_min=23.4669,
            rt_seed_max=23.4669,
            ms1_search_rt_min=23.2669,
            ms1_search_rt_max=23.6669,
            ms1_seed_delta_min=0.0,
            ms1_peak_rt_start=23.20,
            ms1_peak_rt_end=23.48,
            ms1_height=205086000.0,
            ms1_trace_quality="GOOD",
            seed_event_count=1,
            ms1_peak_found=True,
            ms1_apex_rt=apex_rt,
            ms1_area=205086000.0,
            ms2_product_max_intensity=367660.1,
            review_priority="HIGH",
            reason="strict MS2 NL seed",
        )

    monoisotopic = _build_candidate(300.160620, 184.113220)
    same_feature_different_scan = _build_candidate(
        300.160620,
        184.113220,
        scan_number=19867,
    )
    isotope = _build_candidate(301.164978, 185.115845)

    assert monoisotopic.best_ms2_scan_id == isotope.best_ms2_scan_id == 19561
    assert same_feature_different_scan.best_ms2_scan_id == 19867
    assert monoisotopic.candidate_id != isotope.candidate_id
    assert monoisotopic.candidate_id != same_feature_different_scan.candidate_id
    assert monoisotopic.candidate_id == (
        "TumorBC2312_DNA#19561@mz300.16062_p184.11322"
    )
    assert isotope.candidate_id == (
        "TumorBC2312_DNA#19561@mz301.164978_p185.115845"
    )
    assert monoisotopic.ms1_feature_row_id == (
        same_feature_different_scan.ms1_feature_row_id
    )
    assert "19561" not in monoisotopic.ms1_feature_row_id
    assert "19867" not in monoisotopic.ms1_feature_row_id
    assert monoisotopic.ms1_feature_row_id != isotope.ms1_feature_row_id
