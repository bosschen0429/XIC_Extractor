from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_workbench.config_io import write_settings, write_targets
from xic_workbench.jobs import AnalysisJobs


def _record_receipts(monkeypatch) -> list[Path]:
    """Expose each run's private frozen-input directory to the assertions."""
    from xic_extractor.configuration import loader

    receipts: list[Path] = []
    prepare = loader.prepare_targeted_config

    def record(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        receipts.append(prepared.input_config_dir)
        return prepared

    monkeypatch.setattr(loader, "prepare_targeted_config", record)
    return receipts


def test_targeted_dispatch_freezes_inputs_until_completion(tmp_path, monkeypatch):
    """A queued run must not read a later batch/method edit or publish its receipt."""
    from xic_workbench import targeted

    raw = tmp_path / "raw"
    dll = tmp_path / "dll"
    raw.mkdir()
    dll.mkdir()
    config_dir = tmp_path / "config"
    settings = {
        "data_dir": str(raw),
        "dll_dir": str(dll),
        "signal_processing_window_points": "17",
        "ms2_precursor_tol_da": "0.37",
        "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
    }
    targets = [
        dict(
            label=label,
            mz=mz,
            rt_min=1,
            rt_max=3,
            ppm_tol=5,
            neutral_loss_da=116,
            nl_ppm_warn=10,
            nl_ppm_max=20,
            is_istd=False,
            istd_pair="",
        )
        for label, mz in (("A", 250), ("B", 270))
    ]
    submitted = []
    captured = []
    receipts = _record_receipts(monkeypatch)

    class Service:
        def execute_application(self, request):
            captured.append(request)
            receipt = receipts[0]
            assert (receipt / "settings.csv").read_bytes() == original_settings
            assert (receipt / "targets.csv").read_bytes() == original_targets
            assert receipts[0].is_dir()
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        targeted, "build_canonical_targeted_application_service", lambda _: Service()
    )
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        write_settings(settings, config_dir=config_dir)
        write_targets(targets, config_dir=config_dir)
        jobs.start("targeted", {"settings": settings, "targets": targets})
        original_settings = (config_dir / "settings.csv").read_bytes()
        original_targets = (config_dir / "targets.csv").read_bytes()
        write_settings(
            {**settings, "ms2_precursor_tol_da": "0.8"}, config_dir=config_dir
        )
        write_targets(
            [{**target, "rt_max": "8"} for target in targets], config_dir=config_dir
        )
        later_settings = (config_dir / "settings.csv").read_bytes()
        callback, *args = submitted[0]
        callback(*args)
        assert jobs.state()["job"]["status"] == "succeeded"
        assert (config_dir / "settings.csv").read_bytes() == later_settings
        request = captured[0]
        assert request.config.ms2_precursor_tol_da == 0.37
        assert request.config.signal_processing_window_points == 17
        assert [(t.label, t.rt_max) for t in request.targets] == [("A", 3), ("B", 3)]
        receipt_dir = Path(receipts[0])
        assert not receipt_dir.exists()
        assert request.config.output_csv.parent == tmp_path / "output"
    finally:
        jobs.close()


def _snapshot_workspace(tmp_path, *, order_source=None):
    raw = tmp_path / "raw"
    dll = tmp_path / "dll"
    raw.mkdir()
    dll.mkdir()
    config_dir = tmp_path / "config"
    settings = {
        "data_dir": str(raw),
        "dll_dir": str(dll),
        "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
    }
    if order_source is not None:
        settings["injection_order_source"] = str(order_source)
    targets = [
        dict(
            label="Target",
            mz=250,
            rt_min=1,
            rt_max=3,
            ppm_tol=5,
            neutral_loss_da=116,
            nl_ppm_warn=10,
            nl_ppm_max=20,
            is_istd=False,
            istd_pair="",
        )
    ]
    write_settings(settings, config_dir=config_dir)
    write_targets(targets, config_dir=config_dir)
    return config_dir, settings, targets


def test_queued_targeted_metadata_membership_and_order_are_frozen(
    tmp_path, monkeypatch
):
    import csv

    from xic_extractor.analysis_set import select_analysis_set
    from xic_extractor.extraction import targeted_inputs
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
    from xic_workbench import targeted

    order_source = tmp_path / "samples.csv"

    def write_metadata(*, changed):
        with order_source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SAMPLE_METADATA_COLUMNS)
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "schema_version": "sample_metadata",
                        "sample_name": "A",
                        "raw_stem": "A",
                        "injection_order": "9" if changed else "1",
                        "sample_role": "blank" if changed else "study_sample",
                        "excluded": "false",
                    },
                    {
                        "schema_version": "sample_metadata",
                        "sample_name": "B",
                        "raw_stem": "B",
                        "injection_order": "8" if changed else "2",
                        "sample_role": "study_sample" if changed else "blank",
                        "excluded": "false",
                    },
                ]
            )

    write_metadata(changed=False)
    config_dir, settings, targets = _snapshot_workspace(
        tmp_path, order_source=order_source
    )
    raw_paths = [tmp_path / "raw" / f"{stem}.raw" for stem in ("A", "B")]
    for path in raw_paths:
        path.write_bytes(b"external RAW test stand-in")
    # Only the external RAW header read is replaced; metadata and membership
    # decisions still cross their real owners.
    monkeypatch.setattr(
        targeted_inputs,
        "read_analysis_set",
        lambda _root, _dll, metadata: select_analysis_set(
            (), {}, metadata, unread_paths=[str(path) for path in raw_paths]
        ),
    )
    captured = []
    receipts = _record_receipts(monkeypatch)
    submitted = []

    class Service:
        def execute_application(self, request):
            selected = targeted_inputs.resolve_targeted_raw_paths(request.config)
            inputs = targeted_inputs.resolve_targeted_inputs(
                request.config,
                raw_paths=selected,
                injection_order=None,
            )
            assert request.config.injection_order_source.read_bytes() == original_order
            captured.append((request, selected, inputs))
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        targeted, "build_canonical_targeted_application_service", lambda _: Service()
    )
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        jobs.start("targeted", {"settings": settings, "targets": targets})
        original_order = order_source.read_bytes()
        write_metadata(changed=True)
        callback, *args = submitted[0]
        callback(*args)
        assert jobs.state()["job"]["status"] == "succeeded"
        request, selected, inputs = captured[0]
        assert [path.stem for path in selected] == ["A"]
        assert inputs.injection_order == {"A": 1, "B": 2}
        assert not request.config.injection_order_source.exists()
        assert request.config.injection_order_source != order_source
        assert order_source.read_bytes() != original_order
        assert not receipts[0].exists()
    finally:
        jobs.close()


@pytest.mark.parametrize("mutation", ["add", "remove"])
def test_queued_targeted_changed_raw_file_set_requires_resubmission(
    tmp_path, monkeypatch, mutation
):
    from xic_workbench import targeted

    config_dir, settings, targets = _snapshot_workspace(tmp_path)
    raw_path = tmp_path / "raw" / "changed.raw"
    if mutation == "remove":
        raw_path.write_bytes(b"original acquisition")
    submitted = []
    monkeypatch.setattr(
        targeted,
        "build_canonical_targeted_application_service",
        lambda _: (_ for _ in ()).throw(AssertionError("analysis must not start")),
    )
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        jobs.start("targeted", {"settings": settings, "targets": targets})
        if mutation == "add":
            raw_path.write_bytes(b"new acquisition")
        else:
            raw_path.unlink()
        callback, *args = submitted[0]
        callback(*args)
        state = jobs.state()["job"]
        assert state["status"] == "failed"
        assert "RAW 檔案集合已變更" in state["error"]
        assert "重新提交" in state["error"]
        assert state["available_outputs"] == []
    finally:
        jobs.close()


def test_relative_workbench_config_uses_absolute_temporary_inputs(
    tmp_path, monkeypatch
):
    from xic_workbench import targeted

    _snapshot_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = []
    receipts = _record_receipts(monkeypatch)

    class Service:
        def execute_application(self, request):
            captured.append(request)
            assert receipts[0].is_dir()
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        targeted, "build_canonical_targeted_application_service", lambda _: Service()
    )
    run = targeted.TargetedRun(
        Path("config"),
        progress=lambda *_: None,
        should_cancel=lambda: False,
    )
    run.execute()
    request = captured[0]
    receipt = receipts[0]
    assert receipt.is_absolute()
    assert not receipt.exists()
    assert request.config.output_csv.parent == tmp_path / "output"
