from pathlib import Path
from types import SimpleNamespace

from xic_workbench.config_io import write_settings, write_targets
from xic_workbench.jobs import AnalysisJobs


def test_targeted_dispatch_freezes_values_and_manifest_inputs(tmp_path, monkeypatch):
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

    class Service:
        def execute_application(self, request):
            captured.append(request)
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
        receipt_dir = Path(request.method_manifest_context.config_dir)
        assert (receipt_dir / "settings.csv").read_bytes() == original_settings
        assert (receipt_dir / "targets.csv").read_bytes() == original_targets
        assert request.config.output_csv.parent == tmp_path / "output"
    finally:
        jobs.close()
