from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from gui.workers import discovery_worker as module
from gui.workers.discovery_worker import DiscoveryRequest, DiscoveryWorker
from tests.alignment_pipeline_helpers import write_batch
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_retained_untargeted_cid_nl_route_receipt,
)
from xic_extractor.applications.untargeted import application as untargeted_application
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.pipeline import (
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_discovery_worker_emits_error_for_failed_batch(tmp_path, monkeypatch):
    request = DiscoveryRequest(
        mode="discovery_only",
        preset="dna_dr_product_ready",
        tuning_overrides={},
        raw_dir=tmp_path,
        raw_file=None,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        discovery_batch_index=None,
    )
    worker = DiscoveryWorker(request)
    errors, finished, cancelled = [], [], []
    worker.error.connect(errors.append)
    worker.finished.connect(finished.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    failure = DiscoveryProcessExecutionError(
        DiscoverySampleProcessError(0, "sample", "sample.raw", "read failed"),
        completed_sample_stems=(),
        failed_sample_stems=("sample",),
        cancelled_job_count=0,
        in_flight_job_count=0,
        unsubmitted_job_count=1,
    )

    def fail(_request):
        raise failure

    monkeypatch.setattr(worker, "_run_discovery_only", fail)
    worker.run()
    assert errors == [str(failure)]
    assert not finished and not cancelled


def _alignment_outputs(
    *,
    configured_backend: str = "raw",
    **kwargs: object,
) -> _AlignmentRunOutputs:
    return _AlignmentRunOutputs(
        **kwargs,
        owner_build_route_receipt=_mint_retained_untargeted_cid_nl_route_receipt(
            configured_backend=configured_backend,
        ),
    )


def test_align_only_summary_uses_existing_batch_index_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "\n".join(
            [
                "sample,high_count,medium_count,low_count,candidate_count",
                "A.raw,1,2,3,6",
                "B.raw,0,1,0,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    preset_path = tmp_path / "preset.toml"
    preset_path.write_text(
        "\n".join(
            [
                'name = "Test"',
                'description = "No alignment publication preset"',
                'combine_mode = "single"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "TEST"',
                "value = 1.0",
                "",
                "[discovery]",
                "",
                "[alignment]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir.mkdir()
    output_dir.mkdir()

    def fake_run_alignment(**_kwargs):
        return _alignment_outputs(matrix_tsv=output_dir / "alignment_matrix.tsv")

    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        create_autospec(
            module.run_untargeted_application, side_effect=fake_run_alignment
        ),
    )
    request = DiscoveryRequest(
        mode="align_only",
        preset=str(preset_path),
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=index_path,
    )
    worker = DiscoveryWorker(request)


    summary = worker._run_align_only(request)

    assert summary is not None
    assert summary["sample_count"] == 2
    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 3,
        "LOW": 3,
        "total": 7,
    }
    assert summary["discovery_batch_index"] == str(index_path)


def test_single_file_discovery_only_summary_counts_review_priorities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_file = tmp_path / "Sample_A.raw"
    raw_file.write_bytes(b"raw")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    candidates_csv = output_dir / "discovery_candidates.csv"
    review_csv = output_dir / "discovery_review.csv"
    candidates_csv.write_text("candidate_id\nc1\nc2\nc3\nc4\n", encoding="utf-8")
    review_csv.write_text(
        "\n".join(
            [
                "review_priority,candidate_id",
                "HIGH,c1",
                "MEDIUM,c2",
                "LOW,c3",
                "LOW,c4",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    captured_discovery: dict[str, object] = {}

    def fake_run_discovery(*_args, **_kwargs):
        captured_discovery.update(_kwargs)
        return SimpleNamespace(
            candidates_csv=candidates_csv,
            review_csv=review_csv,
        )

    monkeypatch.setattr(module, "run_discovery", fake_run_discovery)
    request = DiscoveryRequest(
        mode="discovery_only",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=None,
        raw_file=raw_file,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=None,
    )
    worker = DiscoveryWorker(request)

    summary = worker._run_discovery_only(request)

    assert summary is not None
    assert summary["sample_count"] == 1
    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 1,
        "LOW": 2,
        "total": 4,
    }
    assert summary["discovery_candidates_csv"] == str(candidates_csv)
    assert summary["discovery_review_csv"] == str(review_csv)
    assert summary["discovery_batch_index"] is None
    run_spec = captured_discovery["run_spec"]
    settings = captured_discovery["settings"]
    peak_config = captured_discovery["peak_config"]
    assert run_spec.discovery_method.settings == settings
    assert not settings.enable_blank_qualification
    assert run_spec.acquisition_inputs[0].blank_library_sha256 is None
    assert run_spec.acquisition_inputs[0].sample_stem == raw_file.stem
    assert (
        run_spec.signal_processing_profile.window_points
        == peak_config.signal_processing_window_points
        == 15
    )


def test_gui_batch_discovery_uses_bounded_parallel_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    raw_dir.mkdir()
    output_dir.mkdir()
    raw_paths = tuple(raw_dir / f"Sample_{index}.raw" for index in range(8))
    for index, raw_path in enumerate(raw_paths):
        raw_path.write_bytes(f"raw-{index}".encode("utf-8"))
    index_path = output_dir / "discovery_batch_index.csv"
    index_path.write_text(
        "sample,high_count,medium_count,low_count,candidate_count\n"
        + "\n".join(f"{raw_path.name},0,0,0,0" for raw_path in raw_paths)
        + "\n",
        encoding="utf-8",
    )
    captured_discovery: dict[str, object] = {}

    def fake_run_discovery_batch(*_args, **kwargs):
        captured_discovery.update(kwargs)
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(module, "run_discovery_batch", fake_run_discovery_batch)
    from xic_extractor.configuration.execution_profile import ExecutionProfile

    request = DiscoveryRequest(
        mode="discovery_only",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=None,
        execution_profile=ExecutionProfile(parallel_workers=2),
    )

    summary = DiscoveryWorker(request)._run_discovery_only(request)

    assert summary is not None
    assert summary["sample_count"] == 8
    assert captured_discovery["max_workers"] == 2


@pytest.mark.parametrize("mode", ["align_only", "full"])
def test_gui_alignment_modes_apply_product_ready_preset_alignment_runtime(
    mode: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "\n".join(
            [
                "sample,high_count,medium_count,low_count,candidate_count",
                "A.raw,1,0,0,1",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    raw_dir.mkdir()
    output_dir.mkdir()
    (raw_dir / "A.raw").write_bytes(b"raw")
    captured_alignment: dict[str, object] = {}
    captured_discovery_batch: dict[str, object] = {}
    bound_recipes: list[object] = []
    real_bind = module.bind_cid_nl_recipe

    def bind_once(*args, **kwargs):
        recipe = real_bind(*args, **kwargs)
        bound_recipes.append(recipe)
        return recipe

    def fake_run_alignment(**kwargs):
        captured_alignment.update(kwargs)
        return _alignment_outputs(
            configured_backend=kwargs["owner_build_xic_backend"],
            matrix_tsv=output_dir / "alignment_matrix.tsv",
            matrix_identity_tsv=output_dir / "alignment_matrix_identity.tsv",
        )

    def fake_run_discovery_batch(*_args, **kwargs):
        captured_discovery_batch.update(kwargs)
        return SimpleNamespace(
            batch_index_csv=index_path,
        )

    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        create_autospec(
            module.run_untargeted_application, side_effect=fake_run_alignment
        ),
    )
    monkeypatch.setattr(module, "run_discovery_batch", fake_run_discovery_batch)
    monkeypatch.setattr(module, "bind_cid_nl_recipe", bind_once)
    request = DiscoveryRequest(
        mode=mode,
        preset="dna_dr_product_ready",
        tuning_overrides={
            "nl_tolerance_ppm": 15.0,
            "nl_min_intensity_ratio": 0.02,
            "resolver_mode": "savgol",
        },
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=index_path if mode == "align_only" else None,
    )
    worker = DiscoveryWorker(request)

    progress_events: list[tuple[int, int, str]] = []
    worker.progress.connect(
        lambda completed, total, message: progress_events.append(
            (completed, total, message)
        )
    )

    if mode == "align_only":
        summary = worker._run_align_only(request)
    else:
        summary = worker._run_full(request)

    assert summary is not None
    assert len(bound_recipes) == 1
    recipe = bound_recipes[0]
    assert recipe.discovery_settings.nl_tolerance_ppm == 15.0
    assert captured_alignment["discovery_settings"] is recipe.discovery_settings
    peak_config = captured_alignment["peak_config"]
    assert isinstance(peak_config, ExtractionConfig)
    assert peak_config.nl_min_intensity_ratio == 0.02
    assert peak_config.resolver_mode == "savgol"
    assert captured_alignment["alignment_config"] == AlignmentConfig()
    assert captured_alignment["raw_workers"] == DEFAULT_ALIGNMENT_WORKERS
    assert captured_alignment["raw_worker_backend"] == "process"
    assert captured_alignment["owner_build_xic_backend"] == "raw_superwindow"
    assert captured_alignment["output_level"] == "machine"
    assert captured_alignment["emit_alignment_cells"] is False
    assert captured_alignment["emit_alignment_backfill_seed_audit"] is False
    assert captured_alignment["audit_evidence_mode"] == "none"
    should_cancel = captured_alignment["should_cancel"]
    assert callable(should_cancel)
    assert should_cancel() is False
    assert summary["alignment_outputs"] == {
        "matrix_tsv": str(output_dir / "alignment_matrix.tsv"),
        "matrix_identity_tsv": str(output_dir / "alignment_matrix_identity.tsv"),
    }
    assert progress_events == (
        [(0, 2, "Discovery..."), (1, 2, "Alignment..."), (2, 2, "完成")]
        if mode == "full"
        else [(0, 1, "Alignment..."), (1, 1, "完成")]
    )
    if mode == "full":
        assert captured_discovery_batch["max_workers"] == 1
        run_spec = captured_discovery_batch["run_spec"]
        assert run_spec.discovery_method.settings is recipe.discovery_settings
        assert run_spec.acquisition_inputs[0].sample_stem == "A"
    else:
        assert captured_discovery_batch == {}
    assert summary["owner_build_route"] == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }
    assert not any(
        name.startswith("standard_peak_") for name in summary["alignment_outputs"]
    )


def test_discovery_worker_reports_alignment_interruption_as_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = DiscoveryRequest(
        mode="align_only",
        preset="dna_dr_product_ready",
        tuning_overrides={},
        raw_dir=tmp_path / "raw",
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=write_batch(tmp_path, ("A",)),
    )
    worker = DiscoveryWorker(request)
    cancelled: list[bool] = []
    errors: list[str] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.error.connect(errors.append)
    monkeypatch.setattr(
        untargeted_application,
        "complete_untargeted_alignment",
        lambda **_kwargs: (_ for _ in ()).throw(
            InterruptedError("Alignment cancelled before output publication")
        ),
    )

    worker.run()

    assert cancelled == [True]
    assert errors == []


def test_discovery_worker_has_no_automatic_gallery_referrer() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "gui.workers.backfill_gallery" not in source
    assert "build_backfill_review_gallery" not in source
    assert "_build_gallery" not in source


def test_gui_full_reuses_bound_custom_preset_after_discovery(
    tmp_path: Path,
) -> None:
    preset_path = tmp_path / "custom.toml"
    preset_path.write_text(
        "\n".join(
            [
                'name = "Custom"',
                'description = "Bind once before Discovery"',
                'combine_mode = "single"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "TEST"',
                "value = 100.0",
                "",
                "[alignment]",
                'owner_build_xic_backend = "ms1-index"',
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    request = DiscoveryRequest(
        mode="full",
        preset=str(preset_path),
        tuning_overrides={},
        raw_dir=tmp_path / "raw",
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=None,
    )
    worker = DiscoveryWorker(request)

    worker._build_settings(request)
    preset_path.unlink()
    recipe = worker._alignment_runtime(request)

    assert recipe.owner_build_xic_backend == "ms1_index"


def test_gui_full_rejects_retired_standard_peak_route_before_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    preset_path = tmp_path / "backfill-expansion.toml"
    raw_dir.mkdir()
    output_dir.mkdir()
    (raw_dir / "A.raw").write_bytes(b"raw")
    preset_path.write_text(
        "\n".join(
            [
                'name = "Backfill expansion"',
                'description = "Unsupported GUI post-stage"',
                'combine_mode = "single"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "TEST"',
                "value = 100.0",
                "",
                "[discovery]",
                "",
                "[alignment]",
                "standard_peak_backfill = true",
                'backfill_expansion_productization = "clean-target-selective"',
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    def reject_discovery(*_args, **_kwargs):
        raise AssertionError("incompatible preset launched Discovery")

    monkeypatch.setattr(module, "run_discovery_batch", reject_discovery)
    request = DiscoveryRequest(
        mode="full",
        preset=str(preset_path),
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=output_dir,
        discovery_batch_index=None,
    )

    with pytest.raises(
        module.PresetError,
        match="diagnostic-only",
    ):
        DiscoveryWorker(request)._run_full(request)


def test_discovery_worker_forwards_sample_metadata_and_targets_csv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text("sample,candidate_count\nS1.raw,1\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").touch()
    targets_csv = tmp_path / "targets.csv"
    targets_csv.touch()

    sample_metadata = (
        SampleMetadata(
            sample_name="S1",
            raw_stem="S1",
            injection_order=1,
            sample_role="qc",
            batch_id="Batch1",
        ),
    )

    captured_kwargs: dict[str, object] = {}

    def fake_run_alignment(**kwargs):
        captured_kwargs.update(kwargs)
        return _alignment_outputs(
            matrix_tsv=tmp_path / "out" / "alignment_matrix.tsv",
        )

    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        create_autospec(
            module.run_untargeted_application, side_effect=fake_run_alignment
        ),
    )

    request = DiscoveryRequest(
        mode="align_only",
        preset="dna_dr",
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=index_path,
        targets_csv=targets_csv,
        sample_metadata=sample_metadata,
    )

    worker = DiscoveryWorker(request)
    summary = worker._run_align_only(request)

    assert summary is not None
    assert captured_kwargs["targets_csv"] == targets_csv
    assert captured_kwargs["sample_metadata"] == sample_metadata
