from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import (
    owner_backfill_result as _owner_backfill_result,
)
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill_transaction import (
    OwnerBackfillTransactionOutput,
)
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.process_backend import (
    OwnerBuildProcessOutput,
)


def test_run_alignment_emits_identity_coherence_diagnostic_when_opted_in(
    tmp_path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)
    calls = {}

    def fake_build_owners(candidates, **kwargs):
        candidate = tuple(candidates)[0]
        event = IdentityEvent(
            candidate_id=candidate.candidate_id,
            sample_stem=candidate.sample_stem,
            raw_file=str(candidate.raw_file),
            neutral_loss_tag=candidate.neutral_loss_tag,
            precursor_mz=candidate.precursor_mz,
            product_mz=candidate.product_mz,
            observed_neutral_loss_da=candidate.observed_neutral_loss_da,
            seed_rt=candidate.best_seed_rt,
            evidence_score=candidate.evidence_score,
            seed_event_count=candidate.seed_event_count,
        )
        owner = SampleLocalMS1Owner(
            owner_id="OWN-1",
            sample_stem=candidate.sample_stem,
            raw_file=str(candidate.raw_file),
            precursor_mz=candidate.precursor_mz,
            owner_apex_rt=float(candidate.ms1_apex_rt),
            owner_peak_start_rt=float(candidate.ms1_peak_rt_start),
            owner_peak_end_rt=float(candidate.ms1_peak_rt_end),
            owner_area=float(candidate.ms1_area),
            owner_height=float(candidate.ms1_height),
            primary_identity_event=event,
            supporting_events=(),
            identity_conflict=False,
            assignment_reason="primary_identity_event",
        )
        return OwnershipBuildResult(
            owners=(owner,),
            assignments=(
                OwnerAssignment(
                    candidate_id=candidate.candidate_id,
                    owner_id="OWN-1",
                    assignment_status="primary",
                    reason="primary_identity_event",
                ),
            ),
            ambiguous_records=(),
        )

    def fake_diagnostic(**kwargs):
        calls.update(kwargs)

        class Result:
            records = ()
            context = type(
                "Context",
                (),
                {"raw_xic_request_count": 0, "xic_point_count": 0},
            )()

        return Result()

    monkeypatch.setattr(pipeline_module, "build_sample_local_owners", fake_build_owners)
    monkeypatch.setattr(
        pipeline_module,
        "run_identity_coherence_diagnostic",
        fake_diagnostic,
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        emit_identity_coherence_diagnostic=True,
        identity_coherence_output_dir=tmp_path / "identity",
    )

    assert calls["output_dir"] == tmp_path / "identity"
    assert calls["fragment_profile_id"] == "alignment-cid-neutral-loss-v0.4"
    assert outputs.identity_coherence_output_dir == tmp_path / "identity"


def test_run_alignment_passes_identity_coherence_worker_policy(
    tmp_path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    def fake_build_owners(*args, **kwargs):
        return OwnerBuildProcessOutput(
            ownership=OwnershipBuildResult(
                owners=(),
                assignments=(),
                ambiguous_records=(),
            ),
            timing_stats=(),
        )

    def fake_backfill(*args, **kwargs):
        return OwnerBackfillTransactionOutput(
            matrix=AlignmentMatrix(
                clusters=(),
                cells=(),
                sample_order=("Sample_A",),
            ),
            candidate_audit_rows=(),
            timing_stats=(),
            request_reuse_observations=(),
            prepared_peak_facts=(),
            peak_fact_cost_receipts=(),
        )

    def fake_diagnostic(**kwargs):
        calls.update(kwargs)

        class Result:
            records = ()
            context = type(
                "Context",
                (),
                {"raw_xic_request_count": 0, "xic_point_count": 0},
            )()

        return Result()

    monkeypatch.setattr(pipeline_module, "run_owner_build_process", fake_build_owners)
    monkeypatch.setattr(pipeline_module, "run_owner_backfill_process", fake_backfill)
    monkeypatch.setattr(
        pipeline_module,
        "run_identity_coherence_diagnostic",
        fake_diagnostic,
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_workers=8,
        raw_xic_batch_size=64,
        emit_identity_coherence_diagnostic=True,
    )

    assert calls["raw_workers"] == 8
    assert calls["raw_xic_batch_size"] == 64


def test_identity_coherence_diagnostic_does_not_change_backfill_inputs(
    tmp_path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    captured_features = []

    _patch_owner_pipeline_to_matrix(monkeypatch)
    expected_features = ("sentinel-family",)
    sentinel_feature = SimpleNamespace(feature_family_id=expected_features[0])
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            sentinel_feature,
        ),
    )

    def fake_backfill(features, *, sample_order, raw_sources, **kwargs):
        captured_features.append(
            tuple(feature.feature_family_id for feature in features)
        )
        return _owner_backfill_result(
            evaluation_coverage_context=kwargs.get("evaluation_coverage_context"),
        )

    monkeypatch.setattr(pipeline_module, "build_owner_backfill_result", fake_backfill)
    monkeypatch.setattr(
        pipeline_module,
        "run_identity_coherence_diagnostic",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("diagnostic must not run when disabled")
        ),
    )

    disabled = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path,
        output_dir=tmp_path / "disabled",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
    )
    disabled_features = captured_features[-1]
    assert disabled_features == expected_features
    monkeypatch.setattr(
        pipeline_module,
        "run_identity_coherence_diagnostic",
        lambda **kwargs: type(
            "DiagnosticRun",
            (),
            {
                "records": (),
                "context": type(
                    "Context",
                    (),
                    {"raw_xic_request_count": 0, "xic_point_count": 0},
                )(),
            },
        )(),
    )
    enabled = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path,
        output_dir=tmp_path / "enabled",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        emit_identity_coherence_diagnostic=True,
    )

    assert captured_features == [expected_features, expected_features]
    assert disabled.identity_coherence_output_dir is None
    assert enabled.identity_coherence_output_dir == (
        tmp_path / "enabled" / "identity_coherence"
    )


def test_run_alignment_propagates_identity_coherence_diagnostic_errors(
    tmp_path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)

    def failing_diagnostic(**kwargs):
        raise RuntimeError("identity diagnostic failed")

    monkeypatch.setattr(
        pipeline_module,
        "run_identity_coherence_diagnostic",
        failing_diagnostic,
    )

    with pytest.raises(RuntimeError, match="identity diagnostic failed"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path,
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=FakeRawOpener(),
            emit_identity_coherence_diagnostic=True,
        )
