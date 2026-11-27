"""Partition storage preserves authority and releases serialized result graphs."""

import gc
import pickle
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from xic_extractor.execution.result_store import (
    StreamedResultStore,
)
from xic_extractor.immutable_sequence import ImmutableSequence

_restored_children = []


def _restore_child(value):
    _restored_children.append(value)
    return value


class _ObservedChild:
    def __reduce__(self):
        return _restore_child, ("scientific-child",)


def _restore_ephemeral_child():
    return _EphemeralChild()


class _EphemeralChild:
    def __reduce__(self):
        return _restore_ephemeral_child, ()


@dataclass(frozen=True)
class Payload:
    child_outputs: tuple
    method_assessments: tuple = ()
    excluded_candidates: tuple = ()
    label: str = "partition"


def test_roundtrip_preserves_explicit_nested_owner_identity_and_proxy(tmp_path):
    owner = object()
    shared_container = (owner,)
    payload = Payload((MappingProxyType({"owner": owner, "shared": shared_container}),))
    store = StreamedResultStore(tmp_path, (owner, shared_container))
    entry = store.append("subject-1", payload)
    assert (entry.index, entry.key) == (0, "subject-1")
    restored = store.read(entry.index)
    assert restored is not payload
    assert restored.child_outputs[0]["owner"] is owner
    assert restored.child_outputs[0]["shared"] is shared_container
    with pytest.raises(TypeError):
        restored.child_outputs[0]["new"] = "mutation"


def test_roundtrip_preserves_array_larger_than_one_pickle_frame(tmp_path):
    trace = np.arange(1 << 14, dtype=float)
    store = StreamedResultStore(tmp_path, ())
    entry = store.append("large-trace", Payload((trace, "next record")))
    array, marker = store.read(entry.index).child_outputs
    np.testing.assert_array_equal(array, trace)
    assert marker == "next record"
    assert store.read_selected(entry.index, "child_outputs", (1,)) == ("next record",)


def test_sealed_sequence_preserves_order_indices_and_empty_chunks(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    for index, values in enumerate(((), (1, 2), (), (3,), ())):
        store.append(str(index), Payload(values))
    with pytest.raises(ValueError, match="seal"):
        store.sequence("child_outputs")
    store.seal()
    sequence = store.sequence("child_outputs")
    assert len(sequence) == 3
    assert tuple(sequence) == (1, 2, 3)
    assert sequence[1] == 2
    assert sequence[-1] == 3
    assert sequence[::-1] == (3, 2, 1)
    assert tuple(store.sequence("method_assessments")) == ()
    with pytest.raises(IndexError):
        _ = sequence[3]
    with pytest.raises(ValueError, match="tuple field"):
        store.sequence("label")
    with pytest.raises(ValueError, match="sealed"):
        store.append("late", Payload(()))


def test_empty_sealed_store_has_empty_sequence(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    store.seal()
    assert tuple(store.sequence("child_outputs")) == ()


def test_tuple_field_read_skips_header_and_other_fields(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    expected = tuple((f"S{index}", index * 2) for index in range(2050))
    store.append(
        "initial",
        Payload(
            (_ObservedChild(),), method_assessments=expected, label=_ObservedChild()
        ),
    )
    store.append("other", Payload((), method_assessments=(("other", -1),)))
    _restored_children.clear()
    store.seal()
    assert store.read_tuple(0, "method_assessments") == expected
    assert store.read_tuple(1, "method_assessments") == (("other", -1),)
    assert store.read_tuple(0, "excluded_candidates") == ()
    assert not _restored_children
    with pytest.raises(ValueError, match="tuple field"):
        store.read_tuple(0, "label")


def test_header_field_read_skips_acquired_records(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    store.append("first", Payload((_ObservedChild(),), label="grouping-S1"))
    store.append("second", Payload((_ObservedChild(),), label="grouping-S2"))
    _restored_children.clear()
    store.seal()
    assert store.read_header_field(0, "label") == "grouping-S1"
    assert store.read_header_field(1, "label") == "grouping-S2"
    assert not _restored_children
    with pytest.raises(ValueError, match="non-tuple field"):
        store.read_header_field(0, "child_outputs")


def test_streaming_children_matches_append_bytes_and_reader(tmp_path):
    payload = Payload(
        (1, 2, 3), method_assessments=(4,), excluded_candidates=(5,), label="same"
    )
    tuple_store = StreamedResultStore(tmp_path / "tuple", ())
    tuple_store.append("partition", payload)
    stream_store = StreamedResultStore(tmp_path / "stream", ())

    def child_outputs():
        yield from payload.child_outputs

    stream_store.append_child_outputs(
        "partition",
        Payload(
            (), payload.method_assessments, payload.excluded_candidates, payload.label
        ),
        child_outputs=child_outputs(),
    )
    assert next((tmp_path / "stream").glob("*.pickle")).read_bytes() == (
        next((tmp_path / "tuple").glob("*.pickle")).read_bytes()
    )
    stream_store.seal()
    assert stream_store.read(0) == payload
    assert tuple(stream_store.sequence("child_outputs")) == payload.child_outputs


def test_streaming_children_failure_does_not_publish_or_seal(tmp_path):
    store = StreamedResultStore(tmp_path, ())

    def failing_children():
        yield 1
        raise OSError("injected child failure")

    with pytest.raises(OSError, match="injected child failure"):
        store.append_child_outputs(
            "partition",
            Payload((), (), (), "partition"),
            child_outputs=failing_children(),
        )
    assert not (tmp_path / "00000000.pickle").exists()
    store.append("partition", Payload((2,)))


def test_streaming_children_checks_cancellation_before_publication(tmp_path):
    store = StreamedResultStore(tmp_path, ())

    def cancel():
        raise InterruptedError("cancel before publication")

    with pytest.raises(InterruptedError, match="cancel before publication"):
        store.append_child_outputs(
            "partition",
            Payload((), (), (), "partition"),
            child_outputs=(1,),
            before_publish=cancel,
        )
    assert not (tmp_path / "00000000.pickle").exists()
    store.append("partition", Payload((2,)))


def test_streaming_children_releases_previous_value_before_next_child(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    references = []

    def child_outputs():
        value = _ObservedChild()
        references.append(weakref.ref(value))
        yield value
        del value
        gc.collect()
        assert references[0]() is None
        yield "second"

    store.append_child_outputs(
        "partition",
        Payload((), (), (), "partition"),
        child_outputs=child_outputs(),
    )
    gc.collect()
    assert references[0]() is None


def test_sequential_sequence_reuses_one_open_file_per_partition(tmp_path, monkeypatch):
    store = StreamedResultStore(tmp_path, ())
    store.append("one", Payload((1, 2)))
    store.append("two", Payload((3,)))
    store.seal()

    opened = []
    original_open = Path.open

    def tracking_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracking_open)
    sequence = store.sequence("child_outputs")
    assert tuple(sequence) == (1, 2, 3)
    assert len(opened) == 2
    assert all(stream.closed for stream in opened)
    assert sequence[1] == 2
    assert len(opened) == 3


def test_sequential_sequence_closes_partition_on_early_close_and_error(
    tmp_path, monkeypatch
):
    store = StreamedResultStore(tmp_path, ())
    store.append("one", Payload((1,)))
    store.seal()

    opened = []
    original_open = Path.open

    def tracking_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracking_open)
    iterator = iter(store.sequence("child_outputs"))
    assert next(iterator) == 1
    assert not opened[0].closed
    iterator.close()
    assert opened[0].closed

    def malformed_record(_stream, _offset, _length):
        return ()

    monkeypatch.setattr(store, "_read_record", malformed_record)
    with pytest.raises(ValueError, match="sealed cardinality"):
        tuple(store.sequence("child_outputs"))
    assert opened[-1].closed


def test_sequential_sequence_releases_previous_value_before_next_record(
    tmp_path, monkeypatch
):
    store = StreamedResultStore(tmp_path, ())
    first = _EphemeralChild()
    second = _EphemeralChild()
    store.append("one", Payload((first,)))
    store.append("two", Payload((second,)))
    store.seal()

    previous = []
    original_read = store._read_record

    def observe_read(stream, offset, length):
        if previous:
            assert previous[0]() is None
        values = original_read(stream, offset, length)
        previous[:] = [weakref.ref(values[0])]
        return values

    monkeypatch.setattr(store, "_read_record", observe_read)
    iterator = iter(store.sequence("child_outputs"))
    value = next(iterator)
    del value
    assert isinstance(next(iterator), _EphemeralChild)


def test_sequential_loader_rejects_changed_chunk_cardinality():
    sequence = ImmutableSequence(
        (1,),
        lambda _index: (1,),
        lambda: iter(((),)),
    )
    with pytest.raises(ValueError, match="sealed cardinality"):
        tuple(sequence)


def test_diagnostic_field_read_does_not_restore_unrelated_scientific_children(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    store.append(
        "one", Payload((_ObservedChild(),), tuple(range(5000)), tuple(range(5000)))
    )
    store.seal()
    _restored_children.clear()
    values = store.sequence("method_assessments")
    assert tuple(values) == tuple(range(5000))
    assert values[2048] == 2048
    assert store.read_selected(0, "excluded_candidates", (4999, 0, 2048)) == (
        4999,
        0,
        2048,
    )
    assert _restored_children == []


def test_append_does_not_retain_payload_and_reads_do_not_cache(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    payload = Payload((object(),))
    reference = weakref.ref(payload)
    store.append("one", payload)
    del payload
    gc.collect()
    assert reference() is None
    restored = store.read(0)
    reference = weakref.ref(restored)
    del restored
    gc.collect()
    assert reference() is None


def test_failed_append_does_not_publish_or_consume_partition_key(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    with pytest.raises((AttributeError, TypeError, pickle.PicklingError)):
        store.append("retry", Payload((lambda: None,)))
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(IndexError):
        store.read(0)
    assert store.append("retry", Payload((1,))).index == 0
    with pytest.raises(ValueError, match="duplicate"):
        store.append("retry", Payload((2,)))
    assert store.read(0).child_outputs == (1,)


@pytest.mark.parametrize("key", ("", ".", "..", "../escape", "a/b", "a\\b", "a:b"))
def test_partition_keys_cannot_escape_owner_directory(tmp_path, key):
    store = StreamedResultStore(tmp_path, ())
    with pytest.raises(ValueError, match="partition key"):
        store.append(key, Payload(()))
    assert list(tmp_path.iterdir()) == []
