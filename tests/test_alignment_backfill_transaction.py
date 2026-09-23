from __future__ import annotations

import gc
import pickle
import weakref
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_owner_backfill_peak_fact_provenance import _live_available_inputs
from xic_extractor.alignment import owner_matrix
from xic_extractor.alignment.backfill_transaction import (
    OwnerBackfillSampleResult,
    OwnerBackfillSampleTransaction,
    OwnerBackfillTimingStats,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillRequestReuseObservation,
)
from xic_extractor.execution_runtime_owner import (
    derive_runtime_peak_fact_cost_metrics,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)


def _audit_row(cell, *, candidate_index: int = 1):
    return OwnerBackfillCandidateAuditRow(
        feature_family_id=cell.cluster_id,
        group_hypothesis_id=cell.group_hypothesis_id,
        public_family_id=cell.public_family_id,
        sample_stem=cell.sample_stem,
        candidate_index=candidate_index,
        candidate_phase="primary_query",
        selected_for_output=True,
        candidate_status=cell.status,
        candidate_outcome="backfilled",
        trace_quality=cell.trace_quality,
        area=cell.area,
        apex_rt=cell.apex_rt,
        peak_start_rt=cell.peak_start_rt,
        peak_end_rt=cell.peak_end_rt,
        rt_delta_sec=cell.rt_delta_sec,
        backfill_seed_mz=cell.backfill_seed_mz,
        backfill_seed_rt=cell.backfill_seed_rt,
        backfill_request_rt_min=cell.backfill_request_rt_min,
        backfill_request_rt_max=cell.backfill_request_rt_max,
        backfill_request_ppm=cell.backfill_request_ppm,
        reason=cell.reason,
        selection_note="selected",
    )


def test_sample_transaction_defers_matrix_cell_allocation_until_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    median_calls = 0
    original_median = owner_matrix.median_owner_area

    def counted_median(feature):
        nonlocal median_calls
        median_calls += 1
        return original_median(feature)

    monkeypatch.setattr(owner_matrix, "median_owner_area", counted_median)

    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(
        ValueError, match="cannot publish incomplete samples"
    ):
        transaction.finish()

    receipt = transaction.deliver(
        0,
        OwnerBackfillSampleResult(
            sample_index=2,
            sample_stem="sample-b",
            cells=(live.observation.cell,),
        ),
    )

    assert receipt.cell_count == 1
    assert receipt.sample_stem == "sample-b"
    output = transaction.finish()
    assert len(output.matrix.cells) == 2
    cells_by_sample = {c.sample_stem: c for c in output.matrix.cells}
    assert set(cells_by_sample.keys()) == {"sample-a", "sample-b"}
    assert cells_by_sample["sample-b"].status == live.observation.cell.status
    assert cells_by_sample["sample-a"].status == "detected"
    assert median_calls == 0


def test_sample_transaction_preserves_every_backfill_attachment(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    cell = live.observation.cell
    audit = _audit_row(cell)
    timing = OwnerBackfillTimingStats(
        sample_stem=cell.sample_stem,
        elapsed_sec=1.25,
        extract_xic_count=1,
        point_count=61,
    )
    reuse = OwnerBackfillRequestReuseObservation(
        sample_stem=cell.sample_stem,
        validation_mode="primary",
        logical_request_count=1,
        canonical_request_class_count=1,
    )
    cost = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=None,
        raw_open_receipts=(),
        work_receipts=(),
        artifact_io=None,
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    receipt = transaction.deliver(
        0,
        OwnerBackfillSampleResult(
            sample_index=2,
            sample_stem="sample-b",
            cells=(cell,),
            candidate_audit_rows=(audit,),
            timing_stats=(timing,),
            request_reuse_observations=(reuse,),
            peak_fact_observations=(live.observation,),
            peak_fact_cost=cost,
        ),
    )
    output = transaction.finish()

    assert receipt.sample_index == 2
    assert receipt.cell_count == 1
    assert receipt.candidate_audit_row_count == 1
    assert receipt.peak_fact_count == 1
    assert output.candidate_audit_rows == (audit,)
    assert output.timing_stats == (timing,)
    assert output.request_reuse_observations == (reuse,)
    assert output.peak_fact_cost_receipts == (cost,)
    assert len(output.prepared_peak_facts) == 1
    assert output.prepared_peak_facts[0].stage.work_unit_key == live.stage.work_unit_key
    assert output.prepared_peak_facts[0].stage.feature_order_index == 0
    assert output.prepared_peak_facts[0].stage.sample_order_index == 1
    assert output.prepared_peak_facts[0].physical_row.work_unit_key == (
        live.stage.work_unit_key
    )
    by_sample = {item.sample_stem: item for item in output.matrix.cells}
    assert by_sample["sample-b"].status == "backfilled"
    assert by_sample["sample-b"].area == cell.area


def test_sample_transaction_releases_delivered_result_graph(tmp_path: Path) -> None:
    live = _live_available_inputs(tmp_path)
    feature = live.feature
    sample_order = (
        "sample-a",
        "sample-b",
        "sample-c",
        "sample-d",
        "sample-e",
        "sample-f",
    )
    transaction = OwnerBackfillSampleTransaction(
        (feature,),
        matrix_sample_order=sample_order,
        expected_samples=tuple(enumerate(sample_order[1:], start=2)),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    result_refs = []
    cell_refs = []
    for ordinal, sample_stem in enumerate(sample_order[1:]):
        cell = replace(live.observation.cell, sample_stem=sample_stem)
        result = OwnerBackfillSampleResult(
            sample_index=ordinal + 2,
            sample_stem=sample_stem,
            cells=(cell,),
            peak_fact_observations=(
                (live.observation,) if sample_stem == "sample-b" else ()
            ),
        )
        result_refs.append(weakref.ref(result))
        cell_refs.append(weakref.ref(cell))
        transaction.deliver(ordinal, result)
        del result, cell

    del live
    gc.collect()

    assert all(ref() is None for ref in result_refs)
    assert all(ref() is None for ref in cell_refs)
    assert all(
        cell.status == "backfilled" for cell in transaction.finish().matrix.cells[1:]
    )


def test_sample_transaction_freezes_in_owner_order_after_reverse_completion(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    cell_b = live.observation.cell
    cell_c = replace(cell_b, sample_stem="sample-c")
    timing_b = OwnerBackfillTimingStats("sample-b", 2.0, 1, 61)
    timing_c = OwnerBackfillTimingStats("sample-c", 3.0, 1, 61)
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b", "sample-c"),
        expected_samples=((2, "sample-b"), (3, "sample-c")),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    transaction.deliver(
        1,
        OwnerBackfillSampleResult(3, "sample-c", (cell_c,), timing_stats=(timing_c,)),
    )
    transaction.deliver(
        0,
        OwnerBackfillSampleResult(2, "sample-b", (cell_b,), timing_stats=(timing_b,)),
    )
    output = transaction.finish()

    assert tuple(cell.sample_stem for cell in output.matrix.cells) == (
        "sample-a",
        "sample-b",
        "sample-c",
    )
    assert output.timing_stats == (timing_b, timing_c)


def test_sample_transaction_freezes_feature_major_order_with_multiple_features(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    second_feature = replace(
        live.feature,
        feature_family_id="FAM000002",
        group_hypothesis_id="GRP000002",
        public_family_id="PUB000002",
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature, second_feature),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((1, "sample-a"), (2, "sample-b")),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    transaction.deliver(1, OwnerBackfillSampleResult(2, "sample-b", ()))
    transaction.deliver(0, OwnerBackfillSampleResult(1, "sample-a", ()))

    assert tuple(
        (cell.cluster_id, cell.sample_stem, cell.status)
        for cell in transaction.finish().matrix.cells
    ) == (
        ("FAM000001", "sample-a", "detected"),
        ("FAM000001", "sample-b", "absent"),
        ("FAM000002", "sample-a", "detected"),
        ("FAM000002", "sample-b", "absent"),
    )


def test_sample_transaction_rejects_duplicate_rescued_cell_before_mutation(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    duplicate = replace(live.observation.cell, area=live.observation.cell.area + 1.0)
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="duplicate rescued owner Matrix cell"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (live.observation.cell, duplicate),
            ),
        )

    assert transaction._builder._cells_by_sample == {}
    with pytest.raises(ValueError, match="incomplete samples"):
        transaction.finish()


def test_candidate_audit_keeps_batch_order_when_matrix_order_differs(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    cell_b = live.observation.cell
    cell_c = replace(cell_b, sample_stem="sample-c")
    audit_b = _audit_row(cell_b)
    audit_c = _audit_row(cell_c)
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-c", "sample-b"),
        expected_samples=((2, "sample-b"), (3, "sample-c")),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    transaction.deliver(
        1,
        OwnerBackfillSampleResult(
            3,
            "sample-c",
            (cell_c,),
            candidate_audit_rows=(audit_c,),
        ),
    )
    transaction.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (cell_b,),
            candidate_audit_rows=(audit_b,),
        ),
    )

    assert transaction.finish().candidate_audit_rows == (audit_b, audit_c)


def test_sample_transaction_rejects_cross_sample_attachments_before_mutation(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    sample_a_cell = replace(live.observation.cell, sample_stem="sample-a")
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="attachments cross sample identity"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (sample_a_cell,),
                candidate_audit_rows=(_audit_row(sample_a_cell),),
            ),
        )

    with pytest.raises(ValueError, match="incomplete samples"):
        transaction.finish()


def test_sample_transaction_consumes_worker_prepared_peak_facts_without_live_graph(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    source.deliver(
        0,
        OwnerBackfillSampleResult(
            sample_index=2,
            sample_stem="sample-b",
            cells=(live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    prepared = source.finish().prepared_peak_facts
    compact_result = OwnerBackfillSampleResult(
        sample_index=2,
        sample_stem="sample-b",
        cells=(live.observation.cell,),
        prepared_peak_facts=prepared,
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    transaction.deliver(0, compact_result)
    output = transaction.finish()

    assert output.prepared_peak_facts == prepared
    assert not compact_result.peak_fact_observations
    assert len(pickle.dumps(compact_result)) < len(
        pickle.dumps(
            OwnerBackfillSampleResult(
                sample_index=2,
                sample_stem="sample-b",
                cells=(live.observation.cell,),
                peak_fact_observations=(live.observation,),
            )
        )
    )


def test_sample_transaction_rejects_worker_prepared_peak_fact_order_misbinding(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    crossed_source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-b", "sample-a"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    crossed_source.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    crossed = crossed_source.finish().prepared_peak_facts[0]
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="crosses sample order"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (live.observation.cell,),
                prepared_peak_facts=(crossed,),
            ),
        )


def test_sample_transaction_revalidates_worker_prepared_feature_binding(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    source.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    crossed = source.finish().prepared_peak_facts[0]
    object.__setattr__(crossed, "feature_family_id", "forged-feature")
    forged_feature = replace(live.feature, feature_family_id="forged-feature")
    transaction = OwnerBackfillSampleTransaction(
        (forged_feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="crosses feature identity"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (),
                prepared_peak_facts=(crossed,),
            ),
        )


def test_sample_transaction_revalidates_worker_prepared_stage_row_binding(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    source.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    crossed = source.finish().prepared_peak_facts[0]
    object.__setattr__(
        crossed,
        "physical_row",
        replace(crossed.physical_row, group_hypothesis_id="forged-group"),
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="crosses stage identity"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (),
                prepared_peak_facts=(crossed,),
            ),
        )


def test_sample_transaction_revalidates_selected_integration_binding(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    source.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    crossed = source.finish().prepared_peak_facts[0]
    assert crossed.selected_integration_authority_content_id is not None
    object.__setattr__(
        crossed,
        "selected_integration_authority_content_id",
        "selected-integration:forged",
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="crosses selected integration"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (),
                prepared_peak_facts=(crossed,),
            ),
        )


def test_sample_transaction_rejects_coordinated_selected_integration_replacement(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    source.deliver(
        0,
        OwnerBackfillSampleResult(
            2,
            "sample-b",
            (live.observation.cell,),
            peak_fact_observations=(live.observation,),
        ),
    )
    crossed = source.finish().prepared_peak_facts[0]
    assert crossed.selected_integration_authority is not None
    forged_integration = replace(
        crossed.selected_integration_authority,
        rt_apex_min=crossed.selected_integration_authority.rt_apex_min + 0.1,
    )
    forged_id = forged_integration.selected_integration_authority_content_id
    object.__setattr__(
        crossed.stage,
        "selected_integration_authority_content_id",
        forged_id,
    )
    object.__setattr__(crossed, "selected_integration_authority", forged_integration)
    object.__setattr__(
        crossed,
        "selected_integration_authority_content_id",
        forged_id,
    )
    transaction = OwnerBackfillSampleTransaction(
        (live.feature,),
        matrix_sample_order=("sample-a", "sample-b"),
        expected_samples=((2, "sample-b"),),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    with pytest.raises(ValueError, match="crosses selected integration facts"):
        transaction.deliver(
            0,
            OwnerBackfillSampleResult(
                2,
                "sample-b",
                (),
                prepared_peak_facts=(crossed,),
            ),
        )
