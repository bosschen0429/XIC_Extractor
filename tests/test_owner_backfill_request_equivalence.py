import pickle
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.owner_backfill as owner_backfill_module
import xic_extractor.alignment.owner_backfill_request_plan as request_plan_module
from tests.test_alignment_owner_backfill import _feature, _peak_config
from tests.test_alignment_owner_clustering import _owner
from xic_extractor.acquisition_contract import (
    _mint_raw_acquisition_session_receipt,
    mint_raw_acquisition_session_capability,
)
from xic_extractor.alignment.claim_registry import apply_ms1_peak_claim_registry
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ms1_index_source import OwnerBackfillXicBackend
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillLogicalExtractionResult,
    _iter_extracted_request_traces,
    build_owner_backfill_result,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    OwnerBackfillRequestEquivalenceContext,
    OwnerBackfillRequestIdentity,
    OwnerBackfillSampleRequestProjection,
    OwnerBackfillValidationMode,
    OwnerBackfillWindowStrategy,
    build_owner_backfill_request_plan,
    build_owner_backfill_sample_runtime_projections,
    mint_owner_backfill_request_identity,
    validate_owner_backfill_sample_runtime_projection,
)
from xic_extractor.alignment.owner_backfill_runtime_projection import (
    OwnerBackfillOwnerEligibility,
    backfill_runtime_owner_eligibility,
    mint_owner_backfill_runtime_feature,
    validate_owner_backfill_runtime_feature,
)
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.primary_consolidation import (
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.process_backend import (
    OwnerBackfillSampleJob,
    OwnerBackfillSampleResult,
    extract_owner_backfill_sample_job,
)
from xic_extractor.alignment.raw_sources import timed_owner_backfill_sources
from xic_extractor.alignment.tsv_writer import write_alignment_cells_tsv
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICRequest, XICTrace


class _OwnerBoundBatchSource:
    def __init__(
        self,
        acquisition_source: AcquisitionSourceIdentity | None,
        *,
        height: float = 120.0,
    ) -> None:
        self.acquisition_source = acquisition_source
        self.height = height
        self.batch_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        requests = tuple(requests)
        self.batch_sizes.append(len(requests))
        return tuple(
            _trace_for_request(request, height=self.height) for request in requests
        )

    def extract_xic(self, *_args: object) -> tuple[object, object]:
        raise AssertionError("batch-capable source should not call extract_xic")


class _OwnerBoundWindowSource(_OwnerBoundBatchSource):
    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return int(round(request.rt_min * 100)), int(round(request.rt_max * 100))

    def retention_time_for_scan(self, scan_number: int) -> float:
        return scan_number / 100.0


class _FailingOwnerBoundWindowSource(_OwnerBoundWindowSource):
    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        requests = tuple(requests)
        self.batch_sizes.append(len(requests))
        if any(request.rt_max - request.rt_min > 2.0 for request in requests):
            raise OSError("super-window unavailable")
        return tuple(
            _trace_for_request(request, height=self.height) for request in requests
        )


def test_compact_runtime_projection_rebuilds_full_request_plan_and_roundtrips() -> None:
    feature_with_multiple_seeds = replace(
        _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
        backfill_seed_centers=((500.0, 8.5), (500.2, 8.7)),
    )
    feature_with_confirmed_owner = replace(
        _feature(feature_family_id="FAM000002", mz=510.0, rt=9.0),
        owners=(
            replace(_owner("sample-a", "a"), owner_area=1000.0),
            replace(_owner("sample-b", "b"), owner_area=100.0),
        ),
        confirm_local_owners_with_backfill=True,
    )
    features = (feature_with_multiple_seeds, feature_with_confirmed_owner)
    sample_order = ("sample-a", "sample-b", "sample-c")
    raw_sample_stems = frozenset(sample_order)
    alignment_config = AlignmentConfig(max_rt_sec=60.0)

    full_plan = build_owner_backfill_request_plan(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )
    runtime_projections = build_owner_backfill_sample_runtime_projections(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )

    assert set(runtime_projections) == {"sample-b", "sample-c"}
    assert all(
        not hasattr(feature, "owners") and not hasattr(feature, "members")
        for projection in runtime_projections.values()
        for feature in projection.features
    )
    for sample_stem, runtime_projection in runtime_projections.items():
        restored = pickle.loads(pickle.dumps(runtime_projection))
        assert restored == runtime_projection
        compact_plan = build_owner_backfill_request_plan(
            restored.features,
            sample_order=(sample_stem,),
            raw_sample_stems=frozenset({sample_stem}),
            alignment_config=alignment_config,
        )
        assert _request_plan_projection(
            compact_plan.requests_for_sample(sample_stem)
        ) == _request_plan_projection(full_plan.requests_for_sample(sample_stem))


@pytest.mark.parametrize("owner_area", (True, float("nan"), 0.0, -1.0))
def test_compact_runtime_projection_rejects_noncanonical_direct_owner_eligibility(
    owner_area: object,
) -> None:
    assert OwnerBackfillOwnerEligibility("sample-a", None).owner_area is None
    with pytest.raises(ValueError, match="owner eligibility owner_area"):
        OwnerBackfillOwnerEligibility("sample-a", owner_area)  # type: ignore[arg-type]


@pytest.mark.parametrize("owner_area", (0.0, float("nan"), -1.0))
def test_compact_runtime_projection_preserves_unavailable_owner_area_parity(
    owner_area: float,
) -> None:
    source_feature = _feature()
    feature = replace(
        source_feature,
        owners=(replace(source_feature.owners[0], owner_area=owner_area),),
    )
    sample_order = ("sample-a", "sample-b")
    alignment_config = AlignmentConfig()
    full_plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=alignment_config,
    )
    runtime_projection = build_owner_backfill_sample_runtime_projections(
        (feature,),
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=alignment_config,
    )["sample-b"]

    assert runtime_projection.features[0].owner_eligibility == (
        OwnerBackfillOwnerEligibility("sample-a", None),
    )
    restored = pickle.loads(pickle.dumps(runtime_projection))
    compact_plan = build_owner_backfill_request_plan(
        restored.features,
        sample_order=("sample-b",),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=alignment_config,
    )
    assert _request_plan_projection(
        compact_plan.requests_for_sample("sample-b")
    ) == _request_plan_projection(full_plan.requests_for_sample("sample-b"))


def test_full_feature_missing_owners_preserves_attribute_error_sentinel() -> None:
    with pytest.raises(AttributeError, match="owners"):
        backfill_runtime_owner_eligibility(SimpleNamespace())

    with pytest.raises(TypeError, match="requires non-empty owner tuple"):
        backfill_runtime_owner_eligibility(SimpleNamespace(owners=()))


def test_compact_runtime_projection_mints_each_source_feature_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_a = _feature(feature_family_id="FAM000001")
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0)
    mint_calls: list[str] = []
    real_mint = request_plan_module.mint_owner_backfill_runtime_feature

    def counted_mint(feature):
        mint_calls.append(feature.feature_family_id)
        return real_mint(feature)

    monkeypatch.setattr(
        "xic_extractor.alignment.owner_backfill_request_plan.mint_owner_backfill_runtime_feature",
        counted_mint,
    )

    projections = build_owner_backfill_sample_runtime_projections(
        (feature_a, feature_b, feature_a),
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_sample_stems=frozenset({"sample-a", "sample-b", "sample-c"}),
        alignment_config=AlignmentConfig(),
    )

    assert set(projections) == {"sample-b", "sample-c"}
    assert mint_calls == ["FAM000001", "FAM000002"]


def test_compact_runtime_projection_rejects_empty_owner_sample_and_feature_id() -> None:
    with pytest.raises(ValueError, match="owner eligibility sample_stem"):
        OwnerBackfillOwnerEligibility("", 1.0)

    feature = mint_owner_backfill_runtime_feature(_feature())
    with pytest.raises(ValueError, match="feature_family_id"):
        replace(feature, feature_family_id="")


def test_compact_runtime_projection_rejects_relabelled_feature_or_sample_binding() -> (
    None
):
    feature = mint_owner_backfill_runtime_feature(_feature())
    with pytest.raises(ValueError, match="runtime_feature_id mismatches"):
        replace(feature, group_hypothesis_id="other-group")

    sample_projection = build_owner_backfill_sample_runtime_projections(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
    )["sample-b"]
    with pytest.raises(ValueError, match="runtime_projection_id mismatches"):
        replace(sample_projection, sample_stem="other-sample")
    with pytest.raises(ValueError, match="runtime_projection_id mismatches"):
        replace(sample_projection, logical_request_offset=1)

    validate_owner_backfill_runtime_feature(feature)
    validate_owner_backfill_sample_runtime_projection(sample_projection)


def test_compact_runtime_projection_reduces_material_request_payload() -> None:
    features = tuple(
        _feature(
            feature_family_id=f"FAM{index:06d}",
            mz=500.0 + index * 0.01,
            rt=8.5 + index * 0.001,
        )
        for index in range(64)
    )
    sample_projection = OwnerBackfillSampleRequestProjection(
        sample_stem="sample-b",
        features=features,
        logical_request_count=len(features),
    )
    runtime_projection = build_owner_backfill_sample_runtime_projections(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
    )["sample-b"]

    full_bytes = len(pickle.dumps(sample_projection))
    compact_bytes = len(pickle.dumps(runtime_projection))

    assert compact_bytes < full_bytes
    assert compact_bytes / full_bytes < 0.75


def test_request_plan_mints_complete_partition_in_first_logical_order(
    tmp_path: Path,
) -> None:
    acquisition_source = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5)
    feature_c = _feature(feature_family_id="FAM000003", mz=510.0, rt=9.0)
    context = _context({"sample-b": acquisition_source})

    plan = build_owner_backfill_request_plan(
        (feature_a, feature_b, feature_c),
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        equivalence_context=context,
    )

    assert plan.exact_equivalence_available is True
    classes = plan.equivalence_classes_for_sample("sample-b")
    class_members = [
        tuple(member.logical_index for member in item.members) for item in classes
    ]
    assert class_members == [
        (0, 1),
        (2,),
    ]
    assert [member.group_hypothesis_id for member in classes[0].members] == [
        "FAM000001",
        "FAM000002",
    ]
    logical_request_ids = {
        member.logical_request_id for item in classes for member in item.members
    }
    assert len(logical_request_ids) == 3
    assert classes[0].identity.sample_stem == "sample-b"
    assert classes[0].identity.acquisition_source == acquisition_source
    assert classes[0].identity.request == XICRequest(500.0, 7.5, 9.5, 20.0)
    assert classes[0].identity.backend == "raw"
    assert classes[0].identity.window_strategy == "exact"
    assert classes[0].identity.validation_mode == "primary"
    assert classes[0].identity.result_semantics == OWNER_BACKFILL_TRACE_RESULT_SEMANTICS
    with pytest.raises(
        ValueError,
        match="equivalence classes must follow first logical occurrence",
    ):
        replace(
            plan,
            equivalence_classes_by_sample={"sample-b": tuple(reversed(classes))},
        )


def test_request_identity_rejects_resealed_semantic_mutation(tmp_path: Path) -> None:
    identity = mint_owner_backfill_request_identity(
        sample_stem="sample-b",
        acquisition_source=_acquisition_source(tmp_path, "sample-b", b"sample-b"),
        request=XICRequest(500.0, 7.5, 9.5, 20.0),
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
    )

    with pytest.raises(ValueError, match="mismatches complete owner request"):
        replace(identity, backend="ms1_index")


def test_request_identity_separates_every_incompatible_semantic(
    tmp_path: Path,
) -> None:
    source_a = _acquisition_source(tmp_path, "sample-a", b"sample-a")
    source_b = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    base = _identity(source_a)
    alternatives = (
        _identity(source_a, sample_stem="other-sample"),
        _identity(source_b),
        _identity(source_a, request=XICRequest(500.1, 7.5, 9.5, 20.0)),
        _identity(source_a, request=XICRequest(500.0, 7.4, 9.5, 20.0)),
        _identity(source_a, request=XICRequest(500.0, 7.5, 9.6, 20.0)),
        _identity(source_a, request=XICRequest(500.0, 7.5, 9.5, 10.0)),
        _identity(source_a, backend="ms1_index"),
        _identity(source_a, window_strategy="super-window"),
        _identity(source_a, validation_mode="prefilter"),
        _identity(source_a, validation_mode="validation"),
    )

    assert all(
        item.request_identity_id != base.request_identity_id for item in alternatives
    )
    with pytest.raises(ValueError, match="unsupported owner Backfill result semantics"):
        _identity(source_a, result_semantics="different_trace_semantics")


def test_request_partition_never_reuses_across_samples_or_acquisitions(
    tmp_path: Path,
) -> None:
    source_b = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source_c = _acquisition_source(tmp_path, "sample-c", b"sample-c")
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_sample_stems=frozenset({"sample-b", "sample-c"}),
        alignment_config=AlignmentConfig(),
        equivalence_context=_context(
            {
                "sample-b": source_b,
                "sample-c": source_c,
            }
        ),
    )

    class_b = plan.equivalence_classes_for_sample("sample-b")[0]
    class_c = plan.equivalence_classes_for_sample("sample-c")[0]
    assert class_b.class_id != class_c.class_id
    assert plan.canonical_class_count_for_sample("sample-b") == 1
    assert plan.canonical_class_count_for_sample("sample-c") == 1


def test_request_plan_rejects_partition_rebound_to_another_owner_context(
    tmp_path: Path,
) -> None:
    source = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
        equivalence_context=_context({"sample-b": source}),
    )
    assert plan.equivalence_context is not None
    rebound_context = replace(plan.equivalence_context, backend="ms1_index")

    with pytest.raises(ValueError, match="mismatches its owner context"):
        replace(plan, equivalence_context=rebound_context)


def test_missing_owner_context_cannot_claim_exact_equivalence(tmp_path: Path) -> None:
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
    )

    assert plan.exact_equivalence_available is False
    with pytest.raises(ValueError, match="owner_equivalence_context_not_supplied"):
        plan.canonical_class_count_for_sample("sample-b")

    missing_source_context = _context(
        {"sample-a": _acquisition_source(tmp_path, "sample-a", b"sample-a")}
    )
    with pytest.raises(ValueError, match="identity for sample-b"):
        build_owner_backfill_request_plan(
            (feature,),
            sample_order=("sample-a", "sample-b"),
            raw_sample_stems=frozenset({"sample-b"}),
            alignment_config=AlignmentConfig(),
            equivalence_context=missing_source_context,
        )

    result = build_owner_backfill_result(
        (
            feature,
            _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _OwnerBoundBatchSource(None)},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )
    assert result.request_reuse_observations == ()


def test_equivalence_class_rejects_missing_or_rebound_member(tmp_path: Path) -> None:
    source = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
        equivalence_context=_context({"sample-b": source}),
    )
    equivalence_class = plan.equivalence_classes_for_sample("sample-b")[0]
    assert plan.equivalence_classes_by_sample is not None
    with pytest.raises(ValueError, match="cover each logical request once"):
        replace(
            plan,
            equivalence_classes_by_sample={"sample-b": ()},
        )
    with pytest.raises(ValueError, match="cover each logical request once"):
        replace(
            plan,
            equivalence_classes_by_sample={
                "sample-b": (equivalence_class, equivalence_class)
            },
        )
    rebound_item = equivalence_class.members[0].item._replace(
        request=XICRequest(501.0, 7.5, 9.5, 20.0)
    )
    with pytest.raises(ValueError, match="hypothesis-bound request"):
        replace(
            equivalence_class.members[0],
            item=rebound_item,
        )


def test_owner_backfill_extracts_once_per_class_and_fans_out_complete_results(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _OwnerBoundBatchSource(source_identity)
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5)

    result = build_owner_backfill_result(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=1,
        owner_backfill_xic_backend="raw",
        owner_backfill_window_strategy="exact",
    )

    assert source.batch_sizes == [1]
    assert [(cell.cluster_id, cell.sample_stem) for cell in result.cells] == [
        ("FAM000001", "sample-b"),
        ("FAM000002", "sample-b"),
    ]
    assert [row.feature_family_id for row in result.candidate_audit_rows] == [
        "FAM000001",
        "FAM000002",
    ]
    assert [
        (
            item.validation_mode,
            item.logical_request_count,
            item.canonical_request_class_count,
        )
        for item in result.request_reuse_observations
    ] == [("primary", 2, 1)]


def test_superwindow_extracts_once_per_exact_class_and_fans_out(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _OwnerBoundWindowSource(source_identity)

    result = build_owner_backfill_result(
        (
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=1,
        owner_backfill_xic_backend="raw",
        owner_backfill_window_strategy="super-window",
    )

    assert source.batch_sizes == [1]
    assert len(result.cells) == 2
    assert [
        (item.logical_request_count, item.canonical_request_class_count)
        for item in result.request_reuse_observations
    ] == [(2, 1)]
    assert len(result.peak_fact_observations) == 2
    assert {
        observation.logical_request.group_hypothesis_id
        for observation in result.peak_fact_observations
    } == {"FAM000001", "FAM000002"}
    assert all(
        observation.request_identity.acquisition_source == source_identity
        for observation in result.peak_fact_observations
    )
    assert all(
        observation.trace_evaluated for observation in result.peak_fact_observations
    )
    assert result.peak_fact_unsealed_evaluation_keys == ()
    assert not hasattr(result, "peak_fact_cost")


def test_canonical_preparation_cache_runs_once_per_equivalent_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _OwnerBoundBatchSource(source_identity)
    prepare_calls: list[str] = []
    question_calls: list[str] = []
    real_prepare = owner_backfill_module.prepare_physical_signal
    real_evaluate = owner_backfill_module.evaluate_physical_peak

    def counted_prepare(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        prepare_calls.append(prepared.projection.prepared_signal_id)
        return prepared

    def counted_evaluate(prepared, **kwargs):
        question_calls.append(prepared.projection.prepared_signal_id)
        return real_evaluate(prepared, **kwargs)

    monkeypatch.setattr(
        owner_backfill_module,
        "prepare_physical_signal",
        counted_prepare,
    )
    monkeypatch.setattr(
        owner_backfill_module,
        "evaluate_physical_peak",
        counted_evaluate,
    )

    result = build_owner_backfill_result(
        (
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        raw_xic_batch_size=1,
    )

    assert source.batch_sizes == [1]
    assert len(prepare_calls) == 1
    assert question_calls == prepare_calls * 2
    assert len(result.peak_fact_observations) == 2
    assert {
        observation.physical_evaluation.prepared.prepared_signal_id
        for observation in result.peak_fact_observations
        if observation.physical_evaluation is not None
    } == set(prepare_calls)


def test_typed_fanout_preserves_logical_identity_to_consumer_boundary(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _OwnerBoundBatchSource(source_identity)
    features = (
        _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
        _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5),
    )
    plan = build_owner_backfill_request_plan(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(),
        equivalence_context=_context({"sample-b": source_identity}),
    )
    logical_items = plan.requests_for_sample("sample-b")
    chunks = tuple(
        _iter_extracted_request_traces(
            source,
            logical_items,
            1,
            window_strategy="exact",
            superwindow_span_factor=2,
            equivalence_classes=plan.equivalence_classes_for_sample("sample-b"),
        )
    )

    assert source.batch_sizes == [1]
    assert len(chunks) == 1
    chunk, results = chunks[0]
    assert chunk == logical_items
    assert all(
        isinstance(result, OwnerBackfillLogicalExtractionResult) for result in results
    )
    typed_results = tuple(
        result
        for result in results
        if isinstance(result, OwnerBackfillLogicalExtractionResult)
    )
    assert [result.logical_request.item for result in typed_results] == list(chunk)
    assert (
        len({result.logical_request.logical_request_id for result in typed_results})
        == 2
    )
    assert typed_results[0].canonical_result is typed_results[1].canonical_result


def test_exact_superwindow_failure_falls_back_with_exact_typed_identity(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _FailingOwnerBoundWindowSource(source_identity)
    features = (
        _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
        _feature(feature_family_id="FAM000002", mz=500.0, rt=8.8),
    )
    alignment_config = AlignmentConfig(max_rt_sec=60.0)

    result = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=alignment_config,
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
        owner_backfill_window_strategy="super-window",
    )
    baseline = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _FailingOwnerBoundWindowSource(source_identity)},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=alignment_config,
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
        owner_backfill_window_strategy="exact",
    )
    typed_source = _FailingOwnerBoundWindowSource(source_identity)
    typed_plan = build_owner_backfill_request_plan(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=alignment_config,
        equivalence_context=replace(
            _context({"sample-b": source_identity}),
            window_strategy="super-window",
        ),
    )
    typed_chunks = tuple(
        _iter_extracted_request_traces(
            typed_source,
            typed_plan.requests_for_sample("sample-b"),
            64,
            window_strategy="super-window",
            superwindow_span_factor=2,
            equivalence_classes=typed_plan.equivalence_classes_for_sample("sample-b"),
        )
    )
    typed_results = tuple(
        item
        for _chunk, chunk_results in typed_chunks
        for item in chunk_results
        if isinstance(item, OwnerBackfillLogicalExtractionResult)
    )

    assert source.batch_sizes == [2, 2]
    assert typed_source.batch_sizes == [2, 2]
    assert len(typed_results) == 2
    assert {
        item.canonical_result.request_identity.window_strategy for item in typed_results
    } == {"exact"}
    assert result.cells == baseline.cells
    assert result.candidate_audit_rows == baseline.candidate_audit_rows
    assert [
        (item.logical_request_count, item.canonical_request_class_count)
        for item in result.request_reuse_observations
    ] == [(2, 2)]


@pytest.mark.parametrize("backend", ("raw", "ms1_index", "ms1_index_hybrid"))
def test_serial_backend_wrappers_preserve_owner_acquisition_identity(
    tmp_path: Path,
    backend: OwnerBackfillXicBackend,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    backfill_sources, validation_sources, _stats = timed_owner_backfill_sources(
        {"sample-b": _OwnerBoundBatchSource(source_identity)},
        backend=backend,
        stage="test.owner_backfill",
    )

    assert backfill_sources["sample-b"].acquisition_source == source_identity
    if backend == "ms1_index_hybrid":
        assert validation_sources is not None
        assert validation_sources["sample-b"].acquisition_source == source_identity
    else:
        assert validation_sources is None


def test_exact_reuse_preserves_cells_candidate_audit_and_final_matrix(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5)
    features = (feature_a, feature_b)
    config = AlignmentConfig()
    baseline = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _OwnerBoundBatchSource(None)},
        alignment_config=config,
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )
    exact = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _OwnerBoundBatchSource(source_identity)},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=config,
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert exact.cells == baseline.cells
    assert exact.candidate_audit_rows == baseline.candidate_audit_rows
    baseline_matrix = build_owner_alignment_matrix(
        features,
        sample_order=("sample-a", "sample-b"),
        ambiguous_by_sample={},
        rescued_cells=baseline.cells,
    )
    exact_matrix = build_owner_alignment_matrix(
        features,
        sample_order=("sample-a", "sample-b"),
        ambiguous_by_sample={},
        rescued_cells=exact.cells,
    )
    baseline_claimed = apply_ms1_peak_claim_registry(baseline_matrix, config)
    exact_claimed = apply_ms1_peak_claim_registry(exact_matrix, config)
    assert exact_claimed == baseline_claimed
    baseline_final = consolidate_primary_family_rows(baseline_claimed, config)
    exact_final = consolidate_primary_family_rows(exact_claimed, config)
    assert exact_final == baseline_final
    baseline_tsv = write_alignment_cells_tsv(
        tmp_path / "baseline_cells.tsv",
        baseline_final,
    )
    exact_tsv = write_alignment_cells_tsv(
        tmp_path / "exact_cells.tsv",
        exact_final,
    )
    assert exact_tsv.read_bytes() == baseline_tsv.read_bytes()


def test_hybrid_validation_keeps_phases_in_separate_exact_classes(
    tmp_path: Path,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    prefilter = _OwnerBoundBatchSource(source_identity, height=120.0)
    validator = _OwnerBoundBatchSource(source_identity, height=260.0)
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5)

    result = build_owner_backfill_result(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": prefilter},
        validation_raw_sources={"sample-b": validator},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="ms1_index_hybrid",
    )

    assert prefilter.batch_sizes == [1]
    assert validator.batch_sizes == [1]
    assert [item.validation_mode for item in result.request_reuse_observations] == [
        "prefilter",
        "validation",
    ]
    assert [
        (item.logical_request_count, item.canonical_request_class_count)
        for item in result.request_reuse_observations
    ] == [(2, 1), (2, 1)]
    assert len(result.cells) == 2
    assert {cell.height for cell in result.cells} == {260.0}


def test_owner_bound_identity_is_required_and_must_match_source(
    tmp_path: Path,
) -> None:
    expected = _acquisition_source(tmp_path, "expected", b"expected")
    different = _acquisition_source(tmp_path, "different", b"different")
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)

    with pytest.raises(ValueError, match="is not owner-bound"):
        build_owner_backfill_result(
            (feature,),
            sample_order=("sample-a", "sample-b"),
            raw_sources={"sample-b": _OwnerBoundBatchSource(None)},
            expected_acquisition_sources={"sample-b": expected},
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
        )
    with pytest.raises(ValueError, match="different acquisition identity"):
        build_owner_backfill_result(
            (feature,),
            sample_order=("sample-a", "sample-b"),
            raw_sources={"sample-b": _OwnerBoundBatchSource(different)},
            expected_acquisition_sources={"sample-b": expected},
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
        )


def test_process_worker_rebuilds_owner_partition_and_fans_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source = _OwnerBoundBatchSource(source_identity)
    raw_session_capability = mint_raw_acquisition_session_capability(source_identity)
    source.raw_open_receipt = _mint_raw_acquisition_session_receipt(
        raw_session_capability
    )
    raw_open_calls = []

    def fake_open_raw(*_args, **_kwargs):
        raw_open_calls.append((_args, _kwargs))
        return source

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        fake_open_raw,
    )
    features = (
        _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
        _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5),
    )
    serial_result = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _OwnerBoundBatchSource(source_identity)},
        expected_acquisition_sources={"sample-b": source_identity},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="raw",
    )
    job = OwnerBackfillSampleJob(
        sample_index=1,
        sample_stem="sample-b",
        raw_path=tmp_path / "sample-b.raw",
        dll_dir=tmp_path / "dll",
        sample_projection=build_owner_backfill_sample_runtime_projections(
            features,
            sample_order=("sample-a", "sample-b"),
            raw_sample_stems=frozenset({"sample-b"}),
            alignment_config=AlignmentConfig(),
        )["sample-b"],
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        expected_acquisition_source=source_identity,
        raw_session_capability=raw_session_capability,
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="raw",
        feature_payload_count=2,
        request_payload_count=2,
    )

    result = extract_owner_backfill_sample_job(job)

    assert isinstance(result, OwnerBackfillSampleResult)
    assert source.batch_sizes == [1]
    assert len(result.cells) == 2
    assert result.cells == serial_result.cells
    assert result.candidate_audit_rows == serial_result.candidate_audit_rows
    assert [
        (item.logical_request_count, item.canonical_request_class_count)
        for item in result.request_reuse_observations
    ] == [(2, 1)]
    assert len(result.peak_fact_observations) == 2
    assert [
        (
            observation.logical_request.logical_request_id,
            observation.logical_request.group_hypothesis_id,
            observation.request_identity.request_identity_id,
            observation.cell,
        )
        for observation in result.peak_fact_observations
    ] == [
        (
            observation.logical_request.logical_request_id,
            observation.logical_request.group_hypothesis_id,
            observation.request_identity.request_identity_id,
            observation.cell,
        )
        for observation in serial_result.peak_fact_observations
    ]
    assert [
        (
            observation.work_unit_key,
            observation.prepared_signal_id,
            observation.physical_evaluation.operations,
        )
        for observation in result.peak_fact_observations
        if observation.physical_evaluation is not None
    ] == [
        (
            observation.work_unit_key,
            observation.prepared_signal_id,
            observation.physical_evaluation.operations,
        )
        for observation in serial_result.peak_fact_observations
        if observation.physical_evaluation is not None
    ]
    prepared_signal_ids = {
        observation.physical_evaluation.prepared.prepared_signal_id
        for observation in result.peak_fact_observations
        if observation.physical_evaluation is not None
    }
    assert len(prepared_signal_ids) == 1
    assert result.peak_fact_cost is not None
    assert result.peak_fact_cost.raw_open_count == 1
    assert result.peak_fact_cost.raw_open_unavailable_reason == ""
    assert result.peak_fact_cost.trace_evaluation_count == 1
    assert result.peak_fact_cost.asls_baseline_count == 1
    assert result.peak_fact_cost.smoothing_count == 1
    assert result.peak_fact_cost.segment_enumeration_count == 2
    assert result.peak_fact_cost.integration_count == 2
    assert result.peak_fact_cost.normalization_count == 2
    assert result.peak_fact_cost.raw_descriptor_projection_count == 2
    assert result.peak_fact_cost.artifact_round_trip_count == 0
    assert result.peak_fact_cost.sealed_observation_count == 2
    assert result.peak_fact_cost.identity_unavailable_count == 0
    assert result.peak_fact_cost.elapsed_seconds is not None
    assert result.peak_fact_cost.measurement_source == "execution_runtime_owner:v1"
    assert len(raw_open_calls) == 1
    assert raw_open_calls[0][1]["_raw_session_capability"] == (raw_session_capability)

    unavailable_source = _OwnerBoundBatchSource(source_identity, height=0.0)
    unavailable_source.raw_open_receipt = _mint_raw_acquisition_session_receipt(
        raw_session_capability
    )
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda *_args, **_kwargs: unavailable_source,
    )
    unavailable = extract_owner_backfill_sample_job(job)

    assert isinstance(unavailable, OwnerBackfillSampleResult)
    assert len(unavailable.peak_fact_observations) == 2
    assert {
        observation.physical_evaluation.operations
        for observation in unavailable.peak_fact_observations
        if observation.physical_evaluation is not None
    } == {("trace_evaluation",)}
    assert unavailable.peak_fact_cost is not None
    assert unavailable.peak_fact_cost.trace_evaluation_count == 1
    assert unavailable.peak_fact_cost.asls_baseline_count == 0
    assert unavailable.peak_fact_cost.smoothing_count == 0
    assert unavailable.peak_fact_cost.segment_enumeration_count == 0
    assert unavailable.peak_fact_cost.integration_count == 0
    assert unavailable.peak_fact_cost.normalization_count == 0
    assert unavailable.peak_fact_cost.raw_descriptor_projection_count == 0


def test_process_worker_preserves_global_candidate_identity_across_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a_identity = _acquisition_source(tmp_path, "sample-a", b"sample-a")
    source_b_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    source_b = _OwnerBoundBatchSource(source_b_identity)
    raw_session_capability = mint_raw_acquisition_session_capability(
        source_b_identity
    )
    source_b.raw_open_receipt = _mint_raw_acquisition_session_receipt(
        raw_session_capability
    )
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda *_args, **_kwargs: source_b,
    )
    features = (
        replace(
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            confirm_local_owners_with_backfill=True,
            owners=(
                replace(_owner("sample-a", "a", apex_rt=8.5), owner_area=10.0),
                _owner("sample-x", "x", apex_rt=8.5),
            ),
        ),
        replace(
            _feature(feature_family_id="FAM000002", mz=501.0, rt=8.6),
            confirm_local_owners_with_backfill=True,
            owners=(
                replace(_owner("sample-b", "b", apex_rt=8.6), owner_area=10.0),
                _owner("sample-x", "x", apex_rt=8.6),
            ),
        ),
    )
    serial_result = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={
            "sample-a": _OwnerBoundBatchSource(source_a_identity),
            "sample-b": _OwnerBoundBatchSource(source_b_identity),
        },
        expected_acquisition_sources={
            "sample-a": source_a_identity,
            "sample-b": source_b_identity,
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="raw",
    )
    job = OwnerBackfillSampleJob(
        sample_index=2,
        sample_stem="sample-b",
        raw_path=tmp_path / "sample-b.raw",
        dll_dir=tmp_path / "dll",
        sample_projection=build_owner_backfill_sample_runtime_projections(
            features,
            sample_order=("sample-a", "sample-b"),
            raw_sample_stems=frozenset({"sample-a", "sample-b"}),
            alignment_config=AlignmentConfig(),
        )["sample-b"],
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        expected_acquisition_source=source_b_identity,
        raw_session_capability=raw_session_capability,
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="raw",
        feature_payload_count=2,
        request_payload_count=2,
    )

    result = extract_owner_backfill_sample_job(job)
    serial_sample_b = tuple(
        observation
        for observation in serial_result.peak_fact_observations
        if observation.cell.sample_stem == "sample-b"
    )

    assert isinstance(result, OwnerBackfillSampleResult), result
    assert [
        observation.work_unit_key for observation in result.peak_fact_observations
    ] == [observation.work_unit_key for observation in serial_sample_b]


def test_process_worker_preserves_global_candidate_identity_for_hybrid_rescue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a_identity = _acquisition_source(tmp_path, "sample-a", b"sample-a")
    source_b_identity = _acquisition_source(tmp_path, "sample-b", b"sample-b")
    prefilter_a = _OwnerBoundBatchSource(source_a_identity, height=120.0)
    prefilter_b = _OwnerBoundBatchSource(source_b_identity, height=120.0)
    validator_a = _OwnerBoundBatchSource(source_a_identity, height=260.0)
    validator_b = _OwnerBoundBatchSource(source_b_identity, height=260.0)
    raw_session_capability = mint_raw_acquisition_session_capability(source_b_identity)
    validator_b.raw_open_receipt = _mint_raw_acquisition_session_receipt(
        raw_session_capability
    )
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda *_args, **_kwargs: validator_b,
    )
    monkeypatch.setattr(
        "xic_extractor.alignment.process_backend.source_for_owner_backfill_backend",
        lambda _raw, _backend: prefilter_b,
    )
    features = (
        replace(
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            confirm_local_owners_with_backfill=True,
            owners=(
                replace(_owner("sample-a", "a", apex_rt=8.5), owner_area=10.0),
                _owner("sample-x", "x", apex_rt=8.5),
            ),
        ),
        replace(
            _feature(feature_family_id="FAM000002", mz=501.0, rt=8.6),
            confirm_local_owners_with_backfill=True,
            owners=(
                replace(_owner("sample-b", "b", apex_rt=8.6), owner_area=10.0),
                _owner("sample-x", "x", apex_rt=8.6),
            ),
        ),
    )
    serial_result = build_owner_backfill_result(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-a": prefilter_a, "sample-b": prefilter_b},
        validation_raw_sources={"sample-a": validator_a, "sample-b": validator_b},
        expected_acquisition_sources={
            "sample-a": source_a_identity,
            "sample-b": source_b_identity,
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="ms1_index_hybrid",
    )
    projection = build_owner_backfill_sample_runtime_projections(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_sample_stems=frozenset({"sample-a", "sample-b"}),
        alignment_config=AlignmentConfig(),
    )["sample-b"]
    job = OwnerBackfillSampleJob(
        sample_index=2,
        sample_stem="sample-b",
        raw_path=tmp_path / "sample-b.raw",
        dll_dir=tmp_path / "dll",
        sample_projection=projection,
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
        expected_acquisition_source=source_b_identity,
        raw_session_capability=raw_session_capability,
        raw_xic_batch_size=64,
        owner_backfill_xic_backend="ms1_index_hybrid",
        feature_payload_count=len(projection.features),
        request_payload_count=projection.logical_request_count,
    )

    result = extract_owner_backfill_sample_job(job)
    serial_sample_b = tuple(
        observation
        for observation in serial_result.peak_fact_observations
        if observation.cell.sample_stem == "sample-b"
    )

    assert isinstance(result, OwnerBackfillSampleResult), result
    assert [
        observation.work_unit_key for observation in result.peak_fact_observations
    ] == [observation.work_unit_key for observation in serial_sample_b]


def _context(
    acquisition_sources: dict[str, AcquisitionSourceIdentity],
) -> OwnerBackfillRequestEquivalenceContext:
    return OwnerBackfillRequestEquivalenceContext(
        acquisition_sources=acquisition_sources,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
    )


def _identity(
    acquisition_source: AcquisitionSourceIdentity,
    *,
    sample_stem: str = "sample-a",
    request: XICRequest = XICRequest(500.0, 7.5, 9.5, 20.0),
    backend: OwnerBackfillXicBackend = "raw",
    window_strategy: OwnerBackfillWindowStrategy = "exact",
    validation_mode: OwnerBackfillValidationMode = "primary",
    result_semantics: str = OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
) -> OwnerBackfillRequestIdentity:
    return mint_owner_backfill_request_identity(
        sample_stem=sample_stem,
        acquisition_source=acquisition_source,
        request=request,
        backend=backend,
        window_strategy=window_strategy,
        superwindow_span_factor=2,
        validation_mode=validation_mode,
        result_semantics=result_semantics,
    )


def _acquisition_source(
    tmp_path: Path,
    stem: str,
    content: bytes,
) -> AcquisitionSourceIdentity:
    path = tmp_path / f"{stem}.raw"
    path.write_bytes(content)
    return build_acquisition_source_identity(
        path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )


def _trace_for_request(request: XICRequest, *, height: float) -> XICTrace:
    center = (request.rt_min + request.rt_max) / 2.0
    return XICTrace.from_arrays(
        [center - 0.10, center - 0.01, center, center + 0.01, center + 0.10],
        [0.0, height / 2.0, height, height / 2.0, 0.0],
    )


def _request_plan_projection(
    items: tuple[object, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.feature.feature_family_id,
            item.sample_stem,
            item.request,
            item.preferred_rt,
        )
        for item in items
    )
