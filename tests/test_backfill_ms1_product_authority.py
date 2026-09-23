from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools.diagnostics.authorize_backfill_ms1_pattern_evidence import main
from xic_extractor.alignment import backfill_ms1_product_authority as authority_module
from xic_extractor.alignment.backfill_evidence_projection import (
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.backfill_ms1_product_authority import (
    ALLOWLIST_COLUMNS,
    SCHEMA_VERSION,
    authorize_ms1_pattern_rows,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
)


def test_authorizes_allowlisted_anchor_own_max_ms1_pattern_row(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 1
    row = result.authorized_rows[0]
    assert row["diagnostic_only"] == "FALSE"
    assert row[PRODUCT_AUTHORITY_STATUS_FIELD] == PRODUCT_AUTHORIZED_STATUS
    assert row[PRODUCT_AUTHORITY_SCOPE_FIELD] == PRODUCT_AUTHORIZED_SCOPE
    assert row[PRODUCT_AUTHORITY_SOURCE_FIELD] == "manual_overlay_review"
    assert (
        row["product_authority_observed_anchor_own_max_shape_similarity"]
        == "0.72"
    )
    assert row["product_authority_overlay_trace_data_sha256"] == _sha256_file(
        tmp_path / overlay_json
    )
    assert row["product_authority_expected_overlay_trace_data_sha256"] == (
        _sha256_file(tmp_path / overlay_json)
    )
    assert result.audit_rows[0]["decision"] == "authorized"
    assert result.audit_rows[0]["source_overlay_trace_data_status"] == "valid"


def test_authorize_reuses_trace_json_validation_for_multiple_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    trace_path = tmp_path / overlay_json
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    payload["traces"].append(
        {
            **payload["traces"][0],
            "sample_stem": "S3",
        }
    )
    trace_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    original_reader = authority_module._read_overlay_trace_data_file
    read_paths: list[Path] = []

    def counting_reader(path: Path) -> object:
        read_paths.append(path)
        return original_reader(path)

    monkeypatch.setattr(
        authority_module,
        "_read_overlay_trace_data_file",
        counting_reader,
    )

    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[
            _ms1_row(overlay_json),
            {
                **_ms1_row(overlay_json),
                "sample_stem": "S3",
            },
        ],
        allowlist_rows=[
            _allowlist_row(tmp_path, overlay_json),
            {
                **_allowlist_row(tmp_path, overlay_json),
                "sample_stem": "S3",
            },
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 2
    assert len(read_paths) == 1


def test_rejects_anchor_own_max_similarity_at_or_below_threshold(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[
            {
                **_ms1_row(overlay_json),
                "anchor_peak_own_max_shape_similarity": "0.5",
            }
        ],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision"] == "rejected"
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_anchor_own_max_similarity_below_threshold"
    )


def test_blank_anchor_own_max_threshold_uses_default_floor(tmp_path: Path) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[
            {
                **_ms1_row(overlay_json),
                "anchor_peak_own_max_shape_similarity": "0.5",
            }
        ],
        allowlist_rows=[
            {
                **_allowlist_row(tmp_path, overlay_json),
                "min_anchor_own_max_shape_similarity": "",
            }
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_anchor_own_max_similarity_below_threshold"
    )


def test_rejects_allowlist_threshold_below_default_floor(tmp_path: Path) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[
            {
                **_allowlist_row(tmp_path, overlay_json),
                "min_anchor_own_max_shape_similarity": "0.49",
            }
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision"] == "rejected"
    assert (
        result.audit_rows[0]["decision_reason"]
        == "allowlist_anchor_own_max_threshold_below_default"
    )


def test_rejects_anchor_reason_substring_that_is_not_exact_token(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[
            {
                **_ms1_row(overlay_json),
                "reason": f"{ANCHOR_OWN_MAX_MS1_SUPPORT_REASON}_legacy",
            }
        ],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision"] == "rejected"
    assert result.audit_rows[0]["decision_reason"] == (
        "source_anchor_own_max_reason_missing"
    )


def test_duplicate_source_ms1_pattern_keys_raise() -> None:
    with pytest.raises(
        ValueError,
        match="duplicate backfill MS1 product authority source key",
    ):
        authorize_ms1_pattern_rows(
            ms1_pattern_rows=[_ms1_row(), _ms1_row()],
            allowlist_rows=[_allowlist_row()],
        )


def test_duplicate_ms1_allowlist_keys_raise(tmp_path: Path) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)

    with pytest.raises(
        ValueError,
        match="duplicate backfill MS1 product authority allowlist key",
    ):
        authorize_ms1_pattern_rows(
            ms1_pattern_rows=[_ms1_row(overlay_json)],
            allowlist_rows=[
                _allowlist_row(tmp_path, overlay_json),
                _allowlist_row(tmp_path, overlay_json),
            ],
            artifact_base_dir=tmp_path,
        )


def test_rejects_missing_overlay_trace_json(tmp_path: Path) -> None:
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row("overlays/missing.json")],
        allowlist_rows=[
            _allowlist_row(
                tmp_path,
                "overlays/missing.json",
                overlay_trace_data_sha256="0" * 64,
            )
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision_reason"] == (
        "source_overlay_trace_json_unreadable"
    )
    assert result.audit_rows[0]["source_overlay_trace_data_status"] == (
        "source_overlay_trace_json_unreadable"
    )


def test_rejects_overlay_trace_json_family_or_sample_mismatch(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path, family_id="FAM_OTHER")
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_overlay_trace_family_mismatch"
    )


def test_rejects_overlay_trace_json_without_usable_trace_vector(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path, include_vector=False)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert (
        result.audit_rows[0]["decision_reason"]
        == "source_overlay_trace_vector_invalid"
    )


def test_rejects_overlay_trace_json_hash_mismatch(tmp_path: Path) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[
            _allowlist_row(
                tmp_path,
                overlay_json,
                overlay_trace_data_sha256="0" * 64,
            )
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision_reason"] == (
        "allowlist_overlay_trace_sha256_mismatch"
    )


def test_rejects_overlay_trace_json_outside_source_bundle(tmp_path: Path) -> None:
    outside_json = _write_overlay_trace_data_json(tmp_path.parent)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(Path("..") / outside_json)],
        allowlist_rows=[
            _allowlist_row(
                tmp_path,
                Path("..") / outside_json,
                overlay_trace_data_sha256=_sha256_file(tmp_path.parent / outside_json),
            )
        ],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision_reason"] == (
        "source_overlay_trace_json_path_outside_bundle"
    )


def test_rejects_overlay_trace_json_duplicate_sample_trace(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(tmp_path, duplicate_sample=True)
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision_reason"] == (
        "source_overlay_trace_sample_duplicate"
    )


def test_rejects_overlay_trace_json_missing_own_max_similarity(
    tmp_path: Path,
) -> None:
    overlay_json = _write_overlay_trace_data_json(
        tmp_path,
        include_own_max_similarity=False,
    )
    result = authorize_ms1_pattern_rows(
        ms1_pattern_rows=[_ms1_row(overlay_json)],
        allowlist_rows=[_allowlist_row(tmp_path, overlay_json)],
        artifact_base_dir=tmp_path,
    )

    assert result.summary["authorized_row_count"] == 0
    assert result.audit_rows[0]["decision_reason"] == (
        "source_overlay_trace_own_max_similarity_missing"
    )


def test_authorized_ms1_sidecar_projects_into_rescued_cells(tmp_path: Path) -> None:
    source = tmp_path / "shared_peak_identity_ms1_pattern_coherence_evidence.tsv"
    allowlist = tmp_path / "allowlist.tsv"
    output_dir = tmp_path / "out"
    overlay_json = _write_overlay_trace_data_json(tmp_path)
    _write_tsv(source, _ms1_columns(), [_ms1_row(overlay_json)])
    _write_tsv(allowlist, ALLOWLIST_COLUMNS, [_allowlist_row(tmp_path, overlay_json)])

    assert (
        main(
            [
                "--ms1-pattern-coherence-evidence-tsv",
                str(source),
                "--authority-allowlist-tsv",
                str(allowlist),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    authorized = _read_tsv(
        output_dir / "shared_peak_identity_ms1_pattern_coherence_product_authorized.tsv"
    )
    assert len(authorized) == 1
    assert authorized[0]["product_authority_overlay_trace_data_sha256"] == (
        _sha256_file(tmp_path / overlay_json)
    )
    projected = project_backfill_evidence_to_cells(
        cell_rows=[
            {
                "feature_family_id": "FAM_AUTH",
                "sample_stem": "S2",
                "status": "backfilled",
            }
        ],
        ms1_pattern_coherence_rows=authorized,
    )
    assert projected[0]["backfill_ms1_pattern_status"] == "supportive"
    assert (
        ANCHOR_OWN_MAX_MS1_SUPPORT_REASON
        in projected[0]["backfill_evidence_reason"]
    )
    summary = json.loads(
        (output_dir / "backfill_ms1_pattern_product_authority_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["readiness_label"] == "product_authority_sidecar_candidate"


def _ms1_columns() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "sample_stem",
        "ms1_pattern_status",
        "ms1_pattern_evidence_level",
        "apex_coherence_sec",
        "boundary_overlap_score",
        "shape_correlation_score",
        "relative_pattern_stability_score",
        "local_interference_score",
        "constellation_peak_count",
        "reference_peak_count",
        "drift_compatible_status",
        "reason",
        "diagnostic_only",
        "shape_metric_source",
        "anchor_peak_own_max_shape_similarity",
        "family_ms1_overlay_trace_data_json",
        "peak_quality_vector_status",
        "peak_quality_vector_basis",
    )


def _ms1_row(
    overlay_trace_data_json: str | Path = "overlays/fam-auth.json",
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM_AUTH",
        "sample_stem": "S2",
        "ms1_pattern_status": "supportive",
        "ms1_pattern_evidence_level": "trace_constellation",
        "apex_coherence_sec": "4",
        "boundary_overlap_score": "0.85",
        "shape_correlation_score": "0.72",
        "relative_pattern_stability_score": "0.8",
        "local_interference_score": "0.1",
        "constellation_peak_count": "4",
        "reference_peak_count": "6",
        "drift_compatible_status": "compatible",
        "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        "diagnostic_only": "TRUE",
        "shape_metric_source": "family_ms1_overlay_anchor_peak_own_max",
        "anchor_peak_own_max_shape_similarity": "0.72",
        "family_ms1_overlay_trace_data_json": str(overlay_trace_data_json),
        "peak_quality_vector_status": "supportive",
        "peak_quality_vector_basis": "family_ms1_overlay_raw_trace_vector",
    }


def _allowlist_row(
    base_dir: Path | None = None,
    overlay_trace_data_json: str | Path = "overlays/fam-auth.json",
    *,
    overlay_trace_data_sha256: str | None = None,
) -> dict[str, str]:
    expected_sha256 = (
        overlay_trace_data_sha256
        if overlay_trace_data_sha256 is not None
        else (
            _sha256_file(base_dir / overlay_trace_data_json)
            if base_dir is not None
            else ""
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": "FAM_AUTH",
        "sample_stem": "S2",
        "authority_status": PRODUCT_AUTHORIZED_STATUS,
        "authority_source": "manual_overlay_review",
        "authority_reason": "reviewed own-max same-peak support",
        "expected_overlay_trace_data_json": str(overlay_trace_data_json),
        "expected_overlay_trace_data_sha256": expected_sha256,
        "min_anchor_own_max_shape_similarity": "0.5",
    }


def _write_tsv(
    path: Path,
    columns: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_overlay_trace_data_json(
    base_dir: Path,
    *,
    family_id: str = "FAM_AUTH",
    sample_stem: str = "S2",
    include_vector: bool = True,
    include_own_max_similarity: bool = True,
    duplicate_sample: bool = False,
) -> str:
    relative_path = Path("overlays") / "fam-auth.json"
    path = base_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    trace: dict[str, object] = {
        "sample_stem": sample_stem,
        "status": "backfilled",
        "cell_apex_rt": 10.0,
        "cell_start_rt": 9.9,
        "cell_end_rt": 10.1,
        "cell_height": 500.0,
        "local_window_max_intensity": 1000.0,
        "trace_max_intensity": 1000.0,
        "apex_aligned_shape_similarity": 0.72,
    }
    if include_own_max_similarity:
        trace["absolute_own_max_shape_similarity"] = 0.72
    if include_vector:
        trace["rt"] = [9.8, 9.9, 10.0, 10.1, 10.2]
        trace["intensity"] = [10.0, 80.0, 1000.0, 75.0, 9.0]
    traces = [trace, dict(trace)] if duplicate_sample else [trace]
    payload = {
        "family_id": family_id,
        "rt_min": 9.8,
        "rt_max": 10.2,
        "evidence_summary": {
            "family_verdict": "ms1_shape_supports_family_backfill"
        },
        "traces": traces,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return str(relative_path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
