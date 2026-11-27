"""Same-process partition storage for already validated execution owner results."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Buffer, Callable, Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from xic_extractor.execution._serialization import _OwnerPickler, _OwnerUnpickler
from xic_extractor.immutable_sequence import ImmutableSequence


class StreamingPartitionPayload(Protocol):
    """Dataclass metadata whose ``child_outputs`` are streamed beside it."""

    @property
    def child_outputs(self) -> object: ...


class _PartitionWriter:
    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.size_bytes = 0

    def write(self, data: Buffer) -> int:
        written = self.stream.write(data)
        if written != memoryview(data).nbytes:
            raise OSError("incomplete partition write")
        self.size_bytes += written
        return written


@dataclass(frozen=True, slots=True)
class SealedPartition:
    index: int
    key: str


def _write_partition_payload(
    path: Path,
    payload: Any,
    *,
    shared_objects: tuple[object, ...],
    child_outputs: Iterable[object] | None,
    individually_addressable_fields: frozenset[str],
    before_publish: Callable[[], None] | None,
) -> dict[str, tuple[tuple[int, int], ...]]:
    """Write a sealed partition and return its tuple-record offsets."""

    field_names = tuple(item.name for item in fields(payload))
    tuple_fields = {
        name
        for name in field_names
        if not name.startswith("_") and type(getattr(payload, name)) is tuple
    }
    if child_outputs is not None:
        if "child_outputs" not in field_names:
            raise TypeError("streaming partition payload requires child outputs")
        tuple_fields.add("child_outputs")
    temporary = path.with_suffix(".tmp")
    if path.exists():
        raise FileExistsError(f"partition artifact already exists: {path}")
    stream = temporary.open("xb", buffering=1024 * 1024)
    try:
        with stream:
            writer = _PartitionWriter(stream)
            header = (
                type(payload),
                {
                    item.name: getattr(payload, item.name)
                    for item in fields(payload)
                    if item.name not in tuple_fields
                },
            )
            _OwnerPickler(writer, shared_objects).dump(header)
            records: dict[str, tuple[tuple[int, int], ...]] = {}
            for name in field_names:
                if name == "child_outputs" and child_outputs is not None:
                    entries = []
                    for value in child_outputs:
                        entries.append((writer.size_bytes, 1))
                        _OwnerPickler(writer, shared_objects).dump((value,))
                        del value
                    records["child_outputs"] = tuple(entries)
                    continue
                if name not in tuple_fields:
                    continue
                values = getattr(payload, name)
                width = 1 if name in individually_addressable_fields else 2048
                entries = []
                for start in range(0, len(values), width):
                    chunk = values[start : start + width]
                    entries.append((writer.size_bytes, len(chunk)))
                    _OwnerPickler(writer, shared_objects).dump(chunk)
                records[name] = tuple(entries)
            if before_publish is not None:
                before_publish()
        if path.exists():
            raise FileExistsError(f"partition artifact already exists: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return records


class StreamedResultStore:
    """Spool private payloads while retaining only explicit shared authorities.

    This object owns every readable path and its metadata. Reads reuse the
    owned paths and record offsets without rescanning unchanged files.
    """

    def __init__(
        self,
        directory: Path,
        shared_objects: tuple[object, ...],
        *,
        individually_addressable_fields: frozenset[str] = frozenset(
            {"child_outputs", "excluded_candidates"}
        ),
    ):
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._shared_objects = shared_objects
        self._individually_addressable_fields = frozenset(
            individually_addressable_fields
        )
        self._field_records: list[dict[str, tuple[tuple[int, int], ...]]] = []
        self._keys: set[str] = set()
        self._sealed = False

    def _path(self, index: int) -> Path:
        return self._directory / f"{index:08d}.pickle"

    def append(self, partition_key: str, payload: object) -> SealedPartition:
        return self._append_payload(partition_key, payload)

    def append_child_outputs(
        self,
        partition_key: str,
        metadata_payload: StreamingPartitionPayload,
        child_outputs: Iterable[object],
        before_publish: Callable[[], None] | None = None,
    ) -> SealedPartition:
        """Atomically append child outputs while composing them one at a time."""

        if metadata_payload.child_outputs != ():
            raise ValueError("streaming metadata payload must have empty child outputs")
        return self._append_payload(
            partition_key,
            metadata_payload,
            child_outputs=child_outputs,
            before_publish=before_publish,
        )

    def _append_payload(
        self,
        partition_key: str,
        payload: Any,
        *,
        child_outputs: Iterable[object] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> SealedPartition:
        self._require_writable_key(partition_key)
        index = len(self._field_records)
        records = _write_partition_payload(
            self._path(index),
            payload,
            shared_objects=self._shared_objects,
            child_outputs=child_outputs,
            individually_addressable_fields=self._individually_addressable_fields,
            before_publish=before_publish,
        )
        self._field_records.append(records)
        self._keys.add(partition_key)
        return SealedPartition(index, partition_key)

    def _require_writable_key(self, partition_key: str) -> None:
        if self._sealed:
            raise ValueError("partition store is sealed")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition_key) is None:
            raise ValueError("invalid partition key")
        if partition_key in self._keys:
            raise ValueError("duplicate partition key")

    def read(self, index: int) -> object:
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        with self._path(index).open("rb") as stream:
            payload_type, values = _OwnerUnpickler(stream, self._shared_objects).load()
            for name, records in self._field_records[index].items():
                values[name] = self._read_tuple_records(stream, records)
        payload = object.__new__(payload_type)
        for name, value in values.items():
            object.__setattr__(payload, name, value)
        return payload

    def read_header_field(self, index: int, field_name: str) -> Any:
        """Restore a complete non-tuple field without loading tuple records.

        Stage inputs and acquired records can share one atomic spool write
        without forcing their consumers to rehydrate both graphs.
        """
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        with self._path(index).open("rb") as stream:
            _, values = _OwnerUnpickler(stream, self._shared_objects).load()
        if field_name not in values:
            raise ValueError("read_header_field requires a stored non-tuple field")
        return values[field_name]

    def read_tuple(self, index: int, field_name: str) -> tuple[Any, ...]:
        """Restore a complete tuple field without loading the payload header.

        Intermediate stages can reference acquired records in an earlier spool
        instead of copying them into each derived payload. Both bulk and
        individually addressable tuple fields use their sealed record offsets.
        """
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        if field_name not in self._field_records[index]:
            raise ValueError("read_tuple requires a stored tuple field")
        with self._path(index).open("rb") as stream:
            return self._read_tuple_records(
                stream, self._field_records[index][field_name]
            )

    def _read_tuple_records(
        self, stream: BinaryIO, records: tuple[tuple[int, int], ...]
    ) -> tuple[Any, ...]:
        return tuple(
            item
            for offset, length in records
            for item in self._read_record(stream, offset, length)
        )

    def _read_record(self, stream: BinaryIO, offset: int, length: int) -> tuple:
        stream.seek(offset)
        values = _OwnerUnpickler(stream, self._shared_objects).load()
        if type(values) is not tuple or len(values) != length:
            raise ValueError("stored result record changed its sealed cardinality")
        return values

    def read_selected(
        self, index: int, field_name: str, positions: tuple[int, ...]
    ) -> tuple[Any, ...]:
        """Read selected elements of an independently addressable owner field."""
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        if field_name not in self._individually_addressable_fields:
            raise ValueError("field does not have individually addressable results")
        records = self._field_records[index][field_name]
        if any(
            type(position) is not int or not 0 <= position < len(records)
            for position in positions
        ):
            raise IndexError("field position out of range")
        with self._path(index).open("rb") as stream:
            return tuple(
                self._read_record(stream, *records[position])[0]
                for position in positions
            )

    def seal(self) -> None:
        self._sealed = True

    def sequence(self, field_name: str) -> ImmutableSequence[Any]:
        if not self._sealed:
            raise ValueError("seal partition store before creating a sequence")
        if (
            field_name.startswith("_")
            or not field_name.isidentifier()
            or any(field_name not in records for records in self._field_records)
        ):
            raise ValueError(
                "sequence requires a public tuple field in every partition"
            )

        chunks = tuple(
            (partition, offset, length)
            for partition, fields_by_name in enumerate(self._field_records)
            for offset, length in fields_by_name[field_name]
        )

        def load_chunk(index: int) -> tuple[Any, ...]:
            partition, offset, length = chunks[index]
            with self._path(partition).open("rb") as stream:
                return self._read_record(stream, offset, length)

        def load_chunks():
            chunk_index = 0
            while chunk_index < len(chunks):
                partition = chunks[chunk_index][0]
                with self._path(partition).open("rb") as stream:
                    while (
                        chunk_index < len(chunks)
                        and chunks[chunk_index][0] == partition
                    ):
                        _, offset, length = chunks[chunk_index]
                        yield self._read_record(stream, offset, length)
                        chunk_index += 1

        return ImmutableSequence(
            tuple(length for _, _, length in chunks), load_chunk, load_chunks
        )

    def cleanup(self) -> None:
        """Remove the private spool directory."""
        if self._directory.exists():
            shutil.rmtree(self._directory, ignore_errors=True)
