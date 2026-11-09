import csv
from dataclasses import replace
from pathlib import Path

import pytest

from tests.alignment_pipeline_helpers import (
    _owner_closed_receipt_fields,
    physical_facts_fingerprint_beside,
)
from xic_extractor.discovery.batch_contract import DISCOVERY_BATCH_RECEIPT_COLUMNS
from xic_extractor.discovery.models import DISCOVERY_PEAK_COLUMNS


def test_read_discovery_batch_index_requires_owner_closure(tmp_path: Path) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text("sample_stem,raw_file,peak_csv\n", encoding="utf-8")

    with pytest.raises(ValueError, match="regenerate Discovery"):
        read_discovery_batch_index(index_path)


def test_old_discovery_index_requires_regeneration(tmp_path: Path) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    path = tmp_path / "discovery_batch_index.csv"
    path.write_text(
        "schema_version,sample_stem,raw_file,peak_csv,acquisition_content_sha256\n"
        "discovery_batch_index,S1,S1.raw,candidates.csv,unused\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="regenerate Discovery"):
        read_discovery_batch_index(path)


@pytest.mark.parametrize("provisional,seed_count", [(True, 1), (True, 2), (False, 2)])
def test_owner_bound_csv_preserves_final_provisional_decision(
    tmp_path: Path,
    provisional: bool,
    seed_count: int,
) -> None:
    from xic_extractor.discovery.batch_contract import (
        mint_discovery_peak_universe_receipt,
    )
    from xic_extractor.discovery.csv_reader import (
        read_discovery_peaks_csv,
        read_owner_bound_discovery_peaks_csv,
    )
    from xic_extractor.discovery.csv_writer import write_discovery_peaks_csv
    from xic_extractor.processing_identity import (
        build_local_acquisition_source_identity,
    )

    path = tmp_path / "candidates.csv"
    _write_csv(path, DISCOVERY_PEAK_COLUMNS, [_candidate_row()])
    (original,) = read_discovery_peaks_csv(path)
    candidate = replace(
        original,
        ms1_trace_quality="provisional",
        is_provisional=provisional,
        attached_event_count=seed_count,
    )
    write_discovery_peaks_csv(path, (candidate,))
    raw = tmp_path / "Sample_1.raw"
    raw.write_bytes(b"synthetic acquisition")
    source = build_local_acquisition_source_identity(
        raw,
        acquisition_format="thermo_raw",
        observation_namespace="test",
    )
    receipt = mint_discovery_peak_universe_receipt(
        sample_stem="Sample_1",
        acquisition_source=source,
        peak_csv=path,
        physical_facts=physical_facts_fingerprint_beside(path),
        peaks=(candidate,),
    )
    (restored,) = read_owner_bound_discovery_peaks_csv(path, receipt=receipt)
    assert restored.is_provisional is provisional
    assert restored.attached_event_count == seed_count
    assert restored.ms1_trace_quality == "provisional"


@pytest.mark.parametrize("value", ["", "true", "1", "unknown"])
def test_candidate_csv_rejects_invalid_provisional_flag(
    tmp_path: Path, value: str
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    path = tmp_path / "candidates.csv"
    columns = tuple(c for c in DISCOVERY_PEAK_COLUMNS if c != "is_provisional")
    _write_csv(
        path, (*columns, "is_provisional"), [_candidate_row(is_provisional=value)]
    )
    with pytest.raises(ValueError, match="is_provisional must be TRUE or FALSE"):
        read_discovery_peaks_csv(path)


def test_candidate_csv_requires_current_provisional_column(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    path = tmp_path / "candidates.csv"
    columns = tuple(c for c in DISCOVERY_PEAK_COLUMNS if c != "is_provisional")
    row = _candidate_row(ms1_trace_quality="provisional")
    row.pop("is_provisional", None)
    _write_csv(path, columns, [row])
    with pytest.raises(ValueError, match="missing required columns: is_provisional"):
        read_discovery_peaks_csv(path)


def test_read_discovery_batch_index_preserves_order_and_resolves_candidate_paths(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    index_path = tmp_path / "batch" / "discovery_batch_index.csv"
    index_path.parent.mkdir()
    candidate_paths = (
        index_path.parent / "Sample_B" / "discovery_peaks.csv",
        tmp_path / "abs" / "candidates.csv",
    )
    for candidate_path in candidate_paths:
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(candidate_path, DISCOVERY_PEAK_COLUMNS, [])
    _write_csv(
        index_path,
        (
            "schema_version",
            "sample_stem",
            "raw_file",
            "peak_csv",
            "event_csv",
            "peak_count",
            *DISCOVERY_BATCH_RECEIPT_COLUMNS,
        ),
        [
            {
                **_owner_closed_receipt_fields("Sample_B", candidate_paths[0]),
                "sample_stem": "Sample_B",
                "raw_file": "C:/stale/Sample_B.raw",
                "peak_csv": "Sample_B/discovery_peaks.csv",
                "event_csv": "Sample_B/discovery_events.csv",
            },
            {
                **_owner_closed_receipt_fields("Sample_A", candidate_paths[1]),
                "sample_stem": "Sample_A",
                "raw_file": "C:/stale/Sample_A.raw",
                "peak_csv": str(tmp_path / "abs" / "candidates.csv"),
                "event_csv": "",
            },
        ],
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("Sample_B", "Sample_A")
    assert batch.peak_csvs["Sample_B"] == (
        index_path.parent / "Sample_B" / "discovery_peaks.csv"
    )
    assert batch.peak_csvs["Sample_A"] == tmp_path / "abs" / "candidates.csv"
    assert batch.raw_files["Sample_B"] == Path("C:/stale/Sample_B.raw")
    assert batch.raw_files["Sample_A"] == Path("C:/stale/Sample_A.raw")
    assert batch.event_csvs["Sample_B"] == (
        index_path.parent / "Sample_B" / "discovery_events.csv"
    )
    assert batch.event_csvs["Sample_A"] is None


def test_read_discovery_batch_index_accepts_utf8_bom(tmp_path: Path) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    index_path = tmp_path / "discovery_batch_index.csv"
    candidate_path = tmp_path / "Sample_A" / "candidates.csv"
    candidate_path.parent.mkdir()
    _write_csv(candidate_path, DISCOVERY_PEAK_COLUMNS, [])
    _write_csv(
        index_path,
        (
            "schema_version",
            "sample_stem",
            "raw_file",
            "peak_csv",
            "event_csv",
            "peak_count",
            *DISCOVERY_BATCH_RECEIPT_COLUMNS,
        ),
        [
            {
                "sample_stem": "Sample_A",
                "raw_file": "Sample_A.raw",
                "peak_csv": "Sample_A/candidates.csv",
                "event_csv": "Sample_A/review.csv",
                **_owner_closed_receipt_fields("Sample_A", candidate_path),
            }
        ],
    )
    index_path.write_text(
        "\ufeff" + index_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("Sample_A",)
    assert batch.raw_files["Sample_A"] == Path("Sample_A.raw")
    assert batch.peak_csvs["Sample_A"] == (tmp_path / "Sample_A" / "candidates.csv")


def test_read_discovery_batch_index_unescapes_known_formula_fields(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    index_path = tmp_path / "discovery_batch_index.csv"
    candidate_paths = (tmp_path / "-candidates.csv", tmp_path / "'plain.csv")
    for candidate_path in candidate_paths:
        _write_csv(candidate_path, DISCOVERY_PEAK_COLUMNS, [])
    _write_csv(
        index_path,
        (
            "schema_version",
            "sample_stem",
            "raw_file",
            "peak_csv",
            "event_csv",
            "peak_count",
            *DISCOVERY_BATCH_RECEIPT_COLUMNS,
        ),
        [
            {
                **_owner_closed_receipt_fields("=Sample", candidate_paths[0]),
                "sample_stem": "'=Sample",
                "raw_file": "'+Sample.raw",
                "peak_csv": "'-candidates.csv",
                "event_csv": "'@review.csv",
            },
            {
                **_owner_closed_receipt_fields("'Ordinary", candidate_paths[1]),
                "sample_stem": "'Ordinary",
                "raw_file": "'plain.raw",
                "peak_csv": "'plain.csv",
                "event_csv": "'plain_review.csv",
            },
        ],
    )

    batch = read_discovery_batch_index(index_path)

    assert batch.sample_order == ("=Sample", "'Ordinary")
    assert batch.raw_files["=Sample"] == Path("+Sample.raw")
    assert batch.peak_csvs["=Sample"] == tmp_path / "-candidates.csv"
    assert batch.event_csvs["=Sample"] == tmp_path / "@review.csv"
    assert batch.raw_files["'Ordinary"] == Path("'plain.raw")


def test_read_discovery_candidates_csv_parses_full_candidate_row(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(csv_path, DISCOVERY_PEAK_COLUMNS, [_candidate_row()])

    (candidate,) = read_discovery_peaks_csv(csv_path)

    assert candidate.review_priority == "HIGH"
    assert candidate.evidence_score == 82
    assert candidate.peak_id == "Sample_1@mz500.123456_rt8.4800"
    assert candidate.xic_mz == pytest.approx(500.123456)
    assert candidate.admitted
    assert candidate.best_event_precursor_mz == pytest.approx(500.123456)
    assert candidate.best_event_product_mz == pytest.approx(384.076056)
    assert candidate.best_event_neutral_loss_da == pytest.approx(116.0474)
    assert candidate.best_event_rt == pytest.approx(8.49)
    assert candidate.attached_event_count == 3
    assert candidate.ms1_apex_rt == pytest.approx(8.48)
    assert candidate.ms1_area == pytest.approx(123456.7)
    assert candidate.ms2_product_max_intensity == pytest.approx(9876.5)
    assert candidate.raw_file == Path("C:/raw/Sample_1.raw")
    assert candidate.sample_stem == "Sample_1"
    assert candidate.best_ms2_scan_id == 6095
    assert candidate.event_scan_ids == (6095, 6098, 6102)
    assert candidate.ms1_event_delta_min == pytest.approx(-0.01)
    assert candidate.ms1_scan_support_score == pytest.approx(0.8)
    assert candidate.neutral_loss_error_basis == "measured_scan_precursor_product"
    assert candidate.precursor_mz_basis == "scan_precursor"
    assert candidate.scan_precursor_mz == pytest.approx(500.123456)
    assert candidate.scan_precursor_delta_da == pytest.approx(0.0)
    assert candidate.max_scan_precursor_abs_delta_da == pytest.approx(0.0)
    assert candidate.matched_tag_names == ("DNA_dR",)
    assert candidate.matched_tag_count == 1
    assert candidate.tag_combine_mode == "single"


def test_read_discovery_candidates_preserves_multi_tag_evidence(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    row = _candidate_row(
        selected_tag_count="3",
        matched_tag_count="2",
        matched_tag_names="dR;R",
        primary_tag_name="dR",
        tag_combine_mode="union",
        tag_intersection_status="incomplete",
        tag_evidence_json='{"dR":{"scan_count":1},"R":{"scan_count":1}}',
    )
    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(csv_path, DISCOVERY_PEAK_COLUMNS, [row])

    candidates = read_discovery_peaks_csv(csv_path)

    assert candidates[0].matched_tag_names == ("dR", "R")
    assert candidates[0].matched_tag_count == 2
    assert candidates[0].tag_combine_mode == "union"
    assert "scan_count" in candidates[0].tag_evidence_json


def test_read_discovery_candidates_csv_unescapes_known_formula_fields(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    row = _candidate_row(
        peak_id="'=Sample@mz500.123456_rt8.4800",
        sample_stem="'=Sample",
        raw_file="'-raw.raw",
    )
    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(csv_path, DISCOVERY_PEAK_COLUMNS, [row])

    (candidate,) = read_discovery_peaks_csv(csv_path)

    assert candidate.peak_id == "=Sample@mz500.123456_rt8.4800"
    assert candidate.sample_stem == "=Sample"
    assert candidate.raw_file == Path("-raw.raw")


def test_readers_reject_missing_required_columns(tmp_path: Path) -> None:
    from xic_extractor.discovery.csv_reader import (
        read_discovery_batch_index,
        read_discovery_peaks_csv,
    )

    batch_path = tmp_path / "discovery_batch_index.csv"
    _write_csv(batch_path, ("sample_stem", "raw_file"), [])
    candidates_path = tmp_path / "discovery_peaks.csv"
    _write_csv(
        candidates_path,
        tuple(
            column for column in DISCOVERY_PEAK_COLUMNS if column != "tag_evidence_json"
        ),
        [],
    )

    with pytest.raises(ValueError, match="regenerate Discovery"):
        read_discovery_batch_index(batch_path)
    with pytest.raises(ValueError, match="missing required columns"):
        read_discovery_peaks_csv(candidates_path)


def test_read_discovery_peaks_csv_rejects_duplicate_peak_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(
        csv_path,
        DISCOVERY_PEAK_COLUMNS,
        [
            _candidate_row(),
            _candidate_row(evidence_score="40"),
        ],
    )

    with pytest.raises(ValueError, match="duplicate peak_id"):
        read_discovery_peaks_csv(csv_path)


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
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(
        csv_path,
        DISCOVERY_PEAK_COLUMNS,
        [_candidate_row(**{column: value})],
    )

    with pytest.raises(ValueError, match=rf"row 2.*{column}"):
        read_discovery_peaks_csv(csv_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("evidence_score", "not-int"),
        ("best_event_precursor_mz", "not-float"),
        ("status", "maybe"),
        ("event_scan_ids", "1;two"),
    ],
)
def test_read_discovery_candidates_csv_rejects_malformed_typed_fields_with_row_number(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

    row = _candidate_row(**{column: value})
    csv_path = tmp_path / "discovery_peaks.csv"
    _write_csv(csv_path, DISCOVERY_PEAK_COLUMNS, [row])

    with pytest.raises(ValueError, match=rf"row 2.*{column}"):
        read_discovery_peaks_csv(csv_path)


def test_discovery_input_source_defers_typed_rows_and_loads_selected_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.discovery import input_source as source_owner

    rows = [
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6095,
            state="admitted",
            reason="多行證據\n第二行",
        ),
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6096,
            state="no_nl_event",
        ),
    ]
    batch = _discovery_input_batch(tmp_path, {"Sample_1": rows})
    parsed_rows: list[str] = []
    real_parse = source_owner._parse_peak_row

    def observe_parse(path: Path, row_number: int, row: dict[str, str]):
        parsed_rows.append(row["peak_id"])
        return real_parse(path, row_number, row)

    monkeypatch.setattr(source_owner, "_parse_peak_row", observe_parse)

    source = source_owner.DiscoveryInputSource(batch)

    assert parsed_rows == []
    (feature,) = source.feature_view()["Sample_1"]
    assert feature.peak_id == rows[0]["peak_id"]
    assert feature.reason == "多行證據\n第二行"
    assert parsed_rows == [rows[0]["peak_id"]]
    loaded = source.load_candidates((rows[1]["peak_id"],))
    assert [item.peak_id for item in loaded["Sample_1"]] == [rows[1]["peak_id"]]
    assert parsed_rows == [rows[0]["peak_id"], rows[1]["peak_id"]]


def test_discovery_input_source_keeps_unadmitted_peaks_out_of_features(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.input_source import DiscoveryInputSource

    sample_1_rows = [
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6095,
            state="admitted",
        ),
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6096,
            state="no_nl_event",
            basis="scan_precursor",
        ),
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6097,
            state="no_nl_event",
            basis="mixed",
            mz=500.223456,
        ),
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_1",
            scan=6098,
            state="no_nl_event",
            basis="product_plus_neutral_loss",
        ),
    ]
    sample_2_rows = [
        _indexed_candidate_row(
            tmp_path,
            sample="Sample_2",
            scan=7096,
            state="no_nl_event",
            basis="scan_precursor",
        )
    ]
    source = DiscoveryInputSource(
        _discovery_input_batch(
            tmp_path,
            {"Sample_1": sample_1_rows, "Sample_2": sample_2_rows},
        )
    )

    assert source.feature_candidate_ids() == (sample_1_rows[0]["peak_id"],)
    assert {c.peak_id for c in source["Sample_1"]} == {
        row["peak_id"] for row in sample_1_rows
    }
    assert len(source["Sample_2"]) == 1


def test_discovery_input_source_rejects_changed_csv(tmp_path: Path) -> None:
    from xic_extractor.discovery.input_source import DiscoveryInputSource

    row = _indexed_candidate_row(
        tmp_path,
        sample="Sample_1",
        scan=6095,
        state="admitted",
    )
    batch = _discovery_input_batch(tmp_path, {"Sample_1": [row]})
    source = DiscoveryInputSource(batch)

    with batch.peak_csvs["Sample_1"].open("a", encoding="utf-8") as handle:
        handle.write(" ")

    with pytest.raises(ValueError, match="changed after indexing"):
        source.load_candidates((row["peak_id"],))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _discovery_input_batch(
    root: Path,
    rows_by_sample: dict[str, list[dict[str, str]]],
):
    from xic_extractor.discovery.batch_contract import (
        mint_discovery_peak_universe_receipt,
    )
    from xic_extractor.discovery.csv_reader import (
        DiscoveryBatchInput,
        read_discovery_peaks_csv,
    )
    from xic_extractor.processing_identity import (
        build_local_acquisition_source_identity,
    )

    candidate_csvs = {}
    raw_files = {}
    receipts = {}
    for stem, rows in rows_by_sample.items():
        path = root / stem / "discovery_peaks.csv"
        _write_csv(path, DISCOVERY_PEAK_COLUMNS, rows)
        raw_path = root / f"{stem}.raw"
        raw_path.write_bytes(stem.encode())
        candidates = read_discovery_peaks_csv(path)
        source = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format="thermo_raw",
            observation_namespace="test",
        )
        candidate_csvs[stem] = path
        raw_files[stem] = raw_path
        receipts[stem] = mint_discovery_peak_universe_receipt(
            sample_stem=stem,
            acquisition_source=source,
            peak_csv=path,
            physical_facts=physical_facts_fingerprint_beside(path),
            peaks=candidates,
        )
    order = tuple(rows_by_sample)
    return DiscoveryBatchInput(
        sample_order=order,
        peak_csvs=candidate_csvs,
        raw_files=raw_files,
        event_csvs={stem: None for stem in order},
        peak_receipts=receipts,
    )


def _indexed_candidate_row(
    root: Path,
    *,
    sample: str,
    scan: int,
    state: str,
    basis: str = "scan_precursor",
    mz: float = 500.123456,
    reason: str = "strict NL seed",
) -> dict[str, str]:
    product_mz = mz - 116.0474
    admitted = state == "admitted"
    return _candidate_row(
        peak_id=f"{sample}@mz{mz:.6f}_rt{scan}",
        peak_mz=f"{mz:.6f}",
        xic_mz=f"{mz:.6f}",
        best_event_precursor_mz=f"{mz:.6f}",
        best_event_product_mz=f"{product_mz:.6f}",
        scan_precursor_mz=f"{mz:.6f}",
        raw_file=str(root / f"{sample}.raw"),
        sample_stem=sample,
        best_ms2_scan_id=str(scan),
        event_scan_ids=str(scan),
        precursor_mz_basis=basis,
        reason=reason,
        status=state,
        attached_event_count="1" if admitted else "0",
    )


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
        "status": "admitted",
        "evidence_tier": "A",
        "evidence_score": "82",
        "ms2_support": "strong",
        "ms1_support": "clean",
        "rt_alignment": "aligned",
        "peak_id": "Sample_1@mz500.123456_rt8.4800",
        "peak_mz": "500.123456",
        "xic_mz": "500.123456",
        "best_event_precursor_mz": "500.123456",
        "best_event_product_mz": "384.076056",
        "best_event_neutral_loss_da": "116.0474",
        "ms1_apex_rt": "8.48",
        "ms1_area": "123456.7",
        "attached_event_count": "3",
        "best_event_rt": "8.49",
        "ms2_product_max_intensity": "9876.5",
        "reason": "strict NL seed",
        "raw_file": "C:/raw/Sample_1.raw",
        "sample_stem": "Sample_1",
        "best_ms2_scan_id": "6095",
        "event_scan_ids": "6095;6098;6102",
        "neutral_loss_tag": "DNA_dR",
        "configured_neutral_loss_da": "116.0474",
        "neutral_loss_mass_error_ppm": "2.5",
        "neutral_loss_error_basis": "measured_scan_precursor_product",
        "precursor_mz_basis": "scan_precursor",
        "scan_precursor_mz": "500.123456",
        "scan_precursor_delta_da": "0.0",
        "max_scan_precursor_abs_delta_da": "0.0",
        "event_rt_min": "8.45",
        "event_rt_max": "8.55",
        "ms1_search_rt_min": "8.25",
        "ms1_search_rt_max": "8.75",
        "ms1_event_delta_min": "-0.01",
        "ms1_peak_rt_start": "8.40",
        "ms1_peak_rt_end": "8.60",
        "ms1_height": "2222.2",
        "ms1_apex_mz": "",
        "ms1_trace_quality": "clean",
        "ms1_scan_support_score": "0.8",
        "selected_tag_count": "1",
        "matched_tag_count": "1",
        "matched_tag_names": "DNA_dR",
        "primary_tag_name": "DNA_dR",
        "tag_combine_mode": "single",
        "tag_intersection_status": "not_required",
        "tag_evidence_json": '{"DNA_dR":{"scan_count":3}}',
        "is_provisional": "FALSE",
    }
    row.update(overrides)
    return row
