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
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.csv_io import read_discovery_candidates_csv
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


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
    batch_index = _write_batch(tmp_path, ("Sample_A",))

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


def test_pipeline_loads_candidates_builds_owners_backfills_and_writes_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A", "Sample_B"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
    calls: dict[str, object] = {}

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
    ):
        calls["candidates"] = tuple(candidate.sample_stem for candidate in candidates)
        calls["owner_raw_sources"] = raw_sources
        calls["alignment_config"] = alignment_config
        calls["peak_config"] = peak_config
        calls["owner_raw_xic_batch_size"] = raw_xic_batch_size
        calls["owner_emit_region_audit"] = emit_region_audit
        calls["owner_region_audit_family_ids"] = region_audit_family_ids
        calls["owner_audit_evidence_mode"] = audit_evidence_mode
        return SimpleNamespace(owners=("owner",), ambiguous_records=())

    def fake_cluster_owners(owners, *, config):
        calls["owners"] = owners
        calls["cluster_config"] = config
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

    assert calls["candidates"] == ("Sample_A", "Sample_B")
    assert calls["owners"] == ("owner",)
    assert calls["features"] == ("feature",)
    assert calls["sample_order"] == ("Sample_A", "Sample_B")
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


def test_pipeline_default_path_no_longer_uses_legacy_near_duplicate_folding() -> None:
    assert not hasattr(pipeline_module, "fold_near_duplicate_clusters")


def test_pipeline_builds_owner_features_before_owner_matrix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
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

    monkeypatch.setattr(pipeline_module, "_write_outputs_atomic", fake_write_outputs)

    def artifact_fingerprint_sink(_receipt) -> None:
        return None

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        artifact_fingerprint_sink=artifact_fingerprint_sink,
    )

    assert calls["owners"] == (owner,)
    assert calls["backfill_features"] == (feature, "ambiguous-feature")
    assert calls["matrix_features"] == (feature, "ambiguous-feature")
    assert calls["ambiguous_by_sample"] == {}
    assert calls["rescued_cells"] == ("rescued",)
    assert calls["written_matrix"] is owner_matrix
    write_kwargs = calls["write_kwargs"]
    assert isinstance(write_kwargs, dict)
    assert write_kwargs["artifact_fingerprint_sink"] is artifact_fingerprint_sink


def test_pipeline_production_equivalent_scope_skips_safe_backfill_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A", "Sample_B"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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
    batch_index = _write_batch(tmp_path, ("Sample_A", "Sample_B"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
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
    batch_index = _write_batch(tmp_path, ("Sample_A", "Sample_B"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
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
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
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
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
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
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    drift_lookup = _DriftLookup()
    calls = {}
    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_cluster_owners(
        owners,
        *,
        config,
        drift_lookup=None,
        edge_evidence_sink=None,
    ):
        calls["drift_lookup"] = drift_lookup
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

    assert calls["drift_lookup"] is drift_lookup
    assert calls["edge_evidence_sink"] == []


class _DriftLookup:
    source = "targeted_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return {"Sample_A": 0.1}.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return {"Sample_A": 1}.get(sample_stem)


def _write_owner_closed_batch(tmp_path: Path, raw_path: Path) -> Path:
    batch_index = _write_batch(
        tmp_path,
        ("Sample_A",),
        raw_files={"Sample_A": str(raw_path)},
    )
    candidate_csv = batch_index.parent / "Sample_A" / "discovery_candidates.csv"
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
