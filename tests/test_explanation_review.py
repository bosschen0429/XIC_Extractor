"""Compact review preserves decisions while omitting unrequested audit and curves."""

import hashlib
import json
import sqlite3

import pytest

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    explanation_summary,
    run_alignment_fixture,
)
from tests.explanation_review_fixtures import (
    _competition_review_report,
    _quality_review_report,
)
from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    read_alignment_curves,
    read_alignment_explanation,
    read_explanation_summary,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.applications.untargeted.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.diagnostics.explanation_conversion import compact_explanation
from xic_inspector.api import SelectionRequest, generate_report
from xic_inspector.sources.cid_nl import CidNlSource


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
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    with sqlite3.connect(source) as db:
        summary = json.loads(db.execute("SELECT payload FROM run").fetchone()[0])
        summary["query_index_version"] = 2
        db.execute("UPDATE run SET payload=?", (json.dumps(summary),))
    before = source.read_bytes()
    with pytest.raises(ExplanationError, match="rerun"):
        read_review_evidence(source)
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "stale_content",
    [
        # A support origin current Alignment no longer writes.
        "UPDATE cells SET support_origin='reacquired_support' "
        "WHERE group_id=(SELECT MIN(group_id) FROM cells)",
    ],
    ids=["retired-support-origin"],
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
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    read_explanation_summary(source)
    with sqlite3.connect(source) as db:
        db.execute(stale_content)
    # Rejected where it is opened, not decoded differently by review or curation.
    with pytest.raises(ExplanationError, match="請重新執行 Alignment"):
        read_explanation_summary(source)
    with pytest.raises(ExplanationError, match="請重新執行 Alignment"):
        read_review_evidence(source)


@pytest.mark.parametrize(
    "criteria,expected",
    [
        ({"mz_min": 500, "mz_max": 500, "rt_min": 2, "rt_max": 2}, (5, 5, 0, 2)),
        ({"mz_min": 500, "mz_max": 500}, (5, 5, 4, 0)),
        ({"sample_ids": ("S1",), "mz_min": 500, "mz_max": 500}, (5, 3, 3, 0)),
        ({"sample_ids": ("absent",), "mz_min": 500}, (5, 0, 0, 0)),
        ({"candidate_ids": ("C1",)}, (5, 5, 1, 0)),
        ({"group_ids": ("row-α",), "sample_ids": ("S2",)}, (5, 3, 1, 0)),
        ({"mz_min": 500, "offset": 1, "limit": 2}, (5, 5, 4, 0)),
        ({"rt_min": 99}, (5, 5, 0, 2)),
        ({"strategy": "sample", "seed": 0, "limit": 2}, (5, 5, 5, 0)),
        ({"strategy": "sample", "seed": 19, "limit": 4}, (5, 5, 5, 0)),
        ({"strategy": "sample", "seed": 19, "sample_ids": ("S1",)}, (5, 3, 3, 0)),
        ({"strategy": "sample", "sample_ids": ("S2",), "limit": 1}, (5, 3, 3, 0)),
        ({"strategy": "sample", "sample_ids": ("absent",)}, (5, 0, 0, 0)),
        ({"strategy": "attention", "limit": 2}, (5, 5, 3, 0)),
        ({"strategy": "attention", "sample_ids": ("S1",)}, (5, 3, 2, 0)),
        ({"strategy": "attention", "sample_ids": ("S2",)}, (5, 3, 2, 0)),
    ],
)
def test_indexed_selection_keeps_witness_scope_counts_and_bounded_loading(
    tmp_path, monkeypatch, criteria, expected
):
    from tests.inspector_fixtures import (
        NEUTRAL_LOSS_BY_TAG,
        discovery,
        read_report_payload,
        witness_member,
    )

    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report, review_only=True) as writer:
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
            # C5's terminal observation lies outside its group's members, so its
            # witness has no observation and no RT.
            peak, peak_rt = ("unrelated", 99) if cid == "C5" else (cid + "-obs", rt)
            writer.add_peak(sid, peak, (None, peak_rt, None))
            writer.add_terminal(
                cid, sid, variant="observed", hypothesis_id=cid, physical_peak_key=peak
            )
        # Each member is its candidate's terminal hypothesis.
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
        writer.add_initial_group(
            disposition="completed",
            members=[
                witness_member("S1", "C1", "C1-obs"),
                witness_member("S2", "C4", "C4-obs"),
            ],
        )
        writer.finalize(
            explanation_summary(
                {
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    request = SelectionRequest(**{"strategy": "query", **criteria})
    baseline = tmp_path / "baseline.html"
    with monkeypatch.context() as patch:
        patch.setattr("xic_inspector.api.open_source", lambda path: CidNlSource(path))
        old = generate_report(report, request, baseline)
    assert (
        old.run_total,
        old.eligible_count,
        old.matched_count,
        old.missing_rt_count,
    ) == expected

    def forbidden(*args, **kwargs):
        raise AssertionError("indexed query read the entire review population")

    monkeypatch.setattr("xic_inspector.sources.cid_nl.read_review_evidence", forbidden)
    output = tmp_path / "indexed.html"
    result = generate_report(report, request, output)
    assert (
        result.run_total,
        result.eligible_count,
        result.matched_count,
        result.missing_rt_count,
    ) == expected
    # The in-memory path counts a group's members over the whole run, so only
    # the selected items and their witnesses are compared.
    assert [
        (item["id"], item["witnesses"]) for item in read_report_payload(output)["items"]
    ] == [
        (item["id"], item["witnesses"])
        for item in read_report_payload(baseline)["items"]
    ]


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
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    summary = read_alignment_explanation(report)
    assert summary["retention"] == "review"
    assert summary["plot_evidence_capability"] == "metadata_only"
    candidate_id = candidates["S1"][0].peak_id
    detail = read_alignment_explanation(report, candidate_id=candidate_id)
    assert all("artifact" not in row for row in detail["method_assessments"])
    expected = sorted(
        (
            decision.subject.canonical_sample.sample_name,
            decision.variant,
            getattr(decision, "value", None),
        )
        for child in completed.application_result.children
        for _group, cells in child.group_cells
        for decision in cells
    )
    actual = sorted(
        (row["sample_key"], row["variant"], row["value"])
        for row in _cells(report).values()
    )
    assert actual == expected
    assert read_alignment_curves(report) == []
    # Review retention keeps no full-audit cell observation payload.
    assert all("observation" not in row for row in _cells(report).values())
    result = generate_report(
        report,
        selection=SelectionRequest(strategy="query", candidate_ids=(candidate_id,)),
        output=tmp_path / "review.html",
    )
    assert result.selected_count > 0
    assert (tmp_path / "review.html").is_file()
    # Missing required columns cannot be supplied by compatibility reading.
    with sqlite3.connect(report) as db:
        db.execute("DROP INDEX cells_sample_apex")
        db.execute(f"ALTER TABLE cells DROP COLUMN {missing_cell_column}")
    with pytest.raises(ExplanationError, match="rerun"):
        CidNlSource(report)


def test_selected_curves_are_explicit_and_other_ranges_stay_small(
    tmp_path, monkeypatch
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    arguments = dict(
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    selected = write_alignment_explanation(
        tmp_path / "selected.sqlite", curve_mz_ranges=((499, 501),), **arguments
    )
    assert read_alignment_curves(selected)
    assert read_alignment_explanation(selected)["curve_retention"] == "selected"
    other = write_alignment_explanation(
        tmp_path / "other.sqlite", curve_mz_ranges=((200, 201),), **arguments
    )
    assert read_alignment_curves(other) == []
    full = write_alignment_explanation(
        tmp_path / "full.sqlite", full_audit=True, **arguments
    )
    assert read_alignment_explanation(full)["retention"] == "full_audit"
    assert full.stat().st_size > other.stat().st_size


def test_existing_full_report_conversion_preserves_sample_values(tmp_path, monkeypatch):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    full = write_alignment_explanation(
        tmp_path / "full.sqlite",
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
        full_audit=True,
    )
    before = full.read_bytes()
    review = tmp_path / "compact.sqlite"
    result = compact_explanation(full, review)
    assert full.read_bytes() == before
    assert result["review_bytes"] < result["source_bytes"]
    for candidate in (candidates["S1"][0], candidates["S2"][0]):
        old = read_alignment_explanation(full, candidate_id=candidate.peak_id)
        new = read_alignment_explanation(review, candidate_id=candidate.peak_id)

        def values(detail):
            return sorted(
                (
                    row["feature_id"],
                    row["sample_key"],
                    row["variant"],
                    row.get("value"),
                )
                for row in _detail_cells(detail)
            )

        assert values(old) == values(new)
        for detail in (old, new):
            for cell in _detail_cells(detail):
                assert cell["signal_quality"]
                assert all(
                    quality["status"] != "not_recorded"
                    for quality in cell["signal_quality"]
                )
        assert [c["signal_quality"] for c in _detail_cells(old)] == [
            c["signal_quality"] for c in _detail_cells(new)
        ]
        assert (
            old["groups"][0]["member_subjects"] == new["groups"][0]["member_subjects"]
        )


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
    assert rows["S1"]["signal_quality"][0]["low_signal_to_noise"] is False
    assert rows["S2"]["signal_quality"][0]["low_signal_to_noise"] is True
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert rows["S1"]["status_kind"] == rows["S2"]["status_kind"] == "rest"
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
    assert rows["S1"]["membership_risks"] == []
    risk = rows["S2"]["membership_risks"][0]
    assert risk["cost_gap"] == 0.03
    assert risk["alternative_partition"] == [
        [["S1", 500.0, 1.0]],
        [["S2", 500.0, 1.1], ["S3", 500.0, 1.2]],
    ]
    assert risk["rt_estimate"]["method"] == "median_shift"
    assert rows["S1"]["quantitative_value"] == 1000.0
    assert rows["S2"]["quantitative_value"] == 2000.0
    assert matrix.read_bytes() == original


@pytest.mark.parametrize("full_audit", (False, True))
def test_produced_membership_risk_reaches_final_samples(
    tmp_path, monkeypatch, full_audit
):
    from dataclasses import asdict, replace

    from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
    from xic_extractor.alignment.feature_membership_inference import (
        infer_feature_memberships,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame
    from xic_extractor.alignment.sample_rt_calibration import (
        RtCalibrationPolicy,
        build_batch_rt_calibration,
    )

    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    # Re-evaluate the real owners with a frozen, independently measured sparse
    # reference. The unchanged partition lets this test isolate query-fact
    # projection rather than fabricate risk payloads or new final memberships.
    calibration = build_batch_rt_calibration(
        {"early": {"S1": 7, "S2": 7.1, "S3": 7}, "late": {"S1": 9, "S2": 9.1, "S3": 9}},
        sample_stems=("S1", "S2", "S3"),
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    groups = []
    for group in completed.initial_groups:
        inferred = infer_feature_memberships(
            group.owners,
            rules=CID_NL_GROUPING_RULES,
            config=config,
            rt_frame=SampleRtFrame().with_sample_calibration(calibration),
        )
        assert len(inferred.groups) == 1
        assert set(inferred.groups[0].owner_ids) == {o.owner_id for o in group.owners}
        groups.append(replace(group, inference_decisions=inferred.sparse_decisions))
    completed = replace(completed, initial_groups=tuple(groups))
    risks = {
        d.owner_id: d.accepted_risk
        for group in completed.initial_groups
        for d in group.inference_decisions
        if d.accepted_risk is not None
    }
    assert len(risks) == 2
    assert all(risk.selected_peer_fit is not None for risk in risks.values())
    for group in completed.initial_groups:
        for owner in group.owners:
            estimate = risks[owner.owner_id].rt_estimate
            assert estimate.method == "median_shift"
            assert estimate.anchor_count == 2
            assert estimate.rt_min == pytest.approx(
                owner.peak.integration.rt_apex_min
                - (0.1 if owner.sample_stem == "S2" else 0)
            )
    expected = {
        member.sample_key: json.loads(json.dumps(asdict(risks[member.subject_id])))
        for group in completed.initial_groups
        for member in group.member_subjects
    }
    original = matrix.read_bytes()
    report = write_alignment_explanation(
        tmp_path / "produced.sqlite",
        full_audit=full_audit,
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
    )
    cells = _cells(report).values()
    assert {
        cell["sample_key"]: cell["membership_risks"][0] for cell in cells
    } == expected
    assert matrix.read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    (
        ("cost_gap", "0.03"),
        ("selected_cost", True),
        ("selected_peer_fit", "0.25"),
        ("alternative_peer_fit", True),
        ("peer_fit_gap", "0.15"),
        ("alternative_partition", ["not-a-group"]),
        ("soft_contradictions", "shape_disagreement"),
        ("rt_estimate", "median_shift"),
        ("rt_estimate", {"method": "median_shift", "rt_min": True}),
    ),
)
def test_persisted_membership_fields_reject_wrong_types(tmp_path, field, value):
    import zlib

    report, _ = _competition_review_report(tmp_path)
    # An accepted risk is persisted once, with its group member.
    with sqlite3.connect(report) as db:
        member, payload = db.execute(
            "SELECT id,accepted_risk FROM members WHERE accepted_risk IS NOT NULL"
        ).fetchone()
        risk = json.loads(zlib.decompress(payload))
        risk[field] = value
        db.execute(
            "UPDATE members SET accepted_risk=? WHERE id=?",
            (zlib.compress(json.dumps(risk).encode()), member),
        )
    with pytest.raises((ExplanationError, ValueError), match="membership risk"):
        read_review_evidence(report)


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
    evidence = read_review_evidence(outputs.evidence.report_path)
    assert evidence.summary["retention"] == "review"
    cells = list(_cells(outputs.evidence.report_path).values())
    assert len(cells) == 2
    assert all(cell["membership_risks"] for cell in cells)
    assert all(cell["value"] > 0 for cell in cells)


@pytest.mark.parametrize("review_only", (False, True))
def test_named_cells_preserve_values_and_details_across_input_order(
    tmp_path, review_only
):
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
        with ExplanationReportWriter(path, review_only=review_only) as writer:
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
            writer.finalize(explanation_summary({}, review_only=review_only))
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
    with pytest.raises(ValueError, match="duplicate review cell"):
        with ExplanationReportWriter(path, review_only=True) as writer:
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
