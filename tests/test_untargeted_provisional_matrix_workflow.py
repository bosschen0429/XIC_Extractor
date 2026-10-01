from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.applications.untargeted import provisional_matrix as workflow
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    resolve_signal_processing_profile,
)
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs


@pytest.mark.parametrize("raw_workers", [1, 3])
def test_workflow_routes_complete_discovery_universe_to_full_alignment(
    tmp_path: Path,
    monkeypatch,
    raw_workers: int,
) -> None:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    raw_files = (raw_dir / "sample-a.raw", raw_dir / "sample-b.raw")
    for path in raw_files:
        path.write_bytes(b"raw")
    (dll_dir / "reader.dll").write_bytes(b"dll")
    discovery_index = tmp_path / "discovery_batch_index.csv"
    discovery_index.write_text("sample_stem\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_discovery(*args, **kwargs):
        captured["discovery_raw_files"] = args[0]
        captured["discovery"] = kwargs
        return SimpleNamespace(batch_index_csv=discovery_index)

    provisional_matrix = tmp_path / "provisional_alignment_matrix.tsv"
    physical_provenance = tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
    discovery_settings = DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0473),
        ),
    )

    def fake_alignment(**kwargs):
        captured["alignment"] = kwargs
        return AlignmentRunOutputs(
            owner_backfill_peak_fact_provenance_tsv=physical_provenance,
            provisional_matrix_tsv=provisional_matrix,
            provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
            provisional_cell_provenance_tsv=(
                tmp_path / "provisional_cell_provenance.tsv"
            ),
            provisional_summary_json=tmp_path / "provisional_matrix_summary.json",
        )

    monkeypatch.setattr(workflow, "run_discovery_batch", fake_discovery)
    monkeypatch.setattr(workflow, "run_untargeted_application", fake_alignment)

    timing_recorder = TimingRecorder.disabled("test")

    result = workflow.run_untargeted_provisional_matrix_workflow(
        raw_files=raw_files,
        dll_dir=dll_dir,
        output_dir=tmp_path / "run",
        discovery_settings=discovery_settings,
        alignment_config=AlignmentConfig(),
        raw_workers=raw_workers,
        raw_xic_batch_size=1,
        timing_recorder=timing_recorder,
        capture_alignment_artifact_fingerprints=True,
    )

    assert captured["discovery_raw_files"] == raw_files
    discovery = captured["discovery"]
    assert isinstance(discovery, dict)
    assert discovery["timing_recorder"] is timing_recorder
    assert discovery["max_workers"] == raw_workers
    alignment = captured["alignment"]
    assert isinstance(alignment, dict)
    assert alignment["discovery_batch_index"] == discovery_index
    assert alignment["discovery_settings"] is discovery_settings
    assert alignment["backfill_scope"] == "full-audit"
    assert alignment["selected_family_ids"] == frozenset()
    assert alignment["emit_provisional_matrix"] is True
    assert alignment["require_discovery_owner_closure"] is True
    assert alignment["raw_workers"] == raw_workers
    assert alignment["raw_xic_batch_size"] == 1
    assert alignment["capture_artifact_fingerprints"] is True
    assert "signal_processing_profile" not in alignment
    peak_config = alignment["peak_config"]
    resolved_profile = resolve_signal_processing_profile(
        signal_processing_method=getattr(
            peak_config,
            "signal_processing_method",
            DEFAULT_SIGNAL_PROCESSING_PROFILE.method,
        ),
        signal_processing_window_points=getattr(
            peak_config,
            "signal_processing_window_points",
            DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points,
        ),
    )
    assert resolved_profile == DEFAULT_SIGNAL_PROCESSING_PROFILE
    assert resolved_profile.method == "gaussian"
    assert resolved_profile.window_points == 15
    assert "precursor_mz" not in alignment
    assert result.discovery_index == discovery_index
    assert result.alignment_outputs.provisional_matrix_tsv == provisional_matrix
    assert (
        result.alignment_outputs.owner_backfill_peak_fact_provenance_tsv
        == physical_provenance
    )
    assert physical_provenance.name == (
        "alignment_owner_backfill_peak_fact_provenance.tsv"
    )


def test_pure_cid_nl_rejects_legacy_knobs_before_write_or_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    raw_file = raw_dir / "sample-a.raw"
    raw_file.write_bytes(b"raw")
    (dll_dir / "reader.dll").write_bytes(b"dll")
    output_dir = tmp_path / "run"
    discovery_called = False

    def fail_if_discovery_runs(*args, **kwargs):
        nonlocal discovery_called
        discovery_called = True
        raise AssertionError("Discovery must not run before retained-route validation")

    monkeypatch.setattr(workflow, "run_discovery_batch", fail_if_discovery_runs)

    discovery_settings = DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0473),
        ),
    )
    with pytest.raises(
        ValueError,
        match=(
            "pure CID-NL retained route does not support legacy Alignment options: "
            "raw_xic_batch_size"
        ),
    ):
        workflow.run_untargeted_provisional_matrix_workflow(
            raw_files=(raw_file,),
            dll_dir=dll_dir,
            output_dir=output_dir,
            discovery_settings=discovery_settings,
            alignment_config=AlignmentConfig(),
            raw_workers=3,
            raw_xic_batch_size=16,
        )

    assert discovery_called is False
    assert not output_dir.exists()
