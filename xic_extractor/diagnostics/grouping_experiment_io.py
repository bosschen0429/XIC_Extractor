"""Compact grouping facts and shape descriptors, without full chromatograms."""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
)
from xic_extractor.alignment.grouping_experiment import GroupingInputs, GroupingWorkset
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import ordered_pattern_contexts
from xic_extractor.alignment.rt_drift import RtDriftModel
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
)
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShape
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.physical_peak_models import PhysicalSegment
from xic_extractor.peak_detection.signal_quality import (
    SignalQuality,
    SignalQualityStatus,
)
from xic_extractor.rt_calibration_codec import (
    rt_calibration_from_payload as _read_rt_calibration_payload,
)
from xic_extractor.rt_calibration_codec import (
    rt_calibration_payload as _rt_calibration_payload,
)
from xic_extractor.rt_calibration_codec import (
    rt_drift_from_payload,
    rt_drift_payload,
)
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows

_RtModel = tuple[BatchRtWarp | None, BatchRtCalibration | None, RtDriftModel | None]


def query_discovery(
    index_path: Path, mz_ranges: Sequence[tuple[float, float]]
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Query saved candidates by measured mass; no RAW or physical-curve loading."""
    source = DiscoveryInputSource(read_discovery_batch_index(index_path))
    selected = tuple(
        cid
        for cid, location in source.locations.items()
        if any(lower <= location.peak_mz <= upper for lower, upper in mz_ranges)
    )
    candidates = source.load_candidates(selected)
    return source.batch.sample_order, [
        {
            "sample_stem": sample,
            "candidate_id": candidate.peak_id,
            "mz": candidate.peak_mz,
            "apex_rt": candidate.ms1_apex_rt,
            "peak_status": candidate.status,
            "attached_event_count": candidate.attached_event_count,
            # The representative event's values are its event-table row.
            "best_ms2_scan_id": candidate.best_ms2_scan_id,
        }
        for sample in source.batch.sample_order
        for candidate in candidates[sample]
    ]


def write_table(
    path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> None:
    write_delimited_rows(
        path, rows, columns, delimiter="\t", encoding="utf-8", extrasaction="raise"
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_grouping_inputs(
    path: Path, inputs: GroupingInputs, *, provenance: Mapping[str, Any]
) -> None:
    """Persist replay facts; share batch models and curve-free pattern contexts."""
    rt_models: list[dict[str, Any]] = []
    model_indices: dict[tuple[int, int, int], int] = {}
    contexts: list[SampleElutionContext] = []
    context_indices: dict[SampleElutionContext, int] = {}
    worksets = []
    for workset in inputs.worksets:
        identity = (
            id(workset.rt_warp),
            id(workset.rt_calibration),
            id(workset.rt_drift),
        )
        if identity not in model_indices:
            # Partition restoration can make equal copies of the frozen batch
            # model. Include anchor-owner bindings, which dataclass equality
            # deliberately excludes, when sharing the persisted facts.
            model = {
                "rt_warp": asdict(workset.rt_warp)
                if workset.rt_warp is not None
                else None,
                "rt_calibration": _rt_calibration_payload(workset.rt_calibration)
                if workset.rt_calibration is not None
                else None,
                "rt_drift": rt_drift_payload(workset.rt_drift)
                if workset.rt_drift is not None
                else None,
            }
            index = next(
                (i for i, other in enumerate(rt_models) if other == model),
                len(rt_models),
            )
            if index == len(rt_models):
                rt_models.append(model)
            model_indices[identity] = index
        selected_contexts = []
        for context in ordered_pattern_contexts(workset.elution_contexts):
            scalar = replace(context, rt=(), raw=())
            if scalar not in context_indices:
                context_indices[scalar] = len(contexts)
                contexts.append(scalar)
            selected_contexts.append(context_indices[scalar])
        worksets.append(
            {
                "key": workset.key,
                "owners": [_owner_payload(owner) for owner in workset.owners],
                "known_identity_bindings": workset.known_identity_bindings,
                "rt_model_index": model_indices[identity],
                "elution_context_indices": selected_contexts,
            }
        )
    payload = {
        "schema": "grouping_experiment_inputs",
        "provenance": dict(provenance),
        "sample_stems": inputs.sample_stems,
        "injection_order": dict(inputs.injection_order),
        "alignment_config": asdict(inputs.alignment_config),
        "candidate_ids_by_owner": dict(inputs.candidate_ids_by_owner),
        "rt_models": rt_models,
        "elution_contexts": [
            {
                key: value
                for key, value in asdict(context).items()
                if key not in {"rt", "raw"}
            }
            for context in contexts
        ],
        "worksets": worksets,
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(
            payload, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )


# Saved snapshots may contain these retired, non-semantic settings.
# Unknown keys still reach AlignmentConfig and fail rather than hiding typos.
_RETIRED_ALIGNMENT_CONFIG_KEYS = frozenset(
    {
        "anchor_min_scan_support_score",
        "anchor_priorities",
        "duplicate_fold_min_detected_jaccard",
        "duplicate_fold_min_detected_overlap",
        "duplicate_fold_min_present_overlap",
        "duplicate_fold_min_shared_detected_count",
        "duplicate_fold_observed_loss_ppm",
        "duplicate_fold_ppm",
        "duplicate_fold_product_ppm",
        "duplicate_fold_rt_sec",
        "identity_rt_candidate_window_sec",
        "mz_bucket_neighbor_radius",
        "owner_backfill_min_detected_samples",
        "owner_tail_max_secondary_ratio",
        "owner_tail_seed_guard_sec",
        "owner_window_overlap_fraction",
    }
)


def read_grouping_inputs(path: Path) -> tuple[GroupingInputs, dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema") != "grouping_experiment_inputs":
        raise ValueError("Not a grouping experiment input snapshot")
    config = {
        key: value
        for key, value in payload["alignment_config"].items()
        if key not in _RETIRED_ALIGNMENT_CONFIG_KEYS
    }
    rt_models = [_read_rt_model(value) for value in payload.get("rt_models", ())]
    contexts = tuple(
        _read_elution_context(value) for value in payload.get("elution_contexts", ())
    )
    worksets = []
    for value in payload["worksets"]:
        warp, calibration, drift = (
            _snapshot_item(rt_models, value["rt_model_index"], "RT model")
            if "rt_model_index" in value
            else _read_rt_model(value)
        )
        worksets.append(
            GroupingWorkset(
                key=value["key"],
                owners=tuple(_read_owner(owner) for owner in value["owners"]),
                known_identity_bindings=tuple(
                    tuple(binding) for binding in value["known_identity_bindings"]
                ),
                rt_warp=warp,
                rt_calibration=calibration,
                rt_drift=drift,
                elution_contexts=(
                    tuple(
                        _snapshot_item(contexts, index, "elution context")
                        for index in value["elution_context_indices"]
                    )
                    if "elution_context_indices" in value
                    else tuple(
                        _read_elution_context(context)
                        for context in value.get("elution_contexts", ())
                    )
                ),
            )
        )
    inputs = GroupingInputs(
        sample_stems=tuple(payload["sample_stems"]),
        injection_order=payload["injection_order"],
        alignment_config=AlignmentConfig(**config),
        worksets=tuple(worksets),
        candidate_ids_by_owner={
            key: tuple(ids) for key, ids in payload["candidate_ids_by_owner"].items()
        },
    )
    if len(inputs.sample_stems) != len(set(inputs.sample_stems)):
        raise ValueError("Grouping snapshot has duplicate samples")
    for workset in inputs.worksets:
        if len({owner.owner_id for owner in workset.owners}) != len(workset.owners):
            raise ValueError("Grouping snapshot has duplicate owners in a workset")
        if any(
            owner.sample_stem not in inputs.sample_stems for owner in workset.owners
        ):
            raise ValueError("Grouping snapshot omits an observation's sample")
    return inputs, payload["provenance"]


def _snapshot_item(values: Sequence[Any], index: object, label: str) -> Any:
    if type(index) is not int or not 0 <= index < len(values):
        raise ValueError(f"Grouping snapshot has invalid {label} index: {index}")
    return values[index]


def _read_rt_model(value: Mapping[str, Any]) -> _RtModel:
    raw_warp = value.get("rt_warp")
    warp = (
        None
        if raw_warp is None
        else BatchRtWarp(
            tuple(
                (sample, tuple(tuple(pair) for pair in knots))
                for sample, knots in raw_warp["knots_by_sample"]
            ),
            raw_warp["anchor_group_count"],
        )
    )
    return (
        warp,
        _read_rt_calibration_payload(value["rt_calibration"])
        if value.get("rt_calibration") is not None
        else None,
        rt_drift_from_payload(value["rt_drift"])
        if value.get("rt_drift") is not None
        else None,
    )


def _read_elution_context(value: dict[str, Any]) -> SampleElutionContext:
    landmarks = []
    for item in value["landmarks"]:
        segment = item["physical_segment"]
        landmarks.append(
            ElutionLandmark(
                **{
                    **item,
                    "physical_segment": None
                    if segment is None
                    else PhysicalSegment(
                        **{
                            **segment,
                            "evidence_sources": tuple(segment["evidence_sources"]),
                        }
                    ),
                }
            )
        )
    return SampleElutionContext(
        **{
            **value,
            "rt": tuple(value.get("rt", ())),
            "raw": tuple(value.get("raw", ())),
            "landmarks": tuple(landmarks),
        }
    )


def _owner_payload(owner: SampleLocalMS1Owner) -> dict[str, Any]:
    if owner.cid_nl_composite_assessment is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    member_subject = owner.cross_sample_member_subject
    if member_subject is None:
        raise ValueError("Grouping snapshot requires formal member subjects")
    integration = owner.peak.integration
    # The snapshot keeps the owner's measured values beside its member peak,
    # which carries the trace; scan indices are only used by integration.
    return {
        "owner_id": owner.owner_id,
        "sample_stem": owner.sample_stem,
        "raw_file": owner.raw_file,
        "precursor_mz": owner.peak.mz,
        "owner_apex_rt": integration.rt_apex_min,
        "owner_peak_start_rt": integration.rt_left_min,
        "owner_peak_end_rt": integration.rt_right_min,
        "owner_area": integration.area_raw_counts_seconds,
        "owner_height": integration.height_raw,
        "primary_identity_event": (
            asdict(owner.primary_identity_event)
            if owner.primary_identity_event is not None
            else None
        ),
        "supporting_events": [asdict(item) for item in owner.supporting_events],
        "assignment_reason": owner.assignment_reason,
        "selected_integration": asdict(replace(integration, raw_scan_indices=())),
        "quantification_eligible": owner.quantification_eligible,
        "quantification_exclusion_reason": owner.quantification_exclusion_reason,
        "cross_sample_member_subject": {
            **asdict(replace(member_subject, physical_peaks=())),
            "physical_peaks": [
                _peak_payload(peak) for peak in member_subject.physical_peaks
            ],
        },
        "cid_nl_composite_assessment": None,
        "peak_shape": (
            asdict(owner.peak_shape) if owner.peak_shape is not None else None
        ),
    }


def _peak_payload(peak: PeakObservation) -> dict[str, Any]:
    # A snapshot keeps each member peak's measured values, not its acquisition.
    return {
        "sample_key": peak.sample_key,
        "mz": peak.mz,
        "rt": peak.rt,
        "intensity": peak.intensity,
        "integration": asdict(peak.integration),
    }


def _read_peak(value: dict[str, Any]) -> PeakObservation:
    # Replay compares member peaks by their measured values only; the snapshot
    # retains no trace or query context to restore.
    return PeakObservation(
        sample_key=value["sample_key"],
        mz=value["mz"],
        rt=tuple(value["rt"]),
        intensity=tuple(value["intensity"]),
        integration=_read_integration(value["integration"]),
        generation_provenance=None,  # type: ignore[arg-type]
        source_trace=None,  # type: ignore[arg-type]
    )


def _read_owner(value: dict[str, Any]) -> SampleLocalMS1Owner:
    value = dict(value)
    if (shape := value.get("peak_shape")) is not None:
        value["peak_shape"] = LocalPeakShape(
            half_window_min=shape["half_window_min"],
            intensity=tuple(shape["intensity"]),
            source_point_count=shape["source_point_count"],
            half_height_point_count=shape.get("half_height_point_count"),
            edge_intensity_ratio=(
                tuple(shape["edge_intensity_ratio"])
                if shape.get("edge_intensity_ratio") is not None
                else None
            ),
        )
    value["primary_identity_event"] = (
        IdentityEvent(**value["primary_identity_event"])
        if value["primary_identity_event"]
        else None
    )
    value["supporting_events"] = tuple(
        IdentityEvent(**item) for item in value["supporting_events"]
    )
    # Retained snapshots may include the retired, non-semantic audit envelope
    # and the retired owner flags that production always wrote as False.
    value.pop("region_audit", None)
    value.pop("identity_conflict", None)
    value.pop("is_provisional", None)
    member = value["cross_sample_member_subject"]
    member["physical_peaks"] = tuple(
        _read_peak(peak) for peak in member["physical_peaks"]
    )
    value["peak"] = _read_owner_peak(value, member["physical_peaks"])
    value["cross_sample_member_subject"] = CrossSampleGroupMemberSubject(**member)
    if value["cid_nl_composite_assessment"] is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    owner = SampleLocalMS1Owner(**value)
    if (
        owner.cross_sample_member_subject is None
        or owner.owner_id != owner.cross_sample_member_subject.subject_id
    ):
        raise ValueError("Grouping observation does not match its member subject")
    integration = owner.peak.integration
    if not all(
        math.isfinite(number)
        for number in (
            integration.rt_apex_min,
            integration.rt_left_min,
            integration.rt_right_min,
            integration.area_raw_counts_seconds,
            integration.height_raw,
        )
    ):
        raise ValueError("Grouping observations require finite measured values")
    return owner


def _read_owner_peak(
    value: dict[str, Any], physical_peaks: tuple[PeakObservation, ...]
) -> PeakObservation:
    # The selected integration carries the owner's measured values and the
    # member peak its trace; the written copies are the same values.
    for key in (
        "precursor_mz",
        "owner_apex_rt",
        "owner_peak_start_rt",
        "owner_peak_end_rt",
        "owner_area",
        "owner_height",
    ):
        value.pop(key)
    (member_peak,) = physical_peaks
    integration = _read_integration(value.pop("selected_integration"))
    return replace(
        member_peak,
        integration=replace(
            integration, raw_scan_indices=tuple(range(len(member_peak.rt)))
        ),
    )


def _read_integration(value: dict[str, Any]) -> IntegrationResult:
    return IntegrationResult(
        **{
            **value,
            "raw_scan_indices": tuple(value["raw_scan_indices"]),
            "boundary_sources": tuple(value["boundary_sources"]),
            "signal_quality": signal_quality_from_payload(value.get("signal_quality")),
        }
    )


def read_rt_calibration(path: Path) -> BatchRtCalibration:
    """Read the existing sample_rt_calibration artifact into its domain owner."""
    value = json.loads(path.read_text(encoding="utf-8"))
    return _read_rt_calibration_payload(value)


def write_rt_calibration(path: Path, calibration: BatchRtCalibration) -> None:
    write_json(path, _rt_calibration_payload(calibration))


def group_signature(groups: Sequence[CrossSamplePeakGroupHypothesis]) -> tuple:
    """Compare scientific decisions, independent of display row numbering."""
    return tuple(
        sorted(
            (
                tuple(sorted(group.owner_ids)),
                group.disposition,
                group.evidence,
                group.inference_basis.basis if group.inference_basis else "",
                group.inference_basis.reason if group.inference_basis else "",
            )
            for group in groups
        )
    )


def _members_by_owner(
    source: Path | Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    rows = (
        read_delimited_rows(
            source,
            required_columns=(
                "owner_id",
                "group_id",
                "sample_stem",
                "mz",
                "observed_rt",
            ),
        )
        if isinstance(source, Path)
        else source
    )
    owners = {row["owner_id"]: row for row in rows}
    if len(owners) != len(rows):
        raise ValueError("Duplicate membership owner_id")
    return owners


def write_membership_comparison(
    previous_members: Path,
    current_members: Path | Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> dict[str, int]:
    """Compare partitions of shared observations, independent of display IDs."""
    previous = _members_by_owner(previous_members)
    owners = _members_by_owner(current_members)
    before = {key: row["group_id"] for key, row in previous.items()}
    after = {key: row["group_id"] for key, row in owners.items()}
    shared = before.keys() & after.keys()
    if not shared:
        raise ValueError("No comparable owners in the two membership tables")
    rows = [
        *_membership_changes("merge", after, before, shared, owners),
        *_membership_changes("split", before, after, shared, owners),
    ]
    write_table(
        output_dir / "membership-diff.tsv",
        rows,
        (
            "change",
            "group_id",
            "mz",
            "part_count",
            "part_sizes",
            "part_apex_rt",
            "apex_gap_sec",
            "part_samples",
            "part_owners",
        ),
    )
    return {
        "compared_owner_count": len(shared),
        "before_only_owner_count": len(before) - len(shared),
        "after_only_owner_count": len(after) - len(shared),
        "merge_count": sum(row["change"] == "merge" for row in rows),
        "split_count": sum(row["change"] == "split" for row in rows),
        "merge_within_10s_count": sum(
            row["change"] == "merge" and row["apex_gap_sec"] < 10 for row in rows
        ),
        "split_within_10s_count": sum(
            row["change"] == "split" and row["apex_gap_sec"] < 10 for row in rows
        ),
    }


def _membership_changes(
    change: str,
    whole: Mapping[str, str],
    parts: Mapping[str, str],
    shared: set[str],
    owners: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rows where one group of `whole` holds owners of several `parts` groups."""
    by_whole: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for owner_id in shared:
        by_whole[whole[owner_id]][parts[owner_id]].append(owner_id)
    rows = []
    for whole_id, split in sorted(by_whole.items()):
        if len(split) < 2:
            continue
        medians = {
            part: statistics.median(float(owners[o]["observed_rt"]) for o in members)
            for part, members in split.items()
        }
        ordered = sorted(split, key=lambda part: (medians[part], part))
        rows.append(
            {
                "change": change,
                "group_id": whole_id,
                "mz": statistics.median(
                    float(owners[o]["mz"])
                    for members in split.values()
                    for o in members
                ),
                "part_count": len(split),
                "part_sizes": "|".join(str(len(split[part])) for part in ordered),
                "part_apex_rt": "|".join(f"{medians[part]:.4f}" for part in ordered),
                "apex_gap_sec": (medians[ordered[-1]] - medians[ordered[0]]) * 60.0,
                "part_owners": "|".join(
                    ",".join(sorted(split[part])) for part in ordered
                ),
                "part_samples": "|".join(
                    ",".join(sorted(owners[o]["sample_stem"] for o in split[part]))
                    for part in ordered
                ),
            }
        )
    return rows


def write_grouping_result(
    output: Path,
    inputs: GroupingInputs,
    groups: Sequence[CrossSamplePeakGroupHypothesis],
    *,
    baseline: Sequence[CrossSamplePeakGroupHypothesis] = (),
    compare_to: Path | None = None,
    rt_coordinates: RtFrame | None = None,
) -> dict[str, Any]:
    before = {owner.owner_id: group for group in baseline for owner in group.owners}
    members = []
    changes = []
    group_rows = []
    decisions: list[dict[str, Any]] = []
    coordinate_by_owner = {
        owner.owner_id: rt_coordinates or workset.rt_calibration or workset.rt_warp
        for workset in inputs.worksets
        for owner in workset.owners
    }
    for group in groups:
        basis = group.inference_basis
        risks = {
            decision.owner_id: decision.accepted_risk
            for decision in group.inference_decisions
            if decision.accepted_risk is not None
        }
        group_rows.append(
            {
                "group_id": group.group_hypothesis_id,
                "mz": group.group_center_mz,
                "rt": group.group_center_rt,
                "sample_count": len({owner.sample_stem for owner in group.owners}),
                "disposition": group.disposition,
                "basis": basis.basis if basis else "",
                "reason": basis.reason if basis else "",
                "evidence": group.evidence,
            }
        )
        for owner in group.owners:
            coordinates = coordinate_by_owner[owner.owner_id]
            risk = risks.get(owner.owner_id)
            comparison_rt = (
                coordinates.corrected_rt_min(owner)
                if coordinates
                else owner.peak.integration.rt_apex_min
            )
            if risk is not None:
                # The selected universe can reject an available calibration,
                # or use a weak estimate that the strong-only map cannot supply.
                if risk.rt_basis == "raw":
                    comparison_rt = owner.peak.integration.rt_apex_min
                elif risk.rt_estimate is not None:
                    comparison_rt = risk.rt_estimate.rt_min
            members.append(
                {
                    "group_id": group.group_hypothesis_id,
                    "owner_id": owner.owner_id,
                    "candidate_ids": "|".join(
                        inputs.candidate_ids_by_owner.get(owner.owner_id, ())
                    ),
                    "sample_stem": owner.sample_stem,
                    "mz": repr(float(owner.peak.mz)),
                    "observed_rt": repr(float(owner.peak.integration.rt_apex_min)),
                    "comparison_rt": comparison_rt,
                    "comparison_rt_basis": risk.rt_basis
                    if risk is not None
                    else "not_recorded",
                    "area": owner.peak.integration.area_raw_counts_seconds,
                    "disposition": group.disposition,
                    "reason": basis.reason if basis else "",
                }
            )
            old = before.get(owner.owner_id)
            if old is not None and group_signature((old,)) != group_signature((group,)):
                changes.append(
                    {
                        "owner_id": owner.owner_id,
                        "sample_stem": owner.sample_stem,
                        "mz": owner.peak.mz,
                        "observed_rt": owner.peak.integration.rt_apex_min,
                        "before_group": old.group_hypothesis_id,
                        "after_group": group.group_hypothesis_id,
                        "before_members": len(old.owner_ids),
                        "after_members": len(group.owner_ids),
                        "before_reason": old.inference_basis.reason
                        if old.inference_basis
                        else "",
                        "after_reason": basis.reason if basis else "",
                    }
                )
        for decision in group.inference_decisions:
            fields = asdict(decision)
            risk = fields.pop("accepted_risk")
            fields["accepted_risk_json"] = (
                json.dumps(risk, ensure_ascii=False, allow_nan=False)
                if risk is not None
                else ""
            )
            decisions.append(fields)
    write_table(
        output / "groups.tsv",
        group_rows,
        (
            "group_id",
            "mz",
            "rt",
            "sample_count",
            "disposition",
            "basis",
            "reason",
            "evidence",
        ),
    )
    write_table(
        output / "members.tsv",
        members,
        (
            "group_id",
            "owner_id",
            "candidate_ids",
            "sample_stem",
            "mz",
            "observed_rt",
            "comparison_rt",
            "comparison_rt_basis",
            "area",
            "disposition",
            "reason",
        ),
    )
    write_table(
        output / "changes.tsv",
        changes,
        (
            "owner_id",
            "sample_stem",
            "mz",
            "observed_rt",
            "before_group",
            "after_group",
            "before_members",
            "after_members",
            "before_reason",
            "after_reason",
        ),
    )
    write_table(
        output / "decisions.tsv",
        decisions,
        (
            "direction",
            "sample_stem",
            "owner_id",
            "feature_key",
            "state",
            "reason",
            "cost",
            "witness_owner_ids",
            "selected_witness_owner_id",
            "rt_basis",
            "rt_raw_delta_sec",
            "rt_drift_corrected_delta_sec",
            "accepted_risk_json",
        ),
    )
    present = Counter(row["sample_stem"] for row in members)
    write_table(
        output / "samples.tsv",
        [
            {"sample_stem": sample, "observation_count": present[sample]}
            for sample in inputs.sample_stems
        ],
        ("sample_stem", "observation_count"),
    )
    summary: dict[str, Any] = {
        "sample_count": len(inputs.sample_stems),
        "observation_count": len(members),
        "group_count": len(groups),
        "changed_observation_count": len(changes),
        "groups_by_disposition": dict(Counter(group.disposition for group in groups)),
    }
    if compare_to is not None:
        summary["compared_to"] = str(compare_to)
        summary["membership_diff"] = write_membership_comparison(
            compare_to / "members.tsv", members, output
        )
    return summary


def signal_quality_from_payload(value: object) -> SignalQuality | None:
    """Read optional quality facts in physical, observation and replay payloads."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("signal_quality must be one JSON object")
    quality = value
    if set(quality) != {
        "status",
        "noise_sigma",
        "signal_to_noise",
        "low_signal_to_noise",
    }:
        raise ValueError("signal_quality fields must match the declared schema")
    status = quality["status"]
    if status not in (
        "available",
        "insufficient_points",
        "invalid_trace",
        "missing_baseline",
        "zero_noise",
    ):
        raise ValueError("signal_quality has an unknown status")
    values: dict[str, float | None] = {}
    for key in ("noise_sigma", "signal_to_noise"):
        number = quality[key]
        if number is not None and (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number < 0
        ):
            raise ValueError(f"signal_quality {key} must be finite and nonnegative")
        values[key] = None if number is None else float(number)
    low = quality["low_signal_to_noise"]
    if low is not None and not isinstance(low, bool):
        raise ValueError("signal_quality low_signal_to_noise must be boolean or null")
    sigma, ratio = values["noise_sigma"], values["signal_to_noise"]
    if status == "available":
        if sigma is None or sigma <= 0 or ratio is None or low is None:
            raise ValueError("available signal_quality requires positive noise and S/N")
    elif ratio is not None or low is not None:
        raise ValueError("unavailable signal_quality cannot claim S/N or good quality")
    return SignalQuality(cast(SignalQualityStatus, status), sigma, ratio, low)
