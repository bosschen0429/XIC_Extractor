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
    assert item["reason_label"] == "3 個樣本：1 detect · 1 backfill · 1 空值"
    cards = {r["sample_ids"][0]: r for r in item["details"]["readable_records"]}
    assert set(cards) == {"S1", "S2", "S3"}
    assert cards["S1"]["fields"][0]["value"] == "detect"
    assert cards["S2"]["fields"][0]["value"] == "backfill"
    assert cards["S1"]["fields"][2]["value"] == "低 SNR（非 LOQ 判定）"
    assert cards["S2"]["fields"][2]["value"] == "未記錄需留意的風險。"
    assert (
        cards["S3"]["fields"][1]["value"]
        == "找過，沒有可用的 MS1 峰或符合條件的 MS2 支持。"
    )
    for sid, value in (("S1", 123), ("S2", 456), ("S3", None)):
        assert cards[sid]["title"].startswith(f"Display {sid} · ")
        assert cards[sid]["value"] == value
        assert len(cards[sid]["fields"]) == 3
        assert "technical_refs" not in cards[sid]
    assert (report.read_bytes(), matrix.read_bytes()) == before


@pytest.mark.parametrize(
    ("variant", "states", "reason"),
    [
        ("not_searched", ["not_searched"], "本次未在這個群組位置搜尋。"),
        ("unresolved", ["unresolved"], "有可能的峰，但無法唯一決定採用哪一個。"),
        (
            "searched_no_observation",
            ["searched_no_observation"],
            "在搜尋範圍內找過，沒有可用的峰。",
        ),
        (
            "not_quantifiable",
            ["direct_observation"],
            "有峰，但這次積分無法提供可定量的值。",
        ),
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
    record = next(
        r for r in item["details"]["readable_records"] if r["sample_ids"] == ["S2"]
    )
    assert record["competition_groups"] == [[["S3", 500.0, 1.2]]]
    assert "低 SNR" in record["fields"][2]["value"]
    assert "cost_gap" not in str(record)
