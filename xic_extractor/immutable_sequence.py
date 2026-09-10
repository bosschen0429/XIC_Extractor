"""Immutable chunk metadata over an owner-controlled result loader."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar, overload

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ImmutableSequence(Sequence[T], Generic[T]):
    """Read sealed result chunks without retaining the complete population.

    The producing owner keeps its loader and chunk storage alive for consumers.
    Iteration owns only the current chunk; scalar access loads its one chunk.
    This carrier changes storage, not the semantic authority of its elements.
    """

    chunk_lengths: tuple[int, ...]
    load_chunk: Callable[[int], tuple[T, ...]] = field(repr=False, compare=False)
    load_chunks: Callable[[], Iterator[tuple[T, ...]]] | None = field(
        default=None, repr=False, compare=False
    )
    _ends: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.chunk_lengths) is not tuple or any(
            type(length) is not int or length < 0 for length in self.chunk_lengths
        ):
            raise ValueError("chunk lengths must be non-negative integers")
        if not callable(self.load_chunk):
            raise TypeError("result chunk loader must be callable")
        if self.load_chunks is not None and not callable(self.load_chunks):
            raise TypeError("sequential result chunk loader must be callable")
        total = 0
        ends = []
        for length in self.chunk_lengths:
            total += length
            ends.append(total)
        object.__setattr__(self, "_ends", tuple(ends))

    def __len__(self) -> int:
        return self._ends[-1] if self._ends else 0

    def _chunk(self, index: int) -> tuple[T, ...]:
        values = self.load_chunk(index)
        if type(values) is not tuple or len(values) != self.chunk_lengths[index]:
            raise ValueError("stored result chunk changed its sealed cardinality")
        return values

    def __iter__(self) -> Iterator[T]:
        if self.load_chunks is not None:
            chunks = iter(self.load_chunks())
            chunk_index = 0
            try:
                for values in chunks:
                    if (
                        chunk_index >= len(self.chunk_lengths)
                        or type(values) is not tuple
                        or len(values) != self.chunk_lengths[chunk_index]
                    ):
                        raise ValueError(
                            "stored result chunk changed its sealed cardinality"
                        )
                    yield from values
                    del values
                    chunk_index += 1
                if chunk_index != len(self.chunk_lengths):
                    raise ValueError(
                        "stored result chunk changed its sealed cardinality"
                    )
            finally:
                close = getattr(chunks, "close", None)
                if callable(close):
                    close()
            return
        for index in range(len(self.chunk_lengths)):
            yield from self._chunk(index)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        if isinstance(index, slice):
            return tuple(self[item] for item in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("result index out of range")
        chunk_index = bisect_right(self._ends, index)
        start = self._ends[chunk_index - 1] if chunk_index else 0
        return self._chunk(chunk_index)[index - start]
