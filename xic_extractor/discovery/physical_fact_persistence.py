"""Strict per-sample persistence for Discovery physical evaluations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    deserialize_acquired_trace,
    serialize_acquired_trace,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    deserialize_signal_processing_profile,
)
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalCandidateLink,
    DiscoveryPhysicalFactRecord,
    validate_discovery_physical_fact_record,
)
from xic_extractor.discovery.physical_fact_source import (
    DetachedPhysicalFactSource,
    PhysicalFactSource,
)
from xic_extractor.peak_detection.physical_peak_codec import (
    deserialize_physical_peak_evaluation,
    physical_evaluation_from_payload,
    serialize_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PreparedSignal,
)
from xic_extractor.processing_identity import FileContentFingerprint
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
)
from xic_extractor.validation_primitives import (
    canonical_float_hex,
    canonical_json_bytes,
    make_unique_mapping_hook,
    parse_canonical_float_hex,
    parse_json_mapping_payload,
    require_integer,
    require_keys,
    require_list,
    require_mapping,
    require_text,
)

DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION = "discovery_physical_fact_sidecar"

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
    "best_ms2_scan_id",
    "precursor_mz",
    "product_mz",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
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


def _retained_signal_content(evaluation: PhysicalPeakEvaluation) -> dict | None:
    signal = evaluation.retained_prepared_signal
    if signal is None:
        return None
    return {
        "schema_version": "retained_prepared_signal",
        **{
            name: [_float_text(value) for value in getattr(signal, name)]
            for name in ("rt", "raw", "baseline", "smoothed", "positive_signal")
        },
    }


def _restore_retained_signal(
    record: Mapping[str, Any],
    evaluation: PhysicalPeakEvaluation,
    profile: SignalProcessingProfile,
    acquired: AcquiredTrace,
) -> PhysicalPeakEvaluation:
    value = record.get("retained_prepared_signal")
    # A historical sidecar has no retained processed evidence. Rehydration is
    # scientific validation and cannot manufacture that missing evidence.
    if value is None:
        return replace(evaluation, retained_prepared_signal=None)
    signal = _mapping(value, label="retained_prepared_signal")
    names = ("rt", "raw", "baseline", "smoothed", "positive_signal")
    _require_keys(
        signal,
        ("schema_version", *names),
        label="retained_prepared_signal",
    )
    if signal["schema_version"] != "retained_prepared_signal":
        raise ValueError("unsupported retained prepared signal schema")
    arrays = {
        name: tuple(
            _float_from_text(point, field=f"retained_prepared_signal.{name}")
            for point in _list(signal[name], label=name)
        )
        for name in names
    }
    if (
        arrays["rt"] != tuple(acquired.trace.rt)
        or arrays["raw"] != tuple(acquired.trace.intensity)
        or any(len(values) != len(arrays["rt"]) for values in arrays.values())
    ):
        raise ValueError("retained prepared signal crosses acquired trace")
    return replace(
        evaluation,
        retained_prepared_signal=PreparedSignal(
            projection=evaluation.prepared, profile=profile, **arrays
        ),
    )


def _restore_candidate_links(
    record: Mapping[str, Any], work_ordinal: int
) -> tuple[DiscoveryPhysicalCandidateLink, ...]:
    links = []
    for value in _list(record.get("candidate_links", []), label="candidate_links"):
        link = _mapping(value, label="candidate_link")
        _require_keys(
            link,
            (
                "candidate_id",
                "final_ordinal",
                "representative_source_ordinal",
                "member_source_ordinals",
                "representative_work_ordinal",
                "member_work_ordinals",
            ),
            label="candidate_link",
        )
        member_work = tuple(
            _integer(x, field="member_work_ordinal")
            for x in _list(link["member_work_ordinals"], label="member_work_ordinals")
        )
        if work_ordinal not in member_work:
            raise ValueError("Discovery candidate link crosses its work record")
        links.append(
            DiscoveryPhysicalCandidateLink(
                candidate_id=_text(link["candidate_id"], field="candidate_id"),
                final_ordinal=_integer(link["final_ordinal"], field="final_ordinal"),
                representative_source_ordinal=_integer(
                    link["representative_source_ordinal"],
                    field="representative_source_ordinal",
                ),
                member_source_ordinals=tuple(
                    _integer(x, field="member_source_ordinal")
                    for x in _list(
                        link["member_source_ordinals"], label="member_source_ordinals"
                    )
                ),
                representative_work_ordinal=_integer(
                    link["representative_work_ordinal"],
                    field="representative_work_ordinal",
                ),
                member_work_ordinals=member_work,
            )
        )
    return tuple(links)


_SIDECAR_VERIFY_BLOCK_BYTES = 1 << 20


def write_discovery_physical_fact_sidecar(
    path: Path,
    *,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    work_manifest: Sequence[DiscoveryPhysicalWorkItem],
    records: Sequence[DiscoveryPhysicalFactRecord],
) -> FileContentFingerprint:
    """Encode, byte-verify, and atomically publish one sample sidecar.

    Returns the fingerprint of the published bytes for the batch index.
    """

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
        # One sidecar per sample measured 34-36 MB in the validation run.
        # Decoding the payload to write it and then reading the whole temporary
        # file back to compare held three full copies at once; write the
        # canonical bytes directly and verify what landed by digest instead.
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
        expected = hashlib.sha256(payload).digest()
        landed = hashlib.sha256()
        landed_size = 0
        with temporary.open("rb") as handle:
            for block in iter(lambda: handle.read(_SIDECAR_VERIFY_BLOCK_BYTES), b""):
                landed.update(block)
                landed_size += len(block)
        if landed_size != len(payload) or landed.digest() != expected:
            raise ValueError("Discovery physical sidecar verification changed bytes")
        # The landed bytes are now proven equal to payload, so the canonical
        # check runs once against the copy already in hand.
        _verify_canonical_payload_bytes(payload)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return FileContentFingerprint(content_sha256=expected.hex(), byte_size=len(payload))


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
                    "candidate_links": [
                        asdict(link) for link in record.candidate_links
                    ],
                    "retained_prepared_signal": _retained_signal_content(
                        record.evaluation
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


@dataclass(frozen=True, slots=True)
class DetachedIndexedDiscoveryPhysicalFacts:
    """Compact worker result whose spool remains owned by the caller."""

    source: DetachedPhysicalFactSource
    candidate_ordinals: dict[str, int]
    precursor_mz: dict[str, float]
    physical_apices: dict[str, float]
    resolved_candidate_ids: frozenset[str]


class IndexedDiscoveryPhysicalFacts:
    """Verified manifest routing with physical graphs restored on demand."""

    def __init__(
        self,
        path: Path,
        *,
        expected_run_spec: ProcessingRunSpec,
        expected_acquisition_input: ProcessingAcquisitionInput,
        candidate_ids: frozenset[str],
        spool_dir: Path | None = None,
    ) -> None:
        _validate_acquisition_input(expected_run_spec, expected_acquisition_input)
        self.run_spec = expected_run_spec
        self.acquisition_input = expected_acquisition_input
        self.candidate_ordinals: dict[str, int] = {}
        self.precursor_mz: dict[str, float] = {}
        self.physical_apices: dict[str, float] = {}
        resolved_candidate_ids: set[str] = set()
        manifest_ids: list[str] = []
        record_ids: list[str] = []
        questions: set[tuple[object, ...]] = set()

        def manifest_visitor(index: int, value: dict) -> None:
            item = _work_from_content(value, expected_acquisition_input)
            if item.work_ordinal != index:
                raise ValueError("Discovery physical work order changed")
            question = (
                item.acquisition_source_id,
                item.selection_subject_id,
                item.xic_mz,
                item.xic_rt_min,
                item.xic_rt_max,
                item.xic_ppm_tol,
            )
            if question in questions:
                raise ValueError("Discovery physical questions must be unique")
            questions.add(question)
            manifest_ids.append(item.candidate_id)
            if item.candidate_id in candidate_ids:
                if item.candidate_id in self.candidate_ordinals:
                    raise ValueError(
                        "Discovery physical candidate identity is duplicated"
                    )
                self.candidate_ordinals[item.candidate_id] = index
                self.precursor_mz[item.candidate_id] = item.precursor_mz

        def record_visitor(index: int, value: dict) -> None:
            cid = _text(value.get("candidate_id"), field="candidate_id")
            record_ids.append(cid)
            if cid in candidate_ids:
                physical = _mapping(
                    value.get("physical_evaluation"), label="physical_evaluation"
                )
                evaluation = _mapping(physical.get("evaluation"), label="evaluation")
                trace = _mapping(physical["trace"], label="trace")
                owner_evaluation = physical_evaluation_from_payload(
                    evaluation,
                    profile=expected_run_spec.signal_processing_profile,
                    rt=tuple(
                        _float_from_text(point, field="rt")
                        for point in _list(trace["rt"], label="rt")
                    ),
                    raw=tuple(
                        _float_from_text(point, field="raw")
                        for point in _list(
                            trace["raw"],
                            label="raw",
                        )
                    ),
                )
                if (
                    owner_evaluation.state == "available"
                    and not owner_evaluation.requires_overlap_review
                ):
                    resolved_candidate_ids.add(cid)
                facts = owner_evaluation.facts
                if facts is not None:
                    self.physical_apices[cid] = facts.apex_rt_min

        self.source = PhysicalFactSource(
            path,
            manifest_visitor=manifest_visitor,
            record_visitor=record_visitor,
            retain_record=lambda _index, value: (
                value.get("candidate_id") in candidate_ids
            ),
            spool_dir=spool_dir,
        )
        self.resolved_candidate_ids = frozenset(resolved_candidate_ids)
        try:
            if manifest_ids != record_ids:
                raise ValueError("Discovery physical records crossed their manifest")
            if set(self.candidate_ordinals) != candidate_ids:
                raise ValueError("Discovery candidate lacks its exact physical record")
            # Reuse the strict header owner without materializing either array.
            _read_discovery_physical_fact_records_direct(
                {**self.source.header, "work_manifest": [], "records": []},
                expected_run_spec=expected_run_spec,
                expected_acquisition_input=expected_acquisition_input,
                expected_work_manifest=(),
            )
        except BaseException:
            self.source.close()
            raise

    def read_record(self, index: int) -> DiscoveryPhysicalFactRecord:
        return restore_discovery_physical_fact_record(
            _work_from_content(
                self.source.read_manifest(index), self.acquisition_input
            ),
            self.source.read_record(index),
            expected_run_spec=self.run_spec,
        )

    def load_candidates(
        self, candidate_ids: Sequence[str]
    ) -> dict[str, DiscoveryPhysicalFactRecord]:
        return {
            cid: self.read_record(self.candidate_ordinals[cid]) for cid in candidate_ids
        }

    def close(self) -> None:
        self.source.close()

    def detach(self) -> DetachedIndexedDiscoveryPhysicalFacts:
        """Transfer compact indexes and the private spool to another owner."""

        return DetachedIndexedDiscoveryPhysicalFacts(
            source=self.source.detach(),
            candidate_ordinals=self.candidate_ordinals,
            precursor_mz=self.precursor_mz,
            physical_apices=self.physical_apices,
            resolved_candidate_ids=self.resolved_candidate_ids,
        )

    @classmethod
    def from_detached(
        cls,
        transfer: DetachedIndexedDiscoveryPhysicalFacts,
        *,
        expected_run_spec: ProcessingRunSpec,
        expected_acquisition_input: ProcessingAcquisitionInput,
    ) -> IndexedDiscoveryPhysicalFacts:
        """Adopt a validated worker index without hydrating physical graphs."""

        if not isinstance(transfer, DetachedIndexedDiscoveryPhysicalFacts):
            raise TypeError("indexed physical transfer has the wrong owner type")
        value = object.__new__(cls)
        value.run_spec = expected_run_spec
        value.acquisition_input = expected_acquisition_input
        value.candidate_ordinals = transfer.candidate_ordinals
        value.precursor_mz = transfer.precursor_mz
        value.physical_apices = transfer.physical_apices
        value.resolved_candidate_ids = transfer.resolved_candidate_ids
        value.source = PhysicalFactSource.from_detached(transfer.source)
        return value


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


def read_discovery_physical_fact_records(
    path: Path,
    *,
    expected_run_spec: ProcessingRunSpec,
    expected_acquisition_input: ProcessingAcquisitionInput,
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    """Recover the declared manifest and verify every record against its run."""
    payload = path.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_mapping)
    except UnicodeDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Discovery physical sidecar must be valid JSON") from exc
    root = _mapping(value, label="Discovery physical sidecar")
    _require_keys(root, _ROOT_KEYS, label="Discovery physical sidecar")
    manifest = tuple(
        _work_from_content(item, expected_acquisition_input)
        for item in _list(root["work_manifest"], label="work_manifest")
    )
    return _read_discovery_physical_fact_records_direct(
        root,
        expected_run_spec=expected_run_spec,
        expected_acquisition_input=expected_acquisition_input,
        expected_work_manifest=manifest,
    )


def _read_discovery_physical_fact_records_direct(
    root: Mapping[str, Any],
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
    _require_keys(root, _ROOT_KEYS, label="Discovery physical sidecar")
    if root["schema_version"] != DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION:
        raise ValueError("unsupported Discovery physical sidecar schema_version")
    run_spec_value = _mapping(root["run_spec"], label="run_spec")
    persisted_spec = deserialize_processing_run_spec(
        _canonical_json_bytes(run_spec_value)
    )
    if persisted_spec != expected_run_spec:
        raise ValueError("Discovery physical sidecar crosses expected run spec")
    sample = _mapping(root["sample"], label="sample")
    _require_keys(sample, _SAMPLE_KEYS, label="sample")
    if sample != _sample_content(expected_acquisition_input):
        raise ValueError("Discovery physical sidecar crosses sample input binding")

    persisted_manifest = tuple(
        _work_from_content(raw_item, expected_acquisition_input)
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
    return tuple(
        restore_discovery_physical_fact_record(
            item, raw_record, expected_run_spec=expected_run_spec
        )
        for item, raw_record in zip(persisted_manifest, raw_records, strict=True)
    )


def restore_discovery_physical_fact_record(
    item: DiscoveryPhysicalWorkItem,
    raw_record: object,
    *,
    expected_run_spec: ProcessingRunSpec,
) -> DiscoveryPhysicalFactRecord:
    """Restore one record selected from an owner-verified sidecar manifest."""
    record = _mapping(raw_record, label="record")
    _require_keys(
        record,
        _RECORD_KEYS[:3]
        + ("candidate_links", "retained_prepared_signal")
        + _RECORD_KEYS[3:],
        label="record",
    )
    if _text(record["candidate_id"], field="candidate_id") != item.candidate_id:
        raise ValueError("Discovery physical sidecar candidate ID changed")
    if (
        _text(record["selection_subject_id"], field="selection_subject_id")
        != item.selection_subject_id
    ):
        raise ValueError("Discovery physical selection subject changed")

    acquired = deserialize_acquired_trace(
        _canonical_json_bytes(
            _mapping(record["acquired_trace"], label="acquired_trace")
        ),
    )

    phys_val = _mapping(record["physical_evaluation"], label="physical_evaluation")
    _require_keys(
        phys_val,
        ("schema_version", "profile", "trace", "evaluation"),
        label="physical_evaluation",
    )
    if phys_val["schema_version"] != "physical_peak_evaluation_persistence":
        raise ValueError("unsupported physical evaluation persistence schema")
    profile_content = _mapping(phys_val["profile"], label="profile")
    profile = deserialize_signal_processing_profile(
        _canonical_json_bytes(profile_content)
    )
    if profile != expected_run_spec.signal_processing_profile:
        raise ValueError("Discovery physical sidecar crosses run profile")
    eval_payload = _mapping(phys_val["evaluation"], label="evaluation")
    trace_content = _mapping(phys_val["trace"], label="physical trace")
    evaluation = physical_evaluation_from_payload(
        eval_payload,
        profile=profile,
        rt=tuple(
            _float_from_text(point, field="rt")
            for point in _list(trace_content["rt"], label="rt")
        ),
        raw=tuple(
            _float_from_text(point, field="raw")
            for point in _list(trace_content["raw"], label="raw")
        ),
    )

    restored = DiscoveryPhysicalFactRecord(
        work_item=item,
        acquired_trace=acquired,
        candidate_links=_restore_candidate_links(record, item.work_ordinal),
        evaluation=_restore_retained_signal(
            record,
            evaluation,
            expected_run_spec.signal_processing_profile,
            acquired,
        ),
    )
    validate_discovery_physical_fact_record(restored)
    return restored


def deserialize_discovery_physical_fact_sidecar(
    payload: bytes | str,
    *,
    expected_run_spec: ProcessingRunSpec,
    expected_acquisition_input: ProcessingAcquisitionInput,
    expected_work_manifest: Sequence[DiscoveryPhysicalWorkItem],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
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
    return _deserialize_discovery_physical_fact_sidecar_from_root(
        root,
        raw=raw,
        expected_run_spec=expected_run_spec,
        expected_acquisition_input=expected_acquisition_input,
        expected_work_manifest=expected_work_manifest,
    )


def _deserialize_discovery_physical_fact_sidecar_from_root(
    root: Mapping[str, Any],
    *,
    raw: bytes,
    expected_run_spec: ProcessingRunSpec,
    expected_acquisition_input: ProcessingAcquisitionInput,
    expected_work_manifest: Sequence[DiscoveryPhysicalWorkItem],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    if not isinstance(expected_run_spec, ProcessingRunSpec):
        raise TypeError("expected_run_spec must be a ProcessingRunSpec")
    _validate_acquisition_input(expected_run_spec, expected_acquisition_input)
    expected_manifest = tuple(expected_work_manifest)
    _validate_manifest(expected_manifest, acquisition_input=expected_acquisition_input)
    _require_keys(root, _ROOT_KEYS, label="Discovery physical sidecar")
    if root["schema_version"] != DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION:
        raise ValueError("unsupported Discovery physical sidecar schema_version")
    run_spec_value = _mapping(root["run_spec"], label="run_spec")
    persisted_spec = deserialize_processing_run_spec(
        _canonical_json_bytes(run_spec_value)
    )
    if persisted_spec != expected_run_spec:
        raise ValueError("Discovery physical sidecar crosses expected run spec")
    sample = _mapping(root["sample"], label="sample")
    _require_keys(sample, _SAMPLE_KEYS, label="sample")
    if sample != _sample_content(expected_acquisition_input):
        raise ValueError("Discovery physical sidecar crosses sample input binding")

    persisted_manifest = tuple(
        _work_from_content(raw_item, expected_acquisition_input)
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
        _require_keys(
            record,
            _RECORD_KEYS[:3]
            + ("candidate_links", "retained_prepared_signal")
            + _RECORD_KEYS[3:],
            label="record",
        )
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
            expected_acquisition_source=expected_acquisition_input.acquisition_source,
        )
        physical_value = _mapping(
            record["physical_evaluation"], label="physical_evaluation"
        )
        _, _, profile, evaluation = deserialize_physical_peak_evaluation(
            _canonical_json_bytes(physical_value),
            expected_profile=expected_run_spec.signal_processing_profile,
        )
        if profile != expected_run_spec.signal_processing_profile:
            raise ValueError("Discovery physical sidecar crosses profile identity")
        restored = DiscoveryPhysicalFactRecord(
            work_item=item,
            acquired_trace=acquired,
            candidate_links=_restore_candidate_links(record, item.work_ordinal),
            evaluation=_restore_retained_signal(
                record,
                evaluation,
                expected_run_spec.signal_processing_profile,
                acquired,
            ),
        )
        validate_discovery_physical_fact_record(restored)
        records.append(restored)
    result = tuple(records)
    # Nested decoders already enforce their canonical bytes, and each record
    # decoder validates its exact work/trace/evaluation binding. Re-encode
    # the checked envelope without serializing and validating all records again.
    canonical_root = {
        **root,
        "sample": _sample_content(expected_acquisition_input),
        "work_manifest": [_work_content(item) for item in persisted_manifest],
    }
    if _canonical_json_bytes(canonical_root) != raw:
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
        validate_discovery_physical_fact_record(record)
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
    physical_questions = tuple(
        (
            item.acquisition_source_id,
            item.selection_subject_id,
            item.xic_mz,
            item.xic_rt_min,
            item.xic_rt_max,
            item.xic_ppm_tol,
        )
        for item in work_manifest
    )
    if len(physical_questions) != len(set(physical_questions)):
        raise ValueError("Discovery physical questions must be unique")


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
        "best_ms2_scan_id": item.best_ms2_scan_id,
        "precursor_mz": _float_text(item.precursor_mz),
        "product_mz": _float_text(item.product_mz),
        "query_rt_min": _float_text(item.query_rt_min),
        "context_rt_start_min": _float_text(item.context_rt_start_min),
        "context_rt_end_min": _float_text(item.context_rt_end_min),
        "xic_mz": _float_text(item.xic_mz),
        "xic_rt_min": _float_text(item.xic_rt_min),
        "xic_rt_max": _float_text(item.xic_rt_max),
        "xic_ppm_tol": _float_text(item.xic_ppm_tol),
        "candidate_id": item.candidate_id,
        "selection_subject_id": item.selection_subject_id,
    }


def _work_from_content(
    value: object, acquisition_input: ProcessingAcquisitionInput
) -> DiscoveryPhysicalWorkItem:
    """Restore one work item; its sample binding is stored once per sidecar."""

    content = _mapping(value, label="physical work item")
    _require_keys(content, _WORK_KEYS, label="physical work item")
    item = DiscoveryPhysicalWorkItem(
        work_ordinal=_integer(content["work_ordinal"], field="work_ordinal"),
        acquisition_source_id=(
            acquisition_input.acquisition_source.acquisition_source_id
        ),
        sample_stem=acquisition_input.sample_stem,
        best_ms2_scan_id=_integer(
            content["best_ms2_scan_id"], field="best_ms2_scan_id"
        ),
        precursor_mz=_float_from_text(content["precursor_mz"], field="precursor_mz"),
        product_mz=_float_from_text(content["product_mz"], field="product_mz"),
        query_rt_min=_float_from_text(content["query_rt_min"], field="query_rt_min"),
        context_rt_start_min=_float_from_text(
            content["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_float_from_text(
            content["context_rt_end_min"], field="context_rt_end_min"
        ),
        xic_mz=_float_from_text(content["xic_mz"], field="xic_mz"),
        xic_rt_min=_float_from_text(content["xic_rt_min"], field="xic_rt_min"),
        xic_rt_max=_float_from_text(content["xic_rt_max"], field="xic_rt_max"),
        xic_ppm_tol=_float_from_text(content["xic_ppm_tol"], field="xic_ppm_tol"),
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
    return canonical_float_hex(value, label="physical work numeric fields")


_float_from_text = parse_canonical_float_hex
_canonical_json_bytes = canonical_json_bytes


def _verify_canonical_payload_bytes(payload: bytes) -> None:
    parse_json_mapping_payload(
        payload,
        label="Discovery physical sidecar",
        enforce_canonical=True,
    )


_unique_mapping = make_unique_mapping_hook("Discovery physical sidecar")
_mapping = require_mapping
_list = require_list
_require_keys = require_keys
_text = require_text


def _integer(value: object, *, field: str) -> int:
    return require_integer(value, field=field, type_error_factory=TypeError)


__all__ = [
    "DISCOVERY_PHYSICAL_FACT_SIDECAR_SCHEMA_VERSION",
    "DiscoveryPhysicalFactRecord",
    "deserialize_discovery_physical_fact_sidecar",
    "read_discovery_physical_fact_sidecar",
    "serialize_discovery_physical_fact_sidecar",
    "write_discovery_physical_fact_sidecar",
]
