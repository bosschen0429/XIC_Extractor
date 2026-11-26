"""Local replay snapshots: what an Alignment stage consumes, as the code wrote it.

Backfill, pairing and Matrix snapshots are the compressed pickle of the formal
objects, so they replay exactly what the full run handed the stage and a new
field needs no snapshot code. They are not exchange files: loading one runs
code it names, and it only matches the code that captured it.

The grouping snapshot is JSON without chromatograms or source traces: parallel
regroup loads it in every worker, and on 85RAW the formal objects would not fit
in memory that many times. Curve-based tie priority is kept as context order.

Replay captures you made yourself, and recapture after upstream code changes.
"""

from __future__ import annotations

import gzip
import json
import math
import pickle
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast, get_type_hints

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
from xic_extractor.alignment.grouping_experiment import GroupingInputs, GroupingWorkset
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import ordered_pattern_contexts
from xic_extractor.alignment.rt_anchors import BatchRtModelInputs, RtAnchorObservation
from xic_extractor.alignment.rt_drift import RtDriftModel
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)
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
    rt_calibration_from_payload,
    rt_calibration_payload,
    rt_drift_from_payload,
    rt_drift_payload,
)

_GROUPING_SCHEMA = "grouping_experiment_inputs"
_RtModel = tuple[
    BatchRtWarp | None,
    BatchRtCalibration | None,
    RtDriftModel | None,
    BatchRtModelInputs | None,
]


def _recapture(path: Path) -> ValueError:
    return ValueError(f"{path} was not captured by the current code; capture it again")


def save_snapshot(path: Path, value: object) -> None:
    with gzip.open(path, "wb", compresslevel=1) as stream:
        stream.write(owner_pickle_dumps(value, ()))


def load_snapshot(path: Path) -> Any:
    try:
        with gzip.open(path, "rb") as stream:
            return owner_pickle_loads(stream.read(), ())
    except (
        gzip.BadGzipFile,
        EOFError,
        pickle.UnpicklingError,
        AttributeError,
        ImportError,
    ) as exc:
        raise _recapture(path) from exc


def write_grouping_inputs(
    path: Path, inputs: GroupingInputs, *, provenance: Mapping[str, Any]
) -> None:
    """Share batch models and curve-free pattern contexts by index."""
    rt_models: list[dict[str, Any]] = []
    model_indices: dict[tuple[int, int, int, int], int] = {}
    contexts: list[SampleElutionContext] = []
    context_indices: dict[SampleElutionContext, int] = {}
    worksets = []
    for workset in inputs.worksets:
        identity = (
            id(workset.rt_warp),
            id(workset.rt_calibration),
            id(workset.rt_drift),
            id(workset.rt_model_inputs),
        )
        if identity not in model_indices:
            model = {
                "rt_warp": asdict(workset.rt_warp)
                if workset.rt_warp is not None
                else None,
                "rt_calibration": rt_calibration_payload(workset.rt_calibration)
                if workset.rt_calibration is not None
                else None,
                "rt_drift": rt_drift_payload(workset.rt_drift)
                if workset.rt_drift is not None
                else None,
                "rt_model_inputs": _rt_model_inputs_payload(workset.rt_model_inputs)
                if workset.rt_model_inputs is not None
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
        "schema": _GROUPING_SCHEMA,
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


def read_grouping_inputs(path: Path) -> tuple[GroupingInputs, dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("schema") != _GROUPING_SCHEMA:
            raise _recapture(path)
        inputs = _grouping_inputs(payload)
    except (
        gzip.BadGzipFile,
        EOFError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        raise _recapture(path) from exc
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


def _grouping_inputs(payload: Mapping[str, Any]) -> GroupingInputs:
    rt_models = [_read_rt_model(value) for value in payload["rt_models"]]
    contexts = tuple(
        _read_elution_context(value) for value in payload["elution_contexts"]
    )
    worksets = []
    for value in payload["worksets"]:
        warp, calibration, drift, model_inputs = rt_models[value["rt_model_index"]]
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
                rt_model_inputs=model_inputs,
                elution_contexts=tuple(
                    contexts[index] for index in value["elution_context_indices"]
                ),
            )
        )
    return GroupingInputs(
        sample_stems=tuple(payload["sample_stems"]),
        injection_order=payload["injection_order"],
        alignment_config=parse_grouping_alignment_config(payload["alignment_config"]),
        worksets=tuple(worksets),
        candidate_ids_by_owner={
            key: tuple(ids) for key, ids in payload["candidate_ids_by_owner"].items()
        },
    )


def parse_grouping_alignment_config(value: object) -> AlignmentConfig:
    """Admit settings from a grouping capture or JSON regroup overrides."""
    if not isinstance(value, Mapping):
        raise ValueError("alignment_config must be an object")
    for name, annotation in get_type_hints(AlignmentConfig).items():
        if name not in value:
            continue
        setting = value[name]
        if annotation is bool and type(setting) is not bool:
            raise ValueError(f"{name} must be a boolean")
        if annotation is int and type(setting) is not int:
            raise ValueError(f"{name} must be an integer")
        if annotation is float and not isinstance(setting, (int, float)):
            raise ValueError(f"{name} must be numeric")
    return AlignmentConfig(**value)


def _read_rt_model(value: Mapping[str, Any]) -> _RtModel:
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
    return (
        warp,
        rt_calibration_from_payload(value["rt_calibration"])
        if value["rt_calibration"] is not None
        else None,
        rt_drift_from_payload(value["rt_drift"])
        if value["rt_drift"] is not None
        else None,
        _read_rt_model_inputs(value["rt_model_inputs"])
        if value["rt_model_inputs"] is not None
        else None,
    )


def _rt_model_inputs_payload(inputs: BatchRtModelInputs) -> dict[str, Any]:
    return {
        "sample_stems": list(inputs.sample_stems),
        "sample_cohorts": dict(inputs.sample_cohorts),
        "injection_order": dict(inputs.injection_order),
        "qc_samples": sorted(inputs.qc_samples),
        "measured_istds": [list(item) for item in inputs.measured_istds],
        "anchor_batches": [
            [asdict(anchor) for anchor in batch] for batch in inputs.anchor_batches
        ],
    }


def _read_rt_model_inputs(value: Mapping[str, Any]) -> BatchRtModelInputs:
    return BatchRtModelInputs(
        sample_stems=tuple(value["sample_stems"]),
        sample_cohorts=value["sample_cohorts"],
        injection_order=value["injection_order"],
        qc_samples=frozenset(value["qc_samples"]),
        measured_istds=tuple(
            (label, sample, rt) for label, sample, rt in value["measured_istds"]
        ),
        anchor_batches=tuple(
            tuple(RtAnchorObservation(**anchor) for anchor in batch)
            for batch in value["anchor_batches"]
        ),
    )


def _read_elution_context(value: dict[str, Any]) -> SampleElutionContext:
    if not isinstance(value["sample_stem"], str):
        raise ValueError(
            "elution context requires a sample, mass, bounds and landmarks"
        )
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
        **{**value, "rt": (), "raw": (), "landmarks": tuple(landmarks)}
    )


def _owner_payload(owner: SampleLocalMS1Owner) -> dict[str, Any]:
    if owner.cid_nl_composite_assessment is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    member_subject = owner.cross_sample_member_subject
    if member_subject is None:
        raise ValueError("Grouping snapshot requires formal member subjects")
    integration = owner.peak.integration
    return {
        "owner_id": owner.owner_id,
        "sample_stem": owner.sample_stem,
        "raw_file": owner.raw_file,
        "precursor_mz": owner.peak.mz,
        "owner_apex_rt": owner.peak.rt_apex_min,
        "owner_peak_start_rt": owner.peak.rt_left_min,
        "owner_peak_end_rt": owner.peak.rt_right_min,
        "owner_area": owner.peak.area_raw_counts_seconds,
        "owner_height": owner.peak.height_raw,
        "primary_identity_event": (
            asdict(owner.primary_identity_event)
            if owner.primary_identity_event is not None
            else None
        ),
        "supporting_events": [asdict(item) for item in owner.supporting_events],
        "assignment_reason": owner.assignment_reason,
        "selected_integration": (
            asdict(replace(integration, raw_scan_indices=()))
            if integration is not None
            else None
        ),
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
    return {
        "sample_key": peak.sample_key,
        "mz": peak.mz,
        "rt": peak.rt,
        "intensity": peak.intensity,
        "integration": asdict(peak.integration)
        if peak.integration is not None
        else None,
    }


def _read_peak(value: dict[str, Any]) -> PeakObservation:
    rt = tuple(value["rt"])
    integration = value["integration"]
    return PeakObservation(
        sample_key=value["sample_key"],
        mz=value["mz"],
        rt=rt,
        intensity=tuple(value["intensity"]),
        integration=(
            _read_integration(integration) if integration is not None else None
        ),
        generation_provenance=None,  # type: ignore[arg-type]
        source_trace=None,  # type: ignore[arg-type]
    )


def _read_identity_event(value: dict[str, Any]) -> IdentityEvent:
    return IdentityEvent(
        **{
            **value,
            "source_scans": tuple(tuple(pair) for pair in value["source_scans"]),
        }
    )


def _read_owner(value: dict[str, Any]) -> SampleLocalMS1Owner:
    value = dict(value)
    if (shape := value["peak_shape"]) is not None:
        if type(shape["source_point_count"]) is not int:
            raise ValueError("local peak shape requires a finite normalized RT grid")
        value["peak_shape"] = LocalPeakShape(
            half_window_min=shape["half_window_min"],
            intensity=tuple(shape["intensity"]),
            source_point_count=shape["source_point_count"],
            half_height_point_count=shape["half_height_point_count"],
            edge_intensity_ratio=(
                tuple(shape["edge_intensity_ratio"])
                if shape["edge_intensity_ratio"] is not None
                else None
            ),
        )
    value["primary_identity_event"] = (
        _read_identity_event(value["primary_identity_event"])
        if value["primary_identity_event"]
        else None
    )
    value["supporting_events"] = tuple(
        _read_identity_event(item) for item in value["supporting_events"]
    )
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
    if not all(
        math.isfinite(number)
        for number in (
            owner.peak.rt_apex_min,
            owner.peak.rt_left_min,
            owner.peak.rt_right_min,
            owner.peak.height_raw,
        )
    ):
        raise ValueError("Grouping observations require finite measured values")
    return owner


def _read_owner_peak(
    value: dict[str, Any], physical_peaks: tuple[PeakObservation, ...]
) -> PeakObservation:
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
    selected = value.pop("selected_integration")
    integration = _read_integration(selected) if selected is not None else None
    return replace(
        member_peak,
        integration=(
            replace(integration, raw_scan_indices=tuple(range(len(member_peak.rt))))
            if integration is not None
            else None
        ),
    )


def _read_integration(value: dict[str, Any]) -> IntegrationResult:
    return IntegrationResult(
        **{
            **value,
            "raw_scan_indices": tuple(value["raw_scan_indices"]),
            "boundary_sources": tuple(value["boundary_sources"]),
            "signal_quality": _read_signal_quality(value["signal_quality"]),
        }
    )


def _read_signal_quality(value: object) -> SignalQuality | None:
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
