"""Strict per-sample persistence for Discovery physical evaluations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    acquired_trace_content_id,
    deserialize_acquired_trace,
    serialize_acquired_trace,
)
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    deserialize_physical_peak_evaluation,
    serialize_physical_peak_evaluation,
)
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
)

DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION = "discovery_physical_fact_sidecar_v1"

_ROOT_KEYS = (
    "schema_version",
    "run_spec",
    "sample",
    "work_manifest",
    "records",
)
_SAMPLE_KEYS = (
    "input_ordinal",
    "adapter_input_key",
    "sample_stem",
    "acquisition_source_id",
)
_WORK_KEYS = (
    "work_ordinal",
    "acquisition_source_id",
    "sample_stem",
    "best_ms2_scan_id",
    "precursor_mz",
    "product_mz",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
    "trace_coordinate_id",
    "acquired_trace_content_id",
    "xic_mz",
    "xic_rt_min",
    "xic_rt_max",
    "xic_ppm_tol",
    "candidate_id",
    "selection_subject_id",
)
_RECORD_KEYS = (
    "candidate_id",
    "selection_subject_id",
    "acquired_trace",
    "physical_evaluation",
)


@dataclass(frozen=True, slots=True)
class DiscoveryPhysicalFactRecord:
    work_item: DiscoveryPhysicalWorkItem
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.work_item, DiscoveryPhysicalWorkItem):
            raise TypeError("work_item must be a DiscoveryPhysicalWorkItem")
        if not isinstance(self.acquired_trace, AcquiredTrace):
            raise TypeError("acquired_trace must be an AcquiredTrace")
        if not isinstance(self.evaluation, PhysicalPeakEvaluation):
            raise TypeError("evaluation must be a PhysicalPeakEvaluation")
        _validate_evaluation_work_identity(self.evaluation, self.work_item)
        _validate_acquired_trace_work_identity(self.acquired_trace, self.work_item)

    @property
    def rt(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.rt)

    @property
    def raw(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.intensity)

    @property
    def candidate_id(self) -> str:
        return self.work_item.candidate_id

    @property
    def selection_subject_id(self) -> str:
        return self.work_item.selection_subject_id


def write_discovery_physical_fact_sidecar(
    path: Path,
    *,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    work_manifest: Sequence[DiscoveryPhysicalWorkItem],
    records: Sequence[DiscoveryPhysicalFactRecord],
) -> Path:
    """Purely encode, byte-verify, and atomically publish one sample sidecar."""

    if not isinstance(path, Path):
        raise TypeError("sidecar path must be a Path")
    manifest_tuple = tuple(work_manifest)
    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest_tuple,
        records=records,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload.decode("utf-8"))
            handle.flush()
        persisted = temporary.read_bytes()
        _verify_canonical_payload_bytes(persisted)
        if persisted != payload:
            raise ValueError("Discovery physical sidecar verification changed bytes")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def serialize_discovery_physical_fact_sidecar(
    *,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    work_manifest: Sequence[DiscoveryPhysicalWorkItem],
    records: Sequence[DiscoveryPhysicalFactRecord],
) -> bytes:
    if not isinstance(run_spec, ProcessingRunSpec):
        raise TypeError("run_spec must be a ProcessingRunSpec")
    _validate_acquisition_input(run_spec, acquisition_input)
    manifest_tuple = tuple(work_manifest)
    record_tuple = tuple(records)
    _validate_work_population(
        manifest_tuple,
        record_tuple,
        acquisition_input=acquisition_input,
    )
    profile = run_spec.signal_processing_profile
    return _canonical_json_bytes(
        {
            "schema_version": DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION,
            "run_spec": json.loads(
                serialize_processing_run_spec(run_spec).decode("utf-8")
            ),
            "sample": _sample_content(acquisition_input),
            "work_manifest": [_work_content(item) for item in manifest_tuple],
            "records": [
                {
                    "candidate_id": record.candidate_id,
                    "selection_subject_id": record.selection_subject_id,
                    "acquired_trace": json.loads(
                        serialize_acquired_trace(record.acquired_trace).decode("utf-8")
                    ),
                    "physical_evaluation": json.loads(
                        serialize_physical_peak_evaluation(
                            record.rt,
                            record.raw,
                            profile=profile,
                            evaluation=record.evaluation,
                        ).decode("utf-8")
                    ),
                }
                for record in record_tuple
            ],
        }
    )


def read_discovery_physical_fact_sidecar(
    path: Path,
    *,
    expected_run_spec: ProcessingRunSpec,
    expected_acquisition_input: ProcessingAcquisitionInput,
    expected_work_manifest: Sequence[DiscoveryPhysicalWorkItem],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    if not isinstance(path, Path):
        raise TypeError("sidecar path must be a Path")
    return deserialize_discovery_physical_fact_sidecar(
        path.read_bytes(),
        expected_run_spec=expected_run_spec,
        expected_acquisition_input=expected_acquisition_input,
        expected_work_manifest=expected_work_manifest,
    )


def deserialize_discovery_physical_fact_sidecar(
    payload: bytes | str,
    *,
    expected_run_spec: ProcessingRunSpec,
    expected_acquisition_input: ProcessingAcquisitionInput,
    expected_work_manifest: Sequence[DiscoveryPhysicalWorkItem],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    if not isinstance(expected_run_spec, ProcessingRunSpec):
        raise TypeError("expected_run_spec must be a ProcessingRunSpec")
    _validate_acquisition_input(expected_run_spec, expected_acquisition_input)
    expected_manifest = tuple(expected_work_manifest)
    _validate_manifest(expected_manifest, acquisition_input=expected_acquisition_input)
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise TypeError("Discovery physical sidecar payload must be bytes or str")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be UTF-8") from exc
    try:
        value = json.loads(decoded, object_pairs_hook=_unique_mapping)
    except json.JSONDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be valid JSON") from exc
    root = _mapping(value, label="Discovery physical sidecar")
    _require_keys(root, _ROOT_KEYS, label="Discovery physical sidecar")
    if root["schema_version"] != DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION:
        raise ValueError("unsupported Discovery physical sidecar schema_version")
    run_spec_value = _mapping(root["run_spec"], label="run_spec")
    persisted_spec = deserialize_processing_run_spec(
        _canonical_json_bytes(run_spec_value),
        expected_run_spec_hash=expected_run_spec.run_spec_hash,
        expected_run_spec_id=expected_run_spec.run_spec_id,
    )
    if persisted_spec != expected_run_spec:
        raise ValueError("Discovery physical sidecar crosses expected run spec")
    sample = _mapping(root["sample"], label="sample")
    _require_keys(sample, _SAMPLE_KEYS, label="sample")
    if sample != _sample_content(expected_acquisition_input):
        raise ValueError("Discovery physical sidecar crosses sample input binding")

    persisted_manifest = tuple(
        _work_from_content(raw_item)
        for raw_item in _list(root["work_manifest"], label="work_manifest")
    )
    _validate_manifest(
        persisted_manifest,
        acquisition_input=expected_acquisition_input,
    )
    if persisted_manifest != expected_manifest:
        raise ValueError("Discovery physical sidecar work manifest changed")

    raw_records = _list(root["records"], label="records")
    if len(raw_records) != len(persisted_manifest):
        raise ValueError("Discovery physical sidecar lost a physical work record")
    records: list[DiscoveryPhysicalFactRecord] = []
    for item, raw_record in zip(persisted_manifest, raw_records, strict=True):
        record = _mapping(raw_record, label="record")
        _require_keys(record, _RECORD_KEYS, label="record")
        if _text(record["candidate_id"], field="candidate_id") != item.candidate_id:
            raise ValueError("Discovery physical sidecar candidate ID changed")
        if (
            _text(record["selection_subject_id"], field="selection_subject_id")
            != item.selection_subject_id
        ):
            raise ValueError("Discovery physical selection subject changed")
        acquired_value = _mapping(record["acquired_trace"], label="acquired_trace")
        acquired = deserialize_acquired_trace(
            _canonical_json_bytes(acquired_value),
            expected_acquisition_source_id=item.acquisition_source_id,
            expected_trace_coordinate_id=item.trace_coordinate_id,
            expected_trace_content_id=item.acquired_trace_content_id,
        )
        physical_value = _mapping(
            record["physical_evaluation"], label="physical_evaluation"
        )
        rt, trace_raw, profile, evaluation = deserialize_physical_peak_evaluation(
            _canonical_json_bytes(physical_value),
            expected_profile=expected_run_spec.signal_processing_profile,
        )
        if profile != expected_run_spec.signal_processing_profile:
            raise ValueError("Discovery physical sidecar crosses profile identity")
        records.append(
            DiscoveryPhysicalFactRecord(
                work_item=item,
                acquired_trace=acquired,
                evaluation=evaluation,
            )
        )
        acquired_rt = tuple(float(value) for value in acquired.trace.rt)
        acquired_raw = tuple(float(value) for value in acquired.trace.intensity)
        if rt != acquired_rt or trace_raw != acquired_raw:
            raise ValueError("physical evaluation crosses acquired trace content")
    result = tuple(records)
    _validate_work_population(
        persisted_manifest,
        result,
        acquisition_input=expected_acquisition_input,
    )
    if (
        serialize_discovery_physical_fact_sidecar(
            run_spec=expected_run_spec,
            acquisition_input=expected_acquisition_input,
            work_manifest=expected_manifest,
            records=result,
        )
        != raw
    ):
        raise ValueError("Discovery physical sidecar is not canonical")
    return result


def _validate_work_population(
    work_manifest: tuple[DiscoveryPhysicalWorkItem, ...],
    records: tuple[DiscoveryPhysicalFactRecord, ...],
    *,
    acquisition_input: ProcessingAcquisitionInput,
) -> None:
    _validate_manifest(work_manifest, acquisition_input=acquisition_input)
    if any(not isinstance(record, DiscoveryPhysicalFactRecord) for record in records):
        raise TypeError("records must contain DiscoveryPhysicalFactRecord values")
    if len(work_manifest) != len(records):
        raise ValueError("Discovery physical work manifest and records differ")
    for item, record in zip(work_manifest, records, strict=True):
        if record.work_item != item:
            raise ValueError("Discovery physical record crosses work identity")


def _validate_manifest(
    work_manifest: tuple[DiscoveryPhysicalWorkItem, ...],
    *,
    acquisition_input: ProcessingAcquisitionInput,
) -> None:
    if any(not isinstance(item, DiscoveryPhysicalWorkItem) for item in work_manifest):
        raise TypeError("work_manifest must contain DiscoveryPhysicalWorkItem values")
    if tuple(item.work_ordinal for item in work_manifest) != tuple(
        range(len(work_manifest))
    ):
        raise ValueError("Discovery physical work manifest order changed")
    acquisition_source_id = acquisition_input.acquisition_source.acquisition_source_id
    if any(
        item.acquisition_source_id != acquisition_source_id
        or item.sample_stem != acquisition_input.sample_stem
        for item in work_manifest
    ):
        raise ValueError("Discovery physical work crosses sample acquisition binding")
    candidate_ids = tuple(item.candidate_id for item in work_manifest)
    subject_ids = tuple(item.selection_subject_id for item in work_manifest)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Discovery physical work candidate IDs must be unique")
    if len(subject_ids) != len(set(subject_ids)):
        raise ValueError("Discovery physical selection subjects must be unique")


def _validate_evaluation_work_identity(
    evaluation: PhysicalPeakEvaluation,
    work_item: DiscoveryPhysicalWorkItem,
) -> None:
    question = evaluation.question
    if question.selection_subject_id != work_item.selection_subject_id:
        raise ValueError("physical evaluation crosses Discovery selection subject")
    if question.query_rt_min != work_item.query_rt_min:
        raise ValueError("physical evaluation crosses Discovery query RT")
    if (
        question.context_rt_start_min != work_item.context_rt_start_min
        or question.context_rt_end_min != work_item.context_rt_end_min
    ):
        raise ValueError("physical evaluation crosses Discovery request context")


def _validate_acquired_trace_work_identity(
    acquired_trace: AcquiredTrace,
    work_item: DiscoveryPhysicalWorkItem,
) -> None:
    coordinate = acquired_trace.coordinate
    request = coordinate.request
    if coordinate.acquisition_source_id != work_item.acquisition_source_id:
        raise ValueError("acquired trace crosses Discovery acquisition source")
    if coordinate.trace_coordinate_id != work_item.trace_coordinate_id:
        raise ValueError("acquired trace crosses Discovery trace coordinate")
    if acquired_trace_content_id(acquired_trace) != work_item.acquired_trace_content_id:
        raise ValueError("acquired trace crosses Discovery trace content")
    if (
        request.mz != work_item.xic_mz
        or request.rt_min != work_item.xic_rt_min
        or request.rt_max != work_item.xic_rt_max
        or request.ppm_tol != work_item.xic_ppm_tol
    ):
        raise ValueError("acquired trace crosses Discovery XIC request")


def _sample_content(acquisition_input: ProcessingAcquisitionInput) -> dict[str, object]:
    return {
        "input_ordinal": acquisition_input.input_ordinal,
        "adapter_input_key": acquisition_input.adapter_input_key,
        "sample_stem": acquisition_input.sample_stem,
        "acquisition_source_id": (
            acquisition_input.acquisition_source.acquisition_source_id
        ),
    }


def _work_content(item: DiscoveryPhysicalWorkItem) -> dict[str, object]:
    return {
        "work_ordinal": item.work_ordinal,
        "acquisition_source_id": item.acquisition_source_id,
        "sample_stem": item.sample_stem,
        "best_ms2_scan_id": item.best_ms2_scan_id,
        "precursor_mz": _float_text(item.precursor_mz),
        "product_mz": _float_text(item.product_mz),
        "query_rt_min": _float_text(item.query_rt_min),
        "context_rt_start_min": _float_text(item.context_rt_start_min),
        "context_rt_end_min": _float_text(item.context_rt_end_min),
        "trace_coordinate_id": item.trace_coordinate_id,
        "acquired_trace_content_id": item.acquired_trace_content_id,
        "xic_mz": _float_text(item.xic_mz),
        "xic_rt_min": _float_text(item.xic_rt_min),
        "xic_rt_max": _float_text(item.xic_rt_max),
        "xic_ppm_tol": _float_text(item.xic_ppm_tol),
        "candidate_id": item.candidate_id,
        "selection_subject_id": item.selection_subject_id,
    }


def _work_from_content(value: object) -> DiscoveryPhysicalWorkItem:
    content = _mapping(value, label="physical work item")
    _require_keys(content, _WORK_KEYS, label="physical work item")
    item = DiscoveryPhysicalWorkItem(
        work_ordinal=_integer(content["work_ordinal"], field="work_ordinal"),
        acquisition_source_id=_text(
            content["acquisition_source_id"], field="acquisition_source_id"
        ),
        sample_stem=_text(content["sample_stem"], field="sample_stem"),
        best_ms2_scan_id=_integer(
            content["best_ms2_scan_id"], field="best_ms2_scan_id"
        ),
        precursor_mz=_float_from_text(content["precursor_mz"], field="precursor_mz"),
        product_mz=_float_from_text(content["product_mz"], field="product_mz"),
        query_rt_min=_float_from_text(
            content["query_rt_min"], field="query_rt_min"
        ),
        context_rt_start_min=_float_from_text(
            content["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_float_from_text(
            content["context_rt_end_min"], field="context_rt_end_min"
        ),
        trace_coordinate_id=_text(
            content["trace_coordinate_id"], field="trace_coordinate_id"
        ),
        acquired_trace_content_id=_text(
            content["acquired_trace_content_id"],
            field="acquired_trace_content_id",
        ),
        xic_mz=_float_from_text(content["xic_mz"], field="xic_mz"),
        xic_rt_min=_float_from_text(content["xic_rt_min"], field="xic_rt_min"),
        xic_rt_max=_float_from_text(content["xic_rt_max"], field="xic_rt_max"),
        xic_ppm_tol=_float_from_text(
            content["xic_ppm_tol"], field="xic_ppm_tol"
        ),
    )
    if _text(content["candidate_id"], field="candidate_id") != item.candidate_id:
        raise ValueError("physical work candidate ID does not match owner content")
    if (
        _text(content["selection_subject_id"], field="selection_subject_id")
        != item.selection_subject_id
    ):
        raise ValueError("physical work selection subject does not match owner content")
    return item


def _validate_acquisition_input(
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
) -> None:
    if not isinstance(acquisition_input, ProcessingAcquisitionInput):
        raise TypeError("acquisition_input must be a ProcessingAcquisitionInput")
    ordinal = acquisition_input.input_ordinal
    if ordinal >= len(run_spec.acquisition_inputs):
        raise ValueError("acquisition_input is outside the ProcessingRunSpec")
    if run_spec.acquisition_inputs[ordinal] != acquisition_input:
        raise ValueError("acquisition_input crosses ProcessingRunSpec binding")


def _float_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("physical work numeric fields must be numbers")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("physical work numeric fields must be finite")
    return (0.0 if number == 0.0 else number).hex()


def _float_from_text(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical hexadecimal text")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical hexadecimal text") from exc
    if not math.isfinite(number) or _float_text(number) != value:
        raise ValueError(f"{field} must be canonical finite hexadecimal text")
    return number


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_canonical_payload_bytes(payload: bytes) -> None:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_mapping)
    except UnicodeDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be valid JSON") from exc
    if _canonical_json_bytes(value) != payload:
        raise ValueError("Discovery physical sidecar is not canonical")


def _unique_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Discovery physical sidecar has duplicate field: {key}")
        result[key] = value
    return result


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be one JSON array")
    return value


def _require_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    actual = tuple(value)
    if actual != keys:
        raise ValueError(f"{label} fields must be exactly {keys}; got {actual}")


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


__all__ = [
    "DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION",
    "DiscoveryPhysicalFactRecord",
    "deserialize_discovery_physical_fact_sidecar",
    "read_discovery_physical_fact_sidecar",
    "serialize_discovery_physical_fact_sidecar",
    "write_discovery_physical_fact_sidecar",
]
