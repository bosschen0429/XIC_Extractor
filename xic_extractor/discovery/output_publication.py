from __future__ import annotations

import csv
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoveryCandidate,
    DiscoveryPhysicalWorkItem,
    DiscoveryRunOutputs,
    ReviewPriority,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    DiscoveryPhysicalFactRecord,
    bind_discovery_physical_record_evidence,
    order_physical_bindings_for_candidate_csv,
)
from xic_extractor.discovery.physical_fact_sidecar import (
    stage_discovery_physical_fact_sidecar as write_discovery_physical_fact_sidecar,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)
from xic_extractor.tabular_io import write_delimited_rows

_BATCH_INDEX_COLUMNS = (
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


@dataclass(frozen=True)
class PublishedDiscoverySample:
    """Committed sample artifacts and their bound receipt and batch-index facts."""

    outputs: DiscoveryRunOutputs
    receipt: DiscoveryCandidateUniverseReceipt
    index_row: dict[str, str]


def publish_discovery_batch_sample(
    raw_path: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    output_dir: Path,
    acquisition_source: AcquisitionSourceIdentity,
    timing_recorder: TimingRecorder,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = (),
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = (),
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...] = (),
) -> PublishedDiscoverySample:
    """Publish one sample and bind all batch handoff facts to its persisted rows.

    Both serial and process execution consume this complete handoff. The receipt
    contract owns minting after the writer commits the sample's bytes; no batch
    index row is returned when publication or receipt construction fails.
    """

    candidates = _persisted_discovery_candidates(candidates)
    outputs = _write_persisted_candidate_outputs(
        output_dir / raw_path.stem,
        candidates,
        timing_recorder=timing_recorder,
        sample_stem=raw_path.stem,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        physical_work_manifest=physical_work_manifest,
        physical_evaluations=physical_evaluations,
        physical_bindings=physical_bindings,
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem=raw_path.stem,
        acquisition_source=acquisition_source,
        candidate_csv=outputs.candidates_csv,
        candidates=candidates,
    )
    return PublishedDiscoverySample(
        outputs=outputs,
        receipt=receipt,
        index_row=_batch_index_row(
            raw_path=raw_path,
            outputs=outputs,
            candidates=candidates,
            receipt=receipt,
            index_parent=output_dir,
        ),
    )


def _persisted_discovery_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidate, ...]:
    """Keep measured peaks for review independently of NL search eligibility."""
    return tuple(
        candidate
        for candidate in candidates
        if candidate.ms1_peak_found
        or candidate.precursor_mz_basis
        in {"scan_precursor", "scan_monoisotopic", "mixed"}
    )


def _persisted_physical_records(
    records: tuple[DiscoveryPhysicalFactRecord, ...],
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    """Retain measured peak evidence and compact persistence-local work indices."""
    peak_bindings = tuple(
        binding for binding in bindings if binding.candidate.ms1_peak_found
    )
    used_work = {
        ordinal for binding in peak_bindings for ordinal in binding.member_work_ordinals
    }
    retained = tuple(
        record for record in records if record.work_item.work_ordinal in used_work
    )
    work_indices = {
        record.work_item.work_ordinal: index for index, record in enumerate(retained)
    }
    linked = bind_discovery_physical_record_evidence(retained, peak_bindings)
    return tuple(
        replace(
            record,
            work_item=replace(record.work_item, work_ordinal=index),
            candidate_links=tuple(
                replace(
                    link,
                    representative_work_ordinal=work_indices[
                        link.representative_work_ordinal
                    ],
                    member_work_ordinals=tuple(
                        work_indices[item] for item in link.member_work_ordinals
                    ),
                )
                for link in record.candidate_links
            ),
        )
        for index, record in enumerate(linked)
    )


def write_dual_csvs(
    output_dir: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = (),
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = (),
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...] = (),
) -> DiscoveryRunOutputs:
    """Publish standalone Discovery outputs without a batch receipt or index."""
    return _write_persisted_candidate_outputs(
        output_dir,
        _persisted_discovery_candidates(candidates),
        timing_recorder=timing_recorder,
        sample_stem=sample_stem,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        physical_work_manifest=physical_work_manifest,
        physical_evaluations=physical_evaluations,
        physical_bindings=physical_bindings,
    )


def _write_persisted_candidate_outputs(
    output_dir: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...],
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...],
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
) -> DiscoveryRunOutputs:
    persisted_ids = {candidate.candidate_id for candidate in candidates}
    physical_bindings = order_physical_bindings_for_candidate_csv(
        tuple(
            binding
            for binding in physical_bindings
            if binding.candidate.candidate_id in persisted_ids
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "discovery_candidates.csv"
    review_path = output_dir / "discovery_review.csv"
    physical_facts_path = output_dir / "discovery_physical_facts.json"
    physical_evaluations = _persisted_physical_records(
        physical_evaluations,
        physical_bindings,
    )
    physical_work_manifest = tuple(record.work_item for record in physical_evaluations)

    final_paths = [candidates_path, review_path, physical_facts_path]
    temp_paths = [_discovery_output_temp_path(path) for path in final_paths]
    backup_paths = [_discovery_output_backup_path(path) for path in final_paths]
    existed_before = {path: path.exists() for path in final_paths}
    backups: list[tuple[Path, Path]] = []
    try:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
        with timing_recorder.stage(
            "discover.write_candidates_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_candidates_csv(temp_paths[0], candidates)
            _verify_staged_discovery_csv(
                temp_paths[0],
                columns=DISCOVERY_CANDIDATE_COLUMNS,
                row_count=len(candidates),
            )
        with timing_recorder.stage(
            "discover.write_review_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_review_csv(temp_paths[1], candidates)
            _verify_staged_discovery_csv(
                temp_paths[1],
                columns=DISCOVERY_BRIEF_COLUMNS,
                row_count=len(candidates),
            )
        physical_tmp = temp_paths[2]
        with timing_recorder.stage(
            "discover.write_physical_facts_sidecar",
            sample_stem=sample_stem,
            metrics={
                "work_count": len(physical_work_manifest),
                "record_count": len(physical_evaluations),
            },
        ) as stage:
            write_discovery_physical_fact_sidecar(
                physical_tmp,
                run_spec=run_spec,
                acquisition_input=acquisition_input,
                work_manifest=physical_work_manifest,
                records=physical_evaluations,
            )
            stage.metrics["byte_size"] = physical_tmp.stat().st_size
    except Exception:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
        raise

    with timing_recorder.stage(
        "discover.publish_outputs",
        sample_stem=sample_stem,
        metrics={
            "artifact_count": len(final_paths),
            "replaced_existing_count": sum(
                int(existed_before[path]) for path in final_paths
            ),
        },
    ):
        try:
            for final_path, backup_path in zip(
                final_paths,
                backup_paths,
                strict=True,
            ):
                if final_path.exists():
                    final_path.replace(backup_path)
                    backups.append((final_path, backup_path))
            for temp_path, final_path in zip(
                temp_paths,
                final_paths,
                strict=True,
            ):
                temp_path.replace(final_path)
            for _final_path, backup_path in backups:
                with suppress(OSError):
                    backup_path.unlink(missing_ok=True)
        except Exception:
            for final_path, backup_path in reversed(
                tuple(zip(final_paths, backup_paths, strict=True))
            ):
                if backup_path.exists():
                    final_path.unlink(missing_ok=True)
                    backup_path.replace(final_path)
                elif not existed_before[final_path]:
                    final_path.unlink(missing_ok=True)
            for path in (*temp_paths, *backup_paths):
                path.unlink(missing_ok=True)
                path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
            raise

    return DiscoveryRunOutputs(
        candidates_csv=candidates_path,
        review_csv=review_path,
        physical_facts_json=physical_facts_path,
        physical_bindings=physical_bindings,
    )


def _verify_staged_discovery_csv(
    path: Path,
    *,
    columns: tuple[str, ...],
    row_count: int,
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or tuple(rows[0]) != columns:
        raise ValueError(f"{path}: staged Discovery CSV header verification failed")
    if len(rows) - 1 != row_count:
        raise ValueError(f"{path}: staged Discovery CSV row-count verification failed")


def _discovery_output_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _discovery_output_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _batch_index_row(
    *,
    raw_path: Path,
    outputs: DiscoveryRunOutputs,
    candidates: tuple[DiscoveryCandidate, ...],
    receipt: DiscoveryCandidateUniverseReceipt,
    index_parent: Path,
) -> dict[str, str]:
    counts = _priority_counts(candidates)
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": format_discovery_csv_value("sample_stem", raw_path.stem),
        "raw_file": format_discovery_csv_value("raw_file", raw_path),
        "candidate_csv": format_discovery_csv_value(
            "candidate_csv",
            _relative_batch_artifact(outputs.candidates_csv, parent=index_parent),
        ),
        "review_csv": format_discovery_csv_value(
            "review_csv",
            _relative_batch_artifact(outputs.review_csv, parent=index_parent),
        ),
        "candidate_count": str(len(candidates)),
        "high_count": str(counts["HIGH"]),
        "medium_count": str(counts["MEDIUM"]),
        "low_count": str(counts["LOW"]),
        **discovery_receipt_index_fields(receipt),
    }


def _relative_batch_artifact(path: Path, *, parent: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Discovery batch artifact must stay under its index") from exc
    return relative.as_posix()


def _priority_counts(
    candidates: tuple[DiscoveryCandidate, ...],
) -> dict[ReviewPriority, int]:
    counts: dict[ReviewPriority, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for candidate in candidates:
        counts[candidate.review_priority] += 1
    return counts


def write_batch_index(
    output_path: Path,
    rows: list[dict[str, str]],
    *,
    timing_recorder: TimingRecorder,
) -> Path:
    with timing_recorder.stage(
        "discover.write_batch_index",
        metrics={"raw_count": len(rows), "row_count": len(rows)},
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_delimited_rows(
            output_path,
            rows,
            _BATCH_INDEX_COLUMNS,
            delimiter=",",
            encoding="utf-8",
            extrasaction="raise",
        )
    return output_path
