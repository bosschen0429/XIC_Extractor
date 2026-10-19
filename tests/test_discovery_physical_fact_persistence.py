from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceCoordinateKind,
    acquire_trace_batch,
    acquired_trace_content_id,
    mint_trace_acquisition_capability,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import (
    DiscoveryPhysicalWorkItem,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
    deserialize_discovery_physical_fact_sidecar,
    read_discovery_physical_fact_records,
    read_discovery_physical_fact_sidecar,
    serialize_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.physical_fact_source import PhysicalFactSource
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
    )


def _spec(tmp_path: Path, *, profile_points: int = 3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample acquisition")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=profile_points),
    )
    return raw_path, spec, spec.acquisition_inputs[0]


def _work(
    ordinal: int,
    acquisition_input,
    *,
    terminal: str,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalWorkItem:
    if terminal == "available":
        query = 0.8
        context = (0.0, 3.0)
    elif terminal == "unavailable":
        query = 1.0
        context = (1.0, 1.0)
    else:
        query = 1.5
        context = (1.0, 1.5)
    return DiscoveryPhysicalWorkItem(
        work_ordinal=ordinal,
        acquisition_source_id=(
            acquisition_input.acquisition_source.acquisition_source_id
        ),
        sample_stem=acquisition_input.sample_stem,
        best_ms2_scan_id=100 + ordinal,
        precursor_mz=500.0 + ordinal,
        product_mz=383.9526 + ordinal,
        query_rt_min=query,
        context_rt_start_min=context[0],
        context_rt_end_min=context[1],
        trace_coordinate_id=acquired_trace.coordinate.trace_coordinate_id,
        acquired_trace_content_id=acquired_trace_content_id(acquired_trace),
        xic_mz=acquired_trace.coordinate.request.mz,
        xic_rt_min=acquired_trace.coordinate.request.rt_min,
        xic_rt_max=acquired_trace.coordinate.request.rt_max,
        xic_ppm_tol=acquired_trace.coordinate.request.ppm_tol,
    )


def _record(
    work_item: DiscoveryPhysicalWorkItem,
    *,
    terminal: str,
    profile: SignalProcessingProfile,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalFactRecord:
    rt = acquired_trace.trace.rt
    raw = acquired_trace.trace.intensity
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        selection_subject_id=work_item.selection_subject_id,
        query_rt_min=work_item.query_rt_min,
        request_context_rt_start_min=work_item.context_rt_start_min,
        request_context_rt_end_min=work_item.context_rt_end_min,
    )
    return DiscoveryPhysicalFactRecord(
        work_item=work_item,
        acquired_trace=acquired_trace,
        evaluation=evaluation,
    )


def _acquired_trace(acquisition_input, *, terminal: str, ordinal: int) -> AcquiredTrace:
    if terminal == "available":
        rt = np.linspace(0.0, 3.0, 31)
        raw = np.full(31, 10.0)
        raw[7:10] += [30.0, 100.0, 30.0]
        request_rt = (0.0, 3.0)
    elif terminal == "unavailable":
        rt = np.asarray([1.0])
        raw = np.asarray([10.0])
        request_rt = (1.0, 1.0)
    else:
        rt = np.asarray([1.0, 2.0, 1.5])
        raw = np.asarray([1.0, 2.0, 1.0])
        request_rt = (1.0, 2.0)

    class Adapter:
        raw_chromatogram_call_count = 0

        def extract_xic_many(self, _requests):
            return (XICTrace.from_arrays(rt, raw),)

        def scan_window_for_request(self, _request):
            return (1000 + ordinal, 1010 + ordinal)

    capability = mint_trace_acquisition_capability(
        acquisition_input.acquisition_source,
        adapter_namespace="tests.discovery_physical_fact_persistence",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    request = XICRequest(
        mz=500.0 + ordinal,
        rt_min=request_rt[0],
        rt_max=request_rt[1],
        ppm_tol=20.0,
    )
    return acquire_trace_batch(capability, (request,), Adapter()).traces[0]


def _population(spec, acquisition_input):
    terminals = ("available", "unavailable", "failed")
    manifest = []
    records = []
    for index, terminal in enumerate(terminals):
        acquired = _acquired_trace(
            acquisition_input,
            terminal=terminal,
            ordinal=index,
        )
        item = _work(
            index,
            acquisition_input,
            terminal=terminal,
            acquired_trace=acquired,
        )
        manifest.append(item)
        records.append(
            _record(
                item,
                terminal=terminal,
                profile=spec.signal_processing_profile,
                acquired_trace=acquired,
            )
        )
    return tuple(manifest), tuple(records)


def _physical_index_jobs(tmp_path: Path, *, count: int):
    from xic_extractor.untargeted_execution.alignment import _PhysicalIndexJob

    raw_paths = tuple(tmp_path / f"sample_{index}.raw" for index in range(count))
    for raw_path in raw_paths:
        raw_path.write_bytes(f"acquisition {raw_path.stem}".encode())
    spec = compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )
    spool_dir = tmp_path / "spools"
    spool_dir.mkdir()
    jobs = []
    expected: dict[str, tuple[str, ...]] = {}
    for acquisition_input in spec.acquisition_inputs:
        manifest, records = _population(spec, acquisition_input)
        sidecar = (
            tmp_path
            / acquisition_input.sample_stem
            / "discovery_physical_facts.json"
        )
        write_discovery_physical_fact_sidecar(
            sidecar,
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
        candidate_ids = tuple(record.candidate_id for record in records)
        expected[acquisition_input.sample_stem] = candidate_ids
        jobs.append(
            _PhysicalIndexJob(
                path=sidecar,
                expected_run_spec=spec,
                expected_acquisition_input=acquisition_input,
                candidate_ids=frozenset(candidate_ids),
                spool_dir=spool_dir,
            )
        )
    return tuple(jobs), expected, spool_dir


def test_discovery_sidecar_roundtrips_complete_independent_work_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    path = tmp_path / "out" / "discovery_physical_facts.json"

    assert (
        write_discovery_physical_fact_sidecar(
            path,
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
        == path
    )
    rehydrated = read_discovery_physical_fact_sidecar(
        path,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )

    from xic_extractor.peak_detection import physical_peak_codec as owner

    def unexpected_recompute(*args, **kwargs):
        raise AssertionError("evidence retrieval must not prepare or evaluate")

    monkeypatch.setattr(owner, "prepare_physical_signal", unexpected_recompute)
    monkeypatch.setattr(owner, "evaluate_physical_peak", unexpected_recompute)
    retained = read_discovery_physical_fact_records(
        path, expected_run_spec=spec, expected_acquisition_input=acquisition_input
    )
    for source, restored in zip(records, retained, strict=True):
        assert (
            restored.evaluation.retained_prepared_signal
            == source.evaluation.retained_prepared_signal
        )
        assert restored.rt == source.rt
        assert restored.raw == source.raw
        assert restored.evaluation.facts == source.evaluation.facts

    assert tuple(record.work_item for record in rehydrated) == manifest
    assert (
        read_discovery_physical_fact_records(
            path,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
        )
        == rehydrated
    )
    assert tuple(record.candidate_id for record in rehydrated) == tuple(
        item.candidate_id for item in manifest
    )
    assert tuple(record.evaluation.state for record in rehydrated) == (
        "available",
        "unavailable",
        "failed",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.pop("sample"),
        lambda value: value["sample"].__setitem__("input_ordinal", 0.0),
        lambda value: value["sample"].__setitem__("input_ordinal", False),
        lambda value: value["work_manifest"][0].pop("product_mz"),
        lambda value: value["work_manifest"][0].__setitem__("candidate_id", "swapped"),
        lambda value: value["work_manifest"][0].__setitem__(
            "selection_subject_id", "swapped"
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "acquisition_source_id", "cross-acquisition"
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "trace_coordinate_id", "xic.trace-coordinate:" + "0" * 64
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "acquired_trace_content_id",
            "xic.acquired-trace-content:" + "0" * 64,
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "xic_mz", float(999.0).hex()
        ),
        lambda value: value["records"][0].__setitem__(
            "candidate_id", value["records"][1]["candidate_id"]
        ),
        lambda value: value["records"][0].__setitem__(
            "selection_subject_id", value["records"][1]["selection_subject_id"]
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "acquisition_source_id", "cross-acquisition"
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"][
            "request"
        ].__setitem__("mz", float(999.0).hex()),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "trace_coordinate_id", "xic.trace-coordinate:" + "0" * 64
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "trace_point_count", 1
        ),
        lambda value: value["records"][0]["acquired_trace"]["trace"][
            "intensity"
        ].__setitem__(0, float(999.0).hex()),
        lambda value: value["records"][0]["acquired_trace"].__setitem__(
            "acquired_trace_content_id",
            "xic.acquired-trace-content:" + "0" * 64,
        ),
        lambda value: value["records"][0]["physical_evaluation"]["trace"][
            "raw"
        ].__setitem__(0, float(999.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "question"
        ].__setitem__("query_rt_min", float(1.1).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "answer"
        ].__setitem__("physical_answer_id", "physical-answer:" + "0" * 64),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "facts"
        ].__setitem__("area_counts_seconds", float(1.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "integration"
        ].__setitem__("height_raw", float(1.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"][
            "evaluation"
        ].__setitem__("state", "failed"),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "prepared"
        ].__setitem__("prepared_signal_id", "prepared-signal:" + "0" * 64),
        lambda value: value["records"][0]["physical_evaluation"]["profile"].__setitem__(
            "profile_id", "signal-profile:" + "0" * 64
        ),
        lambda value: value["run_spec"].__setitem__(
            "run_spec_id", "processing-run:" + "0" * 64
        ),
    ],
)
def test_discovery_sidecar_rejects_tamper_even_after_json_reseal(
    tmp_path: Path,
    mutate,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    value = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    mutate(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["records"].pop(),
        lambda value: (value["work_manifest"].pop(), value["records"].pop()),
        lambda value: value["records"].reverse(),
        lambda value: value["work_manifest"].reverse(),
        lambda value: value["records"].__setitem__(1, value["records"][0]),
        lambda value: value["work_manifest"].__setitem__(1, value["work_manifest"][0]),
    ],
)
def test_discovery_sidecar_rejects_deleted_duplicate_or_reordered_work(
    tmp_path: Path,
    mutate,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    value = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    mutate(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


def test_discovery_sidecar_rejects_duplicate_json_field(tmp_path: Path) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    ).decode("utf-8")
    duplicate = payload.replace(
        '{"schema_version":"discovery_physical_fact_sidecar",',
        '{"schema_version":"discovery_physical_fact_sidecar",'
        '"schema_version":"discovery_physical_fact_sidecar",',
        1,
    )

    with pytest.raises(ValueError, match="duplicate"):
        deserialize_discovery_physical_fact_sidecar(
            duplicate,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


def test_discovery_sidecar_rejects_fully_resealed_different_run(tmp_path: Path) -> None:
    _, expected_spec, expected_input = _spec(tmp_path / "expected")
    expected_manifest, _ = _population(expected_spec, expected_input)
    _, other_spec, other_input = _spec(tmp_path / "other", profile_points=5)
    other_manifest, other_records = _population(other_spec, other_input)
    resealed = serialize_discovery_physical_fact_sidecar(
        run_spec=other_spec,
        acquisition_input=other_input,
        work_manifest=other_manifest,
        records=other_records,
    )

    with pytest.raises(ValueError, match="expected run"):
        deserialize_discovery_physical_fact_sidecar(
            resealed,
            expected_run_spec=expected_spec,
            expected_acquisition_input=expected_input,
            expected_work_manifest=expected_manifest,
        )


def test_discovery_sidecar_rejects_fully_resealed_replacement_trace_record(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    original = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    changed_trace = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=0,
    )
    changed_trace.trace.intensity[0] = 999.0
    changed_work = replace(
        manifest[0],
        acquired_trace_content_id=acquired_trace_content_id(changed_trace),
    )
    changed_record = _record(
        changed_work,
        terminal="available",
        profile=spec.signal_processing_profile,
        acquired_trace=changed_trace,
    )
    resealed = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=(changed_work, *manifest[1:]),
            records=(changed_record, *records[1:]),
        )
    )
    original["records"][0] = resealed["records"][0]

    with pytest.raises(ValueError):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(original, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


def test_discovery_sidecar_writer_detects_a_temp_file_that_does_not_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back leg must actually compare the landed bytes.

    Nothing else pins it: the tamper tests all mutate the published file and go
    through the reader, so the writer's own verification could be removed
    without reddening them.
    """

    import io

    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    path = tmp_path / "out" / "discovery_physical_facts.json"
    temporary = path.with_name(f"{path.name}.tmp")
    original_open = Path.open

    def corrupting_open(candidate: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        handle = original_open(candidate, *args, **kwargs)  # type: ignore[arg-type]
        if candidate == temporary and "r" in str(mode):
            with handle:
                landed = handle.read()
            return io.BytesIO(landed[:-1])
        return handle

    monkeypatch.setattr(Path, "open", corrupting_open)

    with pytest.raises(ValueError, match="verification changed bytes"):
        write_discovery_physical_fact_sidecar(
            path,
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )

    assert not path.exists()
    assert not temporary.exists()


def test_discovery_sidecar_writer_publishes_exact_serializer_bytes(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    path = tmp_path / "out" / "discovery_physical_facts.json"

    write_discovery_physical_fact_sidecar(
        path,
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )

    expected = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    assert path.read_bytes() == expected
    assert b"\r" not in path.read_bytes()
    assert not path.with_name(f"{path.name}.tmp").exists()


def test_read_discovery_physical_fact_records_rejects_profile_mismatch(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    payload_str = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    sidecar_path = tmp_path / "normal.json"
    sidecar_path.write_bytes(payload_str)

    # Mismatched expected run spec profile
    other_profile = replace(
        spec.signal_processing_profile,
        window_points=spec.signal_processing_profile.window_points + 2,
    )
    other_spec = replace(spec, signal_processing_profile=other_profile)
    with pytest.raises(ValueError, match="run_spec_hash does not match expected run"):
        read_discovery_physical_fact_records(
            sidecar_path,
            expected_run_spec=other_spec,
            expected_acquisition_input=acquisition_input,
        )

    # Tampered profile in sidecar record
    data = json.loads(payload_str)
    data["records"][0]["physical_evaluation"]["profile"]["profile_id"] = (
        "different-profile-id"
    )
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(
        ValueError, match="Discovery physical sidecar crosses profile identity"
    ):
        read_discovery_physical_fact_records(
            tampered_path,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
        )


def _physical_source_payload(
    *,
    manifest: list[dict] | None = None,
    records: list[dict] | None = None,
) -> bytes:
    return json.dumps(
        {
            "schema_version": "discovery_physical_fact_sidecar",
            "run_spec": {"name": "規格", "nested": [1, {"escaped": "a\\\"b"}]},
            "sample": {"sample_stem": "樣本-α"},
            "work_manifest": manifest
            if manifest is not None
            else [{"ordinal": index, "label": f"工作-{index}"} for index in range(5)],
            "records": records
            if records is not None
            else [
                {
                    "ordinal": index,
                    "text": f"訊號-{index}",
                    "nested": {"arrays": [[index], ["x,y", "[z]"]]},
                }
                for index in range(5)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def test_physical_fact_source_indexes_existing_canonical_sidecar(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    expected = json.loads(payload)
    path = tmp_path / "canonical.json"
    path.write_bytes(payload)

    source = PhysicalFactSource(path)

    assert source.header == {
        key: expected[key] for key in ("schema_version", "run_spec", "sample")
    }
    assert source.read_record(2) == expected["records"][2]


def test_physical_fact_source_recovers_unicode_nested_items_and_random_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.discovery import physical_fact_source as source_owner

    monkeypatch.setattr(source_owner, "_CHUNK_BYTES", 17)
    path = tmp_path / "indexed.json"
    path.write_bytes(_physical_source_payload())

    with PhysicalFactSource(path) as source:
        assert source.header == {
            "schema_version": "discovery_physical_fact_sidecar",
            "run_spec": {"name": "規格", "nested": [1, {"escaped": 'a\\"b'}]},
            "sample": {"sample_stem": "樣本-α"},
        }
        assert source.read_record(4) == {
            "ordinal": 4,
            "text": "訊號-4",
            "nested": {"arrays": [[4], ["x,y", "[z]"]]},
        }
        assert source.read_record(1)["text"] == "訊號-1"

    with pytest.raises(ValueError, match="closed"):
        source.read_record(0)


def test_physical_fact_source_visits_each_item_once_and_iterates_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "visited.json"
    path.write_bytes(_physical_source_payload())
    manifest_visits: list[tuple[int, int]] = []
    record_visits: list[tuple[int, str]] = []

    with PhysicalFactSource(
        path,
        manifest_visitor=lambda index, item: manifest_visits.append(
            (index, item["ordinal"])
        ),
        record_visitor=lambda index, item: record_visits.append((index, item["text"])),
    ):
        pass

    assert manifest_visits == [(index, index) for index in range(5)]
    assert record_visits == [(index, f"訊號-{index}") for index in range(5)]


def test_physical_fact_source_retains_selected_records_without_hiding_visits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "filtered.json"
    path.write_bytes(_physical_source_payload())
    record_visits: list[int] = []

    with PhysicalFactSource(
        path,
        record_visitor=lambda _index, item: record_visits.append(item["ordinal"]),
        retain_record=lambda _index, item: item["ordinal"] % 2 == 0,
    ) as source:
        assert source.record_count == 5
        assert source.read_record(4)["ordinal"] == 4
        with pytest.raises(LookupError, match="records index 1 was not retained"):
            source.read_record(1)

    assert record_visits == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"sample":{},"work_manifest":[],"records":[]',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"sample":{},"work_manifest":[],"records":[]}',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{"x":1,"x":2},"sample":{},'
        b'"work_manifest":[],"records":[]}',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"sample":{},"work_manifest":[{"x":1,"x":2}],'
        b'"records":[{}]}',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"sample":{},"work_manifest":[],"records":[],"extra":1}',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"work_manifest":[],"records":[]}',
        b'{"schema_version":"discovery_physical_fact_sidecar",'
        b'"run_spec":{},"sample":{},"work_manifest":[{}],"records":[]}',
    ),
    ids=(
        "truncated",
        "duplicate-root",
        "duplicate-header-object",
        "duplicate-array-object",
        "unexpected-root-key",
        "missing-root-key",
        "count-mismatch",
    ),
)
def test_physical_fact_source_rejects_malformed_duplicate_or_incomplete_sidecar(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)

    with pytest.raises(ValueError):
        PhysicalFactSource(path)


def test_physical_fact_source_rejects_file_changed_after_index(tmp_path: Path) -> None:
    path = tmp_path / "changed.json"
    payload = _physical_source_payload()
    path.write_bytes(payload)
    source = PhysicalFactSource(path)

    path.write_bytes(payload + b" ")

    with pytest.raises(ValueError, match="changed after indexing"):
        source.read_record(0)


def test_physical_fact_source_never_requests_an_unbounded_or_whole_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.discovery import physical_fact_source as source_owner

    path = tmp_path / "bounded.json"
    path.write_bytes(
        _physical_source_payload(
            manifest=[{"ordinal": index, "padding": "m" * 80} for index in range(4)],
            records=[{"ordinal": index, "padding": "r" * 500} for index in range(4)],
        )
    )
    monkeypatch.setattr(source_owner, "_CHUNK_BYTES", 31)
    original_open = Path.open
    requested_sizes: list[int] = []

    class TrackingReader:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc_info: object) -> None:
            self._handle.close()

        def read(self, size: int = -1):
            requested_sizes.append(size)
            assert 0 <= size <= 31
            return self._handle.read(size)

        def readinto(self, buffer) -> int:
            requested_sizes.append(len(buffer))
            assert len(buffer) <= 31
            return self._handle.readinto(buffer)

        def __getattr__(self, name: str):
            return getattr(self._handle, name)

    def tracking_open(candidate: Path, *args: object, **kwargs: object):
        handle = original_open(candidate, *args, **kwargs)  # type: ignore[arg-type]
        mode = args[0] if args else kwargs.get("mode", "r")
        if candidate == path and "r" in str(mode):
            return TrackingReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracking_open)

    source = PhysicalFactSource(path)
    assert source.read_record(3)["ordinal"] == 3
    assert requested_sizes and max(requested_sizes) == 31
    assert path.stat().st_size > max(requested_sizes)


@pytest.mark.parametrize(
    ("backend", "workers"),
    (("thread", 1), ("thread", 2), ("process", 2)),
)
def test_parallel_physical_indexing_is_deterministic_and_transfers_spool_ownership(
    tmp_path: Path,
    backend: str,
    workers: int,
) -> None:
    from xic_extractor.untargeted_execution.alignment import (
        _index_discovery_physical_sources,
    )

    jobs, expected, spool_dir = _physical_index_jobs(tmp_path, count=3)
    result = _index_discovery_physical_sources(
        jobs,
        raw_workers=workers,
        raw_worker_backend=backend,
    )
    try:
        assert tuple(result.sources) == tuple(expected)
        observed = {
            stem: tuple(
                record.candidate_id
                for record in source.load_candidates(expected[stem]).values()
            )
            for stem, source in result.sources.items()
        }
        assert observed == expected
        if backend == "process":
            assert result.worker_pids
            assert all(pid != os.getpid() for pid in result.worker_pids)
        else:
            assert set(result.worker_pids) == {os.getpid()}
    finally:
        for source in result.sources.values():
            source.close()

    assert not tuple(spool_dir.iterdir())


def test_threaded_physical_indexing_bounds_active_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.untargeted_execution import alignment as owner

    jobs, _expected, _spool_dir = _physical_index_jobs(tmp_path, count=4)
    original = owner._index_discovery_physical_source
    lock = threading.Lock()
    active = 0
    peak = 0

    def observed(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return original(job)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(owner, "_index_discovery_physical_source", observed)
    result = owner._index_discovery_physical_sources(
        jobs,
        raw_workers=2,
        raw_worker_backend="thread",
    )
    try:
        assert peak == 2
    finally:
        for source in result.sources.values():
            source.close()


def test_parallel_physical_indexing_cleans_adopted_spools_after_failure(
    tmp_path: Path,
) -> None:
    from dataclasses import replace as dataclass_replace

    from xic_extractor.untargeted_execution.alignment import (
        _index_discovery_physical_sources,
    )

    jobs, _expected, spool_dir = _physical_index_jobs(tmp_path, count=3)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    failing_jobs = (jobs[0], dataclass_replace(jobs[1], path=invalid), jobs[2])

    with pytest.raises(ValueError):
        _index_discovery_physical_sources(
            failing_jobs,
            raw_workers=2,
            raw_worker_backend="process",
        )

    assert not tuple(spool_dir.iterdir())
