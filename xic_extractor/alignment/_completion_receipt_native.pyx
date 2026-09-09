# cython: language_level=3
# distutils: language=c++
from libcpp.vector cimport vector
from libc.string cimport memcpy
from cpython.buffer cimport PyBUF_READ
from cpython.memoryview cimport PyMemoryView_FromMemory
from cpython.unicode cimport PyUnicode_AsUTF8AndSize
from cpython.bytes cimport PyBytes_FromStringAndSize
"""Native traversal of the retained owner's exact length-prefixed token stream."""
from collections.abc import Mapping, Set
from dataclasses import fields, is_dataclass
from hashlib import sha256
from sys import getrecursionlimit

cdef extern from "Python.h":
    int Py_EnterRecursiveCall(const char *where) except -1
    void Py_LeaveRecursiveCall()

cdef class Walker:
    cdef dict seen
    cdef set identity_only_ids
    cdef vector[char] buffer
    cdef object digest
    cdef Py_ssize_t remaining_depth

    def __cinit__(self, identities, digest=None):
        self.seen = {}
        self.identity_only_ids = {id(item) for item in identities}
        self.digest = digest
        self.buffer.reserve(8192 if digest is None else 64 * 1024)
        self.remaining_depth = getrecursionlimit()

    cdef void flush_digest(self) except *:
        if self.buffer.size():
            self.digest.update(
                PyMemoryView_FromMemory(
                    self.buffer.data(), self.buffer.size(), PyBUF_READ
                )
            )
            self.buffer.clear()

    cdef void feed(self, str text) except *:
        cdef Py_ssize_t length
        cdef const char* encoded = PyUnicode_AsUTF8AndSize(text, &length)
        cdef unsigned long long size = length
        cdef unsigned char prefix[8]
        cdef Py_ssize_t offset = 0
        cdef Py_ssize_t chunk_size
        cdef size_t start
        cdef int i
        for i in range(8):
            prefix[i] = <unsigned char>((size >> (56 - 8*i)) & 255)
        if self.digest is not None:
            if length + 8 > 64 * 1024:
                self.flush_digest()
                self.digest.update(PyBytes_FromStringAndSize(<char*>prefix, 8))
                while offset < length:
                    chunk_size = min(length - offset, 64 * 1024)
                    self.digest.update(
                        PyMemoryView_FromMemory(
                            <char*>encoded + offset,
                            chunk_size,
                            PyBUF_READ,
                        )
                    )
                    offset += chunk_size
                return
            # Owner graphs mostly contain short identity/field/scalar tokens.
            # Batch these instead of crossing Python/hashlib twice per token.
            if self.buffer.size() + 8 + length > 64 * 1024:
                self.flush_digest()
        start = self.buffer.size()
        self.buffer.resize(start + 8 + length)
        memcpy(self.buffer.data() + start, prefix, 8)
        if length:
            memcpy(self.buffer.data() + start + 8, encoded, length)

    cdef void visit(self, object item) except *:
        # Match Python's bounded recursion failure instead of exhausting C stack.
        if self.remaining_depth <= 0:
            raise RecursionError("maximum recursion depth exceeded")
        Py_EnterRecursiveCall(" while encoding retained owner receipt")
        self.remaining_depth -= 1
        try:
            self.visit_item(item)
        finally:
            self.remaining_depth += 1
            Py_LeaveRecursiveCall()

    cdef void visit_item(self, object item) except *:
        cdef object owner_id, owner_field, member, key
        if item is None or type(item) in {bool, int, float, str, bytes}:
            self.feed(f'scalar:{type(item).__module__}.{type(item).__qualname__}')
            self.feed(repr(item))
            return
        owner_id = id(item)
        if owner_id in self.seen:
            self.feed(f'ref:{self.seen[owner_id]}')
            return
        self.seen[owner_id] = len(self.seen)
        self.feed(f'owner:{type(item).__module__}.{type(item).__qualname__}')
        self.feed(str(owner_id))
        if owner_id in self.identity_only_ids:
            self.feed('identity-only')
            return
        if is_dataclass(item) and not isinstance(item, type):
            for owner_field in fields(item):
                if owner_field.name == '_completion_seal':
                    continue
                self.feed(owner_field.name)
                self.visit(getattr(item, owner_field.name))
            return
        if isinstance(item, tuple | list):
            self.feed(f'sequence:{len(item)}')
            for member in item:
                self.visit(member)
            return
        if isinstance(item, Mapping):
            self.feed(f'mapping:{len(item)}')
            for key, member in item.items():
                self.visit(key)
                self.visit(member)
            return
        if isinstance(item, Set):
            self.feed(f'set:{len(item)}')
            for member in sorted(item, key=lambda member: (type(member).__module__, type(member).__qualname__, repr(member))):
                self.visit(member)
            return
        self.feed(repr(item))

def encoded_graph(value, *, identity_only=()):
    cdef Walker walker = Walker(identity_only)
    walker.visit(value)
    return PyBytes_FromStringAndSize(walker.buffer.data(), walker.buffer.size())

def retained_owner_digest(value, *, identity_only=()):
    digest = sha256()
    cdef Walker walker = Walker(identity_only, digest)
    walker.visit(value)
    walker.flush_digest()
    return digest.digest()
