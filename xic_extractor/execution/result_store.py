"""Same-process partition storage for already validated execution owner results."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Buffer, Iterable
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from xic_extractor.execution._serialization import _OwnerPickler, _OwnerUnpickler
from xic_extractor.immutable_sequence import ImmutableSequence

SHARED_OWNERS_NAME = "shared-owners.pickle"
INDIVIDUALLY_ADDRESSABLE_FIELDS = frozenset({"child_outputs", "excluded_candidates"})


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


@dataclass(frozen=True, slots=True)
class WrittenPartition:
    """A partition file another worker wrote into the spool for adoption."""

    path: Path
    records: dict[str, tuple[tuple[int, int], ...]]


@lru_cache(maxsize=1)
def load_shared_owners(path: Path) -> tuple[object, ...]:
    """The spool's shared owners, read once per process."""
    with path.open("rb") as stream:
        shared = _OwnerUnpickler(stream, ()).load()
    if type(shared) is not tuple:
        raise TypeError("spool shared owners must be a tuple")
    return shared


def read_spooled_header_field(path: Path, field_name: str, shared_owners: Path) -> Any:
    """Restore one header field of a sealed partition file in any process."""
    with path.open("rb") as stream:
        _, values = _OwnerUnpickler(stream, load_shared_owners(shared_owners)).load()
    if field_name not in values:
        raise ValueError("read_spooled_header_field requires a stored non-tuple field")
    return values[field_name]


def read_spooled_tuple(
    path: Path, records: tuple[tuple[int, int], ...], shared_owners: Path
) -> tuple[Any, ...]:
    """Restore one tuple field of a sealed partition file in any process."""
    shared = load_shared_owners(shared_owners)
    with path.open("rb") as stream:
        return tuple(
            item
            for offset, length in records
            for item in _read_spool_record(stream, offset, length, shared)
        )


def write_spooled_partition(
    directory: Path,
    payload: Any,
    shared_owners: Path,
    *,
    child_outputs: Iterable[object] | None = None,
) -> WrittenPartition:
    """Write one partition file beside the spool for its store to adopt.

    Streamed child outputs are written one at a time beside a payload whose
    own ``child_outputs`` is empty.
    """
    if child_outputs is not None and payload.child_outputs != ():
        raise ValueError("streaming metadata payload must have empty child outputs")
    path = directory / f"written-{uuid4().hex}.pickle"
    records = _write_partition_payload(
        path,
        payload,
        shared_objects=load_shared_owners(shared_owners),
        child_outputs=child_outputs,
        individually_addressable_fields=INDIVIDUALLY_ADDRESSABLE_FIELDS,
    )
    return WrittenPartition(path, records)


def _read_spool_record(
    stream: BinaryIO, offset: int, length: int, shared_objects: tuple[object, ...]
) -> tuple:
    stream.seek(offset)
    values = _OwnerUnpickler(stream, shared_objects).load()
    if type(values) is not tuple or len(values) != length:
        raise ValueError("stored result record changed its sealed cardinality")
    return values


def _write_partition_payload(
    path: Path,
    payload: Any,
    *,
    shared_objects: tuple[object, ...],
    child_outputs: Iterable[object] | None,
    individually_addressable_fields: frozenset[str],
) -> dict[str, tuple[tuple[int, int], ...]]:
    """Write a sealed partition and return its tuple-record offsets.

    The store calls this in process; a worker writing a partition for the store
    to adopt calls it through write_spooled_partition. One writer keeps both
    paths byte-identical.
    """

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
        if path.exists():
            raise FileExistsError(f"partition artifact already exists: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return records


class StreamedResultStore:
    """Spool private payloads while retaining only explicit shared authorities.

    This object decides every readable path and owns its metadata. A worker
    may read a sealed path or write a file here for the store to adopt; the
    store still assigns its slot. Reads reuse the owned paths and record
    offsets without rescanning unchanged files.
    """

    def __init__(
        self,
        directory: Path,
        shared_objects: tuple[object, ...],
        *,
        individually_addressable_fields: frozenset[str] = (
            INDIVIDUALLY_ADDRESSABLE_FIELDS
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

    @property
    def directory(self) -> Path:
        return self._directory

    def partition_path(self, index: int) -> Path:
        """The sealed file another worker may read with the shared owners."""
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        return self._path(index)

    def shared_owners_path(self) -> Path:
        """Publish the shared owners once, for workers reading or writing here."""
        path = self._directory / SHARED_OWNERS_NAME
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            try:
                with temporary.open("xb") as stream:
                    _OwnerPickler(stream, ()).dump(self._shared_objects)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def append(self, partition_key: str, payload: object) -> SealedPartition:
        return self._append_payload(partition_key, payload)

    def _append_payload(self, partition_key: str, payload: Any) -> SealedPartition:
        self._require_writable_key(partition_key)
        index = len(self._field_records)
        records = _write_partition_payload(
            self._path(index),
            payload,
            shared_objects=self._shared_objects,
            child_outputs=None,
            individually_addressable_fields=self._individually_addressable_fields,
        )
        self._field_records.append(records)
        self._keys.add(partition_key)
        return SealedPartition(index, partition_key)

    def adopt_written(
        self, partition_key: str, written: WrittenPartition
    ) -> SealedPartition:
        """Seal a partition file a worker wrote into this spool.

        The store still decides the slot: the file moves into it here, in the
        order partitions are adopted, so slots stay the positions reads use.
        """

        self._require_writable_key(partition_key)
        if written.path.parent != self._directory:
            raise ValueError("written partition is outside this spool")
        index = len(self._field_records)
        path = self._path(index)
        if path.exists():
            raise FileExistsError(f"partition artifact already exists: {path}")
        os.replace(written.path, path)
        size_bytes = path.stat().st_size
        records = {
            str(name): tuple((int(offset), int(length)) for offset, length in entries)
            for name, entries in written.records.items()
        }
        if any(
            offset < 0 or length < 0 or offset >= size_bytes
            for entries in records.values()
            for offset, length in entries
        ):
            raise ValueError("adopted partition record leaves its sealed bytes")
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
        return _read_spool_record(stream, offset, length, self._shared_objects)

    def field_records(self, index: int, field_name: str) -> tuple[tuple[int, int], ...]:
        """A tuple field's record offsets, for a worker reading the sealed file."""
        if isinstance(index, bool) or not 0 <= index < len(self._field_records):
            raise IndexError("partition index out of range")
        if field_name not in self._field_records[index]:
            raise ValueError("field_records requires a stored tuple field")
        return self._field_records[index][field_name]

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
