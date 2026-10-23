"""Private persistent cache of source-bound Untargeted RAW numeric facts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionBatch,
    TraceAcquisitionCapability,
    acquire_trace_batch,
    deserialize_acquired_trace,
    serialize_acquired_trace,
)
from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_reader import (
    Ms2Scan,
    Ms2ScanEvent,
    RawFileHandle,
    require_owner_bound_raw_handle,
)
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest

_CACHE_SCHEMA = "untargeted_raw_fact_cache"
_MS2_SCHEMA = "untargeted_ms2_precursor_facts"
_LOGGER = logging.getLogger(__name__)


class UntargetedAcquisitionCacheError(ValueError):
    """A persisted RAW fact is malformed or conflicts with its cache key."""


class CachedUntargetedRaw:
    """Source-bound facts, opening a native handle only for a cache miss."""

    def __init__(
        self,
        raw: RawFileHandle | Callable[[], RawFileHandle],
        *,
        source: AcquisitionSourceIdentity,
        cache_dir: Path,
        mass_recalibration: MassRecalibration = MassRecalibration.NONE,
    ) -> None:
        self._opener = raw if callable(raw) else None
        self._mass_recalibration = mass_recalibration
        self._handle = (
            None
            if callable(raw)
            else self._require_policy(require_owner_bound_raw_handle(raw, source))
        )
        self.native_open_attempted = self._handle is not None
        self.native_opened = self._handle is not None
        self._source = source
        self._cache = _FactCache(
            cache_dir, source.acquisition_source_id, mass_recalibration
        )

    def _require_policy(self, handle: RawFileHandle) -> RawFileHandle:
        if handle.mass_recalibration is not self._mass_recalibration:
            raise UntargetedAcquisitionCacheError(
                "RAW handle mass recalibration differs from its fact cache"
            )
        return handle

    @property
    def _raw(self) -> RawFileHandle:
        if self._handle is None:
            assert self._opener is not None
            self.native_open_attempted = True
            self._handle = self._require_policy(
                require_owner_bound_raw_handle(self._opener(), self._source)
            )
            self.native_opened = True
        return self._handle

    @property
    def acquisition_source(self) -> AcquisitionSourceIdentity:
        return self._source

    @property
    def raw_open_receipt(self) -> object:
        return None if self._handle is None else self._handle.raw_open_receipt

    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0 if self._handle is None else self._handle.raw_chromatogram_call_count

    def __enter__(self) -> CachedUntargetedRaw:
        return self

    def __exit__(self, *args: object) -> None:
        if self._handle is not None:
            self._handle.__exit__(*args)

    def extract_xic_many(self, requests):
        return self._raw.extract_xic_many(requests)

    def extract_xic(self, mz: float, rt_min: float, rt_max: float, ppm_tol: float):
        return self._raw.extract_xic(mz, rt_min, rt_max, ppm_tol)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return self._raw.scan_window_for_request(request)

    def acquisition_rt_bounds(self) -> tuple[float, float]:
        key = _key_bytes("acquisition_rt_bounds")
        cached = self._cache.read("metadata", key)
        if cached is not None:
            try:
                values = json.loads(cached)
                if not isinstance(values, list) or len(values) != 2:
                    raise UntargetedAcquisitionCacheError(
                        "invalid cached acquisition bounds"
                    )
                start = _float(values[0], field="acquisition_rt_start")
                end = _float(values[1], field="acquisition_rt_end")
                if start < 0 or end < start:
                    raise UntargetedAcquisitionCacheError(
                        "invalid cached acquisition span"
                    )
                return start, end
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                UntargetedAcquisitionCacheError,
            ) as exc:
                self._cache.reject("metadata", key, exc)
        bounds = self._raw.acquisition_rt_bounds()
        self._cache.write(
            "metadata", key, _canonical_json([_float_text(value) for value in bounds])
        )
        return bounds

    def read_ms1_mass_windows(
        self, requests: Sequence[tuple[int, float, float]]
    ) -> tuple[Ms1PrecursorObservation | None, ...]:
        return self._raw.read_ms1_mass_windows(requests)

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=None)

    def iter_ms2_scan_subset(
        self, rt_min: float, rt_max: float, scan_numbers: frozenset[int]
    ) -> Iterator[Ms2ScanEvent]:
        """Reuse a complete cached window without hydrating unrelated spectra."""
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=scan_numbers)

    def _iter_ms2_scans(
        self, rt_min: float, rt_max: float, *, scan_numbers: frozenset[int] | None
    ) -> Iterator[Ms2ScanEvent]:
        key = _key_bytes("ms2_window", _float_text(rt_min), _float_text(rt_max))
        cached = self._cache.read("ms2", key)
        if cached is not None:
            try:
                yield from _decode_ms2_events(
                    cached, rt_min=rt_min, rt_max=rt_max, scan_numbers=scan_numbers
                )
                return
            except UntargetedAcquisitionCacheError as exc:
                self._cache.reject("ms2", key, exc)
        if scan_numbers is not None:
            key = _key_bytes(
                "ms2_scan_subset",
                _float_text(rt_min),
                _float_text(rt_max),
                ",".join(str(number) for number in sorted(scan_numbers)),
            )
            cached = self._cache.read("ms2", key)
            if cached is not None:
                try:
                    yield from _decode_ms2_events(
                        cached, rt_min=rt_min, rt_max=rt_max, scan_numbers=scan_numbers
                    )
                    return
                except UntargetedAcquisitionCacheError as exc:
                    self._cache.reject("ms2", key, exc)
        events = tuple(
            self._raw.iter_ms2_scans(rt_min, rt_max)
            if scan_numbers is None
            else self._raw.iter_ms2_scan_subset(rt_min, rt_max, scan_numbers)
        )
        if all(
            event.scan is not None and event.parse_error is None for event in events
        ):
            payload = _encode_ms2_events(events, rt_min=rt_min, rt_max=rt_max)
            self._cache.write("ms2", key, payload)
        yield from events

    def acquire_trace_batch(
        self,
        capability: TraceAcquisitionCapability,
        requests: tuple[XICRequest, ...],
    ) -> TraceAcquisitionBatch:
        cached: list[AcquiredTrace | None] = []
        missing: list[tuple[int, XICRequest, bytes]] = []
        for index, request in enumerate(requests):
            key = _trace_key(capability, request)
            payload = self._cache.read("xic", key)
            if payload is None:
                cached.append(None)
                missing.append((index, request, key))
                continue
            try:
                acquired = deserialize_acquired_trace(
                    payload,
                    expected_acquisition_source_id=(
                        capability.acquisition_source.acquisition_source_id
                    ),
                )
                if (
                    acquired.coordinate.capability_id != capability.capability_id
                    or acquired.coordinate.request != request
                    or acquired.coordinate.coordinate_kind
                    is not capability.coordinate_kind
                ):
                    raise UntargetedAcquisitionCacheError(
                        "cached XIC trace conflicts with its exact request key"
                    )
            except (ValueError, TypeError) as exc:
                self._cache.reject("xic", key, exc)
                cached.append(None)
                missing.append((index, request, key))
                continue
            cached.append(acquired)

        adapter_calls = 0
        if missing:
            acquired_missing = acquire_trace_batch(
                capability,
                tuple(request for _index, request, _key in missing),
                RawSuperWindowSource(self._raw),
            )
            adapter_calls = acquired_missing.adapter_chromatogram_call_count
            for (index, _request, key), acquired in zip(
                missing, acquired_missing.traces, strict=True
            ):
                self._cache.write("xic", key, serialize_acquired_trace(acquired))
                cached[index] = acquired
        traces = tuple(item for item in cached if item is not None)
        if len(traces) != len(requests):
            raise UntargetedAcquisitionCacheError(
                "cached XIC trace set lost an exact request"
            )
        return TraceAcquisitionBatch(
            capability=capability,
            traces=traces,
            logical_xic_request_count=len(traces),
            xic_batch_count=1,
            adapter_chromatogram_call_count=adapter_calls,
            trace_point_count=sum(len(item.trace.rt) for item in traces),
        )


def bind_untargeted_acquisition_cache(
    raw: RawFileHandle,
    *,
    source: AcquisitionSourceIdentity,
    cache_dir: Path | None,
) -> RawFileHandle | CachedUntargetedRaw:
    """Bind a verified native handle to the optional persistent fact cache."""

    if cache_dir is None:
        return require_owner_bound_raw_handle(raw, source)
    return CachedUntargetedRaw(
        raw,
        source=source,
        cache_dir=cache_dir,
        mass_recalibration=raw.mass_recalibration,
    )


def require_owner_bound_untargeted_raw(
    raw: object, source: AcquisitionSourceIdentity
) -> RawFileHandle | CachedUntargetedRaw:
    if isinstance(raw, CachedUntargetedRaw):
        if raw.acquisition_source != source:
            raise UntargetedAcquisitionCacheError(
                "cached RAW handle is bound to a different acquisition source"
            )
        return raw
    return require_owner_bound_raw_handle(raw, source)


def acquire_untargeted_trace_batch(
    capability: TraceAcquisitionCapability,
    requests: tuple[XICRequest, ...],
    raw: object,
) -> TraceAcquisitionBatch:
    if isinstance(raw, CachedUntargetedRaw):
        return raw.acquire_trace_batch(capability, requests)
    owner_bound = require_owner_bound_raw_handle(raw, capability.acquisition_source)
    return acquire_trace_batch(capability, requests, RawSuperWindowSource(owner_bound))


class _FactCache:
    def __init__(
        self,
        root: Path,
        source_id: str,
        mass_recalibration: MassRecalibration = MassRecalibration.NONE,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("acquisition cache directory must be a Path")
        # Recalibrated facts never share entries with vendor-mass facts.
        namespace = (
            source_id
            if mass_recalibration is MassRecalibration.NONE
            else f"{source_id}|mass_recalibration={mass_recalibration.value}"
        )
        self._root = root / hashlib.sha256(namespace.encode("utf-8")).hexdigest()
        self._source_id = source_id
        # Calibrated MS2 facts include parent-centroid precursor recognition.
        # Keep XICs and vendor-mass facts reusable without admitting older MS2
        # entries whose fragments and selected precursor may use different scales.
        self._ms2_kind = (
            "ms2"
            if mass_recalibration is MassRecalibration.NONE
            else "ms2_parent_centroid"
        )

    def _path(self, kind: str, key: bytes) -> Path:
        if kind == "ms2":
            kind = self._ms2_kind
        return self._root / kind / f"{hashlib.sha256(key).hexdigest()}.json"

    def read(self, kind: str, key: bytes) -> bytes | None:
        path = self._path(kind, key)
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            envelope = json.loads(payload, object_pairs_hook=_unique_mapping)
            if not isinstance(envelope, dict) or set(envelope) != {
                "schema_version",
                "source_id",
                "key_sha256",
                "payload_sha256",
                "payload_base64",
            }:
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache entry has an invalid envelope"
                )
            if (
                envelope["schema_version"] != _CACHE_SCHEMA
                or envelope["source_id"] != self._source_id
                or envelope["key_sha256"] != hashlib.sha256(key).hexdigest()
            ):
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache entry crossed its source or request identity"
                )
            encoded = envelope["payload_base64"]
            if not isinstance(encoded, str):
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache payload is not base64 text"
                )
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache payload is not valid base64"
                ) from exc
            # Verify the numeric bytes directly. Re-encoding their transport
            # envelope adds no source, request, or content identity evidence.
            if envelope["payload_sha256"] != hashlib.sha256(content).hexdigest():
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache entry failed content identity validation"
                )
            return content
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            UntargetedAcquisitionCacheError,
        ) as exc:
            self.reject(kind, key, exc)
            return None

    def write(self, kind: str, key: bytes, payload: bytes) -> None:
        path = self._path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = self._envelope(key, payload)
        content = _canonical_json(envelope)
        if path.exists():
            existing = self.read(kind, key)
            if existing != payload:
                raise UntargetedAcquisitionCacheError(
                    "acquisition cache key has conflicting numeric facts"
                )
            return
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
            # Cache facts can be reacquired after power loss. Closing before
            # atomic replacement prevents readers seeing a partial write;
            # per-entry disk durability is not a runtime acknowledgement.
            try:
                os.replace(temporary, path)
            except (FileExistsError, PermissionError) as exc:
                # Windows can deny replacement while another worker reads the
                # winner. An identical committed fact already satisfies this write.
                existing = self.read(kind, key)
                if existing is None:
                    raise
                if existing != payload:
                    raise UntargetedAcquisitionCacheError(
                        "acquisition cache race produced conflicting facts"
                    ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def reject(self, kind: str, key: bytes, error: Exception) -> None:
        path = self._path(kind, key)
        _LOGGER.warning(
            "rejecting unusable acquisition cache entry %s: %s", path, error
        )
        path.unlink(missing_ok=True)

    def _envelope(self, key: bytes, payload: bytes) -> dict[str, str]:
        return {
            "schema_version": _CACHE_SCHEMA,
            "source_id": self._source_id,
            "key_sha256": hashlib.sha256(key).hexdigest(),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        }


def _trace_key(capability: TraceAcquisitionCapability, request: XICRequest) -> bytes:
    return _key_bytes(
        "xic",
        capability.capability_id,
        capability.coordinate_kind.value,
        _float_text(request.mz),
        _float_text(request.rt_min),
        _float_text(request.rt_max),
        _float_text(request.ppm_tol),
    )


def _key_bytes(*values: str) -> bytes:
    return _canonical_json({"values": list(values)})


def _encode_ms2_events(
    events: tuple[Ms2ScanEvent, ...], *, rt_min: float, rt_max: float
) -> bytes:
    return _canonical_json(
        {
            "schema_version": _MS2_SCHEMA,
            "rt_min": _float_text(rt_min),
            "rt_max": _float_text(rt_max),
            "events": [_ms2_event_payload(event) for event in events],
        }
    )


def _decode_ms2_events(
    payload: bytes,
    *,
    rt_min: float,
    rt_max: float,
    scan_numbers: frozenset[int] | None = None,
) -> tuple[Ms2ScanEvent, ...]:
    try:
        value = json.loads(payload, object_pairs_hook=_unique_mapping)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UntargetedAcquisitionCacheError(
            "cached MS2 events are malformed"
        ) from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "rt_min",
        "rt_max",
        "events",
    }:
        raise UntargetedAcquisitionCacheError("cached MS2 event envelope is malformed")
    if (
        value["schema_version"] != _MS2_SCHEMA
        or value["rt_min"] != _float_text(rt_min)
        or value["rt_max"] != _float_text(rt_max)
        or not isinstance(value["events"], list)
    ):
        raise UntargetedAcquisitionCacheError("cached MS2 event key changed")
    if scan_numbers is not None:
        # The cache envelope already binds the complete payload to its source
        # and key. Validate and hydrate requested spectra without re-encoding
        # every unrelated spectrum just to reproduce transport formatting.
        selected = []
        for item in value["events"]:
            if not isinstance(item, dict):
                raise UntargetedAcquisitionCacheError("cached MS2 event is malformed")
            number = _integer(item.get("scan_number"), field="scan_number")
            if number in scan_numbers:
                selected.append(_ms2_event_from_payload(item))
        return tuple(selected)
    return tuple(_ms2_event_from_payload(item) for item in value["events"])


def _ms2_event_payload(event: Ms2ScanEvent) -> dict[str, object]:
    scan = event.scan
    return {
        "scan_number": event.scan_number,
        "parse_error": event.parse_error,
        "scan": (
            None
            if scan is None
            else {
                "scan_number": scan.scan_number,
                "rt": _float_text(scan.rt),
                "precursor_mz": _float_text(scan.precursor_mz),
                "masses": [_float_text(item) for item in scan.masses],
                "intensities": [_float_text(item) for item in scan.intensities],
                "base_peak": _float_text(scan.base_peak),
                "precursor_metadata": asdict(scan.precursor_metadata)
                if scan.precursor_metadata is not None
                else None,
            }
        ),
    }


def _ms2_event_from_payload(value: object) -> Ms2ScanEvent:
    if not isinstance(value, dict) or set(value) != {
        "scan_number",
        "parse_error",
        "scan",
    }:
        raise UntargetedAcquisitionCacheError("cached MS2 event is malformed")
    scan_number = value["scan_number"]
    parse_error = value["parse_error"]
    if (
        isinstance(scan_number, bool)
        or not isinstance(scan_number, int)
        or scan_number < 0
        or (parse_error is not None and not isinstance(parse_error, str))
    ):
        raise UntargetedAcquisitionCacheError("cached MS2 event fields are malformed")
    raw_scan = value["scan"]
    if raw_scan is None:
        if parse_error is None:
            raise UntargetedAcquisitionCacheError("cached MS2 failure lacks an error")
        return Ms2ScanEvent(None, parse_error, scan_number)
    if (
        parse_error is not None
        or not isinstance(raw_scan, dict)
        or set(raw_scan)
        != {
            "scan_number",
            "rt",
            "precursor_mz",
            "masses",
            "intensities",
            "base_peak",
            "precursor_metadata",
        }
    ):
        raise UntargetedAcquisitionCacheError("cached MS2 scan is malformed")
    masses = _float_array(raw_scan["masses"], field="masses")
    intensities = _float_array(raw_scan["intensities"], field="intensities")
    if masses.shape != intensities.shape:
        raise UntargetedAcquisitionCacheError("cached MS2 arrays have different shapes")
    scan = Ms2Scan(
        scan_number=_integer(raw_scan["scan_number"], field="scan_number"),
        rt=_float(raw_scan["rt"], field="rt"),
        precursor_mz=_float(raw_scan["precursor_mz"], field="precursor_mz"),
        masses=masses,
        intensities=intensities,
        base_peak=_float(raw_scan["base_peak"], field="base_peak"),
        precursor_metadata=_precursor_metadata_from_payload(
            raw_scan["precursor_metadata"]
        ),
    )
    if scan.scan_number != scan_number:
        raise UntargetedAcquisitionCacheError("cached MS2 scan number changed")
    return Ms2ScanEvent(scan, None, scan_number)


def _precursor_metadata_from_payload(value: object) -> Ms2PrecursorMetadata | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "monoisotopic_mz",
        "charge",
        "isolation_width_da",
        "isolation_offset_da",
        "master_scan_number",
    }:
        raise UntargetedAcquisitionCacheError("cached precursor metadata is malformed")
    try:
        return Ms2PrecursorMetadata(**value)
    except (TypeError, ValueError) as exc:
        raise UntargetedAcquisitionCacheError(
            "cached precursor metadata is malformed"
        ) from exc


def _float_array(value: object, *, field: str) -> np.ndarray:
    if not isinstance(value, list):
        raise UntargetedAcquisitionCacheError(f"cached MS2 {field} is not a list")
    return np.asarray([_float(item, field=field) for item in value], dtype=np.float64)


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UntargetedAcquisitionCacheError(f"cached MS2 {field} is not an integer")
    return value


def _float(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise UntargetedAcquisitionCacheError(f"cached MS2 {field} is not text")
    try:
        parsed = float.fromhex(value)
    except ValueError as exc:
        raise UntargetedAcquisitionCacheError(
            f"cached MS2 {field} is not a hexadecimal float"
        ) from exc
    if not math.isfinite(parsed) or parsed.hex() != value:
        raise UntargetedAcquisitionCacheError(f"cached MS2 {field} is not canonical")
    return parsed


def _float_text(value: float) -> str:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise UntargetedAcquisitionCacheError("cache key requires finite numbers")
    return parsed.hex()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _unique_mapping(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UntargetedAcquisitionCacheError(
                "acquisition cache JSON contains duplicate keys"
            )
        result[key] = value
    return result


__all__ = [
    "CachedUntargetedRaw",
    "UntargetedAcquisitionCacheError",
    "acquire_untargeted_trace_batch",
    "bind_untargeted_acquisition_cache",
    "require_owner_bound_untargeted_raw",
]
