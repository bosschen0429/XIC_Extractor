"""Bounded structural access to persisted Discovery physical facts."""

from __future__ import annotations

import copy
import pickle
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

import ijson

_SCHEMA_VERSION = "discovery_physical_fact_sidecar"
_ROOT_KEYS = frozenset(
    {"schema_version", "run_spec", "sample", "work_manifest", "records"}
)
_HEADER_KEYS = ("schema_version", "run_spec", "sample")
_CHUNK_BYTES = 64 * 1024
_Span = tuple[int, int]
_IndexedSpans = tuple[_Span | None, ...]


@dataclass(frozen=True, slots=True)
class DetachedPhysicalFactSource:
    """Pickle-safe ownership transfer for one validated private spool."""

    path: Path
    signature: tuple[int, int, int, int]
    manifest_spans: _IndexedSpans
    record_spans: _IndexedSpans
    spool_path: Path


class PhysicalFactSource:
    """Index one physical sidecar into a private, bounded-lifetime item spool."""

    def __init__(
        self,
        path: Path,
        *,
        manifest_visitor: Callable[[int, dict], None] | None = None,
        record_visitor: Callable[[int, dict], None] | None = None,
        retain_record: Callable[[int, dict], bool] | None = None,
        spool_dir: Path | None = None,
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError("physical fact source path must be a Path")
        if manifest_visitor is not None and not callable(manifest_visitor):
            raise TypeError("manifest_visitor must be callable or None")
        if record_visitor is not None and not callable(record_visitor):
            raise TypeError("record_visitor must be callable or None")
        if retain_record is not None and not callable(retain_record):
            raise TypeError("retain_record must be callable or None")
        self._path = path
        self._closed = False
        self._spool_path: Path | None = None
        self._header: dict[str, Any] | None = None
        self._signature = _stat_signature(path)
        try:
            header, manifest_spans, record_spans, spool_path = self._build_index(
                manifest_visitor=manifest_visitor,
                record_visitor=record_visitor,
                retain_record=retain_record,
                spool_dir=spool_dir,
            )
            self._spool_path = spool_path
            if _stat_signature(path) != self._signature:
                raise ValueError(
                    "Discovery physical fact source changed while indexing"
                )
        except Exception:
            self.close()
            raise
        self._header = header
        self._manifest_spans = manifest_spans
        self._record_spans = record_spans

    @property
    def header(self) -> dict:
        self._ensure_current()
        if self._header is None:
            raise ValueError("physical fact source header was not transferred")
        return copy.deepcopy(self._header)

    @property
    def manifest_count(self) -> int:
        self._ensure_current()
        return len(self._manifest_spans)

    @property
    def record_count(self) -> int:
        self._ensure_current()
        return len(self._record_spans)

    @property
    def retained_record_count(self) -> int:
        self._ensure_current()
        return sum(span is not None for span in self._record_spans)

    def iter_manifest(self) -> Iterator[dict]:
        return self._iter_indexed(self._manifest_spans, label="work_manifest")

    def iter_records(self) -> Iterator[dict]:
        """Iterate retained records in their original persisted order."""
        return self._iter_indexed(self._record_spans, label="records")

    def read_manifest(self, index: int) -> dict:
        return self._read_indexed(self._manifest_spans, index, label="work_manifest")

    def read_record(self, index: int) -> dict:
        return self._read_indexed(self._record_spans, index, label="records")

    def close(self) -> None:
        self._closed = True
        if self._spool_path is not None:
            self._spool_path.unlink(missing_ok=True)
            self._spool_path = None

    def detach(self) -> DetachedPhysicalFactSource:
        """Transfer spool ownership without copying retained record graphs."""

        self._ensure_current()
        spool_path = self._require_spool()
        transfer = DetachedPhysicalFactSource(
            path=self._path,
            signature=self._signature,
            manifest_spans=self._manifest_spans,
            record_spans=self._record_spans,
            spool_path=spool_path,
        )
        self._spool_path = None
        self._closed = True
        return transfer

    @classmethod
    def from_detached(
        cls, transfer: DetachedPhysicalFactSource
    ) -> PhysicalFactSource:
        """Adopt a worker-produced spool after process-safe transport."""

        if not isinstance(transfer, DetachedPhysicalFactSource):
            raise TypeError("physical fact source transfer has the wrong owner type")
        if not transfer.spool_path.is_file():
            raise ValueError("physical fact source transfer spool is unavailable")
        if _stat_signature(transfer.path) != transfer.signature:
            raise ValueError("Discovery physical fact source changed after indexing")
        value = object.__new__(cls)
        value._path = transfer.path
        value._closed = False
        value._spool_path = transfer.spool_path
        value._signature = transfer.signature
        value._header = None
        value._manifest_spans = transfer.manifest_spans
        value._record_spans = transfer.record_spans
        return value

    def __enter__(self) -> PhysicalFactSource:
        self._ensure_current()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _build_index(
        self,
        *,
        manifest_visitor: Callable[[int, dict], None] | None,
        record_visitor: Callable[[int, dict], None] | None,
        retain_record: Callable[[int, dict], bool] | None,
        spool_dir: Path | None,
    ) -> tuple[
        dict[str, Any],
        _IndexedSpans,
        _IndexedSpans,
        Path,
    ]:
        temporary = tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="xic-physical-facts-",
            suffix=".spool",
            delete=False,
            dir=spool_dir,
        )
        spool_path = Path(temporary.name)
        try:
            with temporary as spool, self._path.open("rb") as source:
                events = iter(
                    ijson.parse(
                        source,
                        buf_size=_CHUNK_BYTES,
                        use_float=True,
                    )
                )
                if next(events, None) != ("", "start_map", None):
                    raise ValueError(
                        "Discovery physical fact source root must be an object"
                    )
                header: dict[str, Any] = {}
                arrays: dict[str, _IndexedSpans] = {}
                seen: set[str] = set()
                while True:
                    event = next(events, None)
                    if event == ("", "end_map", None):
                        break
                    if event is None or event[0] != "" or event[1] != "map_key":
                        raise ValueError(
                            "Discovery physical fact source root is malformed"
                        )
                    key = event[2]
                    if not isinstance(key, str):
                        raise ValueError(
                            "Discovery physical fact root key must be text"
                        )
                    if key in seen:
                        raise ValueError(
                            f"Discovery physical fact source has duplicate key {key!r}"
                        )
                    seen.add(key)
                    if key not in _ROOT_KEYS:
                        raise ValueError(
                            "Discovery physical fact source has unexpected root key "
                            f"{key!r}"
                        )
                    first = next(events, None)
                    if first is None:
                        raise ValueError("Discovery physical fact source is truncated")
                    if key == "work_manifest":
                        arrays[key] = _consume_array(
                            events,
                            first,
                            spool=spool,
                            visitor=manifest_visitor,
                            label=key,
                        )
                    elif key == "records":
                        arrays[key] = _consume_array(
                            events,
                            first,
                            spool=spool,
                            visitor=record_visitor,
                            retain=retain_record,
                            label=key,
                        )
                    else:
                        header[key] = _consume_value(events, first)
                try:
                    extra = next(events)
                except StopIteration:
                    extra = None
                if extra is not None:
                    raise ValueError(
                        "Discovery physical fact source has trailing content"
                    )
                spool.flush()
        except ijson.JSONError as exc:
            spool_path.unlink(missing_ok=True)
            raise ValueError("Discovery physical fact source is malformed") from exc
        except Exception:
            spool_path.unlink(missing_ok=True)
            raise

        if seen != _ROOT_KEYS:
            spool_path.unlink(missing_ok=True)
            missing = sorted(_ROOT_KEYS - seen)
            unexpected = sorted(seen - _ROOT_KEYS)
            raise ValueError(
                "Discovery physical fact source root keys differ: "
                f"missing={missing}, unexpected={unexpected}"
            )
        if header["schema_version"] != _SCHEMA_VERSION:
            spool_path.unlink(missing_ok=True)
            raise ValueError(
                "unsupported Discovery physical fact sidecar schema_version"
            )
        if not isinstance(header["run_spec"], dict) or not isinstance(
            header["sample"], dict
        ):
            spool_path.unlink(missing_ok=True)
            raise ValueError(
                "Discovery physical fact source header objects are malformed"
            )
        manifest_spans = arrays["work_manifest"]
        record_spans = arrays["records"]
        if len(manifest_spans) != len(record_spans):
            spool_path.unlink(missing_ok=True)
            raise ValueError("Discovery physical work manifest and records differ")
        return (
            {key: header[key] for key in _HEADER_KEYS},
            manifest_spans,
            record_spans,
            spool_path,
        )

    def _read_indexed(
        self,
        spans: _IndexedSpans,
        index: int,
        *,
        label: str,
    ) -> dict:
        self._ensure_current()
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"{label} index must be an integer")
        if index < 0 or index >= len(spans):
            raise IndexError(f"{label} index out of range")
        span = spans[index]
        if span is None:
            raise LookupError(f"{label} index {index} was not retained")
        spool_path = self._require_spool()
        with spool_path.open("rb") as spool:
            value = _read_pickled(spool, span)
        self._ensure_current()
        return value

    def _iter_indexed(
        self,
        spans: _IndexedSpans,
        *,
        label: str,
    ) -> Iterator[dict]:
        self._ensure_current()
        spool_path = self._require_spool()
        try:
            with spool_path.open("rb") as spool:
                for span in spans:
                    if span is None:
                        continue
                    yield _read_pickled(spool, span)
        finally:
            self._ensure_current()

    def _require_spool(self) -> Path:
        if self._spool_path is None:
            raise ValueError("Discovery physical fact source is closed")
        return self._spool_path

    def _ensure_current(self) -> None:
        if self._closed:
            raise ValueError("Discovery physical fact source is closed")
        if _stat_signature(self._path) != self._signature:
            raise ValueError("Discovery physical fact source changed after indexing")


class _UniqueMapping(dict):
    def __setitem__(self, key: object, value: object) -> None:
        if key in self:
            raise ValueError(
                f"Discovery physical fact source has duplicate key {key!r}"
            )
        super().__setitem__(key, value)


class _SpoolWriter(Protocol):
    def tell(self) -> int: ...

    def write(self, data: bytes) -> int: ...


def _consume_array(
    events: Iterator[tuple[str, str, Any]],
    first: tuple[str, str, Any],
    *,
    spool: _SpoolWriter,
    visitor: Callable[[int, dict], None] | None,
    retain: Callable[[int, dict], bool] | None = None,
    label: str,
) -> _IndexedSpans:
    if first[1:] != ("start_array", None):
        raise ValueError(f"Discovery physical fact {label} must be an array")
    spans: list[_Span | None] = []
    while True:
        event = next(events, None)
        if event is None:
            raise ValueError("Discovery physical fact source is truncated")
        if event[1:] == ("end_array", None):
            return tuple(spans)
        value = _consume_value(events, event)
        if not isinstance(value, dict):
            raise ValueError(f"Discovery physical fact {label} item must be an object")
        index = len(spans)
        if visitor is not None:
            visitor(index, value)
        if retain is not None and not retain(index, value):
            spans.append(None)
            continue
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        offset = spool.tell()
        spool.write(payload)
        spans.append((offset, len(payload)))


def _consume_value(
    events: Iterator[tuple[str, str, Any]],
    first: tuple[str, str, Any],
) -> Any:
    builder = ijson.ObjectBuilder(map_type=_UniqueMapping)
    _feed_builder(builder, first)
    depth = int(first[1] in {"start_map", "start_array"})
    while depth:
        event = next(events, None)
        if event is None:
            raise ValueError("Discovery physical fact source is truncated")
        _feed_builder(builder, event)
        if event[1] in {"start_map", "start_array"}:
            depth += 1
        elif event[1] in {"end_map", "end_array"}:
            depth -= 1
    return builder.value


def _feed_builder(builder: Any, event: tuple[str, str, Any]) -> None:
    _prefix, event_name, value = event
    builder.event(event_name, value)


def _read_pickled(spool: BinaryIO, span: tuple[int, int]) -> dict:
    offset, length = span
    spool.seek(offset)
    payload = spool.read(length)
    if len(payload) != length:
        raise ValueError("Discovery physical fact source spool is truncated")
    value = pickle.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Discovery physical fact source spool item is malformed")
    return value


def _stat_signature(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ValueError("Discovery physical fact source is unavailable") from exc
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


__all__ = ["DetachedPhysicalFactSource", "PhysicalFactSource"]
