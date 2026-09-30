from __future__ import annotations

import csv
import io
from dataclasses import replace

from xic_extractor.alignment.backfill_peak_fact_projection import (
    SELECTED_INTEGRATION_AUTHORITY_FIELDS,
    selected_integration_authority_projection,
)
from xic_extractor.alignment.backfill_peak_fact_provenance import (
    EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
    render_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult


def test_provenance_writes_one_run_profile_row_with_profile_columns() -> None:
    payload = render_backfill_peak_fact_provenance_tsv(
        EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH
    ).decode("utf-8")
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
    rows = list(reader)

    assert tuple(reader.fieldnames or ()) == (
        "row_kind",
        "schema_version",
        "profile_schema_version",
        "profile_id",
        "profile_hash",
        "signal_processing_method",
        "signal_processing_window_points",
        "physical_signal_formula",
        "projection_content_id",
    )
    assert len(rows) == 1
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE
    assert rows[0]["row_kind"] == "run_profile"
    assert rows[0]["schema_version"] == "alignment_owner_backfill_peak_fact_provenance"
    assert rows[0]["profile_id"] == profile.profile_id
    assert rows[0]["profile_hash"] == profile.profile_hash
    assert rows[0]["signal_processing_method"] == profile.method
    assert rows[0]["signal_processing_window_points"] == str(profile.window_points)
    assert rows[0]["physical_signal_formula"] == profile.physical_signal_formula
    assert rows[0]["projection_content_id"].startswith("owner-backfill-provenance-row:")


def test_selected_integration_authority_id_covers_every_field() -> None:
    integration = selected_integration_authority_projection(
        IntegrationResult(
            rt_left_min=8.2,
            rt_apex_min=8.5,
            rt_right_min=8.8,
            raw_apex_rt_min=8.5,
            rt_width_min=0.6,
            height_raw=1000.0,
            height_smoothed=950.0,
            area_raw_counts_seconds=12345.0,
            area_baseline_corrected=12000.0,
            baseline_type="asls",
            area_ms1_morphology=11000.0,
            ms1_morphology_area_source="morphology",
            ms1_morphology_trace_method="gaussian",
            ms1_morphology_trace_window_points=15,
            ms1_morphology_trace_effective_points=13,
        )
    )

    assert len(SELECTED_INTEGRATION_AUTHORITY_FIELDS) == 18
    for name in SELECTED_INTEGRATION_AUTHORITY_FIELDS:
        changed = replace(
            integration, **{name: _changed_value(getattr(integration, name))}
        )
        assert (
            changed.selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        ), name


def _changed_value(value: object) -> object:
    if value is None:
        return "changed"
    if isinstance(value, str):
        return f"{value}-changed"
    if type(value) is int:
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, tuple):
        if value and type(value[0]) is int:
            return (*value, 1)
        return (*value, "changed")
    raise AssertionError(type(value))
