import csv
import json
import os
import pstats
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import run_alignment
from tests.alignment_pipeline_helpers import (
    _owner_closed_receipt_fields,
    physical_facts_fingerprint_beside,
    write_csv,
)
from xic_extractor.applications.untargeted.application import (
    AlignmentProcessExecutionError,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_COLUMNS,
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    discovery_receipt_index_fields,
    mint_discovery_peak_universe_receipt,
)
from xic_extractor.discovery.models import DISCOVERY_PEAK_COLUMNS
from xic_extractor.execution.process_memory import ProcessMemoryPeak
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
)
from xic_extractor.processing_identity import build_local_acquisition_source_identity
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


def _write_empty_batch_index(path: Path) -> None:
    _write_owner_closed_batch_index(path, [])


def _write_owner_closed_batch_index(
    path: Path,
    rows: list[tuple[str, str, str]],
) -> None:
    batch_rows = []
    for sample_stem, raw_file, candidate_csv in rows:
        candidate_path = path.parent / (candidate_csv or "empty.csv")
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(candidate_path, DISCOVERY_PEAK_COLUMNS, [])
        batch_rows.append(
            {
                "sample_stem": sample_stem,
                "raw_file": raw_file,
                "peak_csv": candidate_csv,
                **_owner_closed_receipt_fields(sample_stem, candidate_path),
            }
        )
    write_csv(path, DISCOVERY_BATCH_INDEX_COLUMNS, batch_rows)


@pytest.mark.parametrize("output_level", ("production", "machine"))
def test_run_alignment_cli_rejects_nondefault_profile_before_acquisition_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    output_level: str,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fail_alignment(request, context=None):
        pytest.fail("unsupported signal profile reached acquisition")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_alignment)
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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured = {}

    def fake_run_alignment(request, context=None):
        captured.update(request=request, context=context)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        for path in (review, matrix):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            cells_tsv=None,
            status_matrix_tsv=None,
        )

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    assert captured["request"].peak_config.baseline_integration_method == "asls"


def test_run_alignment_rejects_retired_linear_edge_baseline_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"

    def fake_run_alignment(request, context=None):
        raise AssertionError("run_alignment should not be called")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fake_run_alignment(request, context=None):
        raise AssertionError("run_alignment should not be called")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)
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


@pytest.mark.parametrize("raw_workers", [1, 2])
def test_run_alignment_cli_accepts_output_level_debug(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_workers: int,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(request, context=None):
        captured.update(request=request, context=context)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    assert captured["request"].publication.output_level == "debug"
    assert captured["request"].execution.raw_workers == raw_workers
    assert captured["request"].execution.raw_worker_backend == "process"


def test_run_alignment_cli_rejects_retired_validation_output_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_alignment.main(
            [
                "--preset",
                "dna_dr",
                "--discovery-batch-index",
                str(tmp_path / "discovery_batch_index.csv"),
                "--raw-dir",
                str(tmp_path),
                "--dll-dir",
                str(tmp_path),
                "--output-level",
                "validation",
            ]
        )

    assert exc_info.value.code == 2
    assert "invalid choice: 'validation'" in capsys.readouterr().err


def test_run_alignment_cli_accepts_raw_worker_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(request, context=None):
        captured.update(request=request, context=context)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
            "--raw-workers",
            "2",
            "--raw-worker-backend",
            "process",
        ],
    )

    assert code == 0
    assert captured["request"].execution.raw_workers == 2
    assert captured["request"].execution.raw_worker_backend == "process"


def test_run_alignment_cli_accepts_validation_minimal_output_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured = {}

    def fake_run_alignment(request, context=None):
        captured.update(request=request, context=context)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    assert captured["request"].publication.output_level == "validation-minimal"


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_run_alignment_cli_dna_dr_preset_binds_retained_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backfill_enabled: bool,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
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

    def fake_run_alignment(request, context=None):
        captured_alignment.update(request=request, context=context)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        cells = output_dir / "alignment_cells.tsv"
        for path in (review, matrix, cells):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            cells_tsv=cells,
        )

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)
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
            "--backfill" if backfill_enabled else "--no-backfill",
        ]
    )

    assert code == 0
    assert captured_alignment["request"].publication.emit_alignment_cells is False
    assert len(bound_recipes) == 1
    assert (
        captured_alignment["request"].alignment_config.backfill_enabled
        is backfill_enabled
    )
    recipe = bound_recipes[0]
    assert captured_alignment["request"].discovery_settings is recipe.discovery_settings


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
resolver_mode = "savgol"
nl_min_intensity_ratio = 0.37
""".strip(),
        encoding="utf-8",
    )
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_run_alignment(request, context=None):
        captured.update(request=request, context=context)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    settings = captured["request"].discovery_settings
    peak_config = captured["request"].peak_config
    assert peak_config.resolver_mode == settings.resolver_mode == "savgol"
    assert (
        peak_config.nl_min_intensity_ratio
        == settings.nl_min_intensity_ratio
        == pytest.approx(0.37)
    )


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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_backfill_expansion: dict[str, object] = {}
    call_order: list[str] = []

    def fake_run_alignment(request, context=None):
        call_order.append("alignment")
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "review_tsv": output_dir / "alignment_review.tsv",
            "matrix_tsv": output_dir / "alignment_matrix.tsv",
            "backfill_cell_evidence_tsv": (
                output_dir / "alignment_backfill_cell_evidence.tsv"
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
                / "backfill_expansion_clean_target_selective_product_activation"
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

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "alignment"
    captured_alignment: dict[str, object] = {}

    def fake_run_alignment(request, context=None):
        captured_alignment.update(request=request, context=context)
        output_dir.mkdir(parents=True, exist_ok=True)
        review = output_dir / "alignment_review.tsv"
        matrix = output_dir / "alignment_matrix.tsv"
        light_cells = output_dir / "alignment_backfill_cell_evidence.tsv"
        for path in (review, matrix, light_cells):
            path.write_text("x\n", encoding="utf-8")
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            backfill_cell_evidence_tsv=light_cells,
        )

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    assert captured_alignment["request"].publication.emit_alignment_cells is False


def test_run_alignment_cli_writes_timing_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    timing_path = tmp_path / "diagnostics" / "alignment_timing.json"

    def fake_run_alignment(request, context=None):
        timing_recorder = context.timing_recorder
        with timing_recorder.stage(
            "alignment.read_candidates",
            metrics={"candidate_count": 0},
        ):
            pass
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    timing_path = tmp_path / "timing.json"
    sampler = _FakeCliMemorySampler()
    monkeypatch.setattr(run_alignment, "ProcessTreeMemorySampler", lambda: sampler)

    def fake_run_alignment(request, context=None):
        assert sampler.running
        sampler.peak = (321, 123)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    sampler = _FakeCliMemorySampler()
    monkeypatch.setattr(run_alignment, "ProcessTreeMemorySampler", lambda: sampler)

    def fail_run_alignment(request, context=None):
        assert sampler.running
        with context.timing_recorder.stage(
            "alignment.failed_runtime", metrics={"completed_partitions": 33}
        ):
            raise ValueError("failed in foreground")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_run_alignment)

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
            "--timing-output",
            str(tmp_path / "timing.json"),
        ]
    )

    assert code == 2
    assert (sampler.start_count, sampler.stop_count) == (1, 1)
    payload = json.loads((tmp_path / "timing.json").read_text())
    assert payload["records"][0]["stage"] == "alignment.failed_runtime"
    assert payload["records"][0]["metrics"] == {"completed_partitions": 33}
    assert any(
        row["metric"] == "process_tree_sampled_peak_rss_bytes"
        for row in payload["measurement_observations"]
    )


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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    live_path = tmp_path / "diagnostics" / "alignment_live_timing.json"

    def fake_run_alignment(request, context=None):
        timing_recorder = context.timing_recorder
        timing_recorder.record("alignment.run_config", elapsed_sec=0.0)
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    assert payload["schema_version"] == "timing.live"
    assert payload["recent_records"][0]["stage"] == "alignment.run_config"
    assert "records" not in payload
    assert "Timing live JSON:" in capsys.readouterr().out


def test_run_alignment_cli_writes_cprofile_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    profile_dir = tmp_path / "profile"

    monkeypatch.setattr(
        run_alignment,
        "run_untargeted_application",
        lambda request, context=None, **kwargs: AlignmentRunOutputs(),
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
    _write_empty_batch_index(batch_index)
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
    _write_empty_batch_index(batch_index)
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
    _write_owner_closed_batch_index(
        batch_index, [("S1", "S1.raw", "s1.csv"), ("S2", "S2.raw", "s2.csv")]
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
    _write_owner_closed_batch_index(
        batch_index, [("S1", "S1.raw", "s1.csv"), ("S1", "S1.raw", "s1_again.csv")]
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
    _write_owner_closed_batch_index(batch_index, [("S1", "S1.raw", "s1.csv")])
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fake_run_alignment(request, context=None):
        return AlignmentRunOutputs()

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_run_alignment)

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
    fieldnames = DISCOVERY_BATCH_INDEX_COLUMNS
    rows: list[dict[str, str]] = []
    for sample_stem in sample_stems:
        raw_path = raw_dir / f"{sample_stem}.raw"
        raw_path.write_bytes(b"")
        candidate_csv = tmp_path / f"{sample_stem.lower()}.csv"
        candidate_csv.write_bytes(b"")
        acquisition_source = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        receipt = mint_discovery_peak_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=acquisition_source,
            peak_csv=candidate_csv,
            physical_facts=physical_facts_fingerprint_beside(candidate_csv),
            peaks=(),
        )
        rows.append(
            {
                "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
                "sample_stem": sample_stem,
                "raw_file": raw_path.name,
                "peak_csv": candidate_csv.name,
                "peak_count": "0",
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

    def fail_run_alignment(request, context=None):
        raise AssertionError("preflight-only should not run alignment")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_run_alignment)

    reference_args = []
    reference_path = tmp_path / "reference.xlsx"
    if reference_kind is not None:
        from tests.alignment_drift_evidence_fixtures import _known_reference_workbook

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
            "--raw-workers",
            "4",
            "--raw-worker-backend",
            "process",
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
    if reference_kind is None:
        expected = (
            run_alignment._repo_root()
            / "xic_extractor"
            / "configuration"
            / "data"
            / "targets.csv"
        )
        assert f"Targets CSV: {expected}" in stdout
        assert "7 ISTD loaded" in stdout
        assert "ISTD alignment: enabled (GUI default)" in stdout
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
    assert "Alignment worker budget: 4" in stdout
    assert "Input indexing / RAW backend: process" in stdout
    assert "Source fingerprint backend: thread" in stdout
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
    _write_owner_closed_batch_index(batch_index, [("S1", "S1.raw", "")])
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
    assert "peak_csv is required" in capsys.readouterr().err


def test_run_alignment_cli_preflight_only_requires_discovery_owner_closure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text(
        "sample_stem,raw_file,peak_csv\nS1,S1.raw,s1.csv\n",
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
    assert "regenerate Discovery" in capsys.readouterr().err


def test_run_alignment_cli_preflight_only_rejects_missing_candidate_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_owner_closed_batch_index(batch_index, [("S1", "S1.raw", "missing.csv")])
    (batch_index.parent / "missing.csv").unlink()
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
    assert "peak CSV does not exist" in stderr
    assert "missing.csv" in stderr


def test_run_alignment_cli_preflight_only_rejects_missing_raw_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_owner_closed_batch_index(batch_index, [("S1", "S1.raw", "s1.csv")])
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
    _write_owner_closed_batch_index(
        batch_index, [(f"S{i}", f"S{i}.raw", f"s{i}.csv") for i in range(85)]
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
            "--output-level",
            "debug",
        ]
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "FULL COHORT canonical launch contract failed" in stderr
    assert "--output-level must be 'production' or 'validation-minimal'" in stderr
    assert "--timing-live-output is required" in stderr


def test_run_alignment_cli_rejects_full_cohort_wrong_python_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_owner_closed_batch_index(
        batch_index, [(f"S{i}", f"S{i}.raw", f"s{i}.csv") for i in range(85)]
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
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    def fail_run_alignment(request, context=None):
        raise exc

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_run_alignment)

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
    _write_owner_closed_batch_index(
        batch_index,
        [("Sample_A", "C:/stale/Sample_A.raw", "missing/discovery_peaks.csv")],
    )
    (batch_index.parent / "missing/discovery_peaks.csv").unlink()
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
    assert "discovery_peaks.csv" in stderr


def test_pyproject_registers_alignment_cli_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["xic-align-cli"] == "scripts.run_alignment:main"
    )


def test_run_alignment_cli_targets_csv_and_workbook_mutually_exclusive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "batch.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    targets_csv = tmp_path / "targets.csv"
    targets_csv.touch()
    workbook = tmp_path / "ref.xlsx"
    workbook.touch()

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
            "--targets-csv",
            str(targets_csv),
            "--known-istd-reference-workbook",
            str(workbook),
        ]
    )
    assert code == 2
    assert "mutually exclusive" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode",
    [
        "uncorrected",
        "conflict_target",
        "conflict_workbook",
        "missing",
        "invalid",
        "explicit",
    ],
)
def test_run_alignment_cli_istd_input_is_explicit_or_validated_before_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    batch_index, raw_dir = _write_owner_closed_preflight_batch(tmp_path, ("S1",))
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    template = (
        run_alignment._repo_root()
        / "xic_extractor"
        / "configuration"
        / "data"
        / "targets.csv"
    )
    monkeypatch.setattr(
        run_alignment,
        "targets_template_path",
        lambda: tmp_path / "config" / "targets.example.csv",
    )

    def no_raw(request, context=None):
        pytest.fail("preflight or invalid ISTD input reached RAW execution")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", no_raw)
    extras = []
    if mode in {"uncorrected", "conflict_target", "conflict_workbook"}:
        extras = ["--allow-uncorrected"]
        if mode == "conflict_target":
            extras += ["--targets-csv", str(template)]
        elif mode == "conflict_workbook":
            extras += ["--known-istd-reference-workbook", str(tmp_path / "ref.xlsx")]
    elif mode == "invalid":
        config = tmp_path / "config"
        config.mkdir()
        (config / "targets.example.csv").write_text(
            "not,a,target,list\n",
            encoding="utf-8",
        )
    elif mode == "explicit":
        extras = ["--targets-csv", str(template)]
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
            *extras,
        ]
    )
    captured = capsys.readouterr()
    if mode == "uncorrected":
        assert code == 0
        assert "ISTD alignment: disabled (explicit --allow-uncorrected)" in captured.out
        assert "Targets CSV:" not in captured.out
    elif mode == "explicit":
        assert code == 0
        assert f"Targets CSV: {template}" in captured.out
        assert "ISTD alignment: enabled (explicit targets CSV)" in captured.out
    else:
        assert code == 2
        assert captured.err
        assert "preflight OK" not in captured.out
        if mode.startswith("conflict"):
            assert "mutually exclusive" in captured.err
        else:
            assert str(tmp_path / "config" / "targets.example.csv") in captured.err


def test_run_alignment_cli_targets_csv_must_exist(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch_index = tmp_path / "batch.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raw"
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
            "--targets-csv",
            str(tmp_path / "non_existent.csv"),
        ]
    )
    assert code == 2
    assert "targets CSV file does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("explicit_metadata", [True, False])
def test_run_alignment_cli_routes_sample_metadata_without_losing_cohorts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    explicit_metadata: bool,
) -> None:
    from tests.raw_header_fakes import install_fake_raw_headers
    from xic_extractor.sample_metadata import SampleMetadata

    install_fake_raw_headers(monkeypatch)
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_owner_closed_batch_index(
        batch_index, [("S1", "S1.raw", "S1.csv"), ("S2", "S2.raw", "S2.csv")]
    )
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    # The RAW clock runs S2 before S1, against the metadata numbers 12 and 3.
    for stem, seconds in (("S1", 600), ("S2", 0)):
        raw = raw_dir / f"{stem}.raw"
        raw.write_bytes(b"")
        os.utime(raw, (1_767_225_600 + seconds, 1_767_225_600 + seconds))
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    metadata = tmp_path / "sample_metadata.tsv"
    metadata.write_text(
        "schema_version\tsample_name\traw_stem\tinjection_order\tsample_role\t"
        "batch_id\tprep_batch_id\tmatrix_type\tgroup\texcluded\texclusion_reason\n"
        "sample_metadata\tQC one\tS1\t12\tpooled_qc\tbatch_A\t"
        "prep_A\ttissue\tA\tfalse\t\n"
        "sample_metadata\tStudy two\tS2\t3\tstudy_sample\tbatch_B\t"
        "prep_B\tplasma\tB\tfalse\t\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def fake_application(request, context=None) -> AlignmentRunOutputs:
        captured.update(request=request, context=context)
        return AlignmentRunOutputs(
            review_tsv=None,
            matrix_tsv=None,
            cells_tsv=None,
            status_matrix_tsv=None,
        )

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fake_application)
    argv = [
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
    if explicit_metadata:
        argv.extend(["--sample-metadata", str(metadata)])

    assert run_alignment.main(argv) == 0
    stdout = capsys.readouterr().out
    if explicit_metadata:
        # Names and order come from the RAW; the metadata adds the rest.
        assert captured["request"].sample_metadata == (
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=1,
                sample_role="sample",
                batch_id="batch_B",
                prep_batch_id="prep_B",
                matrix_type="plasma",
                group="B",
            ),
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=2,
                sample_role="qc",
                batch_id="batch_A",
                prep_batch_id="prep_A",
                matrix_type="tissue",
                group="A",
            ),
        )
        assert str(metadata.resolve()) in stdout
        assert "2 samples; 2 batches" in stdout
    else:
        assert captured.get("sample_metadata") is None
        assert "RAW-derived metadata" in stdout


@pytest.mark.parametrize("invalid_kind", ["role", "duplicate", "missing"])
def test_run_alignment_cli_rejects_invalid_sample_metadata_before_application(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid_kind: str,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    metadata = tmp_path / "sample_metadata.tsv"
    if invalid_kind != "missing":
        row = (
            "sample_metadata\tS1\tS1\t1\t"
            f"{'invalid' if invalid_kind == 'role' else 'sample'}"
            "\tbatch_A\t\t\t\tfalse\t\n"
        )
        metadata.write_text(
            "schema_version\tsample_name\traw_stem\tinjection_order\tsample_role\t"
            "batch_id\tprep_batch_id\tmatrix_type\tgroup\texcluded\texclusion_reason\n"
            + row
            + (row if invalid_kind == "duplicate" else ""),
            encoding="utf-8",
        )

    def fail_application(request, context=None) -> None:
        pytest.fail("invalid metadata reached RAW application")

    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_application)
    assert (
        run_alignment.main(
            [
                "--preset",
                "dna_dr",
                "--discovery-batch-index",
                str(batch_index),
                "--raw-dir",
                str(raw_dir),
                "--dll-dir",
                str(dll_dir),
                "--sample-metadata",
                str(metadata),
            ]
        )
        == 2
    )
    assert capsys.readouterr().err


@pytest.mark.parametrize("profile", ("off", "cprofile"))
def test_run_alignment_stop_after_grouping_writes_snapshot_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    profile: str,
) -> None:
    batch_index = tmp_path / "discovery_batch_index.csv"
    _write_empty_batch_index(batch_index)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    output_dir = tmp_path / "grouping"
    captured = {}

    def fake_snapshot(request, context=None):
        captured.update(request=request)
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = output_dir / "grouping-inputs.json.gz"
        snapshot.write_bytes(b"x")
        return snapshot

    def fail_alignment(request, context=None):
        pytest.fail("--stop-after grouping must not run Backfill or publication")

    monkeypatch.setattr(run_alignment, "snapshot_untargeted_grouping", fake_snapshot)
    monkeypatch.setattr(run_alignment, "run_untargeted_application", fail_alignment)

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
            "--stop-after",
            "grouping",
            "--profile",
            profile,
            "--profile-output-dir",
            str(tmp_path / "profile"),
        ]
    )

    assert code == 0
    assert captured["request"].peak_config.parallel_mode == "process"
    assert "Grouping snapshot:" in capsys.readouterr().out
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "grouping-inputs.json.gz"
    ]
    if profile == "cprofile":
        stats = pstats.Stats(str(tmp_path / "profile" / "profile.prof"))
        assert any(name == "fake_snapshot" for _, _, name in stats.stats)
        assert "fake_snapshot" in (tmp_path / "profile" / "profile_top.txt").read_text(
            encoding="utf-8"
        )
