from __future__ import annotations

import gc
import weakref
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_owner_backfill_peak_fact_provenance import _live_available_inputs
from xic_extractor.alignment.backfill_transaction import (
    OwnerBackfillSampleTransaction,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillRequestReuseObservation,
)
from xic_extractor.alignment.process_backend import (
    OwnerBackfillSampleResult,
    OwnerBackfillTimingStats,
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
        candidate_outcome="rescued",
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
    assert by_sample["sample-b"].status == "rescued"
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
        cell.status == "rescued" for cell in transaction.finish().matrix.cells[1:]
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
