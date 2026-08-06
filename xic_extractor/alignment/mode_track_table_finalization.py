"""Finalize a reviewed fresh ModeTrack projection into task-local tables."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from xic_extractor.alignment import quant_matrix_version as quant_matrix
from xic_extractor.alignment.production_acceptance import (
    build_quant_matrix_version_from_in_process_acceptance,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlModeTrackProjection,
)
from xic_extractor.alignment.xlsx_writer import write_quant_matrix_version_xlsx
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    verify_persisted_peak_observation_claims,
)
from xic_extractor.tabular_io import numeric_equal, render_delimited_rows

VALIDATION_STATUS = "diagnostic_only"


@dataclass(frozen=True, slots=True)
class ModeTrackTableFinalizationResult:
    validation_status: str
    adopted_cell_count: int
    rollback_status: str
    matrix_tsv: Path
    matrix_xlsx: Path
    checked_manifest_tsv: Path


@dataclass(frozen=True, slots=True)
class ModeTrackReviewBundleSummary:
    reviewed_cell_count: int
    feature_family_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _FreshReviewSubject:
    peak_hypothesis_id: str
    sample_stem: str
    feature_family_id: str
    sample_identity_id: str
    peak_observation_content_id: str
    raw_area: str
    observation_content: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _LoadedReviewBundle:
    review_rows: tuple[dict[str, object], ...]
    replay_rows: tuple[dict[str, object], ...]
    proposal_sha256: str
    source_artifact_sha256: str
    summary: ModeTrackReviewBundleSummary


def inspect_mode_track_review_bundle(
    *,
    manual_review_path: Path,
    manual_review_replay_path: Path,
) -> ModeTrackReviewBundleSummary:
    """Validate retained review evidence without granting write authority."""

    return _load_review_bundle(
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
    ).summary


def finalize_reviewed_mode_track_projection(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    manual_review_path: Path,
    manual_review_replay_path: Path,
    output_dir: Path,
) -> ModeTrackTableFinalizationResult:
    """Rebind reviewed physical observations and atomically publish final tables."""

    if not isinstance(projection, UntargetedCidNlModeTrackProjection):
        raise TypeError(
            "ModeTrack table finalization requires a fresh typed projection"
        )
    subjects = _validate_review_authority(
        projection=projection,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
    )
    pending = tuple(dict(row) for row in projection.pending_acceptance_rows)
    if len(subjects) != len(pending):
        raise ValueError("reviewed ModeTrack subject census changed")

    manifest_sha = quant_matrix.production_acceptance_manifest_sha256(pending)
    checked_rows = tuple({**row, "manifest_sha256": manifest_sha} for row in pending)
    source_relpath = _normalized_relpath(projection.source_artifact_relpath)
    result = build_quant_matrix_version_from_in_process_acceptance(
        matrix_header=projection.matrix_header,
        input_quant_matrix_rows=projection.input_quant_matrix_rows,
        input_matrix_identity_rows=projection.input_matrix_identity_rows,
        production_acceptance_rows=checked_rows,
        source_artifacts={source_relpath: projection.source_artifact_bytes},
        expected_diff_rows=projection.expected_diff_rows,
    )
    rollback_status = _validate_terminal_rows(
        projection=projection,
        accepted_rows=pending,
        result=result,
    )

    output_dir = output_dir.resolve()
    staging = output_dir.with_name(f"{output_dir.name}.staging")
    if output_dir.exists() or staging.exists():
        raise ValueError("final or staging output already exists")
    staging.mkdir(parents=True)
    try:
        _write_outputs(
            output_dir=staging,
            projection=projection,
            checked_rows=checked_rows,
            result=result,
            rollback_status=rollback_status,
        )
        _publish_directory_atomic(staging, output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return ModeTrackTableFinalizationResult(
        validation_status=VALIDATION_STATUS,
        adopted_cell_count=len(pending),
        rollback_status=rollback_status,
        matrix_tsv=output_dir / "alignment_matrix.tsv",
        matrix_xlsx=output_dir / "alignment_matrix.xlsx",
        checked_manifest_tsv=output_dir / "checked_manifest_v1.tsv",
    )


def _validate_review_authority(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    manual_review_path: Path,
    manual_review_replay_path: Path,
) -> tuple[_FreshReviewSubject, ...]:
    subjects = _fresh_review_subjects(projection)
    expected_keys = set(subjects)
    bundle = _load_review_bundle(
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
    )

    reviewed: set[tuple[str, str]] = set()
    for row in bundle.review_rows:
        key = _row_key(row)
        if key in reviewed or key not in expected_keys:
            raise ValueError("manual review key is duplicate or foreign")
        reviewed.add(key)
    if reviewed != expected_keys:
        raise ValueError("manual review does not cover every pending row")

    replayed: set[tuple[str, str]] = set()
    for row in bundle.replay_rows:
        key = _row_key(row)
        subject = subjects.get(key)
        if key in replayed or subject is None:
            raise ValueError("physical replay key is duplicate or foreign")
        replayed.add(key)
        observation = row.get("observation_content")
        if not isinstance(observation, dict):
            raise ValueError("physical replay observation content is malformed")
        try:
            verified = verify_persisted_peak_observation_claims(observation)
        except PeakObservationContractError as exc:
            raise ValueError(
                "physical replay observation content is owner-invalid"
            ) from exc
        if (
            row.get("feature_family_id") != subject.feature_family_id
            or row.get("peak_observation_content_id")
            != subject.peak_observation_content_id
            or verified.peak_observation_content_id
            != subject.peak_observation_content_id
            or verified.canonical_sample_identity_id != subject.sample_identity_id
            or row.get("area_parity") != "pass"
            or observation != subject.observation_content
            or not numeric_equal(row.get("proposal_quant_value"), subject.raw_area)
            or not numeric_equal(
                verified.area_raw_counts_seconds,
                subject.raw_area,
            )
        ):
            raise ValueError("physical replay candidate binding changed")
    if replayed != expected_keys:
        raise ValueError("physical replay does not cover every pending row")
    return tuple(subjects[key] for key in sorted(subjects))


def _load_review_bundle(
    *,
    manual_review_path: Path,
    manual_review_replay_path: Path,
) -> _LoadedReviewBundle:
    review_bytes = manual_review_path.read_bytes()
    replay_bytes = manual_review_replay_path.read_bytes()
    review = _read_json_bytes(review_bytes)
    replay = _read_json_bytes(replay_bytes)
    replay_sha = hashlib.sha256(replay_bytes).hexdigest()
    if review.get("schema_version") != "issue180_m9_gate_c_manual_review_v1":
        raise ValueError("manual review schema changed")
    if (
        not review.get("observation_replay_relpath")
        or str(review.get("observation_replay_sha256", "")).lower() != replay_sha
    ):
        raise ValueError("manual review replay binding changed")
    if (
        replay.get("schema_version")
        != "issue180_m9_gate_c_observation_replay_v1"
        or replay.get("validation_status") != VALIDATION_STATUS
    ):
        raise ValueError("physical replay authority binding changed")
    proposal_sha = str(replay.get("proposal_sha256", ""))
    source_sha = str(replay.get("source_artifact_sha256", ""))
    if not _is_sha256(proposal_sha) or not _is_sha256(source_sha):
        raise ValueError("physical replay authority binding changed")

    review_rows = tuple(_mapping_rows(review, "rows"))
    reviewed: set[tuple[str, str]] = set()
    for row in review_rows:
        key = _row_key(row)
        if not all(key) or key in reviewed:
            raise ValueError("manual review key is missing or duplicate")
        reviewed.add(key)
        if (
            str(row.get("proposal_sha256", "")).lower() != proposal_sha.lower()
            or str(row.get("source_artifact_sha256", "")).lower()
            != source_sha.lower()
            or row.get("verdict") != "pass"
            or not _is_sha256(str(row.get("candidate_key_sha256", "")))
            or any(
                not row.get(field)
                for field in ("reviewer_id", "reviewed_at", "evidence_locator")
            )
        ):
            raise ValueError("manual review is unbound or not passing")

    replay_rows = tuple(_mapping_rows(replay, "rows"))
    if replay.get("row_count") != len(replay_rows):
        raise ValueError("physical replay row census is malformed")
    replayed: set[tuple[str, str]] = set()
    family_counts: Counter[str] = Counter()
    for row in replay_rows:
        key = _row_key(row)
        if not all(key) or key in replayed:
            raise ValueError("physical replay key is missing or duplicate")
        replayed.add(key)
        family_id = str(row.get("feature_family_id", ""))
        observation = row.get("observation_content")
        proposal_value = row.get("proposal_quant_value")
        if (
            not family_id
            or not row.get("peak_observation_content_id")
            or not isinstance(observation, dict)
            or proposal_value is None
        ):
            raise ValueError("physical replay row is malformed")
        try:
            verified = verify_persisted_peak_observation_claims(observation)
        except PeakObservationContractError as exc:
            raise ValueError(
                "physical replay observation content is owner-invalid"
            ) from exc
        if (
            verified.peak_observation_content_id
            != row.get("peak_observation_content_id")
            or row.get("area_parity") != "pass"
            or not numeric_equal(
                verified.area_raw_counts_seconds,
                proposal_value,
            )
        ):
            raise ValueError("physical replay candidate binding changed")
        family_counts[family_id] += 1
    if not reviewed or reviewed != replayed:
        raise ValueError("manual review and physical replay census differ")

    summary = ModeTrackReviewBundleSummary(
        reviewed_cell_count=len(reviewed),
        feature_family_counts=tuple(sorted(family_counts.items())),
    )
    return _LoadedReviewBundle(
        review_rows=review_rows,
        replay_rows=replay_rows,
        proposal_sha256=proposal_sha,
        source_artifact_sha256=source_sha,
        summary=summary,
    )


def _fresh_review_subjects(
    projection: UntargetedCidNlModeTrackProjection,
) -> dict[tuple[str, str], _FreshReviewSubject]:
    source = _read_json_bytes(projection.source_artifact_bytes)
    pending_by_key = {
        (row["peak_hypothesis_id"], row["sample_stem"]): row
        for row in projection.pending_acceptance_rows
    }
    if not pending_by_key:
        raise ValueError("fresh ModeTrack projection has no reviewed write candidates")
    subjects: dict[tuple[str, str], _FreshReviewSubject] = {}
    for source_row in _mapping_rows(source, "rows"):
        key = (
            str(source_row.get("group_hypothesis_id", "")),
            str(source_row.get("sample_stem", "")),
        )
        pending = pending_by_key.get(key)
        if pending is None:
            continue
        if key in subjects:
            raise ValueError("fresh ModeTrack review subject is duplicated")
        physical_ids = source_row.get("physical_ids")
        peak_observation = source_row.get("peak_observation")
        observation = (
            peak_observation.get("content")
            if isinstance(peak_observation, dict)
            else None
        )
        if (
            not isinstance(physical_ids, list)
            or len(physical_ids) < 3
            or not isinstance(observation, dict)
            or not source_row.get("sample_identity_id")
            or source_row.get("quant_value") is None
        ):
            raise ValueError("fresh ModeTrack review subject is malformed")
        try:
            verified = verify_persisted_peak_observation_claims(observation)
        except PeakObservationContractError as exc:
            raise ValueError(
                "fresh ModeTrack observation content is owner-invalid"
            ) from exc
        if (
            verified.peak_observation_content_id != physical_ids[2]
            or verified.canonical_sample_identity_id
            != source_row.get("sample_identity_id")
            or not numeric_equal(
                verified.area_raw_counts_seconds,
                source_row.get("quant_value"),
            )
            or not numeric_equal(
                source_row.get("quant_value"),
                pending.get("quant_value"),
            )
        ):
            raise ValueError("fresh ModeTrack review subject owner binding changed")
        subjects[key] = _FreshReviewSubject(
            peak_hypothesis_id=key[0],
            sample_stem=key[1],
            feature_family_id=str(pending["feature_family_id"]),
            sample_identity_id=str(source_row["sample_identity_id"]),
            peak_observation_content_id=str(physical_ids[2]),
            raw_area=str(source_row["quant_value"]),
            observation_content=observation,
        )
    if set(subjects) != set(pending_by_key):
        raise ValueError("fresh ModeTrack review subject census is incomplete")
    return subjects


def _validate_terminal_rows(
    *,
    projection: UntargetedCidNlModeTrackProjection,
    accepted_rows: Sequence[Mapping[str, str]],
    result: quant_matrix.QuantMatrixVersionRows,
) -> str:
    header = projection.matrix_header
    baseline = list(map(dict, projection.input_quant_matrix_rows))
    identities = projection.input_matrix_identity_rows
    accepted = {
        (row["peak_hypothesis_id"], row["sample_stem"]) for row in accepted_rows
    }
    if len(result.quant_matrix_rows) != len(baseline):
        raise ValueError("ModeTrack Matrix row count changed")
    for identity, before, after in zip(
        identities,
        baseline,
        result.quant_matrix_rows,
        strict=True,
    ):
        if any(
            after.get(column, "") != before.get(column, "")
            for column in ("Mz", "RT")
        ):
            raise ValueError("ModeTrack Matrix row identity changed")
        group_id = identity["peak_hypothesis_id"]
        expected = {
            sample
            for sample in header[2:]
            if before.get(sample, "") or (group_id, sample) in accepted
        }
        actual = {sample for sample in header[2:] if after.get(sample, "")}
        if actual != expected:
            raise ValueError("ModeTrack Matrix contains an unauthorized value")
    detected_only = quant_matrix.detected_only_matrix_rows_from_quant_version(
        quant_matrix_rows=result.quant_matrix_rows,
        cell_provenance_rows=result.cell_provenance_rows,
        matrix_identity_rows=identities,
    )
    rollback_rows = [
        {column: row.get(column, "") for column in header} for row in detected_only
    ]
    baseline_detected = [
        row for row in baseline if any(row.get(sample, "") for sample in header[2:])
    ]
    if rollback_rows != baseline_detected:
        raise ValueError("ModeTrack result-derived rollback differs from baseline")
    return "baseline_exact"


def _write_outputs(
    *,
    output_dir: Path,
    projection: UntargetedCidNlModeTrackProjection,
    checked_rows: Sequence[Mapping[str, str]],
    result: quant_matrix.QuantMatrixVersionRows,
    rollback_status: str,
) -> None:
    (output_dir / "checked_manifest_v1.tsv").write_text(
        render_delimited_rows(
            checked_rows,
            quant_matrix.PRODUCTION_ACCEPTANCE_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    (output_dir / "alignment_matrix.tsv").write_text(
        render_delimited_rows(
            result.quant_matrix_rows,
            projection.matrix_header,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    for filename, rows, columns in (
        (
            "cell_provenance.tsv",
            result.cell_provenance_rows,
            quant_matrix.CELL_PROVENANCE_COLUMNS,
        ),
        (
            "row_summary.tsv",
            result.row_summary_rows,
            quant_matrix.ROW_SUMMARY_COLUMNS,
        ),
        (
            "expected_diff_summary.tsv",
            result.expected_diff_summary_rows,
            quant_matrix.EXPECTED_DIFF_SUMMARY_COLUMNS,
        ),
    ):
        (output_dir / filename).write_text(
            render_delimited_rows(
                rows,
                columns,
                delimiter="\t",
                extrasaction="raise",
                lineterminator="\n",
            ),
            encoding="utf-8",
        )
    write_quant_matrix_version_xlsx(
        output_dir / "alignment_matrix.xlsx",
        matrix_header=projection.matrix_header,
        quant_matrix_version=result,
        metadata={
            "validation_status": VALIDATION_STATUS,
            "adopted_cell_count": str(len(checked_rows)),
            "rollback_status": rollback_status,
            "writer_authority": "fresh_projection_plus_reviewed_physical_subjects",
        },
    )


def _publish_directory_atomic(staging: Path, output_dir: Path) -> None:
    for attempt in range(5):
        try:
            staging.replace(output_dir)
            return
        except PermissionError:
            if output_dir.exists() or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_json_bytes(content: bytes) -> dict[str, object]:
    value = json.loads(content, object_pairs_hook=_unique_json_mapping)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _unique_json_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _mapping_rows(
    value: Mapping[str, object],
    key: str,
) -> list[dict[str, object]]:
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{key} rows are malformed")
    return rows


def _row_key(row: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(row.get("peak_hypothesis_id", "")),
        str(row.get("sample_stem", "")),
    )


def _normalized_relpath(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not normalized:
        raise ValueError("ModeTrack source artifact path must be relative")
    return path.as_posix()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
