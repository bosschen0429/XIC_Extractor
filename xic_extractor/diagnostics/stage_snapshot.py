"""Local replay snapshots: what an Alignment stage consumes, as the code wrote it.

Backfill, pairing and Matrix snapshots are the compressed pickle of the formal
objects, so they replay exactly what the full run handed the stage and a new
field needs no snapshot code. They are not exchange files: loading one runs
code it names, and it only matches the code that captured it.

The grouping snapshot retains selected peak subtraces and batch models, not
acquisition traces or method assessments. It replays band linking.

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

from xic_extractor.alignment.config import AlignmentConfig, validate_alignment_config
from xic_extractor.alignment.grouping_experiment import GroupingInputs, GroupingWorkset
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.rt_anchors import BatchRtModelInputs, RtAnchorObservation
from xic_extractor.alignment.rt_drift import RtDriftModel
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)
from xic_extractor.peak_detection.integration_result import (
    IntegrationResult,
    validate_stored_integration_numbers,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    PeakObservationContractError,
    canonical_peak_value,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis
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

_GROUPING_SCHEMA = "grouping_band_inputs"
_RtModel = tuple[
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
    """Share batch models by index."""
    rt_models: list[dict[str, Any]] = []
    model_indices: dict[tuple[int, int, int], int] = {}
    worksets = []
    for workset in inputs.worksets:
        identity = (
            id(workset.rt_calibration),
            id(workset.rt_drift),
            id(workset.rt_model_inputs),
        )
        if identity not in model_indices:
            model = {
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
        worksets.append(
            {
                "key": workset.key,
                "owners": [_owner_payload(owner) for owner in workset.owners],
                "known_identity_bindings": workset.known_identity_bindings,
                "rt_model_index": model_indices[identity],
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
    worksets = []
    for value in payload["worksets"]:
        calibration, drift, model_inputs = rt_models[value["rt_model_index"]]
        worksets.append(
            GroupingWorkset(
                key=value["key"],
                owners=tuple(_read_owner(owner) for owner in value["owners"]),
                known_identity_bindings=tuple(
                    tuple(binding) for binding in value["known_identity_bindings"]
                ),
                rt_calibration=calibration,
                rt_drift=drift,
                rt_model_inputs=model_inputs,
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
        if annotation is float and not isinstance(setting, (int, float)):
            raise ValueError(f"{name} must be numeric")
    config = AlignmentConfig(**value)
    validate_alignment_config(config)
    return config


def _read_rt_model(value: Mapping[str, Any]) -> _RtModel:
    return (
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


def _owner_payload(owner: SampleLocalMS1Owner) -> dict[str, Any]:
    if owner.cid_nl_composite_assessment is not None:
        raise ValueError("Grouping snapshots require scalar CID-NL observations")
    return {
        "hypothesis": {
            "hypothesis_id": owner.hypothesis.hypothesis_id,
            "method": owner.hypothesis.method,
            "peak": _peak_payload(owner.peak),
        },
        "sample_stem": owner.sample_stem,
        "raw_file": owner.raw_file,
        "primary_identity_event": asdict(owner.primary_identity_event)
        if owner.primary_identity_event is not None
        else None,
        "supporting_events": [asdict(item) for item in owner.supporting_events],
        "assignment_reason": owner.assignment_reason,
        "quantification_eligible": owner.quantification_eligible,
        "quantification_exclusion_reason": owner.quantification_exclusion_reason,
    }


def _peak_payload(peak: PeakObservation) -> dict[str, Any]:
    return {
        "sample_key": peak.sample_key,
        "mz": peak.mz,
        "rt": peak.rt,
        "intensity": peak.intensity,
        "integration": asdict(
            replace(peak.integration, raw_scan_indices=tuple(range(len(peak.rt))))
        )
        if peak.integration is not None
        else None,
    }


def _read_peak(value: dict[str, Any]) -> PeakObservation:
    mz = canonical_peak_value(value["mz"], field="mz")
    if mz <= 0.0:
        raise PeakObservationContractError("mz must be positive")
    rt = tuple(canonical_peak_value(item, field="rt") for item in value["rt"])
    intensity = tuple(
        canonical_peak_value(item, field="intensity") for item in value["intensity"]
    )
    if not rt or len(rt) != len(intensity):
        raise PeakObservationContractError(
            "selected rt and intensity must be matching non-empty tuples"
        )
    if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
        raise PeakObservationContractError(
            "selected rt values must be strictly increasing"
        )
    stored = value["integration"]
    integration = _read_integration(stored) if stored is not None else None
    if integration is not None and integration.raw_scan_indices != tuple(
        range(len(rt))
    ):
        raise PeakObservationContractError(
            "content integration indices must address the selected subtrace"
        )
    _validate_integration(integration, selected_rt=rt)
    return PeakObservation(
        sample_key=value["sample_key"],
        mz=mz,
        rt=rt,
        intensity=intensity,
        integration=integration,
        generation_provenance=None,  # type: ignore[arg-type]
        source_trace=None,  # type: ignore[arg-type]
    )


def _validate_integration(
    integration: IntegrationResult | None,
    *,
    selected_rt: tuple[float, ...],
) -> None:
    if integration is None:
        return
    if not (
        integration.rt_left_min <= integration.rt_apex_min <= integration.rt_right_min
    ):
        raise PeakObservationContractError(
            "integration RT bounds must contain the selected apex"
        )
    if not (
        selected_rt[0] <= integration.rt_apex_min <= selected_rt[-1]
        and selected_rt[0] <= integration.raw_apex_rt_min <= selected_rt[-1]
    ):
        raise PeakObservationContractError(
            "integration apex is outside the selected trace region"
        )
    if not math.isclose(
        integration.rt_width_min,
        integration.rt_right_min - integration.rt_left_min,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise PeakObservationContractError(
            "integration rt_width_min mismatches its RT bounds"
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
    value["primary_identity_event"] = (
        _read_identity_event(value["primary_identity_event"])
        if value["primary_identity_event"]
        else None
    )
    value["supporting_events"] = tuple(
        _read_identity_event(item) for item in value["supporting_events"]
    )
    hypothesis = value["hypothesis"]
    value["hypothesis"] = MeasuredPeakHypothesis(
        hypothesis_id=hypothesis["hypothesis_id"],
        method=hypothesis["method"],
        peak=_read_peak(hypothesis["peak"]),
    )
    owner = SampleLocalMS1Owner(**value)
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


def _read_integration(value: dict[str, Any]) -> IntegrationResult:
    validate_stored_integration_numbers(value)
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
