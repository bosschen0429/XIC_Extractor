import csv
import json
from contextlib import contextmanager
from dataclasses import replace
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
from tests.alignment_pipeline_helpers import (
    write_owner_closed_partial_batch as _write_owner_closed_partial_batch,
)
from tests.test_targeted_real_data_adapter import _settings
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.csv_io import read_discovery_candidates_csv
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.quantitative_projection_authority import (
    QuantitativeProjectionAuthorityHoldError,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


class _RouteReachedBatch(RuntimeError):
    pass


class _FamilyId(str):
    pass


class _FamilyIds(frozenset):
    pass


def test_pure_single_provider_cid_nl_dispatches_before_legacy_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    expected = pipeline_module.AlignmentRunOutputs(
        matrix_tsv=tmp_path / "out" / "alignment_matrix.tsv"
    )
    calls: list[dict[str, object]] = []

    def run_cid_nl(**kwargs: object):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        pipeline_module,
        "load_quantitative_projection_authority_for_diagnostic",
        lambda: object(),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        run_cid_nl,
    )

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        raw_opener=lambda *_args, **_kwargs: pytest.fail("legacy RAW route ran"),
    )

    assert result is expected
    assert len(calls) == 1
    assert calls[0]["settings"] == _settings()


def test_intersection_settings_remain_on_legacy_alignment_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    settings = _settings()
    intersection = replace(settings, tag_combine_mode="intersection")

    monkeypatch.setattr(
        pipeline_module,
        "load_quantitative_projection_authority_for_diagnostic",
        lambda: object(),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("intersection crossed into CID-NL route"),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_output_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_RouteReachedBatch()),
    )

    with pytest.raises(_RouteReachedBatch):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=intersection,
        )


def _allow_product_quantitative_projection_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        lambda **_kwargs: None,
    )


@pytest.mark.parametrize(
    (
        "backfill_scope",
        "default_profile",
        "selected_family_ids",
        "selected_family_source",
        "expected_outcome",
        "expected_authority",
    ),
    (
        pytest.param("production-equivalent", True, frozenset(), "", "hold", "product"),
        pytest.param(
            "production-equivalent", True, frozenset(), "  ", "hold", "product"
        ),
        pytest.param(
            "production-equivalent",
            True,
            frozenset({"FAM001"}),
            "",
            "reject",
            "none",
        ),
        pytest.param(
            "production-equivalent",
            True,
            frozenset(),
            "families.tsv",
            "reject",
            "none",
        ),
        pytest.param("production-equivalent", False, frozenset(), "", "reject", "none"),
        pytest.param(
            "production-equivalent", False, frozenset(), "  ", "reject", "none"
        ),
        pytest.param(
            "production-equivalent",
            False,
            frozenset({"FAM001"}),
            "",
            "reject",
            "none",
        ),
        pytest.param(
            "production-equivalent",
            False,
            frozenset(),
            "families.tsv",
            "reject",
            "none",
        ),
        pytest.param("full-audit", True, frozenset(), "", "continue", "diagnostic"),
        pytest.param("full-audit", True, frozenset(), "  ", "continue", "diagnostic"),
        pytest.param(
            "full-audit",
            True,
            frozenset({"FAM001"}),
            "",
            "reject",
            "none",
        ),
        pytest.param(
            "full-audit",
            True,
            frozenset(),
            "families.tsv",
            "reject",
            "none",
        ),
        pytest.param("full-audit", False, frozenset(), "", "continue", "none"),
        pytest.param("full-audit", False, frozenset(), "  ", "continue", "none"),
        pytest.param(
            "full-audit",
            False,
            frozenset({"FAM001"}),
            "",
            "reject",
            "none",
        ),
        pytest.param(
            "full-audit",
            False,
            frozenset(),
            "families.tsv",
            "reject",
            "none",
        ),
        pytest.param("selected-families", True, frozenset(), "", "continue", "none"),
        pytest.param("selected-families", True, frozenset(), "  ", "continue", "none"),
        pytest.param(
            "selected-families",
            True,
            frozenset({"FAM001"}),
            "",
            "continue",
            "none",
        ),
        pytest.param(
            "selected-families",
            True,
            frozenset(),
            "families.tsv",
            "continue",
            "none",
        ),
        pytest.param("selected-families", False, frozenset(), "", "continue", "none"),
        pytest.param("selected-families", False, frozenset(), "  ", "continue", "none"),
        pytest.param(
            "selected-families",
            False,
            frozenset({"FAM001"}),
            "",
            "continue",
            "none",
        ),
        pytest.param(
            "selected-families",
            False,
            frozenset(),
            "families.tsv",
            "continue",
            "none",
        ),
    ),
)
def test_pipeline_quantitative_projection_authority_route_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_scope: str,
    default_profile: bool,
    selected_family_ids: frozenset[str],
    selected_family_source: str,
    expected_outcome: str,
    expected_authority: str,
) -> None:
    authority_calls: list[tuple[str, dict[str, str]]] = []

    def require_product_authority(**kwargs: str) -> None:
        authority_calls.append(("product", kwargs))
        raise QuantitativeProjectionAuthorityHoldError(
            "HOLD: quantitative_projection_authority_pending_validation"
        )

    def load_diagnostic_authority() -> object:
        authority_calls.append(("diagnostic", {}))
        return object()

    def stop_at_batch(*_args: object, **_kwargs: object) -> None:
        raise _RouteReachedBatch

    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        require_product_authority,
    )
    monkeypatch.setattr(
        pipeline_module,
        "load_quantitative_projection_authority_for_diagnostic",
        load_diagnostic_authority,
    )
    monkeypatch.setattr(pipeline_module, "read_discovery_batch_index", stop_at_batch)

    peak_config = _peak_config()
    if not default_profile:
        peak_config = replace(peak_config, signal_processing_window_points=21)

    kwargs = {
        "discovery_batch_index": tmp_path / "batch.csv",
        "raw_dir": tmp_path / "raw",
        "dll_dir": tmp_path / "dll",
        "output_dir": tmp_path / "out",
        "alignment_config": AlignmentConfig(),
        "peak_config": peak_config,
        "output_level": "debug",
        "backfill_scope": backfill_scope,
        "selected_family_ids": selected_family_ids,
        "selected_family_source": selected_family_source,
    }
    if expected_outcome == "hold":
        with pytest.raises(
            QuantitativeProjectionAuthorityHoldError,
            match="^HOLD: quantitative_projection_authority_pending_validation$",
        ):
            pipeline_module.run_alignment(**kwargs)
    elif expected_outcome == "reject":
        with pytest.raises(ValueError) as exc_info:
            pipeline_module.run_alignment(**kwargs)
        assert type(exc_info.value) is ValueError
    else:
        with pytest.raises(_RouteReachedBatch):
            pipeline_module.run_alignment(**kwargs)

    if expected_authority == "product":
        assert authority_calls == [
            (
                "product",
                {
                    "consumer_id": (
                        "xic_extractor.alignment.pipeline.run_alignment:"
                        "production-equivalent"
                    ),
                    "signal_processing_profile_id": (
                        resolve_signal_processing_profile().profile_id
                    ),
                },
            )
        ]
    elif expected_authority == "diagnostic":
        assert authority_calls == [("diagnostic", {})]
    else:
        assert authority_calls == []


@pytest.mark.parametrize(
    "selected_family_ids",
    (
        pytest.param(set(), id="set"),
        pytest.param(_FamilyIds(), id="frozenset-subclass"),
        pytest.param(frozenset({1}), id="non-string-element"),
        pytest.param(frozenset({_FamilyId("FAM001")}), id="string-subclass"),
    ),
)
def test_pipeline_rejects_non_exact_selected_family_ids_before_authority_or_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected_family_ids: object,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid selector type must fail before authority or I/O")

    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        fail_if_called,
    )
    monkeypatch.setattr(
        pipeline_module,
        "load_quantitative_projection_authority_for_diagnostic",
        fail_if_called,
    )
    monkeypatch.setattr(
        pipeline_module,
        "read_discovery_batch_index",
        fail_if_called,
    )

    with pytest.raises(
        TypeError, match=r"^selected_family_ids must be an exact frozenset\[str\]$"
    ):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            backfill_scope="production-equivalent",
            selected_family_ids=selected_family_ids,  # type: ignore[arg-type]
        )


def test_pipeline_authority_hold_precedes_timing_batch_output_and_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_calls: list[dict[str, str]] = []

    def hold_product_authority(**kwargs: str) -> None:
        authority_calls.append(kwargs)
        raise QuantitativeProjectionAuthorityHoldError(
            "HOLD: quantitative_projection_authority_pending_validation"
        )

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "authority HOLD must precede timing, batch, output, and RAW"
        )

    class FailTimingRecorder:
        @classmethod
        def disabled(cls, *_args: object, **_kwargs: object) -> None:
            fail_if_called()

    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        hold_product_authority,
    )
    monkeypatch.setattr(pipeline_module, "TimingRecorder", FailTimingRecorder)
    monkeypatch.setattr(pipeline_module, "read_discovery_batch_index", fail_if_called)
    monkeypatch.setattr(pipeline_module, "_output_paths", fail_if_called)
    monkeypatch.setattr(pipeline_module, "_existing_raw_paths", fail_if_called)

    with pytest.raises(
        QuantitativeProjectionAuthorityHoldError,
        match="^HOLD: quantitative_projection_authority_pending_validation$",
    ):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=fail_if_called,
            backfill_scope="production-equivalent",
        )

    assert authority_calls == [
        {
            "consumer_id": (
                "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
            ),
            "signal_processing_profile_id": (
                resolve_signal_processing_profile().profile_id
            ),
        }
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({}, "requires Discovery owner closure"),
        (
            {
                "require_discovery_owner_closure": True,
                "backfill_scope": "selected-families",
            },
            "requires full-audit Backfill",
        ),
        (
            {
                "require_discovery_owner_closure": True,
                "selected_family_ids": frozenset({"FAM000001"}),
            },
            "does not allow a family selection",
        ),
    ),
)
def test_pipeline_provisional_matrix_flags_fail_closed(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            emit_provisional_matrix=True,
            **overrides,
        )


def test_pipeline_owner_closure_rejects_legacy_batch_index(tmp_path: Path) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",), owner_closed=False)

    with pytest.raises(ValueError, match="owner closure is required"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            require_discovery_owner_closure=True,
        )


def test_pipeline_legacy_raw_only_route_fails_before_cross_sample_grouping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",), owner_closed=False)
    grouping_called = False

    def fail_grouping(*_args, **_kwargs):
        nonlocal grouping_called
        grouping_called = True
        raise AssertionError("grouping must not receive an unclosed owner")

    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fail_grouping,
    )

    with pytest.raises(ValueError, match="owner-closed acquisition"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
        )

    assert grouping_called is False


def test_pipeline_rejects_profile_config_conflict_before_read_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = False

    def fail_if_touched(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("entry validation must fail before batch or RAW work")

    monkeypatch.setattr(pipeline_module, "read_discovery_batch_index", fail_if_touched)

    with pytest.raises(ValueError, match="conflicts with peak_config"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=replace(
                _peak_config(),
                signal_processing_window_points=21,
            ),
            signal_processing_profile=resolve_signal_processing_profile(
                signal_processing_window_points=15,
            ),
        )

    assert touched is False


@pytest.mark.parametrize("suffix", ("", ".tmp", ".bak"))
def test_pipeline_rejects_owned_output_collision_before_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    sentinel = output_dir / f"alignment_matrix.tsv{suffix}"
    sentinel.write_bytes(b"sentinel")
    opened = False

    @contextmanager
    def fail_opener(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("RAW must not open after an output collision")
        yield

    monkeypatch.setattr(pipeline_module, "read_discovery_candidates_csv", lambda _p: ())

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=fail_opener,
        )

    assert opened is False
    assert sentinel.read_bytes() == b"sentinel"


def test_pipeline_owner_closure_rejects_raw_replaced_after_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_path = raw_dir / "Sample_A.raw"
    raw_path.write_bytes(b"discovery bytes")
    batch_index = _write_owner_closed_batch(tmp_path, raw_path)
    raw_path.write_bytes(b"replacement bytes")
    owner_build_called = False

    @contextmanager
    def owner_bound_opener(path, _dll_dir, *, expected_acquisition_source=None):
        actual = build_acquisition_source_identity(
            path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        if actual != expected_acquisition_source:
            raise ValueError("RAW path content does not match expected acquisition")
        yield SimpleNamespace()

    def fail_owner_build(*_args, **_kwargs):
        nonlocal owner_build_called
        owner_build_called = True
        raise AssertionError("owner build must not start")

    monkeypatch.setattr(pipeline_module, "_default_raw_opener", owner_bound_opener)
    monkeypatch.setattr(pipeline_module, "build_sample_local_owners", fail_owner_build)

    with pytest.raises(ValueError, match="RAW path content does not match"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            require_discovery_owner_closure=True,
            emit_provisional_matrix=True,
        )

    assert owner_build_called is False
    assert not (tmp_path / "out").exists()


def test_cross_sample_request_binds_decisions_not_presentation_or_excluded_rows(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "Sample_A.raw"
    raw_path.write_bytes(b"stable acquisition")
    roots = tuple(tmp_path / f"request-{index}" for index in range(5))
    for root in roots:
        root.mkdir()
    batches = (
        _write_owner_closed_batch(roots[0], raw_path),
        _write_owner_closed_batch(
            roots[1], raw_path, candidate_updates={"evidence_score": "81"}
        ),
        _write_owner_closed_batch(
            roots[2], raw_path, candidate_updates={"review_priority": "LOW"}
        ),
        _write_owner_closed_batch(
            roots[3],
            raw_path,
            candidate_updates={
                "discovery_candidate_state": "rejected_noise_or_outside_rt"
            },
        ),
        _write_owner_closed_batch(
            roots[4],
            raw_path,
            candidate_updates={
                "discovery_candidate_state": "rejected_noise_or_outside_rt",
                "evidence_score": "81",
            },
        ),
    )
    baseline, decision_change, presentation_change, rejected, rejected_change = (
        _cross_sample_request_id(batch_index) for batch_index in batches
    )

    assert decision_change != baseline
    assert presentation_change == baseline
    assert rejected_change == rejected


def test_cross_sample_request_binds_owner_config_not_value_profile(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "Sample_A.raw"
    raw_path.write_bytes(b"stable acquisition")
    batch_index = _write_owner_closed_batch(tmp_path, raw_path)

    baseline = _cross_sample_request_id(batch_index)
    owner_decision_change = _cross_sample_request_id(
        batch_index,
        peak_config=replace(_peak_config(), resolver_min_absolute_height=26.0),
    )
    value_profile_change = _cross_sample_request_id(
        batch_index,
        peak_config=replace(_peak_config(), signal_processing_window_points=21),
    )

    assert owner_decision_change != baseline
    assert value_profile_change == baseline


def test_cross_sample_request_binds_drift_decisions_not_diagnostic_order(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "Sample_A.raw"
    raw_path.write_bytes(b"stable acquisition")
    batch_index = _write_owner_closed_batch(tmp_path, raw_path)

    baseline = _cross_sample_request_id(
        batch_index,
        drift_lookup=_DriftLookup(delta_min=0.1, injection_order=1),
    )
    delta_change = _cross_sample_request_id(
        batch_index,
        drift_lookup=_DriftLookup(delta_min=0.2, injection_order=1),
    )
    diagnostic_order_change = _cross_sample_request_id(
        batch_index,
        drift_lookup=_DriftLookup(delta_min=0.1, injection_order=2),
    )
    source_change = _cross_sample_request_id(
        batch_index,
        drift_lookup=_DriftLookup(
            delta_min=0.1,
            injection_order=1,
            source="batch_istd_trend",
        ),
    )

    assert delta_change != baseline
    assert diagnostic_order_change == baseline
    assert source_change != baseline


def test_cross_sample_request_binds_supplied_sample_order(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_a = raw_dir / "Sample_A.raw"
    raw_b = raw_dir / "Sample_B.raw"
    raw_a.write_bytes(b"stable acquisition a")
    raw_b.write_bytes(b"stable acquisition b")
    batch_index = _write_owner_closed_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A", "Sample_B"),
        raw_files={"Sample_A": raw_a, "Sample_B": raw_b},
    )

    forward = _cross_sample_request_id(
        batch_index,
        injection_order={"Sample_A": 1, "Sample_B": 2},
    )
    reverse = _cross_sample_request_id(
        batch_index,
        injection_order={"Sample_A": 2, "Sample_B": 1},
    )
    equivalent = _cross_sample_request_id(
        batch_index,
        injection_order={"Sample_B": 2, "Sample_A": 1},
    )

    assert forward != reverse
    assert forward == equivalent


def test_pipeline_loads_candidates_builds_owners_backfills_and_writes_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw-a", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw-b", encoding="utf-8")
    batch_index = _write_owner_closed_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A", "Sample_B"),
        raw_files={
            "Sample_A": raw_dir / "Sample_A.raw",
            "Sample_B": raw_dir / "Sample_B.raw",
        },
    )
    calls: dict[str, object] = {}
    read_candidates = pipeline_module.read_discovery_candidates_csv

    def read_with_review_only_orphan(path, **kwargs):
        current = read_candidates(path, **kwargs)
        if current[0].sample_stem != "Sample_A":
            return current
        return (
            *current,
            replace(
                current[0],
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

    def fake_build_owners(
        candidates,
        *,
        raw_sources,
        alignment_config,
        peak_config,
        raw_xic_batch_size,
        emit_region_audit,
        region_audit_family_ids,
        audit_evidence_mode,
        cross_sample_identity_context,
    ):
        calls["candidates"] = tuple(candidate.sample_stem for candidate in candidates)
        calls["candidate_states"] = tuple(
            candidate.discovery_candidate_state for candidate in candidates
        )
        calls["owner_raw_sources"] = raw_sources
        calls["alignment_config"] = alignment_config
        calls["peak_config"] = peak_config
        calls["owner_raw_xic_batch_size"] = raw_xic_batch_size
        calls["owner_emit_region_audit"] = emit_region_audit
        calls["owner_region_audit_family_ids"] = region_audit_family_ids
        calls["owner_audit_evidence_mode"] = audit_evidence_mode
        calls["cross_sample_identity_context"] = cross_sample_identity_context
        return SimpleNamespace(owners=("owner",), ambiguous_records=())

    def fake_cluster_owners(
        owners,
        *,
        config,
        sample_universe=None,
        injection_order=None,
    ):
        calls["owners"] = owners
        calls["cluster_config"] = config
        calls["sample_universe"] = sample_universe
        calls["injection_order"] = injection_order
        return ("feature",)

    def fake_owner_backfill(
        features,
        *,
        sample_order,
        raw_sources,
        alignment_config,
        peak_config,
        raw_xic_batch_size,
        owner_backfill_window_strategy,
        owner_backfill_superwindow_span_factor,
        emit_region_audit,
        region_audit_family_ids,
        audit_evidence_mode,
    ):
        calls["features"] = features
        calls["sample_order"] = sample_order
        calls["raw_sources"] = raw_sources
        calls["backfill_raw_xic_batch_size"] = raw_xic_batch_size
        calls["owner_backfill_window_strategy"] = owner_backfill_window_strategy
        calls["owner_backfill_superwindow_span_factor"] = (
            owner_backfill_superwindow_span_factor
        )
        calls["emit_region_audit"] = emit_region_audit
        calls["backfill_region_audit_family_ids"] = region_audit_family_ids
        calls["audit_evidence_mode"] = audit_evidence_mode
        return ("rescued",)

    def fake_owner_matrix(
        features,
        *,
        sample_order,
        ambiguous_by_sample,
        rescued_cells,
    ):
        calls["matrix_features"] = features
        calls["ambiguous_by_sample"] = ambiguous_by_sample
        calls["rescued_cells"] = rescued_cells
        return _matrix(sample_order)

    monkeypatch.setattr(pipeline_module, "build_sample_local_owners", fake_build_owners)
    monkeypatch.setattr(
        pipeline_module,
        "read_discovery_candidates_csv",
        read_with_review_only_orphan,
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fake_cluster_owners,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        fake_owner_matrix,
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
    )

    assert calls["candidates"] == ("Sample_A", "Sample_A", "Sample_B")
    assert calls["candidate_states"] == (
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
        "ms1_feature_nl_supported",
    )
    assert calls["owners"] == ("owner",)
    assert calls["features"] == ("feature",)
    assert calls["sample_order"] == ("Sample_A", "Sample_B")
    assert calls["sample_universe"] == frozenset({"Sample_A", "Sample_B"})
    assert calls["injection_order"] is None
    assert set(calls["raw_sources"]) == {"Sample_A", "Sample_B"}
    assert calls["owner_raw_xic_batch_size"] == 1
    assert calls["owner_emit_region_audit"] is False
    assert calls["owner_region_audit_family_ids"] is None
    assert calls["owner_audit_evidence_mode"] == "none"
    assert calls["backfill_raw_xic_batch_size"] == 1
    assert calls["owner_backfill_window_strategy"] == "exact"
    assert calls["owner_backfill_superwindow_span_factor"] == 2
    assert calls["emit_region_audit"] is False
    assert calls["backfill_region_audit_family_ids"] is None
    assert calls["audit_evidence_mode"] == "none"
    assert calls["matrix_features"] == ("feature",)
    assert calls["ambiguous_by_sample"] == {}
    assert calls["rescued_cells"] == ("rescued",)
    assert outputs.review_tsv == tmp_path / "out" / "alignment_review.tsv"
    assert outputs.matrix_tsv == tmp_path / "out" / "alignment_matrix.tsv"
    assert (
        outputs.matrix_identity_tsv
        == tmp_path / "out" / "alignment_matrix_identity.tsv"
    )
    assert outputs.workbook == tmp_path / "out" / "alignment_results.xlsx"
    assert outputs.review_html == tmp_path / "out" / "review_report.html"
    assert outputs.cells_tsv is None
    assert outputs.status_matrix_tsv is None
    assert outputs.workbook.exists()
    assert outputs.review_html.exists()
    assert outputs.review_tsv.exists()
    assert outputs.matrix_tsv.exists()
    assert outputs.matrix_identity_tsv.exists()
    assert not (tmp_path / "out" / "alignment_cells.tsv").exists()
    assert not (tmp_path / "out" / "alignment_matrix_status.tsv").exists()


def test_pipeline_routes_same_run_discovery_bindings_around_old_owner_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    canonical_binding = object()
    processing_run_spec = SimpleNamespace(
        run_spec_id="run-spec-id",
        run_spec_hash="run-spec-hash",
    )
    cross_sample_identity_context = object()
    calls: dict[str, object] = {}

    def ordered_bindings(**kwargs):
        calls["binding_samples"] = tuple(kwargs["bindings_by_sample"])
        calls["candidate_samples"] = tuple(kwargs["candidates_by_sample"])
        calls["processing_run_spec"] = kwargs["run_spec"]
        return (canonical_binding,)

    def reject_old_owner_build(*_args, **_kwargs):
        raise AssertionError("same-run Discovery facts called the old owner builder")

    def build_from_bindings(
        bindings,
        *,
        alignment_config,
        cross_sample_identity_context,
    ):
        calls["bindings"] = bindings
        calls["alignment_config"] = alignment_config
        calls["cross_sample_identity_context"] = cross_sample_identity_context
        return SimpleNamespace(owners=(), ambiguous_records=())

    monkeypatch.setattr(
        pipeline_module,
        "_ordered_discovery_physical_bindings",
        ordered_bindings,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        reject_old_owner_build,
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_owner_build_process",
        reject_old_owner_build,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners_from_discovery_bindings",
        build_from_bindings,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_alignment_cross_sample_identity_context",
        lambda **_kwargs: cross_sample_identity_context,
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, *, config, sample_universe=None, injection_order=None: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        lambda features, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        audit_evidence_mode="none",
        discovery_processing_run_spec=processing_run_spec,
        discovery_physical_bindings_by_sample={"Sample_A": (canonical_binding,)},
    )

    assert calls["binding_samples"] == ("Sample_A",)
    assert calls["candidate_samples"] == ("Sample_A",)
    assert calls["processing_run_spec"] is processing_run_spec
    assert calls["bindings"] == (canonical_binding,)
    assert calls["alignment_config"] == AlignmentConfig()
    assert calls["cross_sample_identity_context"] is cross_sample_identity_context
    assert outputs.owner_build_route_receipt is not None
    assert outputs.owner_build_route_receipt.as_dict() == {
        "configured_owner_build_xic_backend": "raw",
        "effective_owner_build_route": "discovery_physical_bindings",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": "run-spec-id",
        "processing_run_spec_hash": "run-spec-hash",
    }


def test_pipeline_default_path_no_longer_uses_legacy_near_duplicate_folding() -> None:
    assert not hasattr(pipeline_module, "fold_near_duplicate_clusters")


def test_pipeline_builds_owner_features_before_owner_matrix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    batch_index = _write_owner_closed_batch(
        tmp_path,
        raw_dir / "Sample_A.raw",
    )
    calls = {}
    owner = SimpleNamespace(owner_id="OWN-Sample_A-000001")
    feature = SimpleNamespace(
        feature_family_id="FAM000001",
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        event_cluster_ids=("ALN000001",),
        event_member_count=1,
        evidence="single_sample_local_owner",
    )
    owner_matrix = AlignmentMatrix(
        clusters=(feature,),
        cells=(),
        sample_order=("Sample_A",),
    )

    def fake_build_owners(candidates, **kwargs):
        calls["candidates"] = tuple(candidate.candidate_id for candidate in candidates)
        return SimpleNamespace(owners=(owner,), ambiguous_records=("ambiguous",))

    def fake_cluster_owners(
        owners,
        *,
        config,
        sample_universe=None,
        injection_order=None,
        drift_lookup=None,
        edge_evidence_sink=None,
    ):
        calls["owners"] = owners
        return (feature,)

    def fake_backfill(features, *, sample_order, raw_sources, **kwargs):
        calls["backfill_features"] = features
        calls["sample_order"] = sample_order
        calls["raw_sources"] = raw_sources
        return ("rescued",)

    def fake_owner_matrix(features, *, ambiguous_by_sample, rescued_cells, **kwargs):
        calls["matrix_features"] = features
        calls["ambiguous_by_sample"] = ambiguous_by_sample
        calls["rescued_cells"] = rescued_cells
        return owner_matrix

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        fake_build_owners,
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fake_cluster_owners,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: ("ambiguous-feature",),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        fake_owner_matrix,
    )

    def fake_write_outputs(outputs, matrix, **kwargs):
        calls["written_matrix"] = matrix
        calls["write_kwargs"] = kwargs
        return ()

    monkeypatch.setattr(pipeline_module, "_write_outputs_atomic", fake_write_outputs)

    with pytest.raises(TypeError, match="owner-minted fingerprint batch"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=FakeRawOpener(),
            capture_artifact_fingerprints=True,
        )

    assert calls["owners"] == (owner,)
    assert calls["backfill_features"] == (feature, "ambiguous-feature")
    assert calls["matrix_features"] == (feature, "ambiguous-feature")
    assert calls["ambiguous_by_sample"] == {}
    assert calls["rescued_cells"] == ("rescued",)
    assert calls["written_matrix"] is owner_matrix
    write_kwargs = calls["write_kwargs"]
    assert isinstance(write_kwargs, dict)
    assert write_kwargs["capture_artifact_fingerprints"] is True


def test_pipeline_production_equivalent_scope_skips_safe_backfill_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_product_quantitative_projection_authority(monkeypatch)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw-a", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw-b", encoding="utf-8")
    batch_index = _write_owner_closed_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A", "Sample_B"),
        raw_files={
            "Sample_A": raw_dir / "Sample_A.raw",
            "Sample_B": raw_dir / "Sample_B.raw",
        },
    )
    feature = _scope_feature("FAM001", owners=("Sample_A",))
    calls = {}

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
            feature,
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_backfill(features, **kwargs):
        calls["backfill_features"] = features
        return ()

    def fake_owner_matrix(features, *, sample_order, **kwargs):
        calls["matrix_features"] = features
        return _matrix(sample_order)

    monkeypatch.setattr(pipeline_module, "build_owner_backfill_cells", fake_backfill)
    monkeypatch.setattr(
        pipeline_module, "build_owner_alignment_matrix", fake_owner_matrix
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        backfill_scope="production-equivalent",
    )

    assert calls["backfill_features"] == ()
    assert calls["matrix_features"] == (feature,)
    assert outputs.skipped_evidence_ledger_tsv is not None
    ledger_text = outputs.skipped_evidence_ledger_tsv.read_text(encoding="utf-8")
    assert "single_detected_no_consolidation_candidate" in ledger_text
    assert "\tSample_B\t" in ledger_text
    assert outputs.run_metadata_json is not None
    metadata = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
    assert metadata["backfill_scope"] == "production-equivalent"
    assert metadata["audit_evidence_mode"] == "none"
    assert metadata["request_plan_version"] == "p7-owner-backfill-request-plan-v1"


def test_pipeline_production_equivalent_validation_does_not_enable_heavy_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_product_quantitative_projection_authority(monkeypatch)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw-a", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw-b", encoding="utf-8")
    batch_index = _write_owner_closed_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A", "Sample_B"),
        raw_files={
            "Sample_A": raw_dir / "Sample_A.raw",
            "Sample_B": raw_dir / "Sample_B.raw",
        },
    )
    calls: dict[str, object] = {}

    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        calls["audit_evidence_mode"] = kwargs["audit_evidence_mode"]
        return ()

    monkeypatch.setattr(
        pipeline_module, "build_owner_backfill_cells", fake_owner_backfill
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        emit_alignment_cells=True,
        backfill_scope="production-equivalent",
    )

    assert calls["emit_region_audit"] is False
    assert calls["audit_evidence_mode"] == "none"


def test_pipeline_asls_flag_without_audit_destination_stays_slim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_product_quantitative_projection_authority(monkeypatch)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw-a", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw-b", encoding="utf-8")
    batch_index = _write_owner_closed_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A", "Sample_B"),
        raw_files={
            "Sample_A": raw_dir / "Sample_A.raw",
            "Sample_B": raw_dir / "Sample_B.raw",
        },
    )
    calls: dict[str, object] = {}

    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        calls["audit_evidence_mode"] = kwargs["audit_evidence_mode"]
        return ()

    monkeypatch.setattr(
        pipeline_module, "build_owner_backfill_cells", fake_owner_backfill
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=replace(_peak_config(), baseline_audit_method="asls"),
        raw_opener=FakeRawOpener(),
        backfill_scope="production-equivalent",
    )

    assert calls["emit_region_audit"] is False
    assert calls["audit_evidence_mode"] == "none"


def test_pipeline_rejects_selected_audit_mode_outside_selected_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _allow_product_quantitative_projection_authority(monkeypatch)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    batch_index = _write_owner_closed_batch(
        tmp_path,
        raw_dir / "Sample_A.raw",
    )
    _patch_owner_pipeline_to_matrix(monkeypatch)

    with pytest.raises(ValueError, match="selected-families"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            raw_opener=FakeRawOpener(),
            backfill_scope="production-equivalent",
            audit_evidence_mode="selected",
        )


def test_pipeline_applies_claim_registry_before_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    batch_index = _write_owner_closed_batch(
        tmp_path,
        raw_dir / "Sample_A.raw",
    )
    owner_matrix = _matrix(("Sample_A",))
    claimed_matrix = _matrix(("Sample_A",))
    calls = {}

    _patch_owner_pipeline_to_matrix(monkeypatch)
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: owner_matrix,
    )

    def fake_claim_registry(matrix, config):
        calls["claim_matrix"] = matrix
        calls["claim_config"] = config
        return claimed_matrix

    monkeypatch.setattr(
        pipeline_module,
        "apply_ms1_peak_claim_registry",
        fake_claim_registry,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda outputs, matrix, **kwargs: calls.setdefault("written_matrix", matrix),
    )

    alignment_config = AlignmentConfig()
    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=alignment_config,
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
    )

    assert calls["claim_matrix"] is owner_matrix
    assert calls["claim_config"] is alignment_config
    assert calls["written_matrix"] is claimed_matrix


def test_run_alignment_passes_drift_lookup_to_owner_clustering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    batch_index = _write_owner_closed_batch(
        tmp_path,
        raw_dir / "Sample_A.raw",
    )
    drift_lookup = _DriftLookup()
    calls = {}
    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_cluster_owners(
        owners,
        *,
        config,
        sample_universe=None,
        injection_order=None,
        drift_lookup=None,
        edge_evidence_sink=None,
    ):
        calls["drift_lookup"] = drift_lookup
        calls["injection_order"] = injection_order
        calls["edge_evidence_sink"] = edge_evidence_sink
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fake_cluster_owners,
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        drift_lookup=drift_lookup,
    )

    bound_lookup = calls["drift_lookup"]
    assert isinstance(bound_lookup, pipeline_module._BoundAlignmentDriftLookup)
    assert bound_lookup.source == drift_lookup.source
    assert bound_lookup.sample_delta_min("Sample_A") == 0.1
    assert bound_lookup.injection_order("Sample_A") == 1
    assert calls["injection_order"] is None
    assert calls["edge_evidence_sink"] == []


class _DriftLookup:
    def __init__(
        self,
        *,
        delta_min: float = 0.1,
        injection_order: int = 1,
        source: str = "targeted_istd_trend",
    ) -> None:
        self._delta_min = delta_min
        self._injection_order = injection_order
        self.source = source

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._delta_min if sample_stem == "Sample_A" else None

    def injection_order(self, sample_stem: str) -> int | None:
        return self._injection_order if sample_stem == "Sample_A" else None


def _write_owner_closed_batch(
    tmp_path: Path,
    raw_path: Path,
    *,
    candidate_updates: dict[str, str] | None = None,
) -> Path:
    batch_index = _write_batch(
        tmp_path,
        ("Sample_A",),
        raw_files={"Sample_A": str(raw_path)},
    )
    candidate_csv = batch_index.parent / "Sample_A" / "discovery_candidates.csv"
    if candidate_updates:
        with candidate_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            rows = list(reader)
        rows[0].update(candidate_updates)
        with candidate_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    candidates = read_discovery_candidates_csv(candidate_csv)
    acquisition_source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=acquisition_source,
        candidate_csv=candidate_csv,
        candidates=candidates,
    )
    fieldnames = (
        "schema_version",
        "sample_stem",
        "raw_file",
        "candidate_csv",
        "review_csv",
        "candidate_count",
        "high_count",
        "medium_count",
        "low_count",
        *DISCOVERY_BATCH_RECEIPT_COLUMNS,
    )
    row = {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": "Sample_A",
        "raw_file": str(raw_path),
        "candidate_csv": "Sample_A/discovery_candidates.csv",
        "review_csv": "",
        "candidate_count": str(len(candidates)),
        "high_count": "0",
        "medium_count": str(len(candidates)),
        "low_count": "0",
        **discovery_receipt_index_fields(receipt),
    }
    with batch_index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    return batch_index


def _cross_sample_request_id(
    batch_index: Path,
    *,
    peak_config=None,
    injection_order: dict[str, int] | None = None,
    drift_lookup=None,
) -> str:
    batch = pipeline_module.read_discovery_batch_index(batch_index)
    candidates = tuple(
        candidate
        for sample_stem in batch.sample_order
        for candidate in read_discovery_candidates_csv(
            batch.candidate_csvs[sample_stem]
        )
    )
    bound_drift = (
        pipeline_module._BoundAlignmentDriftLookup(
            source=drift_lookup.source,
            sample_deltas={
                sample_stem: drift_lookup.sample_delta_min(sample_stem)
                for sample_stem in batch.sample_order
            },
            injection_orders={
                sample_stem: drift_lookup.injection_order(sample_stem)
                for sample_stem in batch.sample_order
            },
            istd_identity_anchor=None,
        )
        if drift_lookup is not None
        else None
    )
    context = pipeline_module._alignment_cross_sample_identity_context(
        batch=batch,
        candidates=candidates,
        alignment_config=AlignmentConfig(),
        peak_config=peak_config or _peak_config(),
        injection_order=injection_order,
        drift_lookup=bound_drift,
        expected_acquisition_sources=pipeline_module._expected_acquisition_sources(
            batch
        ),
        discovery_physical_bindings=None,
    )
    return context.processing_owners.request.processing_request_id


def _scope_feature(
    family_id: str,
    *,
    owners: tuple[str, ...],
    confirm: bool = False,
):
    return SimpleNamespace(
        feature_family_id=family_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        owners=tuple(
            SimpleNamespace(sample_stem=sample, owner_area=100.0) for sample in owners
        ),
        evidence="single_sample_local_owner",
        review_only=False,
        confirm_local_owners_with_backfill=confirm,
        backfill_seed_centers=(),
    )
