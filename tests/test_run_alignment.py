import csv
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import xic_extractor.alignment.pipeline as alignment_pipeline_module
from scripts import run_alignment
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_owner_build_route_receipt,
)
from xic_extractor.alignment.pipeline import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    QuantitativeProjectionAuthorityHoldError,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    RawReaderError,
)


def AlignmentRunOutputs(  # noqa: N802 - test factory mirrors the product type
    *,
    _owner_build_xic_backend: str = "raw",
    **kwargs: Any,
) -> _AlignmentRunOutputs:
    return _AlignmentRunOutputs(
        **kwargs,
        owner_build_route_receipt=_mint_owner_build_route_receipt(
            configured_backend=_owner_build_xic_backend,
        ),
    )


def test_run_alignment_cli_passes_paths_settings_and_debug_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        cells = output_dir / "alignment_cells.tsv"
        integration = output_dir / "alignment_cell_integration_audit.tsv"
        backfill_seed = output_dir / "alignment_owner_backfill_seed_audit.tsv"
        backfill_candidate = output_dir / "alignment_owner_backfill_candidate_audit.tsv"
        status = output_dir / "alignment_matrix_status.tsv"
        for path in (
            review,
            matrix,
            cells,
            integration,
            backfill_seed,
            backfill_candidate,
            status,
        ):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            workbook=output_dir / "alignment_results.xlsx",
            review_html=output_dir / "review_report.html",
            review_tsv=review,
            matrix_tsv=matrix,
            cells_tsv=cells,
            integration_audit_tsv=integration,
            backfill_seed_audit_tsv=backfill_seed,
            backfill_candidate_audit_tsv=backfill_candidate,
            status_matrix_tsv=status,
            edge_evidence_tsv=output_dir / "owner_edge_evidence.tsv",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--resolver-mode",
            "legacy_savgol",
            "--emit-alignment-cells",
            "--emit-alignment-integration-audit",
            "--emit-alignment-backfill-seed-audit",
            "--emit-alignment-backfill-candidate-audit",
            "--emit-alignment-status-matrix",
            "--emit-baseline-audit-asls",
        ]
    )

    assert code == 0
    assert captured["discovery_batch_index"] == batch_index.resolve()
    assert captured["raw_dir"] == raw_dir.resolve()
    assert captured["dll_dir"] == dll_dir.resolve()
    assert captured["output_dir"] == output_dir.resolve()
    assert captured["alignment_config"].max_ppm == 50.0
    peak_config = captured["peak_config"]
    assert peak_config.data_dir == raw_dir.resolve()
    assert peak_config.dll_dir == dll_dir.resolve()
    assert peak_config.output_csv == output_dir.resolve() / "xic_results.csv"
    assert peak_config.diagnostics_csv == output_dir.resolve() / "xic_diagnostics.csv"
    assert peak_config.resolver_mode == "legacy_savgol"
    assert peak_config.baseline_audit_method == "asls"
    assert peak_config.baseline_integration_method == "asls"
    assert peak_config.signal_processing_method == "gaussian"
    assert peak_config.signal_processing_window_points == 15
    signal_profile = captured["signal_processing_profile"]
    assert signal_profile.method == "gaussian"
    assert signal_profile.window_points == 15
    assert peak_config.signal_processing_window_points == signal_profile.window_points
    assert captured["output_level"] == "machine"
    assert captured["emit_alignment_cells"] is True
    assert captured["emit_alignment_integration_audit"] is True
    assert captured["emit_alignment_backfill_seed_audit"] is True
    assert captured["emit_alignment_backfill_candidate_audit"] is True
    assert captured["emit_alignment_status_matrix"] is True
    assert captured["raw_workers"] == 1
    assert captured["raw_xic_batch_size"] == 1
    assert captured["owner_build_xic_backend"] == "raw"
    assert captured["owner_backfill_window_strategy"] == "exact"
    assert captured["owner_backfill_superwindow_span_factor"] == 2
    assert captured["backfill_scope"] == "full-audit"
    assert captured["audit_evidence_mode"] == "auto"
    assert captured["selected_family_ids"] == frozenset()
    assert captured["drift_lookup"] is None
    stdout = capsys.readouterr().out
    assert "Alignment review TSV:" in stdout
    assert "alignment_review.tsv" in stdout
    assert "alignment_cell_integration_audit.tsv" in stdout
    assert "alignment_owner_backfill_seed_audit.tsv" in stdout
    assert "alignment_owner_backfill_candidate_audit.tsv" in stdout
    assert "alignment_matrix_status.tsv" in stdout
    assert "Owner edge evidence TSV:" in stdout


def test_run_alignment_cli_passes_nondefault_signal_processing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--signal-processing-method",
            "gaussian",
            "--signal-processing-window-points",
            "21",
        ]
    )

    assert code == 0
    profile = captured["signal_processing_profile"]
    assert profile.method == "gaussian"
    assert profile.window_points == 21
    assert captured["peak_config"].signal_processing_window_points == 21


@pytest.mark.parametrize("value", ["true", "2", "1000"])
def test_run_alignment_cli_rejects_invalid_signal_processing_window(
    value: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_alignment._parse_args(
            [
                "--discovery-batch-index",
                "batch.csv",
                "--raw-dir",
                "raw",
                "--dll-dir",
                "dll",
                "--signal-processing-window-points",
                value,
            ]
        )

    assert exc_info.value.code == 2


def test_run_alignment_cli_rejects_unsupported_signal_processing_method() -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_alignment._parse_args(
            [
                "--discovery-batch-index",
                "batch.csv",
                "--raw-dir",
                "raw",
                "--dll-dir",
                "dll",
                "--signal-processing-method",
                "asls",
            ]
        )

    assert exc_info.value.code == 2


def test_run_alignment_accepts_asls_baseline_integration_method_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        for path in (review, matrix):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            workbook=None,
            review_html=None,
            review_tsv=review,
            matrix_tsv=matrix,
            cells_tsv=None,
            integration_audit_tsv=None,
            backfill_seed_audit_tsv=None,
            status_matrix_tsv=None,
            edge_evidence_tsv=None,
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--baseline-integration-method",
            "asls",
        ]
    )

    assert code == 0
    assert captured["peak_config"].baseline_integration_method == "asls"


def test_run_alignment_rejects_retired_linear_edge_baseline_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fake_run_alignment(**_kwargs):
        raise AssertionError("run_alignment should not be called")

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--baseline-integration-method",
            "linear_edge",
        ]
    )

    assert code == 2
    assert (
        "linear_edge baseline integration is retired; use asls"
        in capsys.readouterr().err
    )


def test_run_alignment_rejects_retired_linear_edge_baseline_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fake_run_alignment(**_kwargs):
        raise AssertionError("run_alignment should not be called")

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setenv("BASELINE_INTEGRATION_METHOD", "linear_edge")

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(tmp_path / "alignment"),
        ]
    )

    assert code == 2
    assert (
        "linear_edge baseline integration is retired; use asls"
        in capsys.readouterr().err
    )


def test_run_alignment_cli_passes_selected_family_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    allowlist = tmp_path / "families.tsv"
    allowlist.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--backfill-scope",
            "selected-families",
            "--backfill-family-list-tsv",
            str(allowlist),
            "--backfill-family-id",
            "FAM002",
        ]
    )

    assert code == 0
    assert captured["backfill_scope"] == "selected-families"
    assert captured["selected_family_ids"] == frozenset({"FAM001", "FAM002"})
    assert "families.tsv" in captured["selected_family_source"]
    assert "inline:FAM002" in captured["selected_family_source"]


def test_run_alignment_cli_passes_audit_evidence_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--backfill-scope",
            "production-equivalent",
            "--audit-evidence-mode",
            "none",
        ]
    )

    assert code == 0
    assert captured["backfill_scope"] == "production-equivalent"
    assert captured["audit_evidence_mode"] == "none"


def test_run_alignment_cli_rejects_allowlist_outside_selected_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    calls = {}

    def fake_run_alignment(**kwargs):
        calls.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--backfill-family-id",
            "FAM001",
        ]
    )

    assert code == 2
    assert calls == {}
    assert "backfill family allowlist flags require" in capsys.readouterr().err


def test_run_alignment_env_enables_asls_baseline_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setenv("BASELINE_AUDIT_METHOD", "asls")

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    assert captured["peak_config"].baseline_audit_method == "asls"


def test_run_alignment_cli_passes_identity_coherence_flags(monkeypatch, tmp_path):
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    out_dir = tmp_path / "out"
    controls = tmp_path / "controls.tsv"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()
    controls.write_text("control_id\n", encoding="utf-8")
    seen = {}

    def fake_run_alignment(**kwargs):
        seen.update(kwargs)
        return AlignmentRunOutputs(
            identity_coherence_output_dir=out_dir / "identity",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(out_dir),
            "--emit-identity-coherence-diagnostic",
            "--identity-coherence-output-dir",
            str(out_dir / "identity"),
            "--identity-coherence-controls-manifest",
            str(controls),
        ]
    )

    assert code == 0
    assert seen["emit_identity_coherence_diagnostic"] is True
    assert seen["identity_coherence_output_dir"] == out_dir / "identity"
    assert seen["identity_coherence_controls_manifest"] == controls


def test_run_alignment_cli_ignores_identity_manifest_when_diagnostic_disabled(
    monkeypatch,
    tmp_path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    out_dir = tmp_path / "out"
    missing_controls = tmp_path / "missing-controls.tsv"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()
    seen = {}

    def fake_run_alignment(**kwargs):
        seen.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(out_dir),
            "--identity-coherence-controls-manifest",
            str(missing_controls),
        ]
    )

    assert code == 0
    assert seen["emit_identity_coherence_diagnostic"] is False
    assert seen["identity_coherence_controls_manifest"] is None


def test_run_alignment_cli_accepts_output_level_debug(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-level",
            "debug",
        ],
    )

    assert code == 0
    assert captured["output_level"] == "debug"


def test_run_alignment_cli_defaults_to_local_minimum_production_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ],
    )

    assert code == 0
    assert captured["peak_config"].resolver_mode == "local_minimum"


def test_run_alignment_cli_keeps_region_first_safe_merge_out_of_production_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--resolver-mode",
            "region_first_safe_merge",
        ],
    )

    assert code == 0
    assert captured["peak_config"].resolver_mode == "local_minimum"


def test_run_alignment_rejects_retired_arbitrated_resolver_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_alignment.main(
            [
                "--discovery-batch-index",
                str(batch_index),
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(dll_dir),
                "--resolver-mode",
                "arbitrated",
            ],
        )

    assert exc_info.value.code == 2
    assert "arbitrated resolver mode is retired" in capsys.readouterr().err


def test_run_alignment_cli_accepts_validation_minimal_output_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-level",
            "validation-minimal",
        ],
    )

    assert code == 0
    assert captured["output_level"] == "validation-minimal"


def test_run_alignment_cli_dna_dr_preset_runs_owner_backfill_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_alignment: dict[str, object] = {}
    captured_preset: dict[str, object] = {}

    def fake_run_alignment(**kwargs):
        captured_alignment.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        identity = output_dir / "alignment_matrix_identity.tsv"
        cells = output_dir / "alignment_cells.tsv"
        seed_audit = output_dir / "alignment_owner_backfill_seed_audit.tsv"
        for path in (review, matrix, identity, cells, seed_audit):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            matrix_identity_tsv=identity,
            cells_tsv=cells,
            backfill_seed_audit_tsv=seed_audit,
        )

    def fake_preset_runner(**kwargs):
        captured_preset.update(kwargs)
        summary_json = output_dir / "standard_peak_backfill_preset_summary.json"
        summary_json.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            summary_json=summary_json,
            published_alignment_manifest_json=output_dir / "publish_manifest.json",
            gallery_html=output_dir / "gallery.html",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_preset_runner,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr",
        ]
    )

    assert code == 0
    assert captured_alignment["emit_alignment_backfill_seed_audit"] is False
    assert captured_alignment["emit_alignment_cells"] is False
    assert captured_alignment["require_discovery_owner_closure"] is True
    assert captured_preset == {}


def test_run_alignment_cli_product_ready_preset_runs_owner_backfill_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_alignment: dict[str, object] = {}
    captured_standard_peak: dict[str, object] = {}
    captured_product_ready_check: dict[str, object] = {}
    call_order: list[str] = []

    def fake_run_alignment(**kwargs):
        call_order.append("alignment")
        captured_alignment.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "review_tsv": output_dir / "alignment_review.tsv",
            "matrix_tsv": output_dir / "alignment_matrix.tsv",
            "matrix_identity_tsv": output_dir / "alignment_matrix_identity.tsv",
            "backfill_cell_evidence_tsv": (
                output_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            "backfill_seed_audit_tsv": (
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        }
        for path in paths.values():
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            **paths,
            _owner_build_xic_backend=kwargs["owner_build_xic_backend"],
        )

    def fake_standard_peak_runner(**kwargs):
        call_order.append("standard_peak")
        captured_standard_peak.update(kwargs)
        return SimpleNamespace(
            summary_json=output_dir / "standard_peak_summary.json",
            published_alignment_manifest_json=None,
            gallery_html=None,
        )

    def fail_backfill_expansion_runner(**_kwargs):
        raise AssertionError("builtin product-ready preset must not run fixed replay")

    def fake_product_ready_check(**kwargs):
        call_order.append("product_ready_check")
        captured_product_ready_check.update(kwargs)
        return SimpleNamespace(
            status="pass",
            summary_json=output_dir / "product_ready_check_summary.json",
            checks_tsv=output_dir / "product_ready_check_checks.tsv",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_standard_peak_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "run_backfill_expansion_clean_target_selective_preset_from_alignment",
        fail_backfill_expansion_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "check_product_ready_preset_publication",
        fake_product_ready_check,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr_product_ready",
            "--output-level",
            "validation-minimal",
        ]
    )

    assert code == 0
    assert call_order == ["alignment"]
    assert captured_alignment["emit_alignment_backfill_seed_audit"] is False
    assert captured_alignment["emit_alignment_cells"] is False
    assert captured_alignment["owner_build_xic_backend"] == "raw_superwindow"
    assert captured_standard_peak == {}
    assert captured_product_ready_check == {}
    stdout = capsys.readouterr().out
    assert "Backfill expansion productization summary JSON:" not in stdout
    assert "Product-ready preset publication summary JSON:" not in stdout
    assert "Configured owner-build XIC backend: raw_superwindow" in stdout
    assert "Effective owner-build route: configured_backend:raw_superwindow" in stdout
    assert "Effective route validation: owner_validated" in stdout
    assert "ProcessingRunSpec identity: <none" in stdout
    assert "Preset readiness authority: not_evaluated" in stdout


def test_run_alignment_cli_can_override_product_ready_owner_build_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_alignment = {}

    def fake_run_alignment(**kwargs):
        captured_alignment.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        return AlignmentRunOutputs(
            review_tsv=output_dir / "alignment_review.tsv",
            matrix_tsv=output_dir / "alignment_matrix.tsv",
            matrix_identity_tsv=output_dir / "alignment_matrix_identity.tsv",
            backfill_cell_evidence_tsv=(
                output_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            backfill_seed_audit_tsv=(
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        )

    def fake_standard_peak_runner(**_kwargs):
        return SimpleNamespace(
            summary_json=output_dir / "standard_peak_summary.json",
            published_alignment_manifest_json=(
                output_dir / "standard_peak_default_matrix_manifest.json"
            ),
            gallery_html=None,
        )

    def fake_product_ready_check(**_kwargs):
        return SimpleNamespace(
            status="pass",
            summary_json=output_dir / "product_ready_check_summary.json",
            checks_tsv=output_dir / "product_ready_check_checks.tsv",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_standard_peak_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "check_product_ready_preset_publication",
        fake_product_ready_check,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr_product_ready",
            "--owner-build-xic-backend",
            "raw",
        ]
    )

    assert code == 0
    assert captured_alignment["owner_build_xic_backend"] == "raw"


def test_run_alignment_cli_product_ready_preset_ignores_retired_publication_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fake_run_alignment(**kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "review_tsv": output_dir / "alignment_review.tsv",
            "matrix_tsv": output_dir / "alignment_matrix.tsv",
            "matrix_identity_tsv": output_dir / "alignment_matrix_identity.tsv",
            "backfill_cell_evidence_tsv": (
                output_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            "backfill_seed_audit_tsv": (
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        }
        for path in paths.values():
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            **paths,
            _owner_build_xic_backend=kwargs["owner_build_xic_backend"],
        )

    def fake_standard_peak_runner(**_kwargs):
        return SimpleNamespace(
            summary_json=output_dir / "standard_peak_summary.json",
            published_alignment_manifest_json=None,
            gallery_html=None,
        )

    def fake_product_ready_check(**_kwargs):
        return SimpleNamespace(
            status="fail",
            summary_json=output_dir / "product_ready_check_summary.json",
            checks_tsv=output_dir / "product_ready_check_checks.tsv",
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_standard_peak_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "check_product_ready_preset_publication",
        fake_product_ready_check,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr_product_ready",
            "--output-level",
            "validation-minimal",
        ]
    )

    assert code == 0
    stderr = capsys.readouterr().err
    assert "Product-ready preset publication check failed" not in stderr


def test_run_alignment_cli_rejects_explicit_retired_backfill_replay_preset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    preset_path = tmp_path / "explicit_replay.toml"
    preset_path.write_text(
        """
name = "Explicit replay"
description = "Explicit retained clean-target replay"
combine_mode = "single"

[[tag]]
strategy = "neutral_loss"
name = "DNA_dR"
value = 116.0474

[alignment]
standard_peak_backfill = true
standard_peak_backfill_publication_mode = "matrix-only"
backfill_expansion_productization = "clean-target-selective"
""".strip(),
        encoding="utf-8",
    )
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_backfill_expansion: dict[str, object] = {}
    call_order: list[str] = []

    def fake_run_alignment(**_kwargs):
        call_order.append("alignment")
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "review_tsv": output_dir / "alignment_review.tsv",
            "matrix_tsv": output_dir / "alignment_matrix.tsv",
            "matrix_identity_tsv": output_dir / "alignment_matrix_identity.tsv",
            "backfill_cell_evidence_tsv": (
                output_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            "backfill_seed_audit_tsv": (
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        }
        for path in paths.values():
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(**paths)

    def fake_standard_peak_runner(**_kwargs):
        call_order.append("standard_peak")
        return SimpleNamespace(
            summary_json=output_dir / "standard_peak_summary.json",
            published_alignment_manifest_json=None,
            gallery_html=None,
        )

    def fake_backfill_expansion_runner(**kwargs):
        call_order.append("backfill_expansion")
        captured_backfill_expansion.update(kwargs)
        return SimpleNamespace(
            summary_json=(
                output_dir
                / "backfill_expansion_productization_preset"
                / "backfill_expansion_productization_preset_summary.json"
            ),
            clean_target_activation_summary_json=(
                output_dir
                / "backfill_expansion_productization_preset"
                / "docs"
                / "backfill_expansion_clean_target_selective_product_activation_v1"
                / (
                    "backfill_expansion_clean_target_selective_product_activation_"
                    "summary.json"
                )
            ),
            product_authority_scope=(
                "backfill_expansion_clean_target_selective_activation_84_cells"
            ),
        )

    def fail_product_ready_check(**_kwargs):
        raise AssertionError("custom explicit replay preset is not built-in")

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_standard_peak_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "run_backfill_expansion_clean_target_selective_preset_from_alignment",
        fake_backfill_expansion_runner,
        raising=False,
    )
    monkeypatch.setattr(
        run_alignment,
        "check_product_ready_preset_publication",
        fail_product_ready_check,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            str(preset_path),
            "--output-level",
            "validation-minimal",
        ]
    )

    assert code == 2
    assert call_order == []
    assert captured_backfill_expansion == {}
    assert "diagnostic-only" in capsys.readouterr().err


def test_run_alignment_cli_rejects_retired_publication_mode_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    cache_dir = tmp_path / "standard_peak_cache"
    captured_preset: dict[str, object] = {}

    def fake_run_alignment(**_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "review_tsv": output_dir / "alignment_review.tsv",
            "matrix_tsv": output_dir / "alignment_matrix.tsv",
            "matrix_identity_tsv": output_dir / "alignment_matrix_identity.tsv",
            "cells_tsv": output_dir / "alignment_cells.tsv",
            "backfill_seed_audit_tsv": (
                output_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
        }
        for path in paths.values():
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(**paths)

    def fake_preset_runner(**kwargs):
        captured_preset.update(kwargs)
        return SimpleNamespace(
            summary_json=output_dir / "summary.json",
            published_alignment_manifest_json=None,
            gallery_html=None,
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        fake_preset_runner,
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr",
            "--standard-peak-backfill-publication-mode",
            "deep-audit",
            "--standard-peak-evidence-cache-dir",
            str(cache_dir),
        ]
    )

    assert code == 2
    assert captured_preset == {}


def test_run_alignment_cli_rejects_standard_peak_cache_without_standard_peak_preset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--standard-peak-evidence-cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "--standard-peak-evidence-cache-dir" in stderr
    assert "diagnostic-only" in stderr


def test_run_alignment_cli_dna_dr_preset_uses_light_cells_for_validation_minimal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_alignment: dict[str, object] = {}

    def fake_run_alignment(**kwargs):
        captured_alignment.update(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        identity = output_dir / "alignment_matrix_identity.tsv"
        light_cells = output_dir / "alignment_backfill_cell_evidence.tsv"
        seed_audit = output_dir / "alignment_owner_backfill_seed_audit.tsv"
        for path in (review, matrix, identity, light_cells, seed_audit):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            matrix_identity_tsv=identity,
            backfill_cell_evidence_tsv=light_cells,
            backfill_seed_audit_tsv=seed_audit,
        )

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(
        run_alignment,
        "run_standard_peak_backfill_preset",
        lambda **_kwargs: SimpleNamespace(
            summary_json=output_dir / "summary.json",
            published_alignment_manifest_json=None,
            gallery_html=None,
        ),
        raising=False,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "dna_dr",
            "--output-level",
            "validation-minimal",
        ]
    )

    assert code == 0
    assert captured_alignment["emit_alignment_backfill_seed_audit"] is False
    assert captured_alignment["emit_alignment_cells"] is False


def test_run_alignment_cli_passes_raw_workers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--raw-workers",
            "4",
        ],
    )

    assert code == 0
    assert captured["raw_workers"] == 4


def test_run_alignment_cli_passes_raw_xic_batch_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--raw-xic-batch-size",
            "64",
        ],
    )

    assert code == 0
    assert captured["raw_xic_batch_size"] == 64


def test_run_alignment_cli_passes_owner_backfill_xic_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-backfill-xic-backend",
            "ms1-index",
        ],
    )

    assert code == 0
    assert captured["owner_backfill_xic_backend"] == "ms1_index"


def test_run_alignment_cli_passes_owner_build_xic_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-build-xic-backend",
            "ms1-index",
        ],
    )

    assert code == 0
    assert captured["owner_build_xic_backend"] == "ms1_index"


def test_run_alignment_cli_passes_raw_superwindow_owner_build_xic_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-build-xic-backend",
            "raw-super-window",
        ],
    )

    assert code == 0
    assert captured["owner_build_xic_backend"] == "raw_superwindow"


def test_run_alignment_cli_passes_owner_backfill_window_strategy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-backfill-window-strategy",
            "super-window",
            "--owner-backfill-superwindow-span-factor",
            "2",
        ],
    )

    assert code == 0
    assert captured["owner_backfill_window_strategy"] == "super-window"
    assert captured["owner_backfill_superwindow_span_factor"] == 2


def test_run_alignment_cli_rejects_invalid_superwindow_span_factor(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_alignment.main(
            [
                "--discovery-batch-index",
                str(tmp_path / "missing.csv"),
                "--raw-dir",
                str(tmp_path / "raws"),
                "--dll-dir",
                str(tmp_path / "dll"),
                "--owner-backfill-superwindow-span-factor",
                "0",
            ],
        )

    assert exc_info.value.code == 2


def test_run_alignment_cli_passes_hybrid_owner_backfill_xic_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-backfill-xic-backend",
            "ms1-index-hybrid",
        ],
    )

    assert code == 0
    assert captured["owner_backfill_xic_backend"] == "ms1_index_hybrid"


def test_run_alignment_cli_passes_preconsolidate_owner_families(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--preconsolidate-owner-families",
        ],
    )

    assert code == 0
    assert captured["preconsolidate_owner_families"] is True


def test_run_alignment_cli_passes_early_loser_shadow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--emit-early-loser-shadow",
        ],
    )

    assert code == 0
    assert captured["emit_early_loser_shadow"] is True


def test_run_alignment_cli_validation_fast_profile_sets_raw_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--performance-profile",
            "validation-fast",
        ],
    )

    assert code == 0
    assert captured["raw_workers"] == 11
    assert captured["raw_xic_batch_size"] == 64


def test_run_alignment_cli_explicit_raw_options_override_performance_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--performance-profile",
            "validation-fast",
            "--raw-workers",
            "3",
            "--raw-xic-batch-size",
            "16",
        ],
    )

    assert code == 0
    assert captured["raw_workers"] == 3
    assert captured["raw_xic_batch_size"] == 16


def test_run_alignment_cli_requires_sample_info_and_targeted_istd_together(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(tmp_path / "missing.csv"),
            "--raw-dir",
            str(tmp_path / "missing-raws"),
            "--dll-dir",
            str(tmp_path / "missing-dll"),
            "--sample-info",
            str(tmp_path / "sample_info.csv"),
        ],
    )

    assert code == 2
    assert (
        "--sample-info is required with --targeted-istd-workbook, "
        "and both must be provided together"
    ) in capsys.readouterr().err


def test_run_alignment_cli_builds_and_passes_drift_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text("sample\nSample_A\n", encoding="utf-8")
    targeted_workbook = tmp_path / "targeted.xlsx"
    targeted_workbook.write_text("workbook", encoding="utf-8")
    drift_lookup = object()
    captured = {}

    def fake_read_targeted_istd_drift_evidence(**kwargs):
        captured["drift_kwargs"] = kwargs
        return drift_lookup

    def fake_run_alignment(**kwargs):
        captured["run_kwargs"] = kwargs
        return AlignmentRunOutputs()

    monkeypatch.setattr(
        run_alignment,
        "read_targeted_istd_drift_evidence",
        fake_read_targeted_istd_drift_evidence,
    )
    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-info",
            str(sample_info),
            "--targeted-istd-workbook",
            str(targeted_workbook),
            "--raw-workers",
            "3",
            "--raw-xic-batch-size",
            "16",
        ],
    )

    assert code == 0
    assert captured["drift_kwargs"]["targeted_workbook"] == targeted_workbook.resolve()
    assert captured["drift_kwargs"]["sample_info"] == sample_info.resolve()
    assert captured["drift_kwargs"]["local_window"] == 40
    assert captured["drift_kwargs"]["target_snapshot"] is not None
    assert captured["run_kwargs"]["drift_lookup"] is drift_lookup
    assert captured["run_kwargs"]["raw_workers"] == 3
    assert captured["run_kwargs"]["raw_xic_batch_size"] == 16


def test_run_alignment_cli_passes_custom_drift_local_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text("sample\nSample_A\n", encoding="utf-8")
    targeted_workbook = tmp_path / "targeted.xlsx"
    targeted_workbook.write_text("workbook", encoding="utf-8")
    captured = {}

    def fake_read_targeted_istd_drift_evidence(**kwargs):
        captured["drift_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        run_alignment,
        "read_targeted_istd_drift_evidence",
        fake_read_targeted_istd_drift_evidence,
    )
    monkeypatch.setattr(
        run_alignment,
        "run_alignment",
        lambda **kwargs: AlignmentRunOutputs(),
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-info",
            str(sample_info),
            "--targeted-istd-workbook",
            str(targeted_workbook),
            "--drift-local-window",
            "12",
        ],
    )

    assert code == 0
    assert captured["drift_kwargs"]["local_window"] == 12


def test_run_alignment_cli_rejects_missing_sample_info_with_targeted_workbook(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    targeted_workbook = tmp_path / "targeted.xlsx"
    targeted_workbook.write_text("workbook", encoding="utf-8")

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-info",
            str(tmp_path / "missing_sample_info.csv"),
            "--targeted-istd-workbook",
            str(targeted_workbook),
        ],
    )

    assert code == 2
    assert "sample info does not exist" in capsys.readouterr().err


def test_run_alignment_cli_rejects_missing_targeted_workbook_with_sample_info(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text("sample\nSample_A\n", encoding="utf-8")

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-info",
            str(sample_info),
            "--targeted-istd-workbook",
            str(tmp_path / "missing_targeted.xlsx"),
        ],
    )

    assert code == 2
    assert "targeted ISTD workbook does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("exc", [OSError("cannot read workbook"), KeyError("Sample")])
def test_run_alignment_cli_returns_2_for_drift_reader_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text("sample\nSample_A\n", encoding="utf-8")
    targeted_workbook = tmp_path / "targeted.xlsx"
    targeted_workbook.write_text("workbook", encoding="utf-8")

    def fail_read_targeted_istd_drift_evidence(**kwargs):
        raise exc

    monkeypatch.setattr(
        run_alignment,
        "read_targeted_istd_drift_evidence",
        fail_read_targeted_istd_drift_evidence,
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--sample-info",
            str(sample_info),
            "--targeted-istd-workbook",
            str(targeted_workbook),
        ],
    )

    assert code == 2
    assert str(exc) in capsys.readouterr().err


def test_run_alignment_cli_passes_owner_backfill_min_detected_samples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(**kwargs):
        captured.update(kwargs)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--owner-backfill-min-detected-samples",
            "3",
        ],
    )

    assert code == 0
    assert captured["alignment_config"].owner_backfill_min_detected_samples == 3


def test_run_alignment_cli_writes_timing_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    timing_path = tmp_path / "diagnostics" / "alignment_timing.json"

    def fake_run_alignment(**kwargs):
        timing_recorder = kwargs["timing_recorder"]
        with timing_recorder.stage(
            "alignment.read_candidates",
            metrics={"candidate_count": 0},
        ):
            pass
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--timing-output",
            str(timing_path),
        ],
    )

    assert code == 0
    payload = json.loads(timing_path.read_text(encoding="utf-8"))
    assert payload["pipeline"] == "alignment"
    assert payload["records"][0]["stage"] == "alignment.read_candidates"
    assert payload["records"][0]["metrics"] == {"candidate_count": 0}
    assert "Timing JSON:" in capsys.readouterr().out


def test_run_alignment_cli_writes_live_timing_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    live_path = tmp_path / "diagnostics" / "alignment_live_timing.json"

    def fake_run_alignment(**kwargs):
        timing_recorder = kwargs["timing_recorder"]
        timing_recorder.record("alignment.run_config", elapsed_sec=0.0)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--timing-live-output",
            str(live_path),
        ],
    )

    assert code == 0
    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "timing.live.v2"
    assert payload["recent_records"][0]["stage"] == "alignment.run_config"
    assert "records" not in payload
    assert "Timing live JSON:" in capsys.readouterr().out


def test_run_alignment_cli_writes_cprofile_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    profile_dir = tmp_path / "profile"

    monkeypatch.setattr(
        run_alignment,
        "run_alignment",
        lambda **kwargs: AlignmentRunOutputs(),
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--profile",
            "cprofile",
            "--profile-output-dir",
            str(profile_dir),
        ],
    )

    assert code == 0
    assert (profile_dir / "profile.prof").is_file()
    assert (profile_dir / "profile_top.txt").is_file()


def test_run_alignment_cli_rejects_invalid_raw_workers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        run_alignment.main(
            [
                "--discovery-batch-index",
                str(batch_index),
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(dll_dir),
                "--raw-workers",
                "0",
            ],
        )

    assert exc_info.value.code == 2
    assert "value must be an integer >= 1" in capsys.readouterr().err


def test_run_alignment_cli_rejects_missing_batch_index(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(tmp_path / "missing.csv"),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert "discovery batch index does not exist" in capsys.readouterr().err


def test_run_alignment_cli_rejects_missing_raw_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(tmp_path / "missing-raws"),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert "raw directory does not exist" in capsys.readouterr().err


def test_run_alignment_cli_rejects_missing_dll_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(tmp_path / "missing-dll"),
        ]
    )

    assert code == 2
    assert "dll directory does not exist" in capsys.readouterr().err


def test_run_alignment_cli_rejects_expected_sample_count_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,s1.csv\nS2,S2.raw,s2.csv\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--expected-sample-count",
            "85",
        ]
    )

    assert code == 2
    assert "expected 85 discovery batch samples, found 2" in capsys.readouterr().err


def test_run_alignment_cli_rejects_duplicate_sample_stems(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\n"
        "S1,S1.raw,s1.csv\n"
        "S1,S1.raw,s1_again.csv\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--expected-sample-count",
            "2",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "duplicate sample_stem values are not supported" in stderr
    assert "S1" in stderr


def test_run_alignment_cli_normal_run_does_not_require_all_raw_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,s1.csv\n",
        encoding="utf-8",
    )
    (tmp_path / "s1.csv").write_text("", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fake_run_alignment(**kwargs):
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 0


def _write_owner_closed_preflight_batch(
    tmp_path: Path,
    sample_stems: tuple[str, ...],
) -> tuple[Path, Path]:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    batch_index = tmp_path / "discovery_batch_index.csv"
    fieldnames = (
        "schema_version",
        "sample_stem",
        "raw_file",
        "candidate_csv",
        "candidate_count",
        *DISCOVERY_BATCH_RECEIPT_COLUMNS,
    )
    rows: list[dict[str, str]] = []
    for sample_stem in sample_stems:
        raw_path = raw_dir / f"{sample_stem}.raw"
        raw_path.write_bytes(b"")
        candidate_csv = tmp_path / f"{sample_stem.lower()}.csv"
        candidate_csv.write_bytes(b"")
        acquisition_source = build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=acquisition_source,
            candidate_csv=candidate_csv,
            candidates=(),
        )
        rows.append(
            {
                "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
                "sample_stem": sample_stem,
                "raw_file": raw_path.name,
                "candidate_csv": candidate_csv.name,
                "candidate_count": "0",
                **discovery_receipt_index_fields(receipt),
            }
        )
    with batch_index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return batch_index, raw_dir


def test_run_alignment_cli_preflight_only_prints_launch_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index, raw_dir = _write_owner_closed_preflight_batch(
        tmp_path,
        ("S1", "S2"),
    )
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fail_run_alignment(**kwargs):
        raise AssertionError("preflight-only should not run alignment")

    monkeypatch.setattr(run_alignment, "run_alignment", fail_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--output-level",
            "validation-minimal",
            "--backfill-scope",
            "production-equivalent",
            "--audit-evidence-mode",
            "none",
            "--performance-profile",
            "validation-fast",
            "--owner-backfill-window-strategy",
            "super-window",
            "--expected-sample-count",
            "2",
            "--timing-output",
            str(output_dir / "timing.json"),
            "--timing-live-output",
            str(output_dir / "timing.live.json"),
            "--preflight-only",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert (
        "Alignment launch preflight OK (diagnostic_only; no validation completed)"
        in stdout
    )
    assert "Preflight scope: owner-closed discovery batch" in stdout
    assert "no candidate CSVs loaded; no RAW files opened" in stdout
    assert "Discovery batch samples: 2" in stdout
    assert "Candidate CSVs found: 2" in stdout
    assert "RAW paths found: 2" in stdout
    assert "Output level: validation-minimal" in stdout
    assert "Backfill scope: production-equivalent" in stdout
    assert "Audit evidence mode: none" in stdout
    assert "Performance profile: validation-fast" in stdout
    assert "Owner backfill window strategy: super-window" in stdout
    assert "Effective owner-build route: <not executed>" in stdout
    assert "Effective route validation: not_executed" in stdout
    assert "owner_validated" not in stdout
    assert "85RAW canonical contract: not requested" in stdout
    assert "Python executable:" in stdout
    assert "run_alignment module:" in stdout
    assert "Working directory:" in stdout
    assert f"Timing JSON: {output_dir / 'timing.json'}" in stdout
    assert "Timing live JSON:" in stdout


def test_run_alignment_cli_preflight_only_uses_shared_batch_parser(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--preflight-only",
        ]
    )

    assert code == 2
    assert "candidate_csv is required" in capsys.readouterr().err


def test_run_alignment_cli_preflight_only_requires_discovery_owner_closure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,s1.csv\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").write_text("", encoding="utf-8")
    (tmp_path / "s1.csv").write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--preflight-only",
        ]
    )

    assert code == 2
    assert "owner closure is required" in capsys.readouterr().err


def test_run_alignment_cli_preflight_only_rejects_missing_candidate_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,missing.csv\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").write_text("", encoding="utf-8")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--preflight-only",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "candidate CSV does not exist" in stderr
    assert "missing.csv" in stderr


def test_run_alignment_cli_preflight_only_rejects_missing_raw_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\nS1,S1.raw,s1.csv\n",
        encoding="utf-8",
    )
    (tmp_path / "s1.csv").write_text("", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--preflight-only",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "RAW file does not exist" in stderr
    assert "S1" in stderr


def test_run_alignment_cli_rejects_noncanonical_85raw_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\n"
        + "".join(f"S{i},S{i}.raw,s{i}.csv\n" for i in range(85)),
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--expected-sample-count",
            "85",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "85RAW canonical launch contract failed" in stderr
    assert "--output-level must be 'validation-minimal'" in stderr
    assert "--timing-live-output is required" in stderr


def test_run_alignment_cli_rejects_85raw_wrong_python_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\n"
        + "".join(f"S{i},S{i}.raw,s{i}.csv\n" for i in range(85)),
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    monkeypatch.setattr(
        run_alignment.sys,
        "executable",
        str(tmp_path / "wrong-python" / "python.exe"),
    )

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--expected-sample-count",
            "85",
            "--output-level",
            "validation-minimal",
            "--backfill-scope",
            "production-equivalent",
            "--audit-evidence-mode",
            "none",
            "--performance-profile",
            "validation-fast",
            "--owner-backfill-window-strategy",
            "super-window",
            "--timing-output",
            str(output_dir / "timing.json"),
            "--timing-live-output",
            str(output_dir / "timing.live.json"),
            "--preflight-only",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "Python executable must be under this worktree .venv" in stderr


@pytest.mark.parametrize("exc", [RawReaderError("raw fail"), ValueError("bad input")])
def test_run_alignment_cli_returns_2_for_user_visible_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fail_run_alignment(**kwargs):
        raise exc

    monkeypatch.setattr(run_alignment, "run_alignment", fail_run_alignment)

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    assert str(exc) in capsys.readouterr().err


@pytest.mark.parametrize("profile", ("none", "cprofile"))
def test_run_alignment_cli_authority_hold_has_exact_terminal_and_zero_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    profile: str,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    diagnostics_dir = tmp_path / "diagnostics"
    profile_dir = tmp_path / "profile"
    for directory in (output_dir, diagnostics_dir, profile_dir):
        directory.mkdir()
    timing_path = diagnostics_dir / "alignment_timing.json"
    live_timing_path = diagnostics_dir / "alignment_live_timing.json"
    sentinel_paths = (
        output_dir / "alignment_results.xlsx",
        output_dir / "alignment_matrix.tsv",
        output_dir / "alignment_review.tsv",
        output_dir / "review_report.html",
        output_dir / "alignment_matrix.tsv.tmp",
        output_dir / "alignment_results.xlsx.bak",
        timing_path,
        live_timing_path,
        profile_dir / "profile.prof",
        profile_dir / "profile_top.txt",
    )
    for index, path in enumerate(sentinel_paths):
        path.write_bytes(f"sentinel-{index}".encode())
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    authority_calls: list[dict[str, str]] = []

    def hold_product_authority(**kwargs: str) -> None:
        authority_calls.append(kwargs)
        raise QuantitativeProjectionAuthorityHoldError(
            "HOLD: quantitative_projection_authority_pending_validation"
        )

    monkeypatch.setattr(
        alignment_pipeline_module,
        "require_product_quantitative_projection_authority",
        hold_product_authority,
    )
    argv = [
        "--discovery-batch-index",
        str(batch_index),
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(output_dir),
        "--backfill-scope",
        "production-equivalent",
        "--timing-output",
        str(timing_path),
        "--timing-live-output",
        str(live_timing_path),
    ]
    if profile == "cprofile":
        argv.extend(
            [
                "--profile",
                "cprofile",
                "--profile-output-dir",
                str(profile_dir),
            ]
        )

    code = run_alignment.main(argv)

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == (
        "HOLD: quantitative_projection_authority_pending_validation\n"
    )
    assert authority_calls == [
        {
            "consumer_id": (
                "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
            ),
            "signal_processing_profile_id": (
                "signal-profile.v1:"
                "bcefd76d05bc26a0ec9f333fdb76427ccb2133c1946f5ec711916e85efc06de2"
            ),
        }
    ]
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_run_alignment_cli_returns_2_for_missing_candidate_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,candidate_csv\n"
        "Sample_A,C:/stale/Sample_A.raw,missing/discovery_candidates.csv\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "missing" in stderr
    assert "discovery_candidates.csv" in stderr


def test_pyproject_registers_alignment_cli_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["xic-align-cli"] == "scripts.run_alignment:main"
    )
