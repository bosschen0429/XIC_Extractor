from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction import untargeted_provisional_matrix_workflow as workflow


def test_workflow_routes_complete_discovery_universe_to_full_alignment(
    tmp_path: Path,
    monkeypatch,
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

    def fake_alignment(**kwargs):
        captured["alignment"] = kwargs
        return AlignmentRunOutputs(
            provisional_matrix_tsv=provisional_matrix,
            provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
            provisional_cell_provenance_tsv=(
                tmp_path / "provisional_cell_provenance.tsv"
            ),
            provisional_summary_json=tmp_path / "provisional_matrix_summary.json",
        )

    monkeypatch.setattr(workflow, "run_discovery_batch", fake_discovery)
    monkeypatch.setattr(workflow, "run_alignment", fake_alignment)

    timing_recorder = TimingRecorder.disabled("test")
    result = workflow.run_untargeted_provisional_matrix_workflow(
        raw_files=raw_files,
        dll_dir=dll_dir,
        output_dir=tmp_path / "run",
        discovery_settings=DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0473),
            ),
        ),
        alignment_config=AlignmentConfig(),
        raw_workers=3,
        raw_xic_batch_size=16,
        timing_recorder=timing_recorder,
    )

    assert captured["discovery_raw_files"] == raw_files
    discovery = captured["discovery"]
    assert isinstance(discovery, dict)
    assert discovery["timing_recorder"] is timing_recorder
    assert discovery["max_workers"] == 3
    alignment = captured["alignment"]
    assert isinstance(alignment, dict)
    assert alignment["discovery_batch_index"] == discovery_index
    assert alignment["backfill_scope"] == "full-audit"
    assert alignment["selected_family_ids"] == frozenset()
    assert alignment["emit_provisional_matrix"] is True
    assert alignment["require_discovery_owner_closure"] is True
    assert alignment["raw_workers"] == 3
    assert alignment["raw_xic_batch_size"] == 16
    assert "precursor_mz" not in alignment
    assert result.discovery_index == discovery_index
    assert result.alignment_outputs.provisional_matrix_tsv == provisional_matrix
