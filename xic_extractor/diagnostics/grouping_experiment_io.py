"""Compact grouping facts and shape descriptors, without full chromatograms."""

from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

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
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    CalibrationKnot,
    RtCalibrationPolicy,
    SampleRtCalibration,
)
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShape
from xic_extractor.tabular_io import write_delimited_rows


def query_discovery(
    index_path: Path, mz_ranges: Sequence[tuple[float, float]]
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Query saved candidates by measured mass; no RAW or physical-curve loading."""
    source = DiscoveryInputSource(read_discovery_batch_index(index_path))
    selected = tuple(
        cid
        for cid, location in source.locations.items()
        if any(
            lower
            <= source.ms1_apex_mz_by_candidate.get(cid, location.precursor_mz)
            <= upper
            for lower, upper in mz_ranges
        )
    )
    candidates = source.load_candidates(selected)
    return source.batch.sample_order, [
        {
            "sample_stem": sample,
            "candidate_id": candidate.candidate_id,
            "mz": candidate.physical_mz,
            "seed_mz": candidate.precursor_mz,
            "apex_rt": candidate.ms1_apex_rt,
            "seed_rt": candidate.best_seed_rt,
            "state": candidate.discovery_candidate_state,
            "ms1_peak_found": candidate.ms1_peak_found,
            "seed_event_count": candidate.seed_event_count,
            "area": candidate.ms1_area,
            "reason": candidate.reason,
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
    """Persist grouping facts and shape evidence, excluding full acquisition traces."""
    payload = {
        "schema": "grouping_experiment_inputs",
        "provenance": dict(provenance),
        "sample_stems": inputs.sample_stems,
        "injection_order": dict(inputs.injection_order),
        "alignment_config": asdict(inputs.alignment_config),
        "candidate_ids_by_owner": dict(inputs.candidate_ids_by_owner),
        "worksets": [
            {
                "key": workset.key,
                "owners": [_owner_payload(owner) for owner in workset.owners],
                "known_identity_bindings": workset.known_identity_bindings,
                "elution_contexts": [
                    asdict(context) for context in workset.elution_contexts
                ],
                "rt_warp": asdict(workset.rt_warp)
                if workset.rt_warp is not None
                else None,
                "rt_calibration": _rt_calibration_payload(workset.rt_calibration)
                if workset.rt_calibration is not None
                else None,
            }
            for workset in inputs.worksets
        ],
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(
            payload, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )


def read_grouping_inputs(path: Path) -> tuple[GroupingInputs, dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema") != "grouping_experiment_inputs":
        raise ValueError("Not a grouping experiment input snapshot")
    config = payload["alignment_config"]
    config["anchor_priorities"] = tuple(config["anchor_priorities"])
    worksets = []
    for value in payload["worksets"]:
        raw_warp = value["rt_warp"]
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
        worksets.append(
            GroupingWorkset(
                key=value["key"],
                owners=tuple(_read_owner(owner) for owner in value["owners"]),
                known_identity_bindings=tuple(
                    tuple(binding) for binding in value["known_identity_bindings"]
                ),
                rt_warp=warp,
                rt_calibration=_read_rt_calibration_payload(value["rt_calibration"])
                if value.get("rt_calibration") is not None
                else None,
                elution_contexts=tuple(
                    SampleElutionContext(
                        **{
                            **context,
                            "landmarks": tuple(
                                ElutionLandmark(**landmark)
                                for landmark in context["landmarks"]
                            ),
                        }
                    )
                    for context in value.get("elution_contexts", ())
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


def _owner_payload(owner: SampleLocalMS1Owner) -> dict[str, Any]:
    if owner.cid_nl_composite_assessment is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    integration = owner.selected_integration
    # Scan indices are only used by integration, never by grouping.
    compact = replace(
        owner,
        selected_integration=replace(integration, raw_scan_indices=())
        if integration
        else None,
    )
    value = asdict(compact)
    member = value["cross_sample_member_subject"]
    if member is None:
        raise ValueError("Grouping snapshot requires formal member subjects")
    member["basis_bytes"] = member["basis_bytes"].decode("utf-8")
    return value


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
    integration = value["selected_integration"]
    if integration is not None:
        integration["raw_scan_indices"] = tuple(integration["raw_scan_indices"])
        integration["boundary_sources"] = tuple(integration["boundary_sources"])
        value["selected_integration"] = IntegrationResult(**integration)
    # Retained snapshots may include the retired, non-semantic audit envelope.
    value.pop("region_audit", None)
    member = value["cross_sample_member_subject"]
    member["physical_observation_ids"] = tuple(member["physical_observation_ids"])
    member["basis_bytes"] = member["basis_bytes"].encode("utf-8")
    value["cross_sample_member_subject"] = CrossSampleGroupMemberSubject(**member)
    if value["cid_nl_composite_assessment"] is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    owner = SampleLocalMS1Owner(**value)
    if (
        owner.cross_sample_member_subject is None
        or owner.owner_id != owner.cross_sample_member_subject.subject_id
    ):
        raise ValueError("Grouping observation does not match its member subject")
    if not all(
        math.isfinite(number)
        for number in (
            owner.precursor_mz,
            owner.owner_apex_rt,
            owner.owner_peak_start_rt,
            owner.owner_peak_end_rt,
            owner.owner_area,
            owner.owner_height,
        )
    ):
        raise ValueError("Grouping observations require finite measured values")
    return owner


def read_rt_calibration(path: Path) -> BatchRtCalibration:
    """Read the existing sample_rt_calibration artifact into its domain owner."""
    value = json.loads(path.read_text(encoding="utf-8"))
    return _read_rt_calibration_payload(value)


def _read_rt_calibration_payload(value: dict[str, Any]) -> BatchRtCalibration:
    if value.get("schema") != "sample_rt_calibration":
        raise ValueError("Expected a sample_rt_calibration JSON artifact")
    samples = {}
    for row in value["samples"]:
        knots = tuple(
            (float(x), float(y)) for x, y in row["knots_observed_reference_min"]
        )
        if (
            any(not math.isfinite(v) for pair in knots for v in pair)
            or any(
                x2 <= x1 or y2 <= y1
                for (x1, y1), (x2, y2) in zip(knots, knots[1:], strict=False)
            )
            or len(knots) == 1
        ):
            raise ValueError(f"Invalid RT knots for {row['sample_stem']}")
        stem = row["sample_stem"]
        if stem in samples:
            raise ValueError("RT calibration has duplicate samples")
        samples[stem] = SampleRtCalibration(
            sample_stem=stem,
            status=row["status"],
            knots=knots,
            front_shift_min=row.get("front_shift_min"),
            prior_injection=None,
            perturbation_status=row.get("perturbation_status", "unmeasured"),
            anchor_count=row.get("anchor_count", 0),
            standard_residuals=tuple(
                tuple(item) for item in row.get("standard_residuals", ())
            ),
        )
    version = value.get("schema_version", 1)
    if version not in (1, 2):
        raise ValueError(f"Unsupported RT calibration version: {version}")
    policy = dict(value["policy"])
    if version == 1:
        # Historical snapshots retain their exact frozen coordinates. Retired
        # fitting parameters are not applied to, or refitted from, those maps.
        policy.pop("anchor_min_sample_fraction", None)
        policy.pop("knot_merge_tolerance_min", None)
    return BatchRtCalibration(
        policy=RtCalibrationPolicy(**policy),
        knots=tuple(
            CalibrationKnot(**{**row, "feature_ids": tuple(row["feature_ids"])})
            for row in value["knots"]
        ),
        samples=samples,
        front_shift_center_min=value.get("front_shift_center_min"),
        front_shift_mad_min=value.get("front_shift_mad_min"),
        perturbation_tolerance_min=value.get("perturbation_tolerance_min"),
    )


def write_rt_calibration(path: Path, calibration: BatchRtCalibration) -> None:
    write_json(path, _rt_calibration_payload(calibration))


def _rt_calibration_payload(calibration: BatchRtCalibration) -> dict[str, Any]:
    return {
        "schema": "sample_rt_calibration",
        "schema_version": 2,
        "policy": asdict(calibration.policy),
        "knots": [asdict(knot) for knot in calibration.knots],
        "samples": [
            {
                "sample_stem": sample.sample_stem,
                "status": sample.status,
                "knots_observed_reference_min": sample.knots,
                "front_shift_min": sample.front_shift_min,
                "perturbation_status": sample.perturbation_status,
                "anchor_count": sample.anchor_count,
                "standard_residuals": sample.standard_residuals,
            }
            for sample in calibration.samples.values()
        ],
        "front_shift_center_min": calibration.front_shift_center_min,
        "front_shift_mad_min": calibration.front_shift_mad_min,
        "perturbation_tolerance_min": calibration.perturbation_tolerance_min,
    }


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


def compare_membership(
    previous_members: Path,
    groups: Sequence[CrossSamplePeakGroupHypothesis],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Group merges and splits against a previous replay of the same snapshot.

    Owner identities come from the shared snapshot, so another code version's
    `members.tsv` maps onto the current groups. The gap is the spread of the
    parts' median observed apex RT.
    """
    with previous_members.open(encoding="utf-8", newline="") as stream:
        before = {
            row["owner_id"]: row["group_id"]
            for row in csv.DictReader(stream, delimiter="	")
        }
    owners = {owner.owner_id: owner for group in groups for owner in group.owners}
    after = {
        owner.owner_id: group.group_hypothesis_id
        for group in groups
        for owner in group.owners
    }
    shared = before.keys() & after.keys()
    rows = [
        *_membership_changes("merge", after, before, shared, owners),
        *_membership_changes("split", before, after, shared, owners),
    ]
    return rows, {
        "compared_owner_count": len(shared),
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
    owners: Mapping[str, SampleLocalMS1Owner],
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
            part: statistics.median(owners[o].owner_apex_rt for o in members)
            for part, members in split.items()
        }
        ordered = sorted(split, key=lambda part: (medians[part], part))
        rows.append(
            {
                "change": change,
                "group_id": whole_id,
                "mz": statistics.median(
                    owners[o].precursor_mz
                    for members in split.values()
                    for o in members
                ),
                "part_count": len(split),
                "part_sizes": "|".join(str(len(split[part])) for part in ordered),
                "part_apex_rt": "|".join(f"{medians[part]:.4f}" for part in ordered),
                "apex_gap_sec": (medians[ordered[-1]] - medians[ordered[0]]) * 60.0,
                "part_samples": "|".join(
                    ",".join(sorted(owners[o].sample_stem for o in split[part]))
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
        group_rows.append(
            {
                "group_id": group.group_hypothesis_id,
                "mz": group.family_center_mz,
                "rt": group.family_center_rt,
                "sample_count": len({owner.sample_stem for owner in group.owners}),
                "disposition": group.disposition,
                "basis": basis.basis if basis else "",
                "reason": basis.reason if basis else "",
                "evidence": group.evidence,
            }
        )
        for owner in group.owners:
            coordinates = coordinate_by_owner[owner.owner_id]
            members.append(
                {
                    "group_id": group.group_hypothesis_id,
                    "owner_id": owner.owner_id,
                    "candidate_ids": "|".join(
                        inputs.candidate_ids_by_owner.get(owner.owner_id, ())
                    ),
                    "sample_stem": owner.sample_stem,
                    "mz": owner.precursor_mz,
                    "observed_rt": owner.owner_apex_rt,
                    "comparison_rt": coordinates.corrected_rt_min(owner)
                    if coordinates
                    else owner.owner_apex_rt,
                    "area": owner.owner_area,
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
                        "mz": owner.precursor_mz,
                        "observed_rt": owner.owner_apex_rt,
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
        decisions.extend(asdict(decision) for decision in group.inference_decisions)
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
    return {
        "sample_count": len(inputs.sample_stems),
        "observation_count": len(members),
        "group_count": len(groups),
        "changed_observation_count": len(changes),
        "groups_by_disposition": dict(Counter(group.disposition for group in groups)),
    }
