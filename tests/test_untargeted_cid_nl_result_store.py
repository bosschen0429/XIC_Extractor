"""Partition storage preserves authority and releases serialized result graphs."""

import gc
import hashlib
import weakref
from dataclasses import dataclass
from types import MappingProxyType

import pytest

from xic_extractor.extraction.untargeted_cid_nl_result_store import PartitionResultStore

_restored_children = []


def _restore_child(value):
    _restored_children.append(value)
    return value


class _ObservedChild:
    def __reduce__(self):
        return _restore_child, ("scientific-child",)


@dataclass(frozen=True)
class Payload:
    children: tuple
    method_assessments: tuple = ()
    excluded_candidates: tuple = ()
    label: str = "partition"


def test_roundtrip_preserves_explicit_nested_owner_identity_and_proxy(tmp_path):
    owner = object()
    shared_container = (owner,)
    payload = Payload((MappingProxyType({"owner": owner, "shared": shared_container}),))
    store = PartitionResultStore(tmp_path, (owner, shared_container))
    entry = store.append("subject-1", payload)
    assert (entry.index, entry.key) == (0, "subject-1")
    assert entry.size_bytes > 0
    stored_bytes = next(tmp_path.iterdir()).read_bytes()
    assert entry.size_bytes == len(stored_bytes)
    assert entry.sha256 == hashlib.sha256(stored_bytes).hexdigest()
    restored = store.read(entry.index)
    assert restored is not payload
    assert restored.children[0]["owner"] is owner
    assert restored.children[0]["shared"] is shared_container
    with pytest.raises(TypeError):
        restored.children[0]["new"] = "mutation"


def test_sealed_sequence_preserves_order_indices_and_empty_chunks(tmp_path):
    store = PartitionResultStore(tmp_path, ())
    for index, values in enumerate(((), (1, 2), (), (3,), ())):
        store.append(str(index), Payload(values))
    with pytest.raises(ValueError, match="seal"):
        store.sequence("children")
    store.seal()
    sequence = store.sequence("children")
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
    store = PartitionResultStore(tmp_path, ())
    store.seal()
    assert tuple(store.sequence("children")) == ()


def test_diagnostic_field_read_does_not_restore_unrelated_scientific_children(tmp_path):
    store = PartitionResultStore(tmp_path, ())
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
    store = PartitionResultStore(tmp_path, ())
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
    store = PartitionResultStore(tmp_path, ())
    with pytest.raises((AttributeError, TypeError)):
        store.append("retry", Payload((lambda: None,)))
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(IndexError):
        store.read(0)
    assert store.append("retry", Payload((1,))).index == 0
    with pytest.raises(ValueError, match="duplicate"):
        store.append("retry", Payload((2,)))
    assert store.read(0).children == (1,)


@pytest.mark.parametrize("key", ("", ".", "..", "../escape", "a/b", "a\\b", "a:b"))
def test_partition_keys_cannot_escape_owner_directory(tmp_path, key):
    store = PartitionResultStore(tmp_path, ())
    with pytest.raises(ValueError, match="partition key"):
        store.append(key, Payload(()))
    assert list(tmp_path.iterdir()) == []
