"""Same-process partition storage for already validated execution owner results."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable, Iterable
from copy import copy
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, BinaryIO

from xic_extractor.execution_engine.owner_pickle import (
    _OwnerPickler,
    _OwnerUnpickler,
)
from xic_extractor.immutable_sequence import ImmutableSequence


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


class StreamedResultStore:
    """Spool private payloads while retaining only explicit shared authorities.

    This object owns every readable path and its metadata. Reads reuse the
    append receipt rather than rehashing owner-controlled, unchanged files.
    A failure snapshot retains this private store for diagnostic replay; it
    neither resumes a runtime attempt nor grants publication authority.
    """

    def __init__(
        self,
        directory: Path,
        shared_objects: tuple[object, ...],
        *,
        individually_addressable_fields: frozenset[str] = frozenset(
            {"children", "excluded_candidates"}
        ),
    ):
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._shared_objects = shared_objects
        self._individually_addressable_fields = frozenset(
            individually_addressable_fields
        )
        self._partitions: list[SealedPartition] = []
        self._field_lengths: list[dict[str, int]] = []
        self._field_records: list[dict[str, tuple[tuple[int, int], ...]]] = []
        self._field_caches: list[dict[str, tuple[tuple[Any, ...], ...]]] = []
        self._keys: set[str] = set()
        self._sealed = False

    def _path(self, index: int) -> Path:
        return self._directory / f"{index:08d}.pickle"

    def append(
        self, partition_key: str, payload: object, retain_memory: bool = False
    ) -> SealedPartition:
        if not is_dataclass(payload) or isinstance(payload, type):
            raise TypeError("partition payload requires a dataclass result")
        return self._append_payload(
            partition_key, payload, retain_memory=retain_memory
        )

    def append_children(
        self,
        partition_key: str,
        metadata_payload: object,
        children: Iterable[object],
        before_publish: Callable[[], None] | None = None,
        retain_memory: bool = False,
    ) -> SealedPartition:
        """Atomically append children while composing them one at a time."""

        if not is_dataclass(metadata_payload) or isinstance(metadata_payload, type):
            raise TypeError("partition payload requires a dataclass result")
        if not hasattr(metadata_payload, "children"):
            raise TypeError("streaming partition payload requires children")
        if getattr(metadata_payload, "children") != ():
            raise ValueError("streaming metadata payload must have empty children")
        return self._append_payload(
            partition_key,
            metadata_payload,
            children=children,
            before_publish=before_publish,
            retain_memory=retain_memory,
        )

    def _append_payload(
        self,
        partition_key: str,
        payload: Any,
        *,
        children: Iterable[object] | None = None,
        before_publish: Callable[[], None] | None = None,
        retain_memory: bool = False,
    ) -> SealedPartition:
        if self._sealed:
            raise ValueError("partition store is sealed")
        if (
            not isinstance(partition_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition_key) is None
        ):
            raise ValueError("invalid partition key")
        if partition_key in self._keys:
            raise ValueError("duplicate partition key")
        field_names = tuple(item.name for item in fields(payload))
        lengths = {
            item.name: len(getattr(payload, item.name))
            for item in fields(payload)
            if not item.name.startswith("_")
            and type(getattr(payload, item.name)) is tuple
            and not (item.name == "children" and children is not None)
        }
        if children is not None:
            if "children" not in field_names:
                raise TypeError("streaming partition payload requires children")
            lengths["children"] = 0
        index = len(self._partitions)
        path = self._path(index)
        temporary = path.with_suffix(".tmp")
        if path.exists():
            raise FileExistsError(f"partition artifact already exists: {path}")
        # Exclusive creation happens outside cleanup: an existing file belongs
        # to another writer and must never be deleted by this attempt.
        stream = temporary.open("xb", buffering=1024 * 1024)
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
                _OwnerPickler(writer, self._shared_objects).dump(header)
                records = {}
                cached_fields: dict[str, tuple[tuple[Any, ...], ...]] = {}
                for name in field_names:
                    if name == "children" and children is not None:
                        entries = []
                        cached_items: list[tuple[Any, ...]] | None = (
                            [] if retain_memory else None
                        )
                        for value in children:
                            entries.append((writer.size_bytes, 1))
                            _OwnerPickler(writer, self._shared_objects).dump((value,))
                            if cached_items is not None:
                                cached_items.append((value,))
                            del value
                        lengths["children"] = len(entries)
                        records["children"] = tuple(entries)
                        if cached_items is not None:
                            cached_fields["children"] = tuple(cached_items)
                        continue
                    if name not in lengths:
                        continue
                    values = getattr(payload, name)
                    # Individually addressable fields need independent identity-safe
                    # graph restoration; exclusions are addressed by canonical
                    # candidate position.
                    width = (
                        1 if name in self._individually_addressable_fields else 2048
                    )
                    entries = []
                    cached_chunks: list[tuple[Any, ...]] | None = (
                        [] if retain_memory else None
                    )
                    for start in range(0, len(values), width):
                        chunk = values[start : start + width]
                        entries.append((writer.size_bytes, len(chunk)))
                        _OwnerPickler(writer, self._shared_objects).dump(chunk)
                        if cached_chunks is not None:
                            cached_chunks.append(chunk)
                    records[name] = tuple(entries)
                    if cached_chunks is not None:
                        cached_fields[name] = tuple(cached_chunks)
                if before_publish is not None:
                    before_publish()
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
        self._field_caches.append(cached_fields)
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
        if field_name not in self._individually_addressable_fields:
            raise ValueError("field does not have individually addressable results")
        records = self._field_records[index][field_name]
        if any(
            type(position) is not int or not 0 <= position < len(records)
            for position in positions
        ):
            raise IndexError("field position out of range")
        if (
            index < len(self._field_caches)
            and field_name in self._field_caches[index]
        ):
            cached = self._field_caches[index][field_name]
            return tuple(cached[position][0] for position in positions)
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

        all_cached = len(self._field_caches) == len(self._partitions) and all(
            field_name in caches for caches in self._field_caches
        )
        if all_cached:
            cached_chunks = tuple(
                chunk
                for caches in self._field_caches
                for chunk in caches[field_name]
            )
            return ImmutableSequence(
                tuple(len(chunk) for chunk in cached_chunks),
                load_chunk=lambda index: cached_chunks[index],
                load_chunks=lambda: iter(cached_chunks),
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

    def preserve_failed_results(self) -> Path | None:
        """Retain sealed records and their shared references after a failure.

        Only the already-written partitions are retained. The private snapshot
        uses the same pickle codec and is read-only; it is not an external input
        format or a completed workflow result.
        """
        if not self._partitions:
            self.cleanup()
            return None
        snapshot = copy(self)
        snapshot._sealed = True
        snapshot._field_caches = []
        destination = self._directory / "failed-results.pickle"
        temporary = destination.with_suffix(".tmp")
        if destination.exists():
            raise FileExistsError(destination)
        stream = temporary.open("xb")
        try:
            with stream:
                _OwnerPickler(stream, ()).dump(snapshot)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        self._field_caches.clear()
        return destination

    def cleanup(self) -> None:
        """Remove private spool directory and release in-memory caches."""
        self._field_caches.clear()
        if self._directory.exists():
            shutil.rmtree(self._directory, ignore_errors=True)


PartitionResultStore = StreamedResultStore
