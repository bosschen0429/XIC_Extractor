from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.alignment_pipeline_helpers import write_batch
from tests.discovery_worker_fixtures import (
    _alignment_outputs,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.applications.untargeted.full_analysis import (
    FullAnalysisRequest,
    UntargetedAnalysis,
)
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)
from xic_extractor.presets import PresetError, load_preset
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.untargeted import parse_request


def _execute(request: FullAnalysisRequest) -> dict[str, Any]:
    return UntargetedAnalysis(
        request, progress=lambda *_args: None, should_cancel=lambda: False
    ).execute()


def _full_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **fields: Any,
) -> tuple[FullAnalysisRequest, dict[str, Any]]:
    """A one-RAW complete analysis whose Discovery stage is faked."""
    import xic_extractor.applications.untargeted.full_analysis as module

    index_path = write_batch(tmp_path, ("A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    raw_path = raw_dir / "A.raw"
    raw_path.write_bytes(b"raw")
    captured_discovery: dict[str, Any] = {}

    def fake_run_discovery_batch(run_spec: Any, **kwargs: Any) -> SimpleNamespace:
        captured_discovery.update(kwargs, run_spec=run_spec)
        return SimpleNamespace(store=index_path)

    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda _raw_dir, _dll_dir, samples, **_kwargs: SimpleNamespace(
            raw_paths=(raw_path,),
            sample_metadata=samples
            or (SampleMetadata("A", "A", sample_role="sample"),),
        ),
    )
    monkeypatch.setattr(module, "run_discovery_batch", fake_run_discovery_batch)
    values: dict[str, Any] = {
        "preset": "dna_dr",
        "tuning_overrides": {},
        "raw_dir": raw_dir,
        "dll_dir": tmp_path / "dll",
        "output_dir": tmp_path / "out",
        "execution_profile": ExecutionProfile(parallel_workers=3, alignment_workers=2),
    }
    values.update(fields)
    return FullAnalysisRequest(**values), captured_discovery


def test_full_analysis_applies_default_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as module

    output_dir = tmp_path / "out"
    request, captured_discovery = _full_request(
        tmp_path,
        monkeypatch,
        tuning_overrides={
            "nl_tolerance_ppm": 15.0,
            "nl_min_intensity_ratio": 0.02,
        },
    )
    captured_alignment: dict[str, Any] = {}

    def fake_run_alignment(request, context=None) -> _AlignmentRunOutputs:
        captured_alignment.update(request=request, context=context)
        return _alignment_outputs(
            matrix_tsv=output_dir / "alignment_matrix.tsv",
        )

    monkeypatch.setattr(module, "run_untargeted_application", fake_run_alignment)
    summary = _execute(request)

    settings = captured_alignment["request"].discovery_settings
    assert settings.nl_tolerance_ppm == 15.0
    assert settings.nl_min_intensity_ratio == 0.02
    assert captured_alignment["request"].alignment_config == AlignmentConfig()
    assert captured_alignment["request"].execution.profile == request.execution_profile
    assert captured_alignment["request"].publication.output_level == "machine"
    assert captured_alignment["request"].publication.emit_alignment_cells is False
    assert captured_alignment["request"].publication.emit_alignment_explanation is True
    should_cancel = captured_alignment["context"].should_cancel
    assert callable(should_cancel) and should_cancel() is False
    assert summary["alignment_outputs"] == {
        "matrix_tsv": str(output_dir / "alignment_matrix.tsv"),
    }
    assert captured_discovery["execution"] == request.execution_profile
    run_spec = captured_discovery["run_spec"]
    assert run_spec.settings == settings
    assert run_spec.acquisition_inputs[0].raw_path.stem == "A"


def test_alignment_interruption_propagates_from_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as module

    request, _discovery = _full_request(tmp_path, monkeypatch)
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
    import xic_extractor.applications.untargeted.full_analysis as module

    preset_path = tmp_path / "custom.toml"
    preset_path.write_text(
        'name = "Custom"\n'
        'description = "Bound before execution"\n'
        'combine_mode = "single"\n\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "TEST"\nvalue = 100.0\n',
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **kwargs: (
            captured.update(request=request, context=context) or _alignment_outputs()
        ),
    )
    request, _discovery = _full_request(
        tmp_path,
        monkeypatch,
        preset=str(preset_path),
        preset_snapshot=load_preset(preset_path),
    )
    preset_path.unlink()

    _execute(request)

    assert "request" in captured


def test_request_rejects_retired_standard_peak_route_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as module

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
        UntargetedAnalysis(
            parse_request(
                {
                    "preset": str(preset_path),
                    "raw_dir": str(tmp_path / "raw"),
                    "dll_dir": str(tmp_path / "dll"),
                    "output_dir": str(tmp_path / "out"),
                }
            ),
            progress=lambda *_: None,
            should_cancel=lambda: False,
        )


def test_execute_forwards_sample_metadata_and_targets_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.untargeted.full_analysis as module

    targets_csv = tmp_path / "targets.csv"
    targets_csv.touch()
    sample_metadata = (
        SampleMetadata(
            sample_name="A",
            raw_stem="A",
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
    request, _discovery = _full_request(
        tmp_path,
        monkeypatch,
        targets_csv=targets_csv,
        sample_metadata=sample_metadata,
    )
    _execute(request)

    assert captured["request"].targets_csv != targets_csv
    assert captured["request"].targets_csv.read_bytes() == targets_csv.read_bytes()
    assert captured["request"].sample_metadata == sample_metadata
