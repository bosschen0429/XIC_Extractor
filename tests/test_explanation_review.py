"""Compact review preserves decisions while omitting unrequested audit and curves."""

import hashlib
import sqlite3
from typing import get_args

import pytest

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    explanation_summary,
    run_alignment_fixture,
    write_alignment_explanation,
)
from tests.explanation_review_fixtures import (
    _competition_review_report,
    _quality_review_report,
)
from tests.untargeted_partition_fixtures import children_of
from xic_extractor.alignment.cell_decision.owner import CellOrigin
from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.review import SUPPORT_ORIGINS
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.injection_timeline import InjectionSequence
from xic_inspector.api import (
    InspectorError,
    SelectionRequest,
    generate_report,
    prepare_report,
)


def _cells(report):
    """Every stored cell, by row and sample."""
    return {
        (group.feature_id, sample): cell
        for group in read_review_evidence(report).groups.values()
        for sample, cell in group.cells.items()
    }


def _detail_cells(detail):
    return [cell for group in detail["groups"] for cell in group.get("cells", [])]


def test_summary_from_an_older_format_is_rejected_without_conversion(
    tmp_path, monkeypatch
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    source = write_alignment_explanation(
        tmp_path / "old.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    with sqlite3.connect(source) as db:
        db.execute("ALTER TABLE run ADD COLUMN query_index_version INTEGER")
    before = source.read_bytes()
    with pytest.raises(ExplanationError, match="請重新執行 Alignment"):
        read_review_evidence(source)
    assert source.read_bytes() == before


def test_written_cell_origin_has_exactly_two_classes() -> None:
    """A third origin must change this test, the TSV status and the review DB."""
    assert get_args(CellOrigin) == ("detected", "backfilled")
    assert SUPPORT_ORIGINS == {"detected", "backfilled", "unavailable"}


@pytest.mark.parametrize(
    "stale_content",
    [
        "ALTER TABLE cells ADD COLUMN risks BLOB",
        "DROP TRIGGER peaks_fixed",
    ],
    ids=["previous-cell-columns", "missing-constraint"],
)
def test_explanation_from_an_older_alignment_is_rejected_on_open(
    tmp_path, monkeypatch, stale_content
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    source = write_alignment_explanation(
        tmp_path / "old.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    read_alignment_explanation(source)
    with sqlite3.connect(source) as db:
        db.execute(stale_content)
    with pytest.raises(ExplanationError, match="請重新執行 Alignment"):
        read_alignment_explanation(source)
    with pytest.raises(ExplanationError, match="請重新執行 Alignment"):
        read_review_evidence(source)


@pytest.mark.parametrize(
    "criteria,counts,witnesses",
    [
        ({"mz_min": 500, "mz_max": 500, "rt_min": 2, "rt_max": 2}, (5, 5, 0, 2), ()),
        (
            {"mz_min": 500, "mz_max": 500},
            (5, 5, 4, 0),
            ("C4@S2", "C3@S1", "C5@S1", "C1@S1 C2@S2"),
        ),
        ({"sample_ids": ("absent",), "mz_min": 500}, (5, 0, 0, 0), ()),
        ({"candidate_ids": ("C1",)}, (5, 5, 1, 0), ("C1@S1 C2@S2",)),
        ({"group_ids": ("row-α",), "sample_ids": ("S2",)}, (5, 3, 1, 0), ("C2@S2",)),
        ({"mz_min": 500, "offset": 1, "limit": 2}, (5, 5, 4, 0), ("C3@S1", "C5@S1")),
        ({"rt_min": 99}, (5, 5, 0, 2), ()),
    ],
)
def test_review_selection_keeps_witness_scope_and_counts(
    tmp_path, criteria, counts, witnesses
):
    from tests.inspector_fixtures import (
        NEUTRAL_LOSS_BY_TAG,
        discovery,
        witness_member,
    )

    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report) as writer:
        writer.declare_samples(
            [{"sample_key": s, "sample_stem": s} for s in ("S1", "S2")]
        )
        for cid, sid, mz, rt in (
            ("C1", "S1", 500, 1),
            ("C2", "S2", 600, 2),
            ("C3", "S1", 500, None),
            ("C4", "S2", 500, 3),
            ("C5", "S1", 500, 4),
        ):
            writer.add_candidate(cid, sid, precursor_mz=mz, **discovery(cid))
            peak, peak_rt = ("unrelated", 99) if cid == "C5" else (cid + "-obs", rt)
            writer.add_peak(sid, peak, (None, peak_rt, None))
            writer.add_terminal(cid, sid, hypothesis_id=cid, physical_peak_key=peak)
        for gid, rid, keys in (
            ("G1", "row-α", [("C1", "S1"), ("C2", "S2")]),
            ("G2", None, [("C3", "S1")]),
            ("G3", 'row-"z', [("C5", "S1")]),
            ("G4", "empty", []),
        ):
            writer.add_final_group(
                gid,
                feature_id=rid,
                disposition="completed",
                members=[
                    witness_member(sid, cid, "C5-obs" if cid == "C5" else cid + "-obs")
                    for cid, sid in keys
                ],
            )
            if rid is not None:
                writer.add_cells(
                    gid,
                    [
                        {
                            "sample_key": sid,
                            "variant": "unresolved",
                            "value": None,
                        }
                        for sid in sorted({s for _, s in keys} or {"S2"})
                    ],
                )
        writer.finalize(
            explanation_summary(
                {
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                },
            )
        )
    request = SelectionRequest(**{"strategy": "query", **criteria})
    result = prepare_report(report, request)
    assert (
        result.run_total,
        result.eligible_count,
        result.matched_count,
        result.missing_rt_count,
    ) == counts
    actual_witnesses = tuple(
        " ".join(f"{w['candidate_id']}@{w['sample_key']}" for w in item["witnesses"])
        for item in result.payload["items"]
    )
    assert actual_witnesses == witnesses


@pytest.mark.parametrize("scenario", ("positive", "all_hold", "review_only"))
@pytest.mark.parametrize("missing_cell_column", ("support_origin", "apex_rt_min"))
def test_default_review_keeps_values_and_query_without_curves(
    tmp_path, monkeypatch, scenario, missing_cell_column
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, scenario
    )
    report = write_alignment_explanation(
        tmp_path / "review.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
    )
    candidate_id = candidates["S1"][0].peak_id
    expected = sorted(
        (
            decision.subject.canonical_sample.sample_name,
            decision.variant,
            getattr(decision, "value", None),
        )
        for child in children_of(completed.application_result)
        for _group, cells in child.group_cells
        for decision in cells
    )
    actual = sorted(
        (row["sample_key"], row["variant"], row["value"])
        for row in _cells(report).values()
    )
    assert actual == expected
    result = generate_report(
        report,
        selection=SelectionRequest(strategy="query", candidate_ids=(candidate_id,)),
        output=tmp_path / "review.html",
    )
    assert result.selected_count > 0
    assert (tmp_path / "review.html").is_file()
    with sqlite3.connect(report) as db:
        db.execute("DROP INDEX cells_sample_apex")
        db.execute(f"ALTER TABLE cells DROP COLUMN {missing_cell_column}")
    with pytest.raises(InspectorError, match="請重新執行 Alignment") as exc:
        generate_report(
            report,
            selection=SelectionRequest(strategy="query", candidate_ids=(candidate_id,)),
            output=tmp_path / "invalid.html",
        )
    assert exc.value.code == 4


def test_signal_quality_survives_review_storage_without_changing_results(tmp_path):
    from xic_inspector.api import prepare_report

    report, matrix = _quality_review_report(tmp_path)
    original = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    rows = {
        row["sample_id"]: row for item in payload["items"] for row in item["samples"]
    }
    stored = _cells(report)
    assert stored["G1", "S1"]["signal_quality"][0]["low_signal_to_noise"] is False
    assert stored["G1", "S2"]["signal_quality"][0]["low_signal_to_noise"] is True
    assert rows["S1"]["risk_labels"] == []
    assert rows["S2"]["risk_labels"] == ["低 SNR"]
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert matrix.read_bytes() == original


def test_accepted_competition_roundtrip_keeps_sample_attribution_and_values(tmp_path):
    from xic_inspector.api import prepare_report

    report, matrix = _competition_review_report(tmp_path)
    original = matrix.read_bytes()
    payload = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload
    rows = {
        row["sample_id"]: row for item in payload["items"] for row in item["samples"]
    }
    stored = _cells(report)
    assert stored["G1", "S1"]["competition"] == []
    assert stored["G1", "S2"]["risk_flags"] == ["accepted_competition"]
    assert stored["G1", "S2"]["competition"] == [[["S3", 500.0, 1.2]]]
    assert rows["S1"]["risk_labels"] == []
    assert "可能改與 S3 1.20 min 同組" in rows["S2"]["risk_labels"]
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert matrix.read_bytes() == original


def test_production_publication_automatically_includes_recorded_risk(
    tmp_path, monkeypatch
):
    from xic_extractor.applications.untargeted.publication import (
        UntargetedPublicationPolicy,
        publish_untargeted_outputs,
    )
    from xic_extractor.execution.timing import TimingRecorder

    completed, candidates, _, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    from dataclasses import replace

    from tests.owner_peak_fixtures import with_peak
    from xic_extractor.alignment.band_grouping import group_peak_bands
    from xic_extractor.alignment.cross_sample.peak_groups import (
        accepted_membership_risks,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame

    (group,) = completed.initial_groups
    owner = group.owners[0]
    rival = with_peak(
        owner,
        apex_rt=owner.peak.rt_apex_min + 0.2,
        start_rt=owner.peak.rt_left_min + 0.2,
        end_rt=owner.peak.rt_right_min + 0.2,
    )
    rival = replace(rival, hypothesis=replace(rival.hypothesis, hypothesis_id="rival"))
    bands = group_peak_bands(
        (*group.owners, rival),
        config=config,
        rt_frame=SampleRtFrame(
            injections=InjectionSequence(
                {o.sample_stem: i for i, o in enumerate(group.owners)}
            )
        ),
    )
    (admitted,) = tuple(band for band in bands if band.destination == "matrix")
    assert admitted.owner_ids == group.owner_ids
    assert admitted.member_risks
    completed = replace(
        completed,
        initial_groups=(admitted,),
        application_result=replace(
            completed.application_result,
            has_flagged_membership_risk=any(
                risk.risk_flags
                for risk in accepted_membership_risks((admitted,)).values()
            ),
        ),
    )
    assert completed.application_result.has_flagged_membership_risk
    completed = completed.bind_publication_inputs(
        peaks_by_sample=candidates,
        **explanation_discovery_facts(completed),
        alignment_config=config,
    )
    policy = UntargetedPublicationPolicy()
    directory = tmp_path / "published"
    outputs = publish_untargeted_outputs(
        completed,
        output_dir=directory,
        outputs=policy.output_paths(directory),
        policy=policy,
        metadata={},
        timing_recorder=TimingRecorder("alignment", enabled=False),
        should_cancel=None,
    )
    assert outputs.evidence is not None
    assert outputs.evidence.matrix_path == outputs.matrix_tsv
    cells = list(_cells(outputs.evidence.report_path).values())
    assert len(cells) == 2
    assert sum(bool(cell["risk_flags"]) for cell in cells) == 1
    assert all(cell["value"] > 0 for cell in cells)


def test_named_cells_preserve_values_and_details_across_input_order(tmp_path):
    cells = [
        {
            "sample_key": "S1",
            "variant": "numeric",
            "value": 11.0,
            "reason_codes": ["first"],
        },
        {
            "sample_key": "S2",
            "variant": "numeric",
            "value": 22.0,
            "reason_codes": ["second"],
        },
    ]
    outputs = []
    for index, ordered in enumerate((cells, cells[::-1])):
        path = tmp_path / f"order-{index}.sqlite"
        with ExplanationReportWriter(path) as writer:
            writer.declare_samples(
                [{"sample_key": s, "sample_stem": s} for s in ("S1", "S2")]
            )
            writer.add_final_group(
                "F1",
                feature_id="F1",
                disposition="completed",
                members=(),
            )
            writer.add_cells("F1", ordered)
            writer.finalize(explanation_summary({}))
        outputs.append(_cells(path))
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT sample,value FROM cells ORDER BY sample"
            ).fetchall() == [(0, 11.0), (1, 22.0)]
    assert outputs[0] == outputs[1]
    assert {k: (v["value"], v["reason_codes"]) for k, v in outputs[0].items()} == {
        ("F1", "S1"): (11.0, ["first"]),
        ("F1", "S2"): (22.0, ["second"]),
    }


def test_named_cells_reject_duplicate_group_sample(tmp_path):
    path = tmp_path / "duplicate.sqlite"
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: cells"):
        with ExplanationReportWriter(path) as writer:
            writer.declare_samples([{"sample_key": "S1", "sample_stem": "S1"}])
            writer.add_final_group(
                "F1",
                feature_id="F1",
                disposition="completed",
                members=(),
            )
            writer.add_cells(
                "F1",
                [
                    {"sample_key": "S1", "variant": "numeric", "value": 11.0},
                    {"sample_key": "S1", "variant": "numeric", "value": 22.0},
                ],
            )
    assert not path.exists()
