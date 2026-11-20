"""Review questions use saved facts, not HOLD or a guessed extraction stage."""

import pytest

from tests.inspector_fixtures import create_inspector_report
from xic_inspector.api import SelectionRequest, prepare_report
from xic_inspector.sources.cid_nl_presentation import blank_reason


def test_three_cell_questions_keep_origins_values_and_real_risks(tmp_path):
    cells = [
        {
            "sample_key": "S1",
            "value": 123.0,
            "variant": "numeric",
            "support_origin": "discovery_support",
            "signal_quality": [
                {
                    "observation_id": "obs-01",
                    "low_signal_to_noise": True,
                    "signal_to_noise": 2,
                }
            ],
        },
        {
            "sample_key": "S2",
            "value": 456.0,
            "variant": "numeric",
            "support_origin": "backfill_support",
            "evidence_states": ["ms1_only_backfill_observation"],
            "reason_codes": ["no_unique_qualifying_relation"],
        },
        {
            "sample_key": "S3",
            "value": None,
            "variant": "unavailable",
            "support_origin": "unavailable",
            "evidence_states": ["unavailable"],
            "reason_codes": ["insufficient_method_evidence"],
        },
    ]
    report, matrix, _ = create_inspector_report(
        tmp_path,
        samples=[
            {"sample_key": sid, "sample_stem": f"Display {sid}"}
            for sid in ("S1", "S2", "S3")
        ],
        cells=cells,
    )
    before = report.read_bytes(), matrix.read_bytes()
    item = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload["items"][0]
    assert item["group_counts"] == {
        "detect": 1,
        "backfill": 1,
        "unrecorded": 0,
        "empty": 1,
        "missing": 0,
        "unresolved": 0,
    }
    samples = {sample["sample_id"]: sample for sample in item["samples"]}
    assert {sid: s["support_origin"]["label"] for sid, s in samples.items()} == {
        "S1": "detect",
        "S2": "backfill",
        "S3": "來源未記錄",
    }
    assert samples["S1"]["risk_labels"] == ["低 SNR"]
    assert samples["S2"]["risk_labels"] == [] and "note" not in samples["S2"]
    assert samples["S3"]["note"] == "找過，沒有 MS1 峰也沒有 MS2 支持"
    assert [s.get("quantitative_value") for s in samples.values()] == [123, 456, None]
    assert {s["sample_stem"] for s in samples.values()} == {
        "Display S1",
        "Display S2",
        "Display S3",
    }
    assert item["details"]["readable_records"] == []
    assert (report.read_bytes(), matrix.read_bytes()) == before


@pytest.mark.parametrize(
    ("variant", "states", "reason"),
    [
        ("not_searched", ["not_searched"], "未在此位置搜尋"),
        ("unresolved", ["unresolved"], "多峰留空"),
        ("searched_no_observation", ["searched_no_observation"], "找過，沒有可用的峰"),
        ("not_quantifiable", ["direct_observation"], "有峰，但無法定量"),
    ],
)
def test_empty_reasons_do_not_conflate_search_and_ambiguity(variant, states, reason):
    assert (
        blank_reason({"value": None, "variant": variant, "evidence_states": states})
        == reason
    )


def test_competition_names_the_other_group_by_sample_and_measured_rt(tmp_path):
    from tests.explanation_review_fixtures import _competition_review_report

    report, _ = _competition_review_report(tmp_path)
    item = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload["items"][0]
    sample = next(s for s in item["samples"] if s["sample_id"] == "S2")
    assert sample["risk_labels"] == ["低 SNR", "可能改與 S3 1.20 min 同組"]
    assert item["badge_label"] == "需留意"
    assert "cost_gap" not in str(sample)


def test_shoulder_overlap_is_noted_with_its_own_area_without_raising_risk(tmp_path):
    report, _, _ = create_inspector_report(
        tmp_path,
        samples=[{"sample_key": sid, "sample_stem": sid} for sid in ("S1", "S2")],
        cells=[
            {
                "sample_key": "S1",
                "value": 123.0,
                "variant": "numeric",
                "support_origin": "discovery_support",
                "review_reasons": ["physical_shoulder_overlap"],
            },
            {
                "sample_key": "S2",
                "value": 456.0,
                "variant": "numeric",
                "support_origin": "backfill_support",
            },
        ],
    )
    item = prepare_report(
        report, SelectionRequest(strategy="query", group_ids=("G1",))
    ).payload["items"][0]
    samples = {sample["sample_id"]: sample for sample in item["samples"]}
    assert samples["S1"]["area_notes"] == ["肩峰／重疊"]
    assert "area_notes" not in samples["S2"]
    assert [samples[sid]["quantitative_value"] for sid in ("S1", "S2")] == [123, 456]
    assert [samples[sid]["risk_labels"] for sid in ("S1", "S2")] == [[], []]
    assert item["badge_label"] == "已記錄"


def _competition(selected, alternative, equivalents=()):
    return {
        "risk_flags": ["accepted_competition"],
        "selected_owners": selected,
        "alternative_partition": alternative,
        "equivalent_alternatives": list(equivalents),
    }


S1, S2, S2_OTHER, S3 = (
    ["S1", 500.0, 1.0],
    ["S2", 500.0, 1.1],
    ["S2", 500.0, 3.4],
    ["S3", 500.0, 1.2],
)


@pytest.mark.parametrize(
    ("risks", "expected"),
    [
        ([_competition([S1, S2], [[S1], [S2, S3]])], [[S3]]),
        ([_competition([S1, S2], [[S1], [S2]])], [[]]),
        ([_competition([S1, S2], [[S1], [S2], [S2_OTHER, S3]])], [[]]),
        (
            [
                _competition([S1, S2], [[S1], [S2, S3]]),
                _competition([S1, S2], [[S1], [S2]]),
            ],
            [[S3], []],
        ),
        ([_competition([S1, S2], None)], []),
    ],
)
def test_competition_names_what_the_alternative_does_with_this_peak(risks, expected):
    from xic_inspector.sources.cid_nl_presentation import competition_groups

    cell = {"sample_key": "S2", "membership_risks": risks}
    assert competition_groups(cell) == expected
