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
    _install_runtime_raws,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
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
    raw_path.write_bytes(b"m7-runner:S1")
    source_raw = _FakeRaw((7.98, 8.02))
    candidates = _current_candidates(
        source_raw,
        raw_file=raw_path,
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
    )
    candidate_path = tmp_path / "S1.discovery_candidates.csv"
    write_discovery_candidates_csv(candidate_path, candidates)
    review_path = tmp_path / "S1.discovery_review.csv"
    review_path.write_text("", encoding="utf-8")
    batch_index = tmp_path / "discovery_batch_index.csv"
    write_delimited_rows(
        batch_index,
        (
            {
                "sample_stem": "S1",
                "raw_file": str(raw_path),
                "candidate_csv": str(candidate_path),
                "review_csv": str(review_path),
                "candidate_count": str(len(candidates)),
                "high_count": str(len(candidates)),
                "medium_count": "0",
                "low_count": "0",
            },
        ),
        (
            "sample_stem",
            "raw_file",
            "candidate_csv",
            "review_csv",
            "candidate_count",
            "high_count",
            "medium_count",
            "low_count",
        ),
        delimiter=",",
        encoding="utf-8",
        extrasaction="raise",
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
    output_dir = tmp_path / "m7-output"

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
    summary = json.loads((output_dir / "m7_summary.json").read_text())
    assert summary["runtime_succeeded"] is True
    assert summary["terminal_state"] == "quant_matrix_version_built"
    assert summary["sample_count"] == 1
    assert summary["pending_acceptance_count"] == 0
    assert summary["samples"][0]["technical_state"] == "evaluated"
    assert summary["samples"][0]["method_result"] == "SUPPORT"
    assert summary["samples"][0]["strict_scan_count"] == 2
    assert (output_dir / "untargeted_cid_nl_source.json").is_file()
    assert (output_dir / "quant_matrix_version.csv").is_file()
    assert (output_dir / "cell_provenance.tsv").is_file()
    assert (output_dir / "runtime.json").is_file()
    assert opened[raw_path][0].chromatogram_request_counts == [1]
    assert opened[raw_path][0].disposed is True

    write_discovery_candidates_csv(
        candidate_path,
        tuple(
            replace(
                candidate,
                precursor_mz_basis="product_plus_neutral_loss",
            )
            for candidate in candidates
        ),
    )
    unsupported_output_dir = tmp_path / "m7-unsupported-output"
    unsupported_argv = list(argv)
    unsupported_argv[unsupported_argv.index("--output-dir") + 1] = str(
        unsupported_output_dir
    )

    unsupported_exit_code = main(unsupported_argv)

    assert unsupported_exit_code == 0
    unsupported_summary = json.loads(
        (unsupported_output_dir / "m7_summary.json").read_text()
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
    assert unsupported_summary["samples"][0]["technical_state"] == "unsupported"
    assert unsupported_summary["samples"][0]["method_result"] is None
    assert unsupported_summary["samples"][0]["backfill_disposition"] is None
    assert unsupported_summary["samples"][0]["source_candidate_count"] == 1
    assert unsupported_summary["samples"][0]["strict_scan_count"] == 0
    assert len(opened[raw_path]) == 1
    assert not (unsupported_output_dir / "untargeted_cid_nl_source.json").exists()
    assert not (unsupported_output_dir / "quant_matrix_version.csv").exists()
