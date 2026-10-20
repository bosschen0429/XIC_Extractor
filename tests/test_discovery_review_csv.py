import csv
from pathlib import Path

from xic_extractor.discovery.csv_writer import (
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DiscoveryBatchOutputs,
    DiscoveryCandidate,
    DiscoveryRunOutputs,
)

EXPECTED_BRIEF_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "candidate_id",
    "precursor_mz",
    "best_seed_rt",
    "ms1_area",
    "seed_event_count",
    "neutral_loss_tag",
    "matched_tag_names",
    "matched_tag_count",
    "tag_intersection_status",
    "review_note",
)


def test_discovery_brief_columns_are_stable_csv_contract() -> None:
    assert DISCOVERY_BRIEF_COLUMNS == EXPECTED_BRIEF_COLUMNS
    assert len(DISCOVERY_BRIEF_COLUMNS) == 16


def test_discovery_run_outputs_carries_two_csv_paths(tmp_path: Path) -> None:
    outputs = DiscoveryRunOutputs(
        candidates_csv=Path("a.csv"),
        review_csv=Path("b.csv"),
        physical_facts_json=tmp_path / "discovery_physical_facts.json",
    )
    assert outputs.candidates_csv == Path("a.csv")
    assert outputs.review_csv == Path("b.csv")


def test_discovery_batch_outputs_carries_index_and_per_sample(tmp_path: Path) -> None:
    per_sample = (
        DiscoveryRunOutputs(
            candidates_csv=Path("s1/c.csv"),
            review_csv=Path("s1/r.csv"),
            physical_facts_json=tmp_path / "discovery_physical_facts.json",
        ),
    )
    batch = DiscoveryBatchOutputs(
        batch_index_csv=Path("idx.csv"),
        per_sample=per_sample,
    )
    assert batch.batch_index_csv == Path("idx.csv")
    assert batch.per_sample == per_sample


def test_write_discovery_review_csv_writes_only_brief_columns(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "discovery_review.csv"

    write_discovery_review_csv(output_path, [_candidate()])

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert tuple(reader.fieldnames or ()) == DISCOVERY_BRIEF_COLUMNS
    assert rows[0]["candidate_id"] == "Sample_1#101"
    assert "raw_file" not in rows[0]


def test_review_csv_uses_same_candidate_order_as_full_csv(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            review_priority="MEDIUM",
            evidence_score=40,
            candidate_id="medium-c",
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=75,
            candidate_id="high-b",
        ),
        _candidate(
            review_priority="HIGH",
            evidence_score=90,
            candidate_id="high-a",
        ),
    ]

    full_path = write_discovery_candidates_csv(
        tmp_path / "discovery_candidates.csv",
        candidates,
    )
    review_path = write_discovery_review_csv(
        tmp_path / "discovery_review.csv",
        candidates,
    )

    assert _candidate_ids(full_path) == _candidate_ids(review_path)


def test_review_csv_escapes_excel_formula_text(tmp_path: Path) -> None:
    output_path = tmp_path / "discovery_review.csv"

    write_discovery_review_csv(
        output_path,
        [
            _candidate(
                candidate_id="=cmd",
                neutral_loss_tag="+DNA_dR",
            )
        ],
    )

    rows = _read_csv(output_path)
    assert rows[0]["candidate_id"] == "'=cmd"
    assert rows[0]["neutral_loss_tag"] == "'+DNA_dR"


def test_review_note_is_concise_and_not_full_reason(tmp_path: Path) -> None:
    output_path = tmp_path / "discovery_review.csv"
    reason = (
        "single MS2 NL seed; MS1 peak found near seed RT; "
        "this is intentionally verbose diagnostic wording"
    )

    write_discovery_review_csv(
        output_path,
        [
            _candidate(
                reason=reason,
                ms2_support="strong",
                ms1_support="moderate",
                rt_alignment="aligned",
            )
        ],
    )

    note = _read_csv(output_path)[0]["review_note"]
    assert note == "strong MS2; moderate MS1; aligned RT"
    assert note != reason


def _candidate(
    *,
    review_priority: str = "MEDIUM",
    evidence_score: int = 50,
    evidence_tier: str = "C",
    ms2_support: str = "moderate",
    ms1_support: str = "moderate",
    rt_alignment: str = "aligned",
    candidate_id: str = "Sample_1#101",
    seed_event_count: int = 1,
    neutral_loss_tag: str = "DNA_dR",
    reason: str = "single MS2 NL seed; MS1 peak found",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        review_priority=review_priority,  # type: ignore[arg-type]
        evidence_score=evidence_score,
        evidence_tier=evidence_tier,
        ms2_support=ms2_support,
        ms1_support=ms1_support,
        rt_alignment=rt_alignment,
        candidate_id=candidate_id,
        precursor_mz=258.1085,
        product_mz=142.0611,
        observed_neutral_loss_da=116.0474,
        best_seed_rt=7.83,
        seed_event_count=seed_event_count,
        ms1_peak_found=True,
        ms1_apex_rt=7.84,
        ms1_area=88765.4,
        ms2_product_max_intensity=12000.0,
        reason=reason,
        raw_file=Path("C:/data/Sample_1.raw"),
        sample_stem="Sample_1",
        best_ms2_scan_id=101,
        seed_scan_ids=(101,),
        neutral_loss_tag=neutral_loss_tag,
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.0,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=7.70,
        ms1_peak_rt_end=7.98,
        ms1_height=4500.0,
        ms1_trace_quality="GOOD",
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _candidate_ids(path: Path) -> list[str]:
    return [row["candidate_id"] for row in _read_csv(path)]
