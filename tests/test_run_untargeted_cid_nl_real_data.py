from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from scripts.run_untargeted_cid_nl_real_data import main
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.pipeline import _BATCH_INDEX_COLUMNS
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
)
from xic_extractor.tabular_io import write_delimited_rows


def test_runner_writes_replay_packet_and_qmv_for_one_support_canary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    raw_path = raw_dir / "S1.synthetic"
    raw_path.write_bytes(b"real-data-runtime-runner:S1")
    source_raw = _FakeRaw((7.98, 8.02))
    candidates = _current_candidates(
        source_raw,
        raw_file=raw_path,
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
    )
    candidate_path = tmp_path / "S1.owner-candidates.csv"
    receipt = _discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": candidates},
    )["S1"]
    review_path = tmp_path / "S1.discovery_review.csv"
    review_path.write_text("", encoding="utf-8")
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_owner_batch_index(
        batch_index,
        raw_path=raw_path,
        candidate_path=candidate_path,
        review_path=review_path,
        receipt=receipt,
    )
    metadata_path = tmp_path / "sample_metadata.tsv"
    write_delimited_rows(
        metadata_path,
        (
            {
                "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
                "sample_name": "S1",
                "raw_stem": "S1",
                "injection_order": "1",
                "sample_role": "study_sample",
                "batch_id": "",
                "prep_batch_id": "",
                "matrix_type": "",
                "group": "",
                "excluded": "false",
                "exclusion_reason": "",
            },
        ),
        SAMPLE_METADATA_COLUMNS,
        delimiter="\t",
        encoding="utf-8",
        extrasaction="raise",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path={raw_path: source_raw},
        tmp_path=dll_dir,
    )
    output_dir = tmp_path / "real-data-runtime-output"

    argv = (
        "--discovery-batch-index",
        str(batch_index),
        "--raw-dir",
        str(raw_dir),
        "--sample-metadata",
        str(metadata_path),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(output_dir),
        "--manifest-root",
        str(tmp_path),
        "--expected-sample-count",
        "1",
        "--precursor-mz",
        "500.0",
        "--rt-min",
        "7.5",
        "--rt-max",
        "8.6",
        "--ms2-precursor-tol-da",
        "0.5",
    )
    exit_code = main(argv)

    assert exit_code == 0
    summary = json.loads(
        (output_dir / "real_data_runtime_summary.json").read_text()
    )
    timing = json.loads((output_dir / "timing.json").read_text())
    assert summary["schema_version"] == "untargeted_cid_nl_real_data_runtime_summary_v2"
    assert timing["schema_version"] == "untargeted_cid_nl_real_data_runtime_timing_v2"
    assert summary["runtime_succeeded"] is True
    assert summary["terminal_state"] == "quant_matrix_version_built"
    assert summary["sample_count"] == 1
    assert summary["pending_acceptance_count"] == 0
    assert summary["samples"][0]["technical_state"] == "evaluated"
    assert summary["samples"][0]["method_result"] == "SUPPORT"
    assert summary["samples"][0]["strict_scan_count"] == 2
    assert summary["samples"][0]["discovery_receipt_id"] == (
        receipt.discovery_receipt_id
    )
    assert summary["call_cost"] == {
        "raw_open_count": 1,
        "logical_xic_request_count": 1,
        "xic_batch_count": 1,
        "adapter_chromatogram_call_count": 1,
        "trace_point_count": summary["samples"][0]["trace_point_count"],
    }
    assert timing["call_cost"] == summary["call_cost"]
    assert (output_dir / "untargeted_cid_nl_source.json").is_file()
    assert (output_dir / "quant_matrix_version.csv").is_file()
    assert (output_dir / "cell_provenance.tsv").is_file()
    assert (output_dir / "runtime.json").is_file()
    assert opened[raw_path][0].chromatogram_request_counts == [1]
    assert opened[raw_path][0].disposed is True

    write_discovery_candidates_csv(candidate_path, ())
    truncated_output_dir = tmp_path / "real-data-runtime-truncated-output"
    truncated_argv = list(argv)
    truncated_argv[truncated_argv.index("--output-dir") + 1] = str(truncated_output_dir)

    truncated_exit_code = main(truncated_argv)

    assert truncated_exit_code == 2
    assert len(opened[raw_path]) == 1

    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5)
        for scan in source_raw.scans
    )
    source_raw.intensity.fill(0.0)
    unsupported_candidates = _current_candidates(
        source_raw,
        raw_file=raw_path,
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
    )
    assert unsupported_candidates
    assert all(
        candidate.precursor_mz_basis == "product_plus_neutral_loss"
        and not candidate.ms1_peak_found
        for candidate in unsupported_candidates
    )
    unsupported_receipt = _discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": unsupported_candidates},
    )["S1"]
    _write_owner_batch_index(
        batch_index,
        raw_path=raw_path,
        candidate_path=candidate_path,
        review_path=review_path,
        receipt=unsupported_receipt,
    )
    unsupported_output_dir = tmp_path / "real-data-runtime-unsupported-output"
    unsupported_argv = list(argv)
    unsupported_argv[unsupported_argv.index("--output-dir") + 1] = str(
        unsupported_output_dir
    )

    unsupported_exit_code = main(unsupported_argv)

    assert unsupported_exit_code == 0
    unsupported_summary = json.loads(
        (unsupported_output_dir / "real_data_runtime_summary.json").read_text()
    )
    assert unsupported_summary["runtime_succeeded"] is True
    assert (
        unsupported_summary["terminal_state"]
        == "assessments_complete_no_direct_support"
    )
    assert unsupported_summary["quant_matrix_version_built"] is False
    assert unsupported_summary["source_artifact_relpath"] is None
    assert unsupported_summary["source_artifact_sha256"] is None
    assert unsupported_summary["pending_acceptance_count"] == 0
    assert unsupported_summary["pending_acceptance_manifest"] is None
    assert unsupported_summary["samples"][0]["technical_state"] == "not_evaluated"
    assert unsupported_summary["samples"][0]["method_result"] is None
    assert unsupported_summary["samples"][0]["backfill_disposition"] is None
    assert unsupported_summary["samples"][0]["source_candidate_count"] == 1
    assert unsupported_summary["samples"][0]["strict_scan_count"] == 0
    assert unsupported_summary["call_cost"] == {
        "raw_open_count": 1,
        "logical_xic_request_count": 0,
        "xic_batch_count": 0,
        "adapter_chromatogram_call_count": 0,
        "trace_point_count": unsupported_summary["samples"][0]["trace_point_count"],
    }
    assert len(opened[raw_path]) == 2
    assert opened[raw_path][1].disposed is True
    assert not (unsupported_output_dir / "untargeted_cid_nl_source.json").exists()
    assert not (unsupported_output_dir / "quant_matrix_version.csv").exists()


def _write_owner_batch_index(
    path: Path,
    *,
    raw_path: Path,
    candidate_path: Path,
    review_path: Path,
    receipt: DiscoveryCandidateUniverseReceipt,
) -> None:
    row = {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": raw_path.stem,
        "raw_file": str(raw_path),
        "candidate_csv": str(candidate_path),
        "review_csv": str(review_path),
        "candidate_count": str(receipt.candidate_count),
        "high_count": str(receipt.candidate_count),
        "medium_count": "0",
        "low_count": "0",
        **discovery_receipt_index_fields(receipt),
    }
    write_delimited_rows(
        path,
        (row,),
        _BATCH_INDEX_COLUMNS,
        delimiter=",",
        encoding="utf-8",
        extrasaction="raise",
    )
