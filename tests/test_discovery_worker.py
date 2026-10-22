from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from tests.alignment_pipeline_helpers import write_batch
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_retained_untargeted_cid_nl_route_receipt,
)
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)
from xic_extractor.presets import PresetError, load_preset
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.untargeted import DiscoveryRequest, UntargetedRun, parse_request


def _alignment_outputs(
    *,
    configured_backend: Literal["raw", "raw_superwindow", "ms1_index"] = "raw",
    **kwargs: Any,
) -> _AlignmentRunOutputs:
    return _AlignmentRunOutputs(
        **kwargs,
        owner_build_route_receipt=_mint_retained_untargeted_cid_nl_route_receipt(
            configured_backend=configured_backend,
        ),
    )


def _execute(
    request: DiscoveryRequest,
    *,
    progress: list[tuple[int, int, str]] | None = None,
) -> dict[str, Any]:
    return UntargetedRun(
        request,
        progress=(
            (
                lambda completed, total, message: progress.append(
                    (completed, total, message)
                )
            )
            if progress is not None
            else lambda *_args: None
        ),
        should_cancel=lambda: False,
    ).execute()


def test_align_only_summary_uses_existing_batch_index_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

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
    raw_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **_kwargs: _alignment_outputs(
            matrix_tsv=output_dir / "alignment_matrix.tsv"
        ),
    )

    summary = _execute(
        DiscoveryRequest(
            mode="align_only",
            preset="dna_dr",
            tuning_overrides={},
            raw_dir=raw_dir,
            raw_file=None,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            discovery_batch_index=index_path,
        )
    )

    assert summary["sample_count"] == 2
    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 3,
        "LOW": 3,
        "total": 7,
    }
    assert summary["discovery_batch_index"] == str(index_path)


def test_single_file_discovery_only_summary_and_run_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

    raw_file = tmp_path / "Sample_A.raw"
    raw_file.write_bytes(b"raw")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    candidates_csv = output_dir / "discovery_candidates.csv"
    review_csv = output_dir / "discovery_review.csv"
    candidates_csv.write_text("candidate_id\nc1\nc2\nc3\nc4\n", encoding="utf-8")
    review_csv.write_text(
        "review_priority,candidate_id\nHIGH,c1\nMEDIUM,c2\nLOW,c3\nLOW,c4\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def fake_run_discovery(*_args: object, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(candidates_csv=candidates_csv, review_csv=review_csv)

    monkeypatch.setattr(module, "run_discovery", fake_run_discovery)
    summary = _execute(
        DiscoveryRequest(
            mode="discovery_only",
            preset="dna_dr",
            tuning_overrides={},
            raw_dir=None,
            raw_file=raw_file,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            discovery_batch_index=None,
        )
    )

    assert summary["candidate_counts"] == {
        "HIGH": 1,
        "MEDIUM": 1,
        "LOW": 2,
        "total": 4,
    }
    assert summary["published_paths"] == [str(candidates_csv), str(review_csv)]
    run_spec = captured["run_spec"]
    settings = captured["settings"]
    assert captured["dll_dir"] == tmp_path / "dll"
    assert run_spec.discovery_method.settings == settings
    assert not settings.enable_blank_qualification
    assert run_spec.acquisition_inputs[0].blank_library_sha256 is None
    assert run_spec.acquisition_inputs[0].sample_stem == raw_file.stem
    assert run_spec.signal_processing_profile.window_points == 9


@pytest.mark.parametrize("mode", ["align_only", "full"])
def test_alignment_modes_apply_product_ready_runtime(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text(
        "sample,high_count,medium_count,low_count,candidate_count\nA.raw,1,0,0,1\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "out"
    raw_dir.mkdir()
    output_dir.mkdir()
    raw_path = raw_dir / "A.raw"
    raw_path.write_bytes(b"raw")
    captured_alignment: dict[str, Any] = {}
    captured_discovery: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(raw_paths=(raw_path,)),
    )

    def fake_run_alignment(request, context=None) -> _AlignmentRunOutputs:
        captured_alignment.update(request=request, context=context)
        return _alignment_outputs(
            configured_backend=request.legacy.owner_build_xic_backend,
            matrix_tsv=output_dir / "alignment_matrix.tsv",
            matrix_identity_tsv=output_dir / "alignment_matrix_identity.tsv",
        )

    def fake_run_discovery_batch(*_args: object, **kwargs: Any) -> SimpleNamespace:
        captured_discovery.update(kwargs)
        return SimpleNamespace(batch_index_csv=index_path, per_sample=())

    monkeypatch.setattr(module, "run_untargeted_application", fake_run_alignment)
    monkeypatch.setattr(module, "run_discovery_batch", fake_run_discovery_batch)
    request = DiscoveryRequest(
        mode=mode,  # type: ignore[arg-type]
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
    progress: list[tuple[int, int, str]] = []

    summary = _execute(request, progress=progress)

    settings = captured_alignment["request"].discovery_settings
    assert settings.nl_tolerance_ppm == 15.0
    peak_config = captured_alignment["request"].peak_config
    assert isinstance(peak_config, ExtractionConfig)
    assert peak_config.nl_min_intensity_ratio == 0.02
    assert peak_config.resolver_mode == "savgol"
    assert captured_alignment["request"].alignment_config == AlignmentConfig()
    assert (
        captured_alignment["request"].execution.raw_workers == DEFAULT_ALIGNMENT_WORKERS
    )
    assert captured_alignment["request"].execution.raw_worker_backend == "process"
    assert (
        captured_alignment["request"].legacy.owner_build_xic_backend
        == "raw_superwindow"
    )
    assert captured_alignment["request"].publication.output_level == "machine"
    assert captured_alignment["request"].publication.emit_alignment_cells is False
    assert (
        captured_alignment["request"].publication.emit_alignment_backfill_seed_audit
        is False
    )
    assert captured_alignment["request"].legacy.audit_evidence_mode == "none"
    assert captured_alignment["request"].publication.emit_cid_nl_explanation is True
    assert captured_alignment["request"].publication.explanation_full_audit is False
    assert captured_alignment["request"].publication.explanation_curve_mz_ranges == ()
    should_cancel = captured_alignment["context"].should_cancel
    assert callable(should_cancel) and should_cancel() is False
    assert summary["alignment_outputs"] == {
        "matrix_tsv": str(output_dir / "alignment_matrix.tsv"),
        "matrix_identity_tsv": str(output_dir / "alignment_matrix_identity.tsv"),
    }
    assert progress == (
        [(0, 2, "Discovery..."), (1, 2, "Alignment..."), (2, 2, "完成")]
        if mode == "full"
        else [(0, 1, "Alignment..."), (1, 1, "完成")]
    )
    if mode == "full":
        assert captured_discovery["max_workers"] == 1
        run_spec = captured_discovery["run_spec"]
        assert run_spec.discovery_method.settings is settings
        assert run_spec.acquisition_inputs[0].sample_stem == "A"
    else:
        assert captured_discovery == {}
    assert summary["owner_build_route"] == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }


def test_alignment_interruption_propagates_from_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

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
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **_kwargs: (_ for _ in ()).throw(
            InterruptedError("Alignment cancelled before output publication")
        ),
    )

    with pytest.raises(InterruptedError, match="cancelled"):
        _execute(request)


def test_full_uses_preset_snapshot_after_source_is_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

    preset_path = tmp_path / "custom.toml"
    preset_path.write_text(
        'name = "Custom"\n'
        'description = "Bound before execution"\n'
        'combine_mode = "single"\n\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "TEST"\nvalue = 100.0\n\n'
        '[alignment]\nowner_build_xic_backend = "ms1-index"\n',
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    index = write_batch(tmp_path, ("A",))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **kwargs: (
            captured.update(request=request, context=context)
            or _alignment_outputs(configured_backend="ms1_index")
        ),
    )
    request = DiscoveryRequest(
        mode="align_only",
        preset=str(preset_path),
        preset_snapshot=load_preset(preset_path),
        tuning_overrides={},
        raw_dir=raw_dir,
        raw_file=None,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        discovery_batch_index=index,
    )
    preset_path.unlink()

    _execute(request)

    assert captured["request"].legacy.owner_build_xic_backend == "ms1_index"


def test_request_rejects_retired_standard_peak_route_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

    preset_path = tmp_path / "backfill-expansion.toml"
    preset_path.write_text(
        'name = "Backfill expansion"\n'
        'description = "Unsupported post-stage"\n'
        'combine_mode = "single"\n\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "TEST"\nvalue = 100.0\n\n'
        "[discovery]\n\n[alignment]\nstandard_peak_backfill = true\n"
        'backfill_expansion_productization = "clean-target-selective"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "run_discovery_batch",
        lambda *_args, **_kwargs: pytest.fail("incompatible preset launched Discovery"),
    )

    with pytest.raises(PresetError, match="diagnostic-only"):
        parse_request(
            {
                "mode": "full",
                "preset": str(preset_path),
                "raw_dir": str(tmp_path / "raw"),
                "dll_dir": str(tmp_path / "dll"),
                "output_dir": str(tmp_path / "out"),
            }
        )


def test_execute_forwards_sample_metadata_and_targets_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_workbench.untargeted as module

    index_path = tmp_path / "discovery_batch_index.csv"
    index_path.write_text("sample,candidate_count\nS1.raw,1\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
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
    captured: dict[str, Any] = {}

    def fake_run_alignment(request, context=None) -> _AlignmentRunOutputs:
        captured.update(request=request, context=context)
        return _alignment_outputs(matrix_tsv=tmp_path / "out" / "alignment_matrix.tsv")

    monkeypatch.setattr(module, "run_untargeted_application", fake_run_alignment)
    _execute(
        DiscoveryRequest(
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
    )

    assert captured["request"].targets_csv != targets_csv
    assert captured["request"].targets_csv.read_bytes() == targets_csv.read_bytes()
    assert captured["request"].sample_metadata == sample_metadata
