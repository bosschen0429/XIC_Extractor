import csv
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import run_alignment
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_retained_untargeted_cid_nl_route_receipt,
)
from xic_extractor.alignment.pipeline import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)
from xic_extractor.alignment.process_execution import AlignmentProcessExecutionError
from xic_extractor.diagnostics.process_memory import ProcessMemoryPeak
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
        owner_build_route_receipt=_mint_retained_untargeted_cid_nl_route_receipt(
            configured_backend=_owner_build_xic_backend,
        ),
    )


@pytest.mark.parametrize("output_level", ("production", "machine"))
def test_run_alignment_cli_rejects_nondefault_profile_before_acquisition_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    output_level: str,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fail_alignment(**_kwargs):
        pytest.fail("unsupported signal profile reached acquisition")

    monkeypatch.setattr(run_alignment, "run_alignment", fail_alignment)
    code = run_alignment.main(
        [
            "--preset",
            "dna_dr",
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--output-level",
            output_level,
            "--signal-processing-window-points",
            "21",
        ]
    )

    assert code == 2
    assert "signal_processing_profile" in capsys.readouterr().err
    assert not output_dir.exists()


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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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


@pytest.mark.parametrize("raw_workers", [1, 2])
def test_run_alignment_cli_accepts_output_level_debug(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_workers: int,
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
            "--preset",
            "dna_dr",
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-level",
            "debug",
            "--raw-workers",
            str(raw_workers),
        ],
    )

    assert code == 0
    assert captured["output_level"] == "debug"
    assert captured["raw_workers"] == raw_workers


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
            "--preset",
            "dna_dr",
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


def test_run_alignment_cli_dna_dr_preset_binds_retained_recipe(
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
    bound_recipes: list[object] = []
    real_bind = run_alignment.bind_cid_nl_recipe

    def bind_once(*args, **kwargs):
        recipe = real_bind(*args, **kwargs)
        bound_recipes.append(recipe)
        return recipe

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

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)
    monkeypatch.setattr(run_alignment, "bind_cid_nl_recipe", bind_once)

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
    assert len(bound_recipes) == 1
    recipe = bound_recipes[0]
    assert captured_alignment["discovery_settings"] is recipe.discovery_settings


def test_run_alignment_cli_binds_pure_preset_peak_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    preset_path = tmp_path / "custom_pure_cid_nl.toml"
    preset_path.write_text(
        """
name = "Custom pure CID-NL"
description = "Custom pure CID-NL settings"
combine_mode = "single"

[[tag]]
strategy = "neutral_loss"
name = "NL116"
value = 116.0474

[discovery]
resolver_mode = "legacy_savgol"
nl_min_intensity_ratio = 0.37
""".strip(),
        encoding="utf-8",
    )
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured: dict[str, object] = {}

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
            "--output-dir",
            str(tmp_path / "alignment"),
            "--preset",
            str(preset_path),
        ]
    )

    assert code == 0
    settings = captured["discovery_settings"]
    peak_config = captured["peak_config"]
    assert peak_config.resolver_mode == settings.resolver_mode == "legacy_savgol"
    assert (
        peak_config.nl_min_intensity_ratio
        == settings.nl_min_intensity_ratio
        == pytest.approx(0.37)
    )


def test_run_alignment_cli_compat_preset_reports_retained_route(
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
    stdout = capsys.readouterr().out
    assert "Backfill expansion productization summary JSON:" not in stdout
    assert "Product-ready preset publication summary JSON:" not in stdout
    assert "Configured owner-build XIC backend: raw_superwindow" in stdout
    assert "Effective owner-build route: retained_untargeted_cid_nl_batch" in stdout
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
            "--preset",
            "dna_dr_product_ready",
            "--owner-build-xic-backend",
            "raw",
        ]
    )

    assert code == 0
    assert captured_alignment["owner_build_xic_backend"] == "raw"


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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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


def test_run_alignment_cli_records_foreground_memory_and_stops_sampler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    timing_path = tmp_path / "timing.json"
    sampler = _FakeCliMemorySampler()
    monkeypatch.setattr(run_alignment, "ProcessTreeMemorySampler", lambda: sampler)

    def fake_run_alignment(**_kwargs):
        assert sampler.running
        sampler.peak = (321, 123)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_alignment", fake_run_alignment)

    code = run_alignment.main(
        [
            "--preset",
            "dna_dr",
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--timing-output",
            str(timing_path),
        ]
    )

    assert code == 0
    assert (sampler.start_count, sampler.stop_count) == (1, 1)
    observations = json.loads(timing_path.read_text())["measurement_observations"]
    assert {
        row["metric"]: row["value"]
        for row in observations
        if row["scope"] == "foreground"
    } == {
        "process_tree_sampled_peak_rss_bytes": 321,
        "process_tree_sampled_peak_private_bytes": 123,
    }


def test_run_alignment_cli_stops_memory_sampler_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sampler = _FakeCliMemorySampler()
    monkeypatch.setattr(run_alignment, "ProcessTreeMemorySampler", lambda: sampler)

    def fail_run_alignment(**_kwargs):
        assert sampler.running
        raise ValueError("failed in foreground")

    monkeypatch.setattr(run_alignment, "run_alignment", fail_run_alignment)

    code = run_alignment.main(
        [
            "--preset",
            "dna_dr",
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--timing-live-output",
            str(tmp_path / "timing.live.json"),
        ]
    )

    assert code == 2
    assert (sampler.start_count, sampler.stop_count) == (1, 1)


class _FakeCliMemorySampler:
    def __init__(self) -> None:
        self.running = False
        self.start_count = 0
        self.stop_count = 0
        self.peak = (1, 1)

    def start(self) -> bool:
        self.running = True
        self.start_count += 1
        return True

    def stop(self) -> ProcessMemoryPeak:
        self.running = False
        self.stop_count += 1
        return ProcessMemoryPeak(
            rss_bytes=self.peak[0],
            private_bytes=self.peak[1],
            availability="measured",
        )


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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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


@pytest.mark.parametrize(
    "reference_kind", [None, "valid", "missing", "schema", "corrupt"]
)
def test_run_alignment_cli_preflight_only_prints_launch_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reference_kind: str | None,
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

    reference_args = []
    reference_path = tmp_path / "reference.xlsx"
    if reference_kind is not None:
        from tests.test_alignment_drift_evidence import _known_reference_workbook

        reference_args = ["--known-istd-reference-workbook", str(reference_path)]
        if reference_kind in {"valid", "schema"}:
            workbook = _known_reference_workbook(reference_path)
            if reference_kind == "schema":
                del workbook["Targets"]
                workbook.save(reference_path)
            workbook.close()
        elif reference_kind == "corrupt":
            reference_path.write_bytes(b"not an Excel workbook")

    code = run_alignment.main(
        [
            "--preset",
            "dna_dr",
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
            "--expected-sample-count",
            "2",
            "--timing-output",
            str(output_dir / "timing.json"),
            "--timing-live-output",
            str(output_dir / "timing.live.json"),
            "--preflight-only",
            *reference_args,
        ]
    )

    if reference_kind in {"missing", "schema", "corrupt"}:
        assert code == 2
        captured = capsys.readouterr()
        assert "preflight OK" not in captured.out
        assert captured.err
        return
    assert code == 0
    stdout = capsys.readouterr().out
    if reference_kind == "valid":
        import hashlib

        assert f"Known ISTD reference workbook: {reference_path.resolve()}" in stdout
        assert hashlib.sha256(reference_path.read_bytes()).hexdigest() in stdout
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
    assert "Effective owner-build route: <not executed>" in stdout
    assert "Effective route validation: not_executed" in stdout
    assert "owner_validated" not in stdout
    assert "FULL COHORT canonical contract: not requested" in stdout
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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


def test_run_alignment_cli_rejects_noncanonical_full_cohort_contract(
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
            "--preset",
            "dna_dr",
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
    assert "FULL COHORT canonical launch contract failed" in stderr
    assert "--output-level must be 'validation-minimal'" in stderr
    assert "--timing-live-output is required" in stderr


def test_run_alignment_cli_rejects_full_cohort_wrong_python_environment(
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
            "--preset",
            "dna_dr",
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


@pytest.mark.parametrize(
    "exc",
    [
        RawReaderError("raw fail"),
        ValueError("bad input"),
        AlignmentProcessExecutionError("retained runtime failed"),
    ],
)
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
            "--preset",
            "dna_dr",
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
            "--preset",
            "dna_dr",
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
