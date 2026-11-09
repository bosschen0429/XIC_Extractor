"""Helpers shared by test_untargeted_cid_nl_real_data_adapter and other test modules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _open_bound_raw,
    _peak_config,
    _settings,
    bind_physical_capture,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    UntargetedCidNlNoRawFixture,
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.csv_reader import located_events_by_peak
from xic_extractor.discovery.csv_writer import write_discovery_events_csv
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    adapt_current_discovery_sample_subject,
)


def build_sample_adapter_fixture(tmp_path: Path, *, sample_stem: str = "S1"):
    """One study sample with a Thermo-format synthetic source, not vendor I/O."""
    return build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={sample_stem: "study_sample"},
        thermo_raw_sources=True,
    )


@dataclass
class SampleAdapterScenario:
    """Staged single-sample replay; mutations and assertions stay in each case.

    Defaults are the raw fixture's single DNA_dR loss (116.0474 Da), RT
    7.5–8.6 min, 0.20 min seed gap/search padding and local-minimum resolver;
    peak configuration and the Gaussian15 TEST_PROFILE come from that fixture.
    No spectra are invented here: each case supplies its own _FakeRaw and
    changes it before discovery/opening. Binding stays explicit so fault
    injection, candidate edits and acquisition happen in their original order.
    Specialized profile/backfill cases do not use these adaptation defaults.
    """

    tmp_path: Path
    fixture: UntargetedCidNlNoRawFixture
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    physical_records: dict = field(default_factory=dict)
    attachments: list = field(default_factory=list)

    def discover(self, source_raw):
        return _current_candidates(
            source_raw,
            raw_file=self.fixture.source_paths["S1"],
            settings=self.settings,
            physical_records=self.physical_records,
            attachments=self.attachments,
        )

    def through_event_table(self, candidates):
        """Hand Discovery peaks to Alignment through the persisted event table."""
        path = write_discovery_events_csv(
            self.tmp_path / "S1" / "discovery_events.csv", self.attachments
        )
        located = located_events_by_peak(
            path,
            raw_file=self.fixture.source_paths["S1"],
            neutral_loss_by_tag=self.settings.neutral_loss_by_tag,
        )
        return tuple(
            replace(candidate, located_events=located.get(candidate.peak_id, ()))
            for candidate in candidates
        )

    def open_raw(self, source_raw):
        return _open_bound_raw(self.fixture, "S1", source_raw, tmp_path=self.tmp_path)

    def bind_physical_capture(self):
        self.fixture = replace(
            self.fixture,
            owners=bind_physical_capture(self.fixture.owners, self.physical_records),
        )

    def adapt(self, candidates, *, raw, **overrides):
        inputs = dict(
            processing_owners=self.fixture.owners,
            sample=self.fixture.sample("S1"),
            method_subject=self.fixture.method_subject,
            peaks=candidates,
            settings=self.settings,
            peak_config=self.peak_config,
            raw=raw,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                self.physical_records[c.peak_id] for c in candidates
            ),
        )
        return adapt_current_discovery_sample_subject(**(inputs | overrides))


def build_sample_adapter_scenario(tmp_path: Path) -> SampleAdapterScenario:
    return SampleAdapterScenario(
        tmp_path=tmp_path,
        fixture=build_sample_adapter_fixture(tmp_path),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
    )


def _without_ms1_peak(candidate: DiscoveryPeak) -> DiscoveryPeak:
    """A peak held at peak level: its MS2 events remain, its MS1 peak is not used."""
    return replace(candidate, status="overlap")
