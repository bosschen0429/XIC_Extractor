"""Same-process partition storage for already validated CID-NL owner results."""

from __future__ import annotations

import copyreg
import hashlib
import os
import pickle
import re
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO

from xic_extractor.immutable_sequence import ImmutableSequence


def _restore_mapping_proxy(values: dict) -> MappingProxyType:
    return MappingProxyType(values)


def _reduce_mapping_proxy(value: MappingProxyType) -> tuple:
    return _restore_mapping_proxy, (dict(value),)


class _OwnerPickler(pickle.Pickler):
    def __init__(self, stream: Any, shared_ids: dict[int, int]):
        super().__init__(stream, protocol=pickle.HIGHEST_PROTOCOL)
        self._shared_ids = shared_ids
        self.dispatch_table = copyreg.dispatch_table.copy()
        self.dispatch_table[MappingProxyType] = _reduce_mapping_proxy

    def persistent_id(self, obj: object) -> int | None:
        return self._shared_ids.get(id(obj))


class _OwnerUnpickler(pickle.Unpickler):
    def __init__(self, stream: BinaryIO, shared_objects: tuple[object, ...]):
        super().__init__(stream)
        self._shared_objects = shared_objects

    def persistent_load(self, identity: object) -> object:
        if type(identity) is not int or not 0 <= identity < len(self._shared_objects):
            raise pickle.UnpicklingError("unknown same-process owner reference")
        return self._shared_objects[identity]


class _HashingWriter:
    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size_bytes = 0

    def write(self, data: bytes) -> int:
        written = self.stream.write(data)
        if written != len(data):
            raise OSError("incomplete partition write")
        self.digest.update(data)
        self.size_bytes += written
        return written


@dataclass(frozen=True, slots=True)
class SealedPartition:
    index: int
    key: str
    sha256: str
    size_bytes: int


class PartitionResultStore:
    """Spool private payloads while retaining only explicit shared authorities.

    This object owns every readable path and its metadata for this process.
    There is no reopening or external pickle import route. Reads reuse the
    append receipt rather than rehashing owner-controlled, unchanged files.
    """

    def __init__(self, directory: Path, shared_objects: tuple[object, ...]):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._shared_objects = shared_objects
        self._shared_ids = {
            id(value): index for index, value in enumerate(shared_objects)
        }
        self._partitions: list[SealedPartition] = []
        self._field_lengths: list[dict[str, int]] = []
        self._field_records: list[dict[str, tuple[tuple[int, int], ...]]] = []
        self._keys: set[str] = set()
        self._sealed = False

    def _path(self, index: int) -> Path:
        return self._directory / f"{index:08d}.pickle"

    def append(self, partition_key: str, payload: object) -> SealedPartition:
        if self._sealed:
            raise ValueError("partition store is sealed")
        if (
            not isinstance(partition_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition_key) is None
        ):
            raise ValueError("invalid partition key")
        if partition_key in self._keys:
            raise ValueError("duplicate partition key")
        lengths = (
            {
                item.name: len(value)
                for item in fields(payload)
                if not item.name.startswith("_")
                for value in (getattr(payload, item.name),)
                if type(value) is tuple
            }
            if is_dataclass(payload) and not isinstance(payload, type)
            else {}
        )
        if not is_dataclass(payload) or isinstance(payload, type):
            raise TypeError("partition payload requires a dataclass result")
        index = len(self._partitions)
        path = self._path(index)
        temporary = path.with_suffix(".tmp")
        if path.exists():
            raise FileExistsError(f"partition artifact already exists: {path}")
        # Exclusive creation happens outside cleanup: an existing file belongs
        # to another writer and must never be deleted by this attempt.
        stream = temporary.open("xb")
        try:
            with stream:
                writer = _HashingWriter(stream)
                # The small header has no child graphs. Independent records let
                # consumers restore only the facts they actually consume.
                header = (
                    type(payload),
                    {
                        item.name: getattr(payload, item.name)
                        for item in fields(payload)
                        if item.name not in lengths
                    },
                )
                _OwnerPickler(writer, self._shared_ids).dump(header)
                records = {}
                for name in lengths:
                    values = getattr(payload, name)
                    # Children need independent identity-safe graph restoration;
                    # exclusions are addressed by canonical candidate position.
                    width = 1 if name in {"children", "excluded_candidates"} else 2048
                    entries = []
                    for start in range(0, len(values), width):
                        chunk = values[start : start + width]
                        entries.append((writer.size_bytes, len(chunk)))
                        _OwnerPickler(writer, self._shared_ids).dump(chunk)
                    records[name] = tuple(entries)
            entry = SealedPartition(
                index, partition_key, writer.digest.hexdigest(), writer.size_bytes
            )
            if path.exists():
                raise FileExistsError(f"partition artifact already exists: {path}")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self._partitions.append(entry)
        self._field_lengths.append(lengths)
        self._field_records.append(records)
        self._keys.add(partition_key)
        return entry

    def read(self, index: int) -> object:
        if type(index) is not int or not 0 <= index < len(self._partitions):
            raise IndexError("partition index out of range")
        with self._path(self._partitions[index].index).open("rb") as stream:
            payload_type, values = _OwnerUnpickler(stream, self._shared_objects).load()
            for name, records in self._field_records[index].items():
                values[name] = tuple(
                    item
                    for offset, length in records
                    for item in self._read_record(stream, offset, length)
                )
        payload = object.__new__(payload_type)
        for name, value in values.items():
            object.__setattr__(payload, name, value)
        return payload

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
        if type(index) is not int or not 0 <= index < len(self._partitions):
            raise IndexError("partition index out of range")
        if field_name not in {"children", "excluded_candidates"}:
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
            not isinstance(field_name, str)
            or field_name.startswith("_")
            or not field_name.isidentifier()
            or any(field_name not in lengths for lengths in self._field_lengths)
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

        return ImmutableSequence(tuple(length for _, _, length in chunks), load_chunk)
