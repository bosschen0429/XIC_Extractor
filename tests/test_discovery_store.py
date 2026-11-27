"""The published Discovery file: what it keeps, derives and refuses."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.discovery_ms1_capture_fixtures import _seed
from tests.discovery_peak_fixtures import _peak
from tests.discovery_store_fixtures import (
    SampleRecords,
    peak,
    stored_events,
    stored_peaks,
    write_discovery,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.evidence_config import DEFAULT_EVIDENCE_PROFILE
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import EventAttachment
from xic_extractor.discovery.priority import order_discovery_peak_bindings
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.store import (
    DISCOVERY_ARTIFACT_NAME,
    merge_sample_parts,
    open_discovery,
    read_discovery_batch,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import Ms1PrecursorObservation

_LOSSES = {"DNA_dR": 116.0474, "R": 132.0423}


def _sample(stem, peaks=(), attachments=()):
    return SampleRecords(
        stem, Path(f"{stem}.raw"), peaks=peaks, attachments=attachments
    )


def test_events_keep_the_evidence_alignment_assesses(tmp_path: Path) -> None:
    inferred = replace(
        _seed(scan_number=33623, rt=38.75, precursor_mz=961.4289),
        raw_file=Path("S.raw"),
        sample_stem="S",
        observed_loss_error_ppm=None,
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=961.4482,
        precursor_metadata=Ms2PrecursorMetadata(
            monoisotopic_mz=961.4478,
            charge=1,
            isolation_width_da=1.0,
            isolation_offset_da=-0.25,
            master_scan_number=33620,
        ),
        ms1_precursor_observation=Ms1PrecursorObservation(
            33620, 38.7412, (961.4281, 961.4302)
        ),
    )
    empty_parent = replace(
        inferred,
        scan_number=33700,
        ms1_precursor_observation=Ms1PrecursorObservation(33698, 38.8, ()),
    )
    recorded = replace(
        _seed(scan_number=33706, rt=38.9), raw_file=Path("S.raw"), sample_stem="S"
    )
    located = peak("S", peak_id="S@mz961.4478_rt38.7477", best_ms2_scan_id=33623)
    other = peak("S", peak_id="S@mz500_rt38.9", best_ms2_scan_id=33706)
    written = [
        EventAttachment(inferred, located.peak_id, "parent_ms1_ion_ambiguous"),
        EventAttachment(empty_parent, located.peak_id, "parent_ms1_ion_missing"),
        EventAttachment(recorded, other.peak_id),
        EventAttachment(replace(recorded, scan_number=33800), "", "no_peak_at_rt"),
    ]

    store = write_discovery(
        tmp_path, [_sample("S", [located, other], written)], neutral_loss_by_tag=_LOSSES
    )

    restored = {item.event.scan_number: item for item in stored_events(store)}
    for item in written:
        back = restored[item.event.scan_number]
        assert (back.peak_id, back.unattached_reason) == (
            item.peak_id,
            item.unattached_reason,
        )
        expected = item.event
        if expected.precursor_metadata is not None:
            expected = replace(
                expected,
                precursor_metadata=replace(
                    expected.precursor_metadata, master_scan_number=None
                ),
            )
        if expected.precursor_mz_basis != "product_plus_neutral_loss":
            assert back.event.observed_neutral_loss_da == (
                expected.precursor_mz - expected.product_mz
            )
            expected = replace(
                expected,
                observed_neutral_loss_da=back.event.observed_neutral_loss_da,
                observed_loss_error_ppm=back.event.observed_loss_error_ppm,
            )
        assert back.event == expected
    assert restored[33623].event.observed_loss_error_ppm is None
    assert restored[33700].event.ms1_precursor_observation.centroid_mzs == ()
    assert restored[33706].event.ms1_precursor_observation is None
    assert restored[33706].event.precursor_metadata is None


def test_peaks_keep_publication_order_and_derive_their_evidence(
    tmp_path: Path,
) -> None:
    def located(peak_id, scan, tag, reason=""):
        return EventAttachment(
            replace(
                _seed(scan_number=scan, rt=7.84),
                raw_file=Path("S.raw"),
                sample_stem="S",
                neutral_loss_tag=tag,
                configured_neutral_loss_da=_LOSSES[tag],
            ),
            peak_id,
            reason,
        )

    peaks = [peak("S", peak_id=peak_id) for peak_id in ("b", "c", "a")]
    attachments = [
        located("b", 1, "R"),
        located("b", 2, "DNA_dR"),
        located("b", 3, "DNA_dR", "rt_outside_peak"),
        located("c", 4, "R", "mass_outside_peak"),
    ]

    store = write_discovery(
        tmp_path, [_sample("S", peaks, attachments)], neutral_loss_by_tag=_LOSSES
    )

    restored = stored_peaks(store)
    assert [p.peak_id for p in restored] == ["b", "c", "a"]
    assert [(p.attached_event_count, p.matched_tag_names) for p in restored] == [
        (2, ("DNA_dR", "R")),
        (0, ("R",)),
        (0, ()),
    ]
    assert [len(p.located_events) for p in restored] == [3, 1, 0]


def test_samples_keep_run_order_across_parts(tmp_path: Path) -> None:
    store = write_discovery(tmp_path, [_sample("B"), _sample("A")])

    assert read_discovery_batch(store).sample_order == ("B", "A")
    assert sorted(path.name for path in tmp_path.iterdir()) == [DISCOVERY_ARTIFACT_NAME]


def test_samples_that_disagree_on_tags_do_not_merge(tmp_path: Path) -> None:
    first = write_discovery(tmp_path / "first", [_sample("A")])
    second = write_discovery(
        tmp_path / "second", [_sample("B")], neutral_loss_by_tag={"R": 132.0423}
    )

    with pytest.raises(ValueError, match="disagree on tags"):
        merge_sample_parts(tmp_path / "out" / DISCOVERY_ARTIFACT_NAME, [first, second])

    assert not (tmp_path / "out" / DISCOVERY_ARTIFACT_NAME).exists()


@pytest.mark.parametrize("content", ["csv", "other_schema"])
def test_only_a_current_discovery_file_opens(tmp_path: Path, content: str) -> None:
    path = tmp_path / DISCOVERY_ARTIFACT_NAME
    if content == "csv":
        path.write_text("peak_id,sample_stem\n", encoding="utf-8")
    else:
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE peaks (peak_id TEXT)")
            db.commit()

    with pytest.raises(ValueError, match="rerun Discovery"):
        with open_discovery(path):
            pass
    with pytest.raises(FileNotFoundError):
        with open_discovery(tmp_path / "missing.sqlite"):
            pass


def test_input_source_keeps_unadmitted_peaks_out_of_features(tmp_path: Path) -> None:
    def sample(stem, statuses):
        peaks = [
            peak(stem, peak_id=f"{stem}-{index}", status=status, best_ms2_scan_id=index)
            for index, status in enumerate(statuses, start=1)
        ]
        return _sample(stem, peaks)

    source = DiscoveryInputSource(
        read_discovery_batch(
            write_discovery(
                tmp_path,
                [
                    sample("S1", ("admitted", "no_nl_event", "doubly_charged")),
                    sample("S2", ("no_nl_event",)),
                ],
            )
        )
    )

    assert source.feature_candidate_ids() == ("S1-1",)
    assert {c.peak_id for c in source["S1"]} == {"S1-1", "S1-2", "S1-3"}
    assert len(source["S2"]) == 1


def test_peak_rows_follow_the_review_queue() -> None:
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
    )

    def binding(
        peak_id: str,
        *,
        status: str = "admitted",
        attached: int = 2,
        score: int = 50,
        intensity: float = 1000.0,
        event_rt: float = 7.84,
    ) -> _OrderedBinding:
        event = _seed(scan_number=6095, rt=event_rt, product_intensity=intensity)
        return _OrderedBinding(
            peak=_peak(
                peak_id=peak_id,
                status=status,
                attached_event_count=attached,
                evidence_score=score,
                located_events=(event,),
            ),
            physical_evaluation=SimpleNamespace(
                state="available",
                answer=SimpleNamespace(
                    selected_segment=SimpleNamespace(area_counts_seconds=1000.0)
                ),
            ),
            final_ordinal=0,
        )

    ordered = order_discovery_peak_bindings(
        (
            binding("blank", status="blank", score=99),
            binding("single", attached=1, score=90),
            binding("far", event_rt=7.0, score=95),
            binding("lead-weak", score=40),
            binding("lead-strong", score=80),
            binding("lead-strong-intense", score=80, intensity=5000.0),
        ),
        settings=settings,  # type: ignore[arg-type]
    )

    assert [item.peak.peak_id for item in ordered] == [
        "lead-strong-intense",
        "lead-strong",
        "lead-weak",
        "far",
        "single",
        "blank",
    ]
    assert [item.final_ordinal for item in ordered] == list(range(6))


@dataclass(frozen=True)
class _OrderedBinding:
    peak: object
    physical_evaluation: object
    final_ordinal: int


def test_discovery_settings_require_neutral_loss_profile() -> None:
    profile = NeutralLossProfile("DNA_dR", 116.0474)

    settings = DiscoverySettings(neutral_loss_profile=profile)

    assert settings.neutral_loss_profile == profile
    assert settings.nl_tolerance_ppm == 10.0
    assert settings.precursor_mz_tolerance_ppm == 10.0
    assert settings.product_mz_tolerance_ppm == 20.0
    assert settings.product_search_ppm == 50.0
    assert settings.nl_min_intensity_ratio == 0.01
    assert settings.ms1_search_padding_min == 0.35
    assert settings.rt_min is None
    assert settings.rt_max is None
    assert settings.evidence_profile == DEFAULT_EVIDENCE_PROFILE
