from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill_transaction import (
    OwnerBackfillTransactionOutput,
)
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.process_backend import (
    OwnerBackfillTimingStats,
    OwnerBuildProcessOutput,
    OwnerBuildTimingStats,
)
from xic_extractor.diagnostics.timing import TimingRecorder


def test_pipeline_applies_single_worker_hybrid_owner_backfill_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.alignment import raw_sources as raw_sources_module

    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_backfill_request_summary",
        lambda *args, **kwargs: {},
    )

    def fake_source_for_owner_backfill_backend(source, backend):
        calls["backend"] = backend
        return SimpleNamespace(kind="indexed", source=source)

    def fake_owner_backfill(
        features,
        *,
        sample_order,
        raw_sources,
        validation_raw_sources=None,
        **kwargs,
    ):
        calls["backfill_raw_sources"] = raw_sources
        calls["validation_raw_sources"] = validation_raw_sources
        return ()

    monkeypatch.setattr(
        raw_sources_module,
        "source_for_owner_backfill_backend",
        fake_source_for_owner_backfill_backend,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        owner_backfill_xic_backend="ms1_index_hybrid",
    )

    assert calls["backend"] == "ms1_index_hybrid"
    assert set(calls["backfill_raw_sources"]) == {"Sample_A"}
    assert set(calls["validation_raw_sources"]) == {"Sample_A"}
    assert (
        calls["backfill_raw_sources"]["Sample_A"]
        is not calls["validation_raw_sources"]["Sample_A"]
    )


def test_pipeline_applies_single_worker_owner_build_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls: dict[str, object] = {}

    def fake_source_for_owner_build_backend(source, backend):
        calls["backend"] = backend
        return SimpleNamespace(kind="owner-build-index", source=source)

    def fake_build_owners(candidates, *, raw_sources, **kwargs):
        calls["build_raw_sources"] = raw_sources
        return SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        )

    monkeypatch.setattr(
        pipeline_module,
        "source_for_owner_build_backend",
        fake_source_for_owner_build_backend,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        fake_build_owners,
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        lambda features, *, sample_order, raw_sources, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        owner_build_xic_backend="ms1_index",
    )

    assert calls["backend"] == "ms1_index"
    build_sources = calls["build_raw_sources"]
    assert set(build_sources) == {"Sample_A"}
    assert build_sources["Sample_A"]._source.kind == "owner-build-index"


def test_pipeline_preconsolidates_owner_families_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_backfill_request_summary",
        lambda *args, **kwargs: {},
    )

    def fake_preconsolidate(features, *, config):
        calls["preconsolidate_features"] = features
        return ("consolidated",)

    def fake_owner_backfill(features, **kwargs):
        calls["backfill_features"] = features
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "consolidate_pre_backfill_identity_families",
        fake_preconsolidate,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        preconsolidate_owner_families=True,
    )

    assert calls["preconsolidate_features"] == ("feature",)
    assert calls["backfill_features"] == ("consolidated",)


def test_pipeline_early_loser_shadow_does_not_feed_backfill_or_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls: dict[str, object] = {}
    source_features = ("source-feature",)
    proposal = SimpleNamespace(
        rows=(SimpleNamespace(proposal_status="loser"),),
    )
    report = SimpleNamespace(
        verdict="GO",
        proposed_loser_count=1,
        confirmed_loser_count=1,
        false_loser_count=0,
        winner_mismatch_count=0,
        late_loser_count=0,
    )

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            source_features
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_backfill_request_summary",
        lambda *args, **kwargs: {},
    )

    def fake_shadow(features, **kwargs):
        calls["shadow_features"] = features
        calls["shadow_kwargs"] = kwargs
        return proposal

    def fake_owner_backfill(features, **kwargs):
        calls["backfill_features"] = features
        return ()

    final_matrix = _matrix(("Sample_A",))

    def fake_compare(received_proposal, matrix):
        calls["comparison_proposal"] = received_proposal
        calls["comparison_matrix"] = matrix
        return report

    def fake_write(outputs, matrix, **kwargs):
        calls["written_matrix"] = matrix
        calls["written_shadow"] = kwargs["early_loser_shadow"]

    monkeypatch.setattr(
        pipeline_module,
        "build_early_loser_shadow_proposal",
        fake_shadow,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: final_matrix,
    )
    monkeypatch.setattr(
        pipeline_module,
        "compare_early_loser_shadow_with_final_authority",
        fake_compare,
    )
    monkeypatch.setattr(pipeline_module, "_write_outputs_atomic", fake_write)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        emit_early_loser_shadow=True,
    )

    assert calls["shadow_features"] == source_features
    assert calls["shadow_features"] is calls["backfill_features"]
    assert calls["shadow_kwargs"]["run_id"]
    assert calls["comparison_proposal"] is proposal
    assert calls["comparison_matrix"] is final_matrix
    assert calls["written_matrix"] is final_matrix
    assert calls["written_shadow"] is report
    assert outputs.early_loser_shadow_json == (
        tmp_path / "out" / "alignment_early_loser_shadow.json"
    )


def test_pipeline_rejects_shadow_with_activated_preconsolidation(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot run with activated pre-Backfill consolidation",
    ):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "unused.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            emit_early_loser_shadow=True,
            preconsolidate_owner_families=True,
        )


def test_pipeline_rejects_shadow_outside_full_audit_scope(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="requires full-audit Backfill scope"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "unused.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            emit_early_loser_shadow=True,
            backfill_scope="production-equivalent",
        )


def test_pipeline_uses_process_owner_backfill_when_raw_workers_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    opener = FakeRawOpener()
    calls = {}
    recorder = TimingRecorder("alignment", run_id="test-process-backfill")
    read_candidates = pipeline_module.read_owner_bound_discovery_candidates_csv

    def read_with_review_only_orphan(path, **kwargs):
        current = read_candidates(path, **kwargs)
        return (
            *current,
            SimpleNamespace(
                sample_stem="Sample_A",
                discovery_candidate_state="ms1_feature_nl_rescued",
            ),
            SimpleNamespace(
                sample_stem="Sample_A",
                discovery_candidate_state="review_only_orphan_nl",
            ),
            SimpleNamespace(
                sample_stem="Sample_A",
                discovery_candidate_state="rejected_noise_or_outside_rt",
            ),
        )

    def fake_process_backfill(features, **kwargs):
        calls["features"] = features
        calls.update(kwargs)
        assert opener.calls == []
        return OwnerBackfillTransactionOutput(
            matrix=_matrix(("Sample_A",)),
            candidate_audit_rows=(),
            timing_stats=(
                OwnerBackfillTimingStats(
                    sample_stem="Sample_A",
                    elapsed_sec=1.5,
                    extract_xic_count=4,
                    point_count=40,
                ),
            ),
            request_reuse_observations=(),
            prepared_peak_facts=(),
            peak_fact_cost_receipts=(),
        )

    def fake_process_build(candidates, **kwargs):
        calls["build_candidates"] = tuple(
            candidate.sample_stem for candidate in candidates
        )
        calls["build_candidate_states"] = tuple(
            candidate.discovery_candidate_state for candidate in candidates
        )
        calls["build_kwargs"] = kwargs
        return OwnerBuildProcessOutput(
            ownership=OwnershipBuildResult(
                owners=(),
                assignments=(),
                ambiguous_records=(),
            ),
            timing_stats=(
                OwnerBuildTimingStats(
                    sample_stem="Sample_A",
                    elapsed_sec=2.5,
                    extract_xic_count=5,
                    point_count=50,
                ),
            ),
        )

    monkeypatch.setattr(
        pipeline_module,
        "run_owner_build_process",
        fake_process_build,
    )
    monkeypatch.setattr(
        pipeline_module,
        "read_owner_bound_discovery_candidates_csv",
        read_with_review_only_orphan,
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_owner_backfill_process",
        fake_process_backfill,
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=opener,
        raw_workers=2,
        owner_build_xic_backend="ms1_index",
        owner_backfill_xic_backend="ms1_index",
        timing_recorder=recorder,
    )

    assert calls["max_workers"] == 2
    assert calls["build_candidates"] == ("Sample_A", "Sample_A")
    assert calls["build_candidate_states"] == (
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
    )
    assert calls["raw_paths"] == {"Sample_A": raw_dir / "Sample_A.raw"}
    assert calls["matrix_sample_order"] == ("Sample_A",)
    assert calls["build_kwargs"]["max_workers"] == 2
    assert calls["raw_xic_batch_size"] == 1
    assert calls["owner_backfill_xic_backend"] == "ms1_index"
    assert calls["emit_region_audit"] is False
    assert calls["build_kwargs"]["raw_xic_batch_size"] == 1
    assert calls["build_kwargs"]["owner_build_xic_backend"] == "ms1_index"
    assert "owner_backfill_xic_backend" not in calls["build_kwargs"]
    records_by_stage_sample = {
        (record.stage, record.sample_stem): record for record in recorder.records
    }
    assert records_by_stage_sample[("alignment.read_candidates", "")].metrics == {
        "candidate_count": 4,
        "raw_owner_build_candidate_count": 2,
        "non_raw_owner_build_candidate_count": 2,
    }
    assert records_by_stage_sample[
        ("alignment.build_owners.extract_xic", "Sample_A")
    ].metrics == {
        "extract_xic_count": 5,
        "extract_xic_batch_count": 0,
        "raw_chromatogram_call_count": 0,
        "point_count": 50,
    }
    assert records_by_stage_sample[
        ("alignment.owner_backfill.extract_xic", "Sample_A")
    ].metrics == {
        "extract_xic_count": 4,
        "extract_xic_batch_count": 0,
        "raw_chromatogram_call_count": 0,
        "point_count": 40,
    }


def test_pipeline_uses_raw_dir_as_authority_and_fallbacks_to_sample_stem(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(
        tmp_path,
        ("Sample_A", "Sample_B", "Sample_C"),
        raw_files={
            "Sample_A": "C:/stale/renamed.raw",
            "Sample_B": "",
            "Sample_C": "C:/stale/missing.raw",
        },
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "renamed.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
    opener = FakeRawOpener()
    captured_sources = {}

    def fake_build_owners(candidates, *, raw_sources, **kwargs):
        captured_sources.update(raw_sources)
        return SimpleNamespace(owners=(), ambiguous_records=())

    monkeypatch.setattr(pipeline_module, "build_sample_local_owners", fake_build_owners)
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        lambda features, *, sample_order, raw_sources, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=opener,
    )

    assert [call[0] for call in opener.calls] == [
        raw_dir / "renamed.raw",
        raw_dir / "Sample_B.raw",
    ]
    assert set(captured_sources) == {"Sample_A", "Sample_B"}


def test_pipeline_enters_and_closes_raw_handles_on_success_and_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from xic_extractor.alignment import pipeline_outputs

    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    opener = FakeRawOpener()

    _patch_owner_pipeline_to_matrix(monkeypatch)

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out_success",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=opener,
    )
    assert opener.handles[0].entered is True
    assert opener.handles[0].closed is True

    def fail_matrix_writer(
        path,
        matrix,
        *,
        alignment_config=None,
        production_decisions=None,
    ):
        raise RuntimeError("writer failed")

    monkeypatch.setattr(
        pipeline_outputs,
        "write_alignment_matrix_tsv",
        fail_matrix_writer,
    )
    with pytest.raises(RuntimeError, match="writer failed"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out_failure",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=opener,
        )
    assert opener.handles[-1].entered is True
    assert opener.handles[-1].closed is True
