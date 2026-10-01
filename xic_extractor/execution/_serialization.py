"""Private pickle codec for restoring immutable execution-owner references."""

from __future__ import annotations

import copyreg
import io
import pickle
import struct
from types import MappingProxyType
from typing import Any, BinaryIO


def _restore_mapping_proxy(values: dict) -> MappingProxyType:
    return MappingProxyType(values)


def _reduce_mapping_proxy(value: MappingProxyType) -> tuple:
    return _restore_mapping_proxy, (dict(value),)


class _OwnerPickler(pickle.Pickler):
    def __init__(self, stream: Any, shared_objects: tuple[object, ...]):
        super().__init__(stream, protocol=pickle.HIGHEST_PROTOCOL)
        self.dispatch_table = copyreg.dispatch_table.copy()
        self.dispatch_table[MappingProxyType] = _reduce_mapping_proxy
        # Native memo references retain exact authorities without a Python
        # persistent_id callback for every scalar in traces and evidence.
        unique = {id(value): value for value in shared_objects}
        self.memo = {
            identity: (index, value)
            for index, (identity, value) in enumerate(unique.items())
        }
        if unique:
            # Prime the native reader through pickle instructions, without
            # assigning its implementation-specific memo table.
            stream.write(
                pickle.PROTO
                + bytes((pickle.HIGHEST_PROTOCOL,))
                + b"".join(
                    pickle.BININT
                    + struct.pack("<i", index)
                    + pickle.BINPERSID
                    + pickle.MEMOIZE
                    + pickle.POP
                    for index in range(len(unique))
                )
                + pickle.NONE
                + pickle.STOP
            )


class _OwnerUnpickler(pickle.Unpickler):
    def __init__(self, stream: BinaryIO, shared_objects: tuple[object, ...]):
        super().__init__(stream)
        unique = {id(value): value for value in shared_objects}
        self._shared_objects = tuple(unique.values())
        if unique and super().load() is not None:
            raise pickle.UnpicklingError("missing shared owner memo prefix")

    def persistent_load(self, identity: object) -> object:
        if type(identity) is not int or not 0 <= identity < len(self._shared_objects):
            raise pickle.UnpicklingError("unknown same-process owner reference")
        return self._shared_objects[identity]


def owner_pickle_dumps(
    value: object,
    shared_objects: tuple[object, ...],
) -> bytes:
    stream = io.BytesIO()
    _OwnerPickler(stream, shared_objects).dump(value)
    return stream.getvalue()


def owner_pickle_loads(
    payload: bytes,
    shared_objects: tuple[object, ...],
) -> object:
    return _OwnerUnpickler(io.BytesIO(payload), shared_objects).load()


__all__ = ["owner_pickle_dumps", "owner_pickle_loads"]
