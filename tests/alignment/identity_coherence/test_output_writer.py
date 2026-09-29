import csv
from dataclasses import replace
from pathlib import Path

import pytest

from tests.alignment.identity_coherence.output_fixtures import output_record
from xic_extractor.alignment.identity_coherence import output_writers
from xic_extractor.alignment.identity_coherence.output import (
    IdentityCoherenceOutputContext,
    IdentityCoherenceOutputPaths,
    IdentityCoherenceOutputRecord,
    render_identity_coherence_summary,
    write_identity_coherence_cell_evidence_tsv,
    write_identity_coherence_controls_tsv,
    write_identity_coherence_decisions_tsv,
    write_identity_coherence_outputs,
    write_identity_coherence_requests_tsv,
)
from xic_extractor.alignment.identity_coherence.output_summary_model import (
    build_identity_coherence_summary_model,
)
from xic_extractor.alignment.identity_coherence.schema import (
    IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS,
    IDENTITY_COHERENCE_CONTROL_COLUMNS,
    IDENTITY_COHERENCE_DECISION_COLUMNS,
    IDENTITY_COHERENCE_REQUEST_COLUMNS,
)


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle, delimiter="\t"))


def _read_header(path: Path) -> tuple[str, ...]:
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    return tuple(first_line.split("\t"))


def _record() -> IdentityCoherenceOutputRecord:
    return output_record()


def _assert_in_order(text: str, headings: tuple[str, ...]) -> None:
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_request_decision_and_cell_tsv_writers_use_contract_headers(tmp_path):
    record = _record()

    requests_path = write_identity_coherence_requests_tsv(
        tmp_path / "requests.tsv",
        (record,),
    )
    decisions_path = write_identity_coherence_decisions_tsv(
        tmp_path / "decisions.tsv",
        (record,),
    )
    cells_path = write_identity_coherence_cell_evidence_tsv(
        tmp_path / "cells.tsv",
        (record,),
    )

    assert _read_header(requests_path) == IDENTITY_COHERENCE_REQUEST_COLUMNS
    assert _read_header(decisions_path) == IDENTITY_COHERENCE_DECISION_COLUMNS
    assert _read_header(cells_path) == IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS
    assert _read_tsv(requests_path)[0]["request_id"] == "REQ-1"
    assert _read_tsv(decisions_path)[0]["decision_id"] == "DEC-1"
    assert _read_tsv(cells_path)[0]["sample_id"] == "RAW-2"


def test_controls_writer_writes_header_when_rows_are_empty(tmp_path):
    path = write_identity_coherence_controls_tsv(tmp_path / "controls.tsv", ())

    text = path.read_text(encoding="utf-8")

    assert text.startswith("control_id\tcontrol_type\tcontrol_name")
    assert _read_header(path) == IDENTITY_COHERENCE_CONTROL_COLUMNS
    assert len(text.splitlines()) == 1


def test_identity_coherence_private_tsv_writer_preserves_raw_contract(tmp_path):
    path = tmp_path / "nested" / "identity.tsv"

    returned = output_writers._write_tsv(
        path,
        ("id", "flag", "empty"),
        [{"id": "REQ-1", "flag": True, "empty": None, "extra": "ignored"}],
    )

    assert returned == path
    assert _read_tsv(path) == (
        {"id": "REQ-1", "flag": "True", "empty": ""},
    )

    empty_path = tmp_path / "empty" / "identity.tsv"
    output_writers._write_tsv(empty_path, ("id", "flag"), ())
    assert empty_path.read_text(encoding="utf-8").splitlines() == ["id\tflag"]


def test_cell_writer_rejects_seed_sample_cell(tmp_path):
    record = _record()
    seed_cell = replace(record.row_result.cells[0], sample_id="RAW-1")
    bad_row = replace(record.row_result, cells=(seed_cell,))
    bad_record = replace(record, row_result=bad_row)

    with pytest.raises(ValueError, match="seed sample"):
        write_identity_coherence_cell_evidence_tsv(
            tmp_path / "cells.tsv",
            (bad_record,),
        )


def test_cell_writer_rejects_unresolved_seed_sample(tmp_path):
    record = _record()
    bad_seed_gate = replace(
        record.seed_gate,
        resolved_request=replace(
            record.seed_gate.resolved_request,
            seed_sample=None,
        ),
    )
    bad_decision = replace(record.row_result.decision, seed_sample=None)
    bad_row = replace(record.row_result, decision=bad_decision)
    bad_record = replace(record, seed_gate=bad_seed_gate, row_result=bad_row)

    with pytest.raises(ValueError, match="resolved seed_sample"):
        write_identity_coherence_cell_evidence_tsv(
            tmp_path / "cells.tsv",
            (bad_record,),
        )


def test_writers_reject_mismatched_record_join_keys(tmp_path):
    record = _record()
    bad_decision = replace(record.row_result.decision, decision_id="OTHER")
    bad_row = replace(record.row_result, decision=bad_decision)
    bad_record = replace(record, row_result=bad_row)

    with pytest.raises(ValueError, match="decision_id"):
        write_identity_coherence_decisions_tsv(
            tmp_path / "decisions.tsv",
            (bad_record,),
        )


def test_writers_reject_forbidden_evidence_used(tmp_path):
    record = _record()
    bad_decision = replace(
        record.row_result.decision,
        forbidden_evidence_used=True,
    )
    bad_row = replace(record.row_result, decision=bad_decision)
    bad_record = replace(record, row_result=bad_row)

    with pytest.raises(ValueError, match="forbidden_evidence_used"):
        write_identity_coherence_decisions_tsv(
            tmp_path / "decisions.tsv",
            (bad_record,),
        )


def test_summary_renderer_reports_required_sections_and_counts():
    markdown = render_identity_coherence_summary(
        (_record(),),
        context=IdentityCoherenceOutputContext(
            command="identity-coherence --inline",
            mode="inline_pre_backfill",
            input_source="pre_backfill_ownership",
            input_hashes=(("ownership.pkl", "sha256:def"),),
            control_manifest_path="not_provided",
            projected_full_cohort_identity_request_count=255,
        ),
        control_rows=(),
    )

    headings = (
        "# Untargeted Identity Coherence Summary",
        "## Run Context",
        "## Input Hashes",
        "## Request Status Counts",
        "## Evidence Firewall",
        "## Seed Gate Counts",
        "## Decision Counts",
        "## Tier Support Counts",
        "## RT-Only Candidate Counts",
        "## Shape And Width Review",
        "## Per-Sample Evidence Coverage",
        "## Infrastructure And Data Quality",
        "## Threshold Count And Fraction Summaries",
        "## Weak Basis Counts",
        "## Identity Controls",
        "## Engineering Go / No-Go",
        "## Cost Counters",
        "## Writer Contract Checks",
    )
    for heading in headings:
        assert heading in markdown
    _assert_in_order(markdown, headings)
    assert "may retrieve RAW/XIC traces for diagnostic identity evidence" in markdown
    assert "does not mutate Backfill" in markdown
    assert "or final-matrix outputs" in markdown
    assert "| `promotion_used_forbidden_evidence` | `false` |" in markdown
    assert "| `would_primary_provisional_identity_family_support` | 1 |" in markdown
    assert "| raw_xic_request_count | not_assessed |" in markdown
    assert "| xic_point_count | not_assessed |" in markdown
    assert "| projected_full_cohort_identity_request_count | 255 |" in markdown


def test_summary_renderer_reports_evaluated_identity_controls():
    markdown = render_identity_coherence_summary(
        (_record(),),
        context=IdentityCoherenceOutputContext(
            command="identity-coherence",
            mode="inline_pre_backfill_diagnostic",
            input_source="pre_backfill",
            control_manifest_path="identity_coherence_controls_manifest.tsv",
        ),
        control_rows=(
            {
                "control_id": "CTRL-ISTD-1",
                "control_type": "positive_targeted_istd",
                "control_status": "assessed",
                "control_pass": "true",
                "positive_control_mapping_status": "mapped",
                "decoy_generation_method": "",
                "control_failure_reason": "",
            },
            {
                "control_id": "CTRL-DECOY-1",
                "control_type": "identity_decoy",
                "control_status": "assessed",
                "control_pass": "true",
                "positive_control_mapping_status": "not_applicable",
                "decoy_generation_method": "mz_shift",
                "control_failure_reason": "",
            },
        ),
    )

    assert "## Identity Controls" in markdown
    assert "| `positive_targeted_istd` | 1 |" in markdown
    assert "| `identity_decoy` | 1 |" in markdown
    assert "| `mapped` | 1 |" in markdown
    assert "| `mz_shift` | 1 |" in markdown
    assert "| positive_control_pass_fraction | 1 |" in markdown
    assert "| decoy_correctly_rejected_count | 1 |" in markdown
    assert "| `true` | 2 |" in markdown


@pytest.mark.parametrize(
    "control_rows",
    [
        (),
        (
            {
                "control_id": "CTRL-ISTD-1",
                "control_type": "positive_targeted_istd",
                "control_status": "assessed",
                "control_pass": "true",
                "positive_control_mapping_status": "mapped",
                "decoy_generation_method": "",
                "control_failure_reason": "",
            },
        ),
    ],
)
def test_summary_renderer_reports_decoy_metric_not_assessed_without_decoys(
    control_rows,
):
    markdown = render_identity_coherence_summary(
        (_record(),),
        context=IdentityCoherenceOutputContext(
            command="identity-coherence",
            mode="inline_pre_backfill_diagnostic",
            input_source="pre_backfill",
            control_manifest_path="identity_coherence_controls_manifest.tsv",
        ),
        control_rows=control_rows,
    )

    assert "| decoy_correctly_rejected_count | not_assessed |" in markdown


def test_summary_model_computes_counts_controls_and_go_no_go_rows():
    model = build_identity_coherence_summary_model(
        (_record(),),
        context=IdentityCoherenceOutputContext(
            command="identity-coherence",
            mode="inline_pre_backfill_diagnostic",
            input_source="pre_backfill",
            raw_xic_request_count=5,
            xic_point_count=123,
            projected_full_cohort_identity_request_count=10,
            max_projected_full_cohort_identity_xic_requests=20,
        ),
        control_rows=(
            {
                "control_type": "positive_targeted_istd",
                "control_status": "assessed",
                "control_pass": "true",
                "positive_control_mapping_status": "mapped",
                "decoy_generation_method": "",
                "control_failure_reason": "",
            },
            {
                "control_type": "identity_decoy",
                "control_status": "assessed",
                "control_pass": "true",
                "positive_control_mapping_status": "not_applicable",
                "decoy_generation_method": "mz_shift",
                "control_failure_reason": "",
            },
        ),
    )

    assert model.input_row_count == 1
    assert model.tier1_count == 2
    assert model.assessed_non_seed_cell_count == 1
    assert model.raw_xic_request_count == 5
    assert model.xic_point_count == 123
    assert model.positive_control_pass_fraction == "1"
    assert model.decoy_correctly_rejected_count == 1
    assert model.control_type_counts["positive_targeted_istd"] == 1
    assert model.control_type_counts["identity_decoy"] == 1
    assert model.decoy_generation_method_counts["mz_shift"] == 1
    assert any(
        "| projected_full_cohort_identity_xic_requests | Proceed |" in row
        for row in model.engineering_go_no_go_rows
    )


def test_write_identity_coherence_outputs_writes_all_frozen_paths(tmp_path):
    paths = write_identity_coherence_outputs(
        tmp_path,
        (_record(),),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            input_hashes=(("synthetic.tsv", "sha256:abc"),),
            projected_full_cohort_identity_request_count=255,
        ),
        control_rows=(),
    )

    assert isinstance(paths, IdentityCoherenceOutputPaths)
    assert paths.requests_tsv.name == "untargeted_identity_coherence_requests.tsv"
    assert paths.decisions_tsv.name == "untargeted_identity_coherence_decisions.tsv"
    assert paths.cell_evidence_tsv.name == (
        "untargeted_identity_coherence_cell_evidence.tsv"
    )
    assert paths.controls_tsv.name == "untargeted_identity_coherence_controls.tsv"
    assert paths.summary_md.name == "untargeted_identity_coherence_summary.md"
    assert paths.requests_tsv.is_file()
    assert paths.decisions_tsv.is_file()
    assert paths.cell_evidence_tsv.is_file()
    assert paths.controls_tsv.is_file()
    assert paths.summary_md.is_file()


def test_summary_renderer_rejects_mixed_threshold_rows():
    record = _record()
    bad_decision = replace(
        record.row_result.decision,
        min_total_coherent_samples=4,
    )
    bad_row = replace(record.row_result, decision=bad_decision)
    bad_record = replace(record, row_result=bad_row)

    with pytest.raises(ValueError, match="min_total_coherent_samples"):
        render_identity_coherence_summary(
            (record, bad_record),
            context=IdentityCoherenceOutputContext(
                command="pytest",
                mode="inline_pre_backfill",
                input_source="synthetic",
            ),
            control_rows=(),
        )


def test_summary_renders_engineering_go_no_go_rows():
    record = output_record()
    markdown = render_identity_coherence_summary(
        (record,),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            firewall_fixture_status="pass",
            spawn_payload_smoke_status="pass",
            max_infrastructure_blocked_fraction=0.05,
            projected_full_cohort_identity_request_count=10,
            max_projected_full_cohort_identity_xic_requests=20,
        ),
    )

    assert "## Engineering Go / No-Go" in markdown
    assert "| evidence_firewall | Proceed |" in markdown
    assert "forbidden_evidence_used_count" not in markdown
    assert "| firewall_fixture | Proceed |" in markdown
    assert "| spawn_payload_smoke | Proceed |" in markdown
    assert "| infrastructure_blocked_fraction | Proceed |" in markdown
    assert "| projected_full_cohort_identity_xic_requests | Proceed |" in markdown


def test_summary_marks_engineering_no_go_when_full_cohort_budget_missing():
    markdown = render_identity_coherence_summary(
        (output_record(),),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            firewall_fixture_status="pass",
            spawn_payload_smoke_status="pass",
            projected_full_cohort_identity_request_count=10,
            max_projected_full_cohort_identity_xic_requests=None,
        ),
    )

    assert (
        "| projected_full_cohort_identity_xic_requests | No-Go for full cohort | "
        "`max_projected_full_cohort_identity_xic_requests` not provided |"
    ) in markdown


def test_summary_marks_infrastructure_pivot_when_blocked_fraction_exceeds_limit():
    record = output_record()
    blocked_decision = replace(
        record.row_result.decision,
        infrastructure_blocked_sample_count=2,
    )
    blocked_record = replace(
        record,
        row_result=replace(record.row_result, decision=blocked_decision),
    )

    markdown = render_identity_coherence_summary(
        (blocked_record,),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            max_infrastructure_blocked_fraction=0.05,
        ),
    )

    assert (
        "| infrastructure_blocked_fraction | Pivot | "
        "`2 / 8 = 0.25` exceeds `0.05` |"
    ) in markdown


def test_summary_uses_row_level_denominator_for_blocked_fraction():
    record = output_record()
    first_decision = replace(
        record.row_result.decision,
        total_coherent_sample_count=4,
        coherent_fraction=0.5,
        infrastructure_blocked_sample_count=1,
    )
    second_decision = replace(
        record.row_result.decision,
        decision_id="DEC-SECOND",
        identity_family_id="IDF-SECOND",
        total_coherent_sample_count=4,
        coherent_fraction=0.5,
        infrastructure_blocked_sample_count=1,
    )
    first_record = replace(
        record,
        row_result=replace(record.row_result, decision=first_decision),
    )
    second_request = replace(
        record.seed_gate.resolved_request,
        decision_id="DEC-SECOND",
    )
    second_seed_gate = replace(
        record.seed_gate,
        resolved_request=second_request,
    )
    second_cells = tuple(
        replace(
            cell,
            decision_id="DEC-SECOND",
            identity_family_id="IDF-SECOND",
        )
        for cell in record.row_result.cells
    )
    second_record = replace(
        record,
        seed_gate=second_seed_gate,
        row_result=replace(
            record.row_result,
            cells=second_cells,
            decision=second_decision,
        ),
    )

    markdown = render_identity_coherence_summary(
        (first_record, second_record),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            max_infrastructure_blocked_fraction=0.2,
        ),
    )

    assert (
        "| infrastructure_blocked_fraction | Proceed | "
        "`2 / 16 = 0.125` <= `0.2` |"
    ) in markdown


def test_summary_marks_blocked_fraction_not_assessed_without_denominator():
    record = output_record()
    zero_denominator_decision = replace(
        record.row_result.decision,
        total_coherent_sample_count=0,
        coherent_fraction=None,
        infrastructure_blocked_sample_count=0,
    )
    zero_denominator_record = replace(
        record,
        row_result=replace(
            record.row_result,
            decision=zero_denominator_decision,
        ),
    )

    markdown = render_identity_coherence_summary(
        (zero_denominator_record,),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
        ),
    )

    assert (
        "| infrastructure_blocked_fraction | Not assessed | "
        "`assessed_sample_total` not assessed |"
    ) in markdown


def test_summary_rejects_forbidden_evidence_used_before_go_no_go_render():
    record = output_record()
    forbidden_decision = replace(
        record.row_result.decision,
        forbidden_evidence_used=True,
    )
    forbidden_record = replace(
        record,
        row_result=replace(record.row_result, decision=forbidden_decision),
    )

    with pytest.raises(ValueError, match="forbidden_evidence_used"):
        render_identity_coherence_summary(
            (forbidden_record,),
            context=IdentityCoherenceOutputContext(
                command="pytest",
                mode="inline_pre_backfill",
                input_source="synthetic",
            ),
        )


@pytest.mark.parametrize(
    ("context_kwargs", "message"),
    (
        (
            {"max_infrastructure_blocked_fraction": -0.01},
            "max_infrastructure_blocked_fraction must be nonnegative",
        ),
        (
            {"max_infrastructure_blocked_fraction": 1.01},
            "max_infrastructure_blocked_fraction must be <= 1",
        ),
        (
            {"projected_full_cohort_identity_request_count": True},
            "projected_full_cohort_identity_request_count must be nonnegative",
        ),
        (
            {"projected_full_cohort_identity_request_count": -1},
            "projected_full_cohort_identity_request_count must be nonnegative",
        ),
        (
            {"max_projected_full_cohort_identity_xic_requests": -1},
            "max_projected_full_cohort_identity_xic_requests must be nonnegative",
        ),
        (
            {"raw_xic_request_count": -1},
            "raw_xic_request_count must be nonnegative",
        ),
        (
            {"xic_point_count": -1},
            "xic_point_count must be nonnegative",
        ),
    ),
)
def test_output_context_rejects_invalid_engineering_values(
    context_kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            **context_kwargs,
        )


def test_summary_can_record_passed_firewall_fixture_status():
    markdown = render_identity_coherence_summary(
        (output_record(),),
        context=IdentityCoherenceOutputContext(
            command="pytest",
            mode="inline_pre_backfill",
            input_source="synthetic",
            firewall_fixture_status="pass",
        ),
    )

    assert "| firewall_fixture | Proceed | firewall A/B fixture passed |" in markdown
