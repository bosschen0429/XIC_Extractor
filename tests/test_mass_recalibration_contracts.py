"""Mass policy persistence, rollback, and per-sample provenance contracts."""

import json

import pytest

from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.presets import apply_to_discovery, load_preset
from xic_extractor.presets.serialization import save_preset


@pytest.mark.parametrize("policy", tuple(MassRecalibration))
def test_preset_rollback_roundtrip(tmp_path, policy):
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


@pytest.mark.parametrize("outcome", ("applied", "not_applicable"))
def test_discovery_file_preserves_each_raw_result(tmp_path, outcome):
    from dataclasses import replace

    from tests.discovery_store_fixtures import single_peak_sample, write_discovery
    from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
    from xic_extractor.discovery.store import read_discovery_batch

    results = {
        sample: MassRecalibrationResult(
            outcome,
            reason="negative_polarity" if outcome == "not_applicable" else None,
            baseline_lm=baseline if outcome == "applied" else None,
            n_anchor=60 if outcome == "applied" else 0,
            policy=MassRecalibration.EASY_IC_RESTORE,
        )
        for sample, baseline in [("A", -0.4), ("B", 0.6)]
    }
    batch = read_discovery_batch(
        write_discovery(
            tmp_path,
            [
                replace(
                    single_peak_sample(tmp_path, sample),
                    mass_recalibration_result=result,
                )
                for sample, result in results.items()
            ],
        )
    )
    assert {
        stem: sample.mass_recalibration_result for stem, sample in batch.samples.items()
    } == results


def test_alignment_metadata_keeps_two_samples_probe_results(tmp_path, monkeypatch):
    from dataclasses import replace

    from tests.alignment_pipeline_helpers import candidate_id
    from tests.cid_nl_raw_fixture import _settings
    from tests.discovery_store_fixtures import single_peak_sample, write_discovery
    from tests.execution_fixtures import in_process_execution
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.applications.untargeted import application
    from xic_extractor.applications.untargeted.request import UntargetedRequest
    from xic_extractor.configuration.execution_profile import (
        UntargetedExecutionPolicy,
    )
    from xic_extractor.configuration.mass_recalibration import (
        MassRecalibrationResult,
        serialize_mass_recalibration_result,
    )

    results = {
        stem: MassRecalibrationResult(
            "applied",
            baseline_lm=a,
            n_anchor=60,
            policy=MassRecalibration.EASY_IC_RESTORE,
        )
        for stem, a in (("S1", -0.4), ("S2", 0.7))
    }
    index = write_discovery(
        tmp_path / "batch",
        [
            replace(
                single_peak_sample(tmp_path, stem, peak_id=candidate_id(stem)),
                mass_recalibration_result=result,
            )
            for stem, result in results.items()
        ],
    )
    expected = {
        stem: json.loads(serialize_mass_recalibration_result(result))
        for stem, result in results.items()
    }
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
            execution=UntargetedExecutionPolicy(profile=in_process_execution()),
            discovery=index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            discovery_settings=_settings(),
        )
    )
    assert json.loads(metadata["mass_recalibration_by_sample"]) == expected
    assert metadata_paths == [tmp_path / "out" / "alignment_run_metadata.json"]


def test_standalone_discovery_publishes_readable_probe_result(tmp_path):
    from tests.discovery_pipeline_fixtures import (
        _FakeRawHandle,
        _run_discovery,
        _settings,
    )
    from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
    from xic_extractor.discovery.store import read_discovery_batch

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
    restored = read_discovery_batch(output.store)
    assert (
        restored.samples["sample"].mass_recalibration_result
        == raw.mass_recalibration_result
    )
