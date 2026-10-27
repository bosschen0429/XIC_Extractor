"""Partition storage preserves authority and releases serialized result graphs."""

import gc
import pickle
import shutil
import subprocess
import sys
import textwrap
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from xic_extractor.execution._serialization import owner_pickle_dumps
from xic_extractor.execution.result_store import (
    PRESERVED_INDEX_NAME,
    StreamedResultStore,
    open_preserved_results,
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
    children: tuple
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
    assert entry.size_bytes > 0
    stored_bytes = next(tmp_path.iterdir()).read_bytes()
    assert entry.size_bytes == len(stored_bytes)
    restored = store.read(entry.index)
    assert restored is not payload
    assert restored.children[0]["owner"] is owner
    assert restored.children[0]["shared"] is shared_container
    with pytest.raises(TypeError):
        restored.children[0]["new"] = "mutation"


def test_sealed_sequence_preserves_order_indices_and_empty_chunks(tmp_path):
    store = StreamedResultStore(tmp_path, ())
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
    store = StreamedResultStore(tmp_path, ())
    store.seal()
    assert tuple(store.sequence("children")) == ()


def test_tuple_field_read_skips_header_and_other_fields_after_preservation(tmp_path):
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
    for reader in (store, open_preserved_results(store.preserve_failed_results())):
        assert reader.read_tuple(0, "method_assessments") == expected
        assert reader.read_tuple(1, "method_assessments") == (("other", -1),)
        assert reader.read_tuple(0, "excluded_candidates") == ()
        assert not _restored_children
        with pytest.raises(ValueError, match="tuple field"):
            reader.read_tuple(0, "label")


def test_header_field_read_skips_acquired_records_after_preservation(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    store.append("first", Payload((_ObservedChild(),), label="grouping-S1"))
    store.append("second", Payload((_ObservedChild(),), label="grouping-S2"))
    _restored_children.clear()
    for reader in (store, open_preserved_results(store.preserve_failed_results())):
        assert reader.read_header_field(0, "label") == "grouping-S1"
        assert reader.read_header_field(1, "label") == "grouping-S2"
        assert not _restored_children
        with pytest.raises(ValueError, match="non-tuple field"):
            reader.read_header_field(0, "children")


def test_streaming_children_matches_append_bytes_and_reader(tmp_path):
    payload = Payload(
        (1, 2, 3), method_assessments=(4,), excluded_candidates=(5,), label="same"
    )
    tuple_store = StreamedResultStore(tmp_path / "tuple", ())
    tuple_entry = tuple_store.append("partition", payload)
    stream_store = StreamedResultStore(tmp_path / "stream", ())

    def children():
        yield from payload.children

    stream_entry = stream_store.append_children(
        "partition",
        Payload(
            (), payload.method_assessments, payload.excluded_candidates, payload.label
        ),
        children=children(),
    )
    assert next((tmp_path / "stream").glob("*.pickle")).read_bytes() == (
        next((tmp_path / "tuple").glob("*.pickle")).read_bytes()
    )
    assert stream_entry.size_bytes == tuple_entry.size_bytes
    stream_store.seal()
    assert stream_store.read(0) == payload
    assert tuple(stream_store.sequence("children")) == payload.children


def test_streaming_children_failure_does_not_publish_or_seal(tmp_path):
    store = StreamedResultStore(tmp_path, ())

    def failing_children():
        yield 1
        raise OSError("injected child failure")

    with pytest.raises(OSError, match="injected child failure"):
        store.append_children(
            "partition",
            Payload((), (), (), "partition"),
            children=failing_children(),
        )
    assert not (tmp_path / "00000000.pickle").exists()
    store.append("partition", Payload((2,)))


def test_streaming_children_checks_cancellation_before_publication(tmp_path):
    store = StreamedResultStore(tmp_path, ())

    def cancel():
        raise InterruptedError("cancel before publication")

    with pytest.raises(InterruptedError, match="cancel before publication"):
        store.append_children(
            "partition",
            Payload((), (), (), "partition"),
            children=(1,),
            before_publish=cancel,
        )
    assert not (tmp_path / "00000000.pickle").exists()
    store.append("partition", Payload((2,)))


def test_streaming_children_releases_previous_value_before_next_child(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    references = []

    def children():
        value = _ObservedChild()
        references.append(weakref.ref(value))
        yield value
        del value
        gc.collect()
        assert references[0]() is None
        yield "second"

    store.append_children(
        "partition",
        Payload((), (), (), "partition"),
        children=children(),
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
    sequence = store.sequence("children")
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
    iterator = iter(store.sequence("children"))
    assert next(iterator) == 1
    assert not opened[0].closed
    iterator.close()
    assert opened[0].closed

    def malformed_record(_stream, _offset, _length):
        return ()

    monkeypatch.setattr(store, "_read_record", malformed_record)
    with pytest.raises(ValueError, match="sealed cardinality"):
        tuple(store.sequence("children"))
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
    iterator = iter(store.sequence("children"))
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
    store = StreamedResultStore(tmp_path, ())
    with pytest.raises(ValueError, match="partition key"):
        store.append(key, Payload(()))
    assert list(tmp_path.iterdir()) == []


def test_retained_memory_sequence_and_selected_bypass_file_reads(tmp_path, monkeypatch):
    payload = Payload(
        (10, 20, 30),
        method_assessments=(100, 200),
        excluded_candidates=(1000, 2000),
        label="retained",
    )
    store = StreamedResultStore(tmp_path, ())
    entry = store.append_children(
        "part-0",
        Payload(
            (),
            payload.method_assessments,
            payload.excluded_candidates,
            payload.label,
        ),
        children=(10, 20, 30),
        retain_memory=True,
    )
    assert entry.size_bytes > 0
    store.seal()

    opened = []
    original_open = Path.open

    def tracking_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracking_open)

    # Reading sequence and selected should not open any files
    seq = store.sequence("children")
    assert tuple(seq) == (10, 20, 30)
    assert seq[1] == 20
    assert len(opened) == 0

    selected = store.read_selected(0, "excluded_candidates", (1, 0))
    assert selected == (2000, 1000)
    assert len(opened) == 0


def test_partition_result_store_cleanup(tmp_path: Path) -> None:
    spool_dir = tmp_path / "spool"
    store = StreamedResultStore(spool_dir, ())
    store.append(
        "part-0",
        Payload(children=(1, 2), method_assessments=("a", "b")),
        retain_memory=True,
    )
    assert spool_dir.exists()
    assert len(store._field_caches) == 1

    store.cleanup()
    assert not spool_dir.exists()
    assert len(store._field_caches) == 0

    # Idempotent cleanup
    store.cleanup()
    assert not spool_dir.exists()


def test_preserved_index_reads_one_record_in_a_separate_process(tmp_path, monkeypatch):
    """A moved preserved spool is readable by a reader that never held the graph."""
    from tests.test_untargeted_cid_nl_bounded_runtime import _plan
    from xic_extractor.untargeted_execution.execution import (
        _sample_worker_shared_objects,
        materialize_untargeted_partition,
    )

    plan, _reference = _plan(tmp_path, monkeypatch, sample_count=2)
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    shared = _sample_worker_shared_objects(
        plan.context.processing_owners,
        (batch.jobs[0]._request_subjects, batch.subject_batch),
    )
    jobs_by_sample = {
        job.sample.identity.sample_key: tuple(
            candidate
            for candidate in batch.jobs
            if candidate.sample.identity.sample_key == job.sample.identity.sample_key
        )
        for job in batch.jobs
    }
    assert len(jobs_by_sample) == 2
    cohorts = tuple(jobs_by_sample.values())
    store = StreamedResultStore(tmp_path / "spool", shared)
    store.append("alpha", Payload(children=cohorts[0]))
    store.append("beta", Payload(children=cohorts[1]))
    store.seal()
    index = store.preserve_results()
    assert index.is_file()
    moved = tmp_path / "moved-spool"
    shutil.move(str(index.parent), str(moved))
    index = moved / index.name

    reader = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from xic_extractor.execution.result_store import (
            open_preserved_results,
        )
        from xic_extractor.untargeted_execution.partition.models import (
            UntargetedPartitionJob,
        )

        store = open_preserved_results(Path(sys.argv[1]))
        job = store.read_selected(int(sys.argv[2]), "children", (int(sys.argv[3]),))[0]
        assert isinstance(job, UntargetedPartitionJob)
        print(
            job.sample.identity.sample_key
            + ":"
            + job.method_subject.method_subject_id
        )
        """
    )
    script = tmp_path / "reader.py"
    script.write_text(reader, encoding="utf-8")

    for partition_index, cohort in enumerate(cohorts):
        position = len(cohort) - 1
        job = cohort[position]
        expected = (
            job.sample.identity.sample_key + ":" + job.method_subject.method_subject_id
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                str(index),
                str(partition_index),
                str(position),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == expected


def test_preserved_index_is_sealed_and_rebinds_to_its_own_directory(tmp_path):
    """Moving a preserved spool keeps it readable and keeps it read-only."""
    store = StreamedResultStore(tmp_path / "spool", ())
    store.append("alpha", Payload(children=("a0", "a1")))
    store.seal()
    store.preserve_results()

    moved = tmp_path / "moved"
    shutil.move(str(tmp_path / "spool"), str(moved))
    restored = open_preserved_results(moved / PRESERVED_INDEX_NAME)

    assert restored.read_selected(0, "children", (1,)) == ("a1",)
    with pytest.raises(ValueError, match="sealed"):
        restored.append("gamma", Payload(children=("c0",)))


def test_preserved_index_refuses_a_truncated_file(tmp_path):
    """A damaged index fails loudly instead of yielding partial records."""
    store = StreamedResultStore(tmp_path / "spool", ())
    store.append("alpha", Payload(children=("a0",)))
    store.seal()
    index = store.preserve_results()

    payload = index.read_bytes()
    index.write_bytes(payload[: len(payload) // 2])
    with pytest.raises(pickle.UnpicklingError):
        open_preserved_results(index)


def test_preserve_results_refuses_to_overwrite_an_existing_index(tmp_path):
    store = StreamedResultStore(tmp_path / "spool", ())
    store.append("alpha", Payload(children=("a0",)))
    store.seal()
    store.preserve_results()
    with pytest.raises(FileExistsError):
        store.preserve_results()


def test_preserved_results_reject_master_pickled_store(tmp_path):
    store = StreamedResultStore(tmp_path, ())
    store.append("partition", Payload((1,)))
    store.seal()
    index = tmp_path / "master-index.pickle"
    # Master serialized the runtime object itself, not a record-index document.
    index.write_bytes(owner_pickle_dumps(store, ()))
    with pytest.raises(ValueError, match="rerun the analysis"):
        open_preserved_results(index)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_preserved_results_require_complete_index_fields(tmp_path, change):
    store = StreamedResultStore(tmp_path, ())
    store.append("partition", Payload((1,)))
    index = store.preserve_results()
    payload = pickle.loads(index.read_bytes())
    if change == "missing":
        del payload["field_records"]
    else:
        payload["extra"] = None
    index.write_bytes(owner_pickle_dumps(payload, ()))
    with pytest.raises(ValueError, match="rerun the analysis"):
        open_preserved_results(index)
