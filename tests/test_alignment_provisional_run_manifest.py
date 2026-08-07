from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.provisional_run_manifest import (
    PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
    ProvisionalRunRuntimeConfiguration,
    validate_provisional_run_manifest,
    write_provisional_run_manifest,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


def test_manifest_binds_effective_configuration_receipts_and_artifact_bytes(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")

    payload = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert payload["code_revision"] == "a" * 40
    assert payload["schema_version"] == PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION
    assert payload["preset_name"] == "dna_dr"
    assert payload["effective_configuration"]["discovery"][
        "neutral_loss_profiles"
    ][0]["tag"] == "DNA_dR"
    assert payload["runtime"]["owner_build_xic_backend"] == "raw_superwindow"
    assert payload["runtime"]["selected_family_source"] == ""
    assert payload["runtime"]["output_level"] == "validation-minimal"
    assert payload["runtime"]["audit_evidence_mode"] == "none"
    assert payload["runtime"]["sample_column_order_source"] == (
        "retained_sample_metadata"
    )
    assert payload["claim_levels"] == {
        "artifact_bytes": "byte_bound",
        "configuration": "structure_bound",
        "embedded_semantics": "unavailable",
    }
    assert payload["authenticity_authority"] is False
    assert payload["semantic_replay_authority"] is False
    assert payload["inputs"]["discovery_batch_index"]["receipt_count"] == 1
    assert payload["artifacts"]["provisional_matrix"]["byte_size"] == packet[
        "artifacts"
    ]["provisional_matrix"].stat().st_size


def test_manifest_rejects_artifact_mutation(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    packet["artifacts"]["provisional_matrix"].write_text(
        "mutated\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_rejects_unresealed_cross_run_artifact_substitution(
    tmp_path: Path,
) -> None:
    first = _packet(tmp_path / "first", matrix_bytes="matrix-a\n")
    second = _packet(tmp_path / "second", matrix_bytes="matrix-b\n")
    shutil.copyfile(
        second["artifacts"]["provisional_matrix"],
        first["artifacts"]["provisional_matrix"],
    )

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_provisional_run_manifest(
            first["manifest"],
            root=first["root"],
        )


def test_manifest_does_not_overclaim_resealed_artifact_semantics(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    matrix = packet["artifacts"]["provisional_matrix"]
    matrix.write_text("internally-inconsistent-but-byte-bound\n", encoding="utf-8")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    binding = payload["artifacts"]["provisional_matrix"]
    binding["sha256"] = hashlib.sha256(matrix.read_bytes()).hexdigest()
    binding["byte_size"] = matrix.stat().st_size
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["claim_levels"]["embedded_semantics"] == "unavailable"


def test_manifest_rejects_incomplete_runtime_structure(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    del payload["runtime"]["raw_workers"]
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime field set is invalid"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_runtime_is_structure_bound_not_execution_replay(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    payload["runtime"]["raw_workers"] = 4
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["runtime"]["raw_workers"] == 4
    assert validated["claim_levels"]["configuration"] == "structure_bound"


def test_manifest_configuration_values_are_structure_bound(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    payload["effective_configuration"]["discovery"]["nl_tolerance_ppm"] = 25.0
    payload["effective_configuration"]["alignment"]["preferred_ppm"] = 25.0
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["effective_configuration"]["discovery"][
        "nl_tolerance_ppm"
    ] == 25.0
    assert validated["effective_configuration"]["alignment"][
        "preferred_ppm"
    ] == 25.0


def test_manifest_rejects_incomplete_configuration_structure(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    del payload["effective_configuration"]["discovery"]["nl_tolerance_ppm"]
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="field set is invalid"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_creation_rejects_non_owner_effective_configuration(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not match the preset owner"):
        _packet(
            tmp_path / "run",
            matrix_bytes="matrix-a\n",
            discovery_settings=DiscoverySettings(
                nl_tolerance_ppm=25.0,
                neutral_loss_profiles=(
                    NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
                ),
            ),
        )


def _packet(
    root: Path,
    *,
    matrix_bytes: str,
    discovery_settings: DiscoverySettings | None = None,
) -> dict[str, object]:
    root.mkdir(parents=True)
    retained_metadata = root / "inputs" / "sample_metadata.tsv"
    retained_metadata.parent.mkdir()
    retained_metadata.write_text(
        "sample_stem\tinjection_order\nSample_A\t1\n",
        encoding="utf-8",
    )
    discovery_index = _write_discovery_packet(root)
    artifact_dir = root / "workflow" / "alignment"
    artifact_dir.mkdir(parents=True)
    artifacts = {
        "provisional_matrix": artifact_dir / "provisional_alignment_matrix.tsv",
        "provisional_identity": artifact_dir / "provisional_row_identity.tsv",
        "provisional_cell_provenance": (
            artifact_dir / "provisional_cell_provenance.tsv"
        ),
        "provisional_summary": artifact_dir / "provisional_matrix_summary.json",
    }
    artifacts["provisional_matrix"].write_text(matrix_bytes, encoding="utf-8")
    for name, path in artifacts.items():
        if name != "provisional_matrix":
            path.write_text(f"{name}\n", encoding="utf-8")
    manifest = write_provisional_run_manifest(
        root / "run_manifest.json",
        root=root,
        code_revision="a" * 40,
        preset_name="dna_dr",
        discovery_settings=discovery_settings
        or DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
            ),
        ),
        alignment_config=AlignmentConfig(),
        runtime=ProvisionalRunRuntimeConfiguration(
            sample_count=1,
            raw_workers=3,
            raw_xic_batch_size=16,
            owner_build_xic_backend="raw_superwindow",
            owner_backfill_xic_backend="raw",
            owner_backfill_window_strategy="super-window",
            owner_backfill_superwindow_span_factor=2,
        ),
        retained_sample_metadata=retained_metadata,
        discovery_batch_index=discovery_index,
        artifacts=artifacts,
    )
    return {
        "root": root,
        "manifest": manifest,
        "artifacts": artifacts,
    }


def _write_discovery_packet(root: Path) -> Path:
    discovery_dir = root / "workflow" / "discovery"
    sample_dir = discovery_dir / "Sample_A"
    sample_dir.mkdir(parents=True)
    raw_path = root / "inputs" / "Sample_A.raw"
    raw_path.write_bytes(b"raw")
    candidate_csv = sample_dir / "discovery_candidates.csv"
    write_discovery_candidates_csv(candidate_csv, ())
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=source,
        candidate_csv=candidate_csv,
        candidates=(),
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
        "candidate_count": "0",
        "high_count": "0",
        "medium_count": "0",
        "low_count": "0",
        **discovery_receipt_index_fields(receipt),
    }
    index_path = discovery_dir / "discovery_batch_index.csv"
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    return index_path
