import csv
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.discovery.models import DISCOVERY_CANDIDATE_COLUMNS


@pytest.mark.parametrize("provisional,seed_count", [(True, 1), (True, 2), (False, 2)])
def test_owner_bound_csv_preserves_final_provisional_decision(
    tmp_path: Path,
    provisional: bool,
    seed_count: int,
) -> None:
    from xic_extractor.alignment.csv_io import (
        read_discovery_candidates_csv,
        read_owner_bound_discovery_candidates_csv,
    )
    from xic_extractor.discovery.batch_contract import (
        mint_discovery_candidate_universe_receipt,
        validate_discovery_candidate_universe,
    )
    from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
    from xic_extractor.processing_identity import (
        IdentityContractError,
        build_acquisition_source_identity,
    )

    path = tmp_path / "candidates.csv"
    _write_csv(path, DISCOVERY_CANDIDATE_COLUMNS, [_candidate_row()])
    (original,) = read_discovery_candidates_csv(path)
    candidate = replace(
        original,
        ms1_trace_quality="provisional",
        is_provisional=provisional,
        seed_event_count=seed_count,
    )
    write_discovery_candidates_csv(path, (candidate,))
    raw = tmp_path / "Sample_1.raw"
    raw.write_bytes(b"synthetic acquisition")
    source = build_acquisition_source_identity(
        raw,
        acquisition_format="thermo_raw",
        observation_namespace="test",
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_1",
        acquisition_source=source,
        candidate_csv=path,
        candidates=(candidate,),
    )
    (restored,) = read_owner_bound_discovery_candidates_csv(path, receipt=receipt)
    assert restored.is_provisional is provisional
    assert restored.seed_event_count == seed_count
    assert restored.ms1_trace_quality == "provisional"
    with pytest.raises(IdentityContractError, match="candidate payload"):
        validate_discovery_candidate_universe(
            receipt,
            acquisition_source=source,
            candidates=(replace(restored, is_provisional=not provisional),),
        )


@pytest.mark.parametrize("value", ["", "true", "1", "unknown"])
def test_candidate_csv_rejects_invalid_provisional_flag(
    tmp_path: Path, value: str
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    path = tmp_path / "candidates.csv"
    columns = tuple(c for c in DISCOVERY_CANDIDATE_COLUMNS if c != "is_provisional")
    _write_csv(
        path, (*columns, "is_provisional"), [_candidate_row(is_provisional=value)]
    )
    with pytest.raises(ValueError, match="is_provisional must be TRUE or FALSE"):
        read_discovery_candidates_csv(path)


def test_candidate_csv_requires_current_provisional_column(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    path = tmp_path / "candidates.csv"
    columns = tuple(c for c in DISCOVERY_CANDIDATE_COLUMNS if c != "is_provisional")
    row = _candidate_row(ms1_trace_quality="provisional")
    row.pop("is_provisional", None)
    _write_csv(path, columns, [row])
    with pytest.raises(ValueError, match="missing required columns: is_provisional"):
        read_discovery_candidates_csv(path)


def test_read_discovery_batch_index_preserves_order_and_resolves_candidate_paths(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_batch_index

    index_path = tmp_path / "batch" / "discovery_batch_index.csv"
    index_path.parent.mkdir()
    _write_csv(
        index_path,
        ("sample_stem", "raw_file", "candidate_csv", "review_csv"),
        [
            {
                "sample_stem": "Sample_B",
                "raw_file": "C:/stale/Sample_B.raw",
                "candidate_csv": "Sample_B/discovery_candidates.csv",
                "review_csv": "Sample_B/discovery_review.csv",
            },
            {
                "sample_stem": "Sample_A",
                "raw_file": "C:/stale/Sample_A.raw",
                "candidate_csv": str(tmp_path / "abs" / "candidates.csv"),
                "review_csv": "",
            },
        ],
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("Sample_B", "Sample_A")
    assert batch.candidate_csvs["Sample_B"] == (
        index_path.parent / "Sample_B" / "discovery_candidates.csv"
    )
    assert batch.candidate_csvs["Sample_A"] == tmp_path / "abs" / "candidates.csv"
    assert batch.raw_files["Sample_B"] == Path("C:/stale/Sample_B.raw")
    assert batch.raw_files["Sample_A"] == Path("C:/stale/Sample_A.raw")
    assert batch.review_csvs["Sample_B"] == (
        index_path.parent / "Sample_B" / "discovery_review.csv"
    )
    assert batch.review_csvs["Sample_A"] is None


def test_read_discovery_batch_index_accepts_utf8_bom(tmp_path: Path) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_batch_index

    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "\ufeffsample_stem,raw_file,candidate_csv,review_csv\n"
        "Sample_A,Sample_A.raw,Sample_A/candidates.csv,Sample_A/review.csv\n",
        encoding="utf-8",
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("Sample_A",)
    assert batch.raw_files["Sample_A"] == Path("Sample_A.raw")
    assert batch.candidate_csvs["Sample_A"] == (
        tmp_path / "Sample_A" / "candidates.csv"
    )


def test_read_discovery_batch_index_unescapes_known_formula_fields(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_batch_index

    index_path = tmp_path / "discovery_batch_index.csv"
    _write_csv(
        index_path,
        ("sample_stem", "raw_file", "candidate_csv", "review_csv"),
        [
            {
                "sample_stem": "'=Sample",
                "raw_file": "'+Sample.raw",
                "candidate_csv": "'-candidates.csv",
                "review_csv": "'@review.csv",
            },
            {
                "sample_stem": "'Ordinary",
                "raw_file": "'plain.raw",
                "candidate_csv": "'plain.csv",
                "review_csv": "'plain_review.csv",
            },
        ],
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("=Sample", "'Ordinary")
    assert batch.raw_files["=Sample"] == Path("+Sample.raw")
    assert batch.candidate_csvs["=Sample"] == tmp_path / "-candidates.csv"
    assert batch.review_csvs["=Sample"] == tmp_path / "@review.csv"
    assert batch.raw_files["'Ordinary"] == Path("'plain.raw")


def test_read_discovery_candidates_csv_parses_full_candidate_row(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [_candidate_row()])

    (candidate,) = read_discovery_candidates_csv(csv_path)

    assert candidate.review_priority == "HIGH"
    assert candidate.evidence_score == 82
    assert candidate.candidate_id == "Sample_1#6095@mz500.123456_p384.076056"
    assert candidate.feature_family_id == "Sample_1@F0001"
    assert candidate.precursor_mz == pytest.approx(500.123456)
    assert candidate.product_mz == pytest.approx(384.076056)
    assert candidate.observed_neutral_loss_da == pytest.approx(116.0474)
    assert candidate.best_seed_rt == pytest.approx(8.49)
    assert candidate.seed_event_count == 3
    assert candidate.ms1_peak_found is True
    assert candidate.discovery_candidate_state == "ms1_feature_nl_supported"
    assert candidate.ms1_feature_row_id == "Sample_1|DNA_dR|500.123456|8.48"
    assert candidate.ms1_apex_rt == pytest.approx(8.48)
    assert candidate.ms1_area == pytest.approx(123456.7)
    assert candidate.ms2_product_max_intensity == pytest.approx(9876.5)
    assert candidate.raw_file == Path("C:/raw/Sample_1.raw")
    assert candidate.sample_stem == "Sample_1"
    assert candidate.best_ms2_scan_id == 6095
    assert candidate.seed_scan_ids == (6095, 6098, 6102)
    assert candidate.ms1_seed_delta_min is None
    assert candidate.ms1_scan_support_score == pytest.approx(0.8)
    assert candidate.neutral_loss_error_basis == "measured_scan_precursor_product"
    assert candidate.precursor_mz_basis == "scan_precursor"
    assert candidate.scan_precursor_mz == pytest.approx(500.123456)
    assert candidate.scan_precursor_delta_da == pytest.approx(0.0)
    assert candidate.max_scan_precursor_abs_delta_da == pytest.approx(0.0)
    assert candidate.matched_tag_names == ("DNA_dR",)
    assert candidate.matched_tag_count == 1
    assert candidate.tag_combine_mode == "single"


def test_read_discovery_candidates_csv_rejects_partial_successor_schema(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    fieldnames = tuple(
        column
        for column in DISCOVERY_CANDIDATE_COLUMNS
        if column != "ms1_feature_row_id"
    )
    row = {
        column: value
        for column, value in _candidate_row().items()
        if column in fieldnames
    }
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, fieldnames, [row])

    with pytest.raises(
        ValueError,
        match="missing required columns: ms1_feature_row_id",
    ):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_preserves_multi_tag_evidence(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    row = _candidate_row(
        selected_tag_count="3",
        matched_tag_count="2",
        matched_tag_names="dR;R",
        primary_tag_name="dR",
        tag_combine_mode="union",
        tag_intersection_status="incomplete",
        tag_evidence_json='{"dR":{"scan_count":1},"R":{"scan_count":1}}',
    )
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [row])

    candidates = read_discovery_candidates_csv(csv_path)

    assert candidates[0].matched_tag_names == ("dR", "R")
    assert candidates[0].matched_tag_count == 2
    assert candidates[0].tag_combine_mode == "union"
    assert "scan_count" in candidates[0].tag_evidence_json


def test_read_discovery_candidates_accepts_writer_rounded_row_identity_mz(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    row = _candidate_row(
        candidate_id="Sample_1#6095@mz1000.377783_p884.330383",
        precursor_mz="1000.38",
        product_mz="884.33",
        observed_neutral_loss_da="116.047",
        scan_precursor_mz="1000.38",
        ms1_feature_row_id="Sample_1|DNA_dR|1000.377783|8.48",
    )
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [row])

    (candidate,) = read_discovery_candidates_csv(csv_path)

    assert candidate.candidate_id == "Sample_1#6095@mz1000.377783_p884.330383"
    assert candidate.precursor_mz == pytest.approx(1000.38)
    assert candidate.product_mz == pytest.approx(884.33)


def test_discovery_writer_to_alignment_reader_roundtrips_rounded_row_identity(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv
    from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
    from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeed

    raw_file = Path("C:/raw/Sample_1.raw")
    seed = DiscoverySeed(
        raw_file=raw_file,
        sample_stem="Sample_1",
        scan_number=6095,
        rt=23.4,
        precursor_mz=1000.377783,
        product_mz=884.330383,
        product_intensity=12000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0474,
        observed_loss_error_ppm=0.0,
    )
    candidate = DiscoveryCandidate.from_values(
        raw_file=raw_file,
        sample_stem="Sample_1",
        precursor_mz=1000.377783,
        product_mz=884.330383,
        observed_neutral_loss_da=116.0474,
        best_seed=seed,
        seed_scan_ids=(6095,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=0.0,
        neutral_loss_error_basis="configured_loss_inferred_precursor",
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=1000.377783,
        scan_precursor_delta_da=0.0,
        max_scan_precursor_abs_delta_da=0.0,
        rt_seed_min=23.35,
        rt_seed_max=23.45,
        ms1_search_rt_min=23.15,
        ms1_search_rt_max=23.65,
        ms1_seed_delta_min=0.0,
        ms1_peak_rt_start=23.25,
        ms1_peak_rt_end=23.55,
        ms1_height=4500.0,
        ms1_trace_quality="GOOD",
        seed_event_count=1,
        ms1_peak_found=True,
        ms1_apex_rt=23.41,
        ms1_area=88765.4,
        ms2_product_max_intensity=12000.0,
        review_priority="HIGH",
        reason="neutral loss seed",
    )
    csv_path = tmp_path / "discovery_candidates.csv"

    write_discovery_candidates_csv(csv_path, [candidate])
    written_row = _read_csv(csv_path)[0]
    (parsed,) = read_discovery_candidates_csv(csv_path)

    assert written_row["candidate_id"] == "Sample_1#6095@mz1000.377783_p884.330383"
    assert written_row["discovery_candidate_state"] == "ms1_feature_nl_rescued"
    assert written_row["ms1_feature_row_id"] == "Sample_1|DNA_dR|1000.377783|23.41"
    assert written_row["precursor_mz"] == "1000.38"
    assert written_row["product_mz"] == "884.33"
    assert parsed.candidate_id == "Sample_1#6095@mz1000.377783_p884.330383"
    assert parsed.discovery_candidate_state == "ms1_feature_nl_rescued"
    assert parsed.ms1_feature_row_id == "Sample_1|DNA_dR|1000.377783|23.41"
    assert parsed.configured_neutral_loss_da == candidate.configured_neutral_loss_da


def test_read_discovery_candidates_rejects_mz_just_outside_display_tolerance(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    row = _candidate_row(
        candidate_id="Sample_1#6095@mz1000.386_p884.330383",
        precursor_mz="1000.38",
        product_mz="884.33",
        observed_neutral_loss_da="116.047",
        scan_precursor_mz="1000.38",
    )
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [row])

    with pytest.raises(
        ValueError,
        match=r"precursor_mz does not match.*tolerance_da=0.005",
    ):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_unescapes_known_formula_fields(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    row = _candidate_row(
        candidate_id="'=Sample#6095@mz500.123456_p384.076056",
        sample_stem="'=Sample",
        raw_file="'-raw.raw",
        feature_family_id="'@family",
        ms1_feature_row_id="'=Sample|DNA_dR|500.123456|8.48",
    )
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [row])

    (candidate,) = read_discovery_candidates_csv(csv_path)

    assert candidate.candidate_id == "=Sample#6095@mz500.123456_p384.076056"
    assert candidate.sample_stem == "=Sample"
    assert candidate.raw_file == Path("-raw.raw")
    assert candidate.feature_family_id == "@family"


def test_readers_reject_missing_required_columns(tmp_path: Path) -> None:
    from xic_extractor.alignment.csv_io import (
        read_discovery_batch_index,
        read_discovery_candidates_csv,
    )

    batch_path = tmp_path / "discovery_batch_index.csv"
    _write_csv(batch_path, ("sample_stem", "raw_file"), [])
    candidates_path = tmp_path / "discovery_candidates.csv"
    _write_csv(candidates_path, tuple(
        column for column in DISCOVERY_CANDIDATE_COLUMNS
        if column != "tag_evidence_json"
    ), [])

    with pytest.raises(ValueError, match="missing required columns.*candidate_csv"):
        read_discovery_batch_index(batch_path)
    with pytest.raises(ValueError, match="missing required columns"):
        read_discovery_candidates_csv(candidates_path)


def test_read_discovery_candidates_csv_rejects_stale_scan_only_candidate_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [_candidate_row(candidate_id="Sample_1#6095")],
    )

    with pytest.raises(ValueError, match="candidate_id must include row identity"):
        read_discovery_candidates_csv(csv_path)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sample_stem": "OtherSample"}, "sample stem does not match"),
        ({"best_ms2_scan_id": "6096"}, "scan id does not match"),
        ({"precursor_mz": "501.123456"}, "precursor_mz does not match"),
        ({"product_mz": "385.076056"}, "product_mz does not match"),
    ],
)
def test_read_discovery_candidates_csv_rejects_candidate_id_row_mismatch(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [_candidate_row(**overrides)])

    with pytest.raises(ValueError, match=message):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_rejects_duplicate_candidate_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(),
            _candidate_row(feature_family_id="Sample_1@F0002"),
        ],
    )

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_rejects_invalid_candidate_state(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [_candidate_row(discovery_candidate_state="accepted")],
    )

    with pytest.raises(ValueError, match="row 2.*discovery_candidate_state"):
        read_discovery_candidates_csv(csv_path)


@pytest.mark.parametrize(
    "state",
    [
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
    ],
)
def test_read_discovery_candidates_csv_rejects_blank_feature_row_id_for_ms1_rows(
    tmp_path: Path,
    state: str,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(
                discovery_candidate_state=state,
                ms1_feature_row_id="",
            )
        ],
    )

    with pytest.raises(ValueError, match="row 2.*ms1_feature_row_id"):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_rejects_ms1_state_without_ms1_peak(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(
                discovery_candidate_state="ms1_feature_nl_supported",
                ms1_peak_found="FALSE",
            )
        ],
    )

    with pytest.raises(ValueError, match="row 2.*ms1_peak_found"):
        read_discovery_candidates_csv(csv_path)


@pytest.mark.parametrize(
    "ms1_feature_row_id",
    [
        "Sample_1|DNA_dR|500.123456",
        "OtherSample|DNA_dR|500.123456|8.48",
        "Sample_1|OtherTag|500.123456|8.48",
        "Sample_1|DNA_dR|999|8.48",
        "Sample_1|DNA_dR|500.123456|9.99",
    ],
)
def test_read_discovery_candidates_csv_rejects_mismatched_ms1_feature_row_id(
    tmp_path: Path,
    ms1_feature_row_id: str,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [_candidate_row(ms1_feature_row_id=ms1_feature_row_id)],
    )

    with pytest.raises(ValueError, match="row 2.*ms1_feature_row_id"):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_rejects_orphan_state_with_ms1_peak(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(
                discovery_candidate_state="review_only_orphan_nl",
                ms1_feature_row_id="",
            )
        ],
    )

    with pytest.raises(ValueError, match="row 2.*review_only_orphan_nl"):
        read_discovery_candidates_csv(csv_path)


def test_read_discovery_candidates_csv_accepts_orphan_without_feature_row_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(
                discovery_candidate_state="review_only_orphan_nl",
                ms1_feature_row_id="",
                ms1_peak_found="FALSE",
                ms1_apex_rt="",
                ms1_area="",
                ms1_peak_rt_start="",
                ms1_peak_rt_end="",
                ms1_height="",
                ms1_trace_quality="missing",
                ms1_scan_support_score="",
            )
        ],
    )

    (candidate,) = read_discovery_candidates_csv(csv_path)

    assert candidate.discovery_candidate_state == "review_only_orphan_nl"
    assert candidate.ms1_feature_row_id == ""
    assert candidate.ms1_peak_found is False


def test_read_discovery_candidates_rejects_duplicate_ms1_feature_row_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            _candidate_row(),
            _candidate_row(
                candidate_id="Sample_1#6096@mz500.123456_p384.076056",
                best_ms2_scan_id="6096",
                seed_scan_ids="6096",
                feature_family_id="Sample_1@F0002",
                discovery_candidate_state="ms1_feature_nl_rescued",
            ),
        ],
    )

    with pytest.raises(ValueError, match="duplicate ms1_feature_row_id"):
        read_discovery_candidates_csv(csv_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("precursor_mz_basis", "target_lookup"),
        ("neutral_loss_error_basis", "measured_truth"),
    ],
)
def test_read_discovery_candidates_csv_rejects_invalid_basis_enum(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(
        csv_path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [_candidate_row(**{column: value})],
    )

    with pytest.raises(ValueError, match=rf"row 2.*{column}"):
        read_discovery_candidates_csv(csv_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("evidence_score", "not-int"),
        ("precursor_mz", "not-float"),
        ("ms1_peak_found", "maybe"),
        ("seed_scan_ids", "1;two"),
    ],
)
def test_read_discovery_candidates_csv_rejects_malformed_typed_fields_with_row_number(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    from xic_extractor.alignment.csv_io import read_discovery_candidates_csv

    row = _candidate_row(**{column: value})
    csv_path = tmp_path / "discovery_candidates.csv"
    _write_csv(csv_path, DISCOVERY_CANDIDATE_COLUMNS, [row])

    with pytest.raises(ValueError, match=rf"row 2.*{column}"):
        read_discovery_candidates_csv(csv_path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _candidate_row(**overrides: str) -> dict[str, str]:
    row = {
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": "82",
        "ms2_support": "strong",
        "ms1_support": "clean",
        "rt_alignment": "aligned",
        "candidate_id": "Sample_1#6095@mz500.123456_p384.076056",
        "discovery_candidate_state": "ms1_feature_nl_supported",
        "ms1_feature_row_id": "Sample_1|DNA_dR|500.123456|8.48",
        "feature_family_id": "Sample_1@F0001",
        "feature_family_size": "2",
        "precursor_mz": "500.123456",
        "product_mz": "384.076056",
        "observed_neutral_loss_da": "116.0474",
        "best_seed_rt": "8.49",
        "seed_event_count": "3",
        "ms1_peak_found": "TRUE",
        "ms1_apex_rt": "8.48",
        "ms1_area": "123456.7",
        "ms2_product_max_intensity": "9876.5",
        "reason": "strict NL seed",
        "raw_file": "C:/raw/Sample_1.raw",
        "sample_stem": "Sample_1",
        "best_ms2_scan_id": "6095",
        "seed_scan_ids": "6095;6098;6102",
        "neutral_loss_tag": "DNA_dR",
        "configured_neutral_loss_da": "116.0474",
        "neutral_loss_mass_error_ppm": "2.5",
        "neutral_loss_error_basis": "measured_scan_precursor_product",
        "precursor_mz_basis": "scan_precursor",
        "scan_precursor_mz": "500.123456",
        "scan_precursor_delta_da": "0.0",
        "max_scan_precursor_abs_delta_da": "0.0",
        "rt_seed_min": "8.45",
        "rt_seed_max": "8.55",
        "ms1_search_rt_min": "8.25",
        "ms1_search_rt_max": "8.75",
        "ms1_seed_delta_min": "",
        "ms1_peak_rt_start": "8.40",
        "ms1_peak_rt_end": "8.60",
        "ms1_height": "2222.2",
        "ms1_trace_quality": "clean",
        "is_provisional": "FALSE",
        "ms1_scan_support_score": "0.8",
        "selected_tag_count": "1",
        "matched_tag_count": "1",
        "matched_tag_names": "DNA_dR",
        "primary_tag_name": "DNA_dR",
        "tag_combine_mode": "single",
        "tag_intersection_status": "not_required",
        "tag_evidence_json": '{"DNA_dR":{"scan_count":3}}',
    }
    row.update(overrides)
    return row
