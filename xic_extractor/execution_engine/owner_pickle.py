"""Private pickle codec for restoring immutable execution-owner references."""

from __future__ import annotations

import copyreg
import io
import pickle
from types import MappingProxyType
from typing import Any, BinaryIO


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


def owner_pickle_dumps(
    value: object,
    shared_objects: tuple[object, ...],
) -> bytes:
    stream = io.BytesIO()
    shared_ids = {id(item): index for index, item in enumerate(shared_objects)}
    _OwnerPickler(stream, shared_ids).dump(value)
    return stream.getvalue()


def owner_pickle_loads(
    payload: bytes,
    shared_objects: tuple[object, ...],
) -> object:
    return _OwnerUnpickler(io.BytesIO(payload), shared_objects).load()


__all__ = ["owner_pickle_dumps", "owner_pickle_loads"]
