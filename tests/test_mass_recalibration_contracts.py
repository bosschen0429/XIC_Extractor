"""Mass policy persistence, rollback, and per-sample provenance contracts."""

import json
from dataclasses import replace

import pytest

from tests.alignment_pipeline_helpers import physical_facts_fingerprint_beside
from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.discovery.models import discovery_settings_identity_fields
from xic_extractor.presets import apply_to_discovery, load_preset
from xic_extractor.presets.serialization import save_preset
from xic_extractor.processing_run_spec import (
    CidNlDiscoveryMethodSpec,
    canonical_cid_nl_method_bytes,
)


@pytest.mark.parametrize("policy", tuple(MassRecalibration))
def test_preset_rollback_roundtrip_and_identity(tmp_path, policy):
    path = tmp_path / "rollback.toml"
    saved = save_preset(
        load_preset("dna_dr"),
        path,
        discovery_overrides={"mass_recalibration": policy.value},
    )
    settings = apply_to_discovery(saved)
    assert settings.mass_recalibration is policy
    assert apply_to_discovery(load_preset(path)).mass_recalibration is policy
    content = path.read_text(encoding="utf-8")
    if policy is MassRecalibration.NONE:
        assert 'mass_recalibration = "none"' in content
    elif policy is MassRecalibration.EASY_IC_RESTORE:
        assert "mass_recalibration" not in content
    else:
        assert 'mass_recalibration = "easy_ic_probe"' in content
    fields = discovery_settings_identity_fields(settings)
    method = json.loads(
        canonical_cid_nl_method_bytes(CidNlDiscoveryMethodSpec(settings))
    )
    if policy is MassRecalibration.NONE:
        assert "mass_recalibration" not in fields
        assert "mass_recalibration" not in method["settings"]
    else:
        assert fields["mass_recalibration"] == policy.value
        assert method["settings"]["mass_recalibration"] == policy.value


@pytest.mark.parametrize("outcome", ("applied", "not_applicable"))
def test_receipt_roundtrip_preserves_each_raw_result_and_legacy_none(tmp_path, outcome):
    from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
    from xic_extractor.discovery.batch_contract import (
        discovery_receipt_from_index_fields,
        discovery_receipt_index_fields,
        mint_discovery_candidate_universe_receipt,
    )
    from xic_extractor.processing_identity import (
        build_local_acquisition_source_identity,
    )

    csv = tmp_path / "candidates.csv"
    csv.write_text("candidate_id\n", encoding="utf-8")
    for sample, baseline in [("A", -0.4), ("B", 0.6)]:
        raw = tmp_path / (sample + ".raw")
        raw.write_bytes(sample.encode())
        result = MassRecalibrationResult(
            outcome,
            reason="negative_polarity" if outcome == "not_applicable" else None,
            baseline_lm=baseline if outcome == "applied" else None,
            n_anchor=60 if outcome == "applied" else 0,
            policy=MassRecalibration.EASY_IC_RESTORE,
        )
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample,
            acquisition_source=build_local_acquisition_source_identity(
                raw, acquisition_format="thermo_raw", observation_namespace="test"
            ),
            candidate_csv=csv,
            physical_facts=physical_facts_fingerprint_beside(csv),
            candidates=(),
            mass_recalibration_result=result,
        )
        fields = discovery_receipt_index_fields(receipt)
        restored = discovery_receipt_from_index_fields(
            fields, sample_stem=sample, candidate_count=0
        )
        assert restored.mass_recalibration_result == result
        fields.pop("mass_recalibration")
        legacy = discovery_receipt_from_index_fields(
            fields, sample_stem=sample, candidate_count=0
        )
        assert legacy.mass_recalibration_result.policy is MassRecalibration.NONE
        assert legacy.mass_recalibration_result.outcome == "none"


@pytest.mark.parametrize("policy", tuple(MassRecalibration))
def test_method_spec_roundtrip_and_rejects_explicit_none(tmp_path, policy):
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )
    from xic_extractor.processing_run_spec import (
        deserialize_processing_run_spec,
        serialize_processing_run_spec,
    )

    raw = tmp_path / "A.raw"
    raw.write_bytes(b"identity fixture")
    settings = replace(
        apply_to_discovery(load_preset("dna_dr")), mass_recalibration=policy
    )
    spec = compile_discovery_processing_run_spec((raw,), discovery_settings=settings)
    payload = serialize_processing_run_spec(spec)
    assert deserialize_processing_run_spec(payload) == spec
    if policy is MassRecalibration.NONE:
        content = json.loads(payload)
        content["content"]["discovery_method"]["settings"]["mass_recalibration"] = (
            "none"
        )
        with pytest.raises(ValueError, match="none is recorded by omission"):
            deserialize_processing_run_spec(json.dumps(content))


def test_none_request_payload_preserves_legacy_fields():
    from tests.alignment_pipeline_helpers import peak_config
    from xic_extractor.configuration.scientific_settings import (
        SCIENTIFIC_SETTING_FIELDS,
        scientific_settings_payload,
    )

    config = replace(peak_config(), mass_recalibration=MassRecalibration.NONE)
    assert scientific_settings_payload(config) == {
        key: getattr(config, key) for key in SCIENTIFIC_SETTING_FIELDS
    }
    assert (
        scientific_settings_payload(
            replace(config, mass_recalibration=MassRecalibration.EASY_IC_PROBE)
        )["mass_recalibration"]
        == MassRecalibration.EASY_IC_PROBE.value
    )


def test_fact_cache_policy_namespace_separates_numeric_facts(tmp_path):
    from xic_extractor.untargeted_execution.acquisition_cache import _FactCache

    legacy = _FactCache(tmp_path, "same-source")
    legacy.write("xic", b"same-query", b"vendor masses")
    probe = _FactCache(tmp_path, "same-source", MassRecalibration.EASY_IC_PROBE)
    assert probe.read("xic", b"same-query") is None
    probe.write("xic", b"same-query", b"corrected masses")
    assert (
        _FactCache(tmp_path, "same-source", MassRecalibration.NONE).read(
            "xic", b"same-query"
        )
        == b"vendor masses"
    )
    assert probe.read("xic", b"same-query") == b"corrected masses"
    restored = _FactCache(tmp_path, "same-source", MassRecalibration.EASY_IC_RESTORE)
    assert restored.read("xic", b"same-query") is None
    restored.write("xic", b"same-query", b"baseline masses")
    assert probe.read("xic", b"same-query") == b"corrected masses"
    assert restored.read("xic", b"same-query") == b"baseline masses"


@pytest.mark.parametrize(
    "policy", (MassRecalibration.EASY_IC_PROBE, MassRecalibration.EASY_IC_RESTORE)
)
@pytest.mark.parametrize("old_ms2_kind", ("ms2", "ms2_parent_centroid"))
def test_corrected_ms2_cache_reacquires_legacy_precursors_but_reuses_xic(
    tmp_path, policy, old_ms2_kind
):
    from xic_extractor.untargeted_execution.acquisition_cache import _FactCache

    legacy = _FactCache(tmp_path, "same-source", policy)
    # Populate the previous producer's persisted MS2 directory.
    legacy._ms2_kind = old_ms2_kind
    legacy.write("ms2", b"same-query", b"uncorrected selected precursor")
    legacy.write("xic", b"same-query", b"corrected MS1 signal")

    current = _FactCache(tmp_path, "same-source", policy)
    assert current.read("ms2", b"same-query") is None
    assert current.read("xic", b"same-query") == b"corrected MS1 signal"
    current.write("ms2", b"same-query", b"corrected selected precursor")
    reopened = _FactCache(tmp_path, "same-source", policy)
    assert reopened.read("ms2", b"same-query") == b"corrected selected precursor"


def test_alignment_metadata_keeps_two_samples_probe_results(tmp_path, monkeypatch):
    import csv

    from tests.alignment_pipeline_helpers import peak_config, write_batch
    from tests.cid_nl_raw_fixture import _settings
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.applications.untargeted import application
    from xic_extractor.applications.untargeted.request import UntargetedRequest
    from xic_extractor.configuration.mass_recalibration import (
        MassRecalibrationResult,
        serialize_mass_recalibration_result,
    )

    index = write_batch(tmp_path, ("S1", "S2"))
    with index.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = [*reader.fieldnames, "mass_recalibration"]
        rows = list(reader)
    expected = {}
    for row, a in zip(rows, (-0.4, 0.7), strict=True):
        result = MassRecalibrationResult(
            "applied",
            baseline_lm=a,
            n_anchor=60,
            policy=MassRecalibration.EASY_IC_RESTORE,
        )
        row["mass_recalibration"] = serialize_mass_recalibration_result(result)
        expected[row["sample_stem"]] = json.loads(row["mass_recalibration"])
    with index.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(
        application, "complete_untargeted_alignment", lambda **kwargs: object()
    )
    metadata_paths = []

    def capture_publication(completed, **kwargs):
        metadata_paths.append(kwargs["outputs"].run_metadata_json)
        return kwargs["metadata"]

    monkeypatch.setattr(application, "publish_untargeted_outputs", capture_publication)
    metadata = application.run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=peak_config(),
            discovery_settings=_settings(),
        )
    )
    assert json.loads(metadata["mass_recalibration_by_sample"]) == expected
    assert metadata_paths == [tmp_path / "out" / "alignment_run_metadata.json"]


def test_standalone_discovery_publishes_readable_probe_receipt(tmp_path):
    from tests.test_discovery_pipeline import _FakeRawHandle, _run_discovery, _settings
    from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
    from xic_extractor.discovery.csv_reader import read_discovery_batch_index

    raw = _FakeRawHandle(events=[])
    raw.mass_recalibration_result = MassRecalibrationResult(
        "unavailable",
        "no_ms1_lock_mass_anchor",
        policy=MassRecalibration.EASY_IC_RESTORE,
    )
    path = tmp_path / "sample.raw"
    output = _run_discovery(
        path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path,
        raw_opener=lambda *args: raw,
    )
    restored = read_discovery_batch_index(output.batch_index_csv)
    assert (
        restored.candidate_receipts["sample"].mass_recalibration_result
        == raw.mass_recalibration_result
    )
