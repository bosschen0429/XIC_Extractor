"""Grouping diagnostics: Discovery queries, replay result tables and comparisons."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.grouping_experiment import GroupingInputs
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
)
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.rt_calibration_codec import (
    rt_calibration_from_payload as _read_rt_calibration_payload,
)
from xic_extractor.rt_calibration_codec import (
    rt_calibration_payload as _rt_calibration_payload,
)
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


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


def read_rt_calibration(path: Path) -> BatchRtCalibration:
    """Read the existing sample_rt_calibration artifact into its domain owner."""
    value = json.loads(path.read_text(encoding="utf-8"))
    return _read_rt_calibration_payload(value)


def write_rt_calibration(path: Path, calibration: BatchRtCalibration) -> None:
    write_json(path, _rt_calibration_payload(calibration))


_GroupDecision = tuple[tuple[str, ...], str, str, str, str]


def group_signature(groups: Sequence[CrossSamplePeakGroupHypothesis]) -> tuple:
    """Compare scientific decisions, independent of display row numbering."""
    return tuple(sorted(_group_decision(group) for group in groups))


def _group_decision(group: CrossSamplePeakGroupHypothesis) -> _GroupDecision:
    basis = group.inference_basis
    return (
        tuple(sorted(group.owner_ids)),
        group.disposition,
        group.evidence,
        basis.basis if basis else "",
        basis.reason if basis else "",
    )


def _baseline_from_tables(directory: Path) -> dict[str, tuple[str, _GroupDecision]]:
    """Each owner's group and decision as written in groups.tsv and members.tsv."""
    groups = {
        row["group_id"]: row
        for row in read_delimited_rows(
            directory / "groups.tsv",
            required_columns=("group_id", "disposition", "evidence", "basis", "reason"),
        )
    }
    owners: dict[str, list[str]] = defaultdict(list)
    for row in read_delimited_rows(
        directory / "members.tsv", required_columns=("group_id", "owner_id")
    ):
        owners[row["group_id"]].append(row["owner_id"])
    baseline: dict[str, tuple[str, _GroupDecision]] = {}
    for group_id, members in owners.items():
        row = groups[group_id]
        decision = (
            tuple(sorted(members)),
            row["disposition"],
            row["evidence"],
            row["basis"],
            row["reason"],
        )
        baseline.update(dict.fromkeys(members, (group_id, decision)))
    return baseline


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
    baseline: Sequence[CrossSamplePeakGroupHypothesis] | Path = (),
    compare_to: Path | None = None,
    rt_coordinates: RtFrame | None = None,
) -> dict[str, Any]:
    """Write the replay tables; ``changes.tsv`` compares with ``baseline``, the
    grouping or a directory holding its ``groups.tsv`` and ``members.tsv``.
    """
    before = (
        _baseline_from_tables(baseline)
        if isinstance(baseline, Path)
        else {
            owner.owner_id: (group.group_hypothesis_id, _group_decision(group))
            for group in baseline
            for owner in group.owners
        }
    )
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
        signature = _group_decision(group)
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
                else owner.peak.rt_apex_min
            )
            if risk is not None:
                if risk.rt_basis == "raw":
                    comparison_rt = owner.peak.rt_apex_min
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
                    "observed_rt": repr(float(owner.peak.rt_apex_min)),
                    "comparison_rt": comparison_rt,
                    "comparison_rt_basis": risk.rt_basis
                    if risk is not None
                    else "not_recorded",
                    "area": owner.peak.area_raw_counts_seconds,
                    "disposition": group.disposition,
                    "reason": basis.reason if basis else "",
                }
            )
            old = before.get(owner.owner_id)
            if old is not None and old[1] != signature:
                old_group, (old_owners, *_, old_reason) = old
                changes.append(
                    {
                        "owner_id": owner.owner_id,
                        "sample_stem": owner.sample_stem,
                        "mz": owner.peak.mz,
                        "observed_rt": owner.peak.rt_apex_min,
                        "before_group": old_group,
                        "after_group": group.group_hypothesis_id,
                        "before_members": len(old_owners),
                        "after_members": len(group.owner_ids),
                        "before_reason": old_reason,
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
