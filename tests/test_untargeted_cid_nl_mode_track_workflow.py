from __future__ import annotations

from pathlib import Path

from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_real_data_runtime import _discovery_receipts
from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
    _mode_track_runtime_jobs,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_runtime_jobs_bind_raw_by_stem_after_injection_order_sort(
    tmp_path: Path,
) -> None:
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
    settings = _settings()
    candidates_by_sample = {"S1": (), "S2": ()}
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=2,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=1,
                sample_role="qc",
            ),
        ),
        method_subject=mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            neutral_loss_da=116.0474,
        ),
        settings=settings,
        peak_config=_peak_config(tmp_path),
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="mode-track-order-test",
    )

    jobs = _mode_track_runtime_jobs(context=context, dll_dir=tmp_path)

    assert [job.sample.metadata.raw_stem for job in jobs] == ["S2", "S1"]
    assert [job.raw_path.stem for job in jobs] == ["S2", "S1"]
    assert [job.work_unit_key for job in jobs] == [
        "untargeted-cid-nl:S2",
        "untargeted-cid-nl:S1",
    ]
