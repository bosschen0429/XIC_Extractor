"""Method-subject membership and worksets."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.discovery_csv_fixtures import representative_event
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _assert_jobs_match_direct_construction,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_alignment_subject_worksets_preserve_only_compatible_overlap() -> None:
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        CidNlMethodSubjectContractError,
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
    )

    first = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    overlapping = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.015,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    disconnected = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.05,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    different_loss = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.005,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=132.0423,
    )
    composite = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.01,
        precursor_tolerance_ppm=20.0,
        composition=CidNlComposition(
            tags=(("DNA_dR", 116.0474), ("RNA_R", 132.0423)),
            combine_mode="union",
        ),
    )

    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite)
    ) == (
        (first, overlapping),
        (disconnected,),
        (different_loss,),
        (composite,),
    )
    # A confirmed identity's search envelope must be compared together before
    # partition dispatch, even if two resolver windows do not directly overlap.
    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite),
        comparison_mz_intervals=((499.99, 500.06),),
    ) == (
        (first, overlapping, disconnected),
        (different_loss,),
        (composite,),
    )
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="duplicate method subjects",
    ):
        cid_nl_alignment_subject_worksets((first, first))


@pytest.mark.parametrize(
    ("masses", "losses", "ppm", "centers"),
    (
        ((500.0, 500.004), (116.0474, 116.0474), 20.0, (500.0, 500.0)),
        ((500.0, 500.02), (116.0474, 116.0474), 20.0, (500.0, 500.02)),
        (
            (500.0, 500.008, 500.016),
            (116.0474,) * 3,
            20.0,
            (500.0, 500.0, 500.016),
        ),
        (
            (512.0, 512.0078125),
            (116.0474, 116.0474),
            15.2587890625,
            (512.0, 512.0),
        ),
    ),
    ids=("nearby", "outside", "fixed-anchor", "inclusive-bound"),
)
def test_production_batch_preserves_bounded_subject_membership(
    tmp_path: Path, masses, losses, ppm, centers
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=ppm
    )
    peak_config = _peak_config(tmp_path)
    from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile

    candidates = tuple(
        _current_candidates(
            _FakeRaw(
                (8.0 + index * 0.1,), scan_precursor_mz=mz, neutral_loss_das=(loss,)
            ),
            raw_file=raw_path,
            settings=replace(
                settings, neutral_loss_profiles=(NeutralLossProfile("DNA_dR", loss),)
            ),
        )[0]
        for index, (mz, loss) in enumerate(zip(masses, losses, strict=True))
    )
    arguments = dict(
        signal_processing_profile=TEST_PROFILE,
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        dll_dir=tmp_path,
    )

    batch = build_dense_partition(**arguments, peaks_by_sample={"S1": candidates})
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    expected_memberships = tuple(
        sorted(
            (
                candidate.peak_id,
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=center,
                    precursor_tolerance_ppm=ppm,
                    neutral_loss_da=loss,
                ).method_subject_id,
            )
            for candidate, center, loss in zip(candidates, centers, losses, strict=True)
        )
    )
    assert batch.subject_batch.candidate_memberships == expected_memberships
    assert len(batch.subject_batch.subjects) == len(
        set(zip(centers, losses, strict=True))
    )
    permuted = build_dense_partition(
        **arguments, peaks_by_sample={"S1": tuple(reversed(candidates))}
    )
    assert permuted.subject_batch == batch.subject_batch
    assert (
        tuple(representative_event(candidate).precursor_mz for candidate in candidates)
        == masses
    )
    assert tuple(job.method_subject for job in batch.jobs) == (
        batch.subject_batch.subjects
    )
    assert all(
        job.candidates is batch.context.peaks_by_sample["S1"] for job in batch.jobs
    )
    _assert_jobs_match_direct_construction(batch)
