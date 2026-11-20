from xic_extractor.output.targeted_overview import (
    rows_needing_look,
    summarize_targeted_results,
)


def _rows():
    return [
        {
            "SampleName": "A",
            "Target": "ISTD",
            "Counted Detection": "TRUE",
            "Area": "100",
            "RT": "9",
        },
        {
            "SampleName": "B",
            "Target": "ISTD",
            "Counted Detection": True,
            "Area": 0,
            "RT": 9.2,
        },
        {
            "SampleName": "A",
            "Target": "Analyte",
            "Counted Detection": "FALSE",
            "Area": "9000",
            "RT": "9.1",
        },
        {
            "SampleName": "B",
            "Target": "Analyte",
            "Counted Detection": "FALSE",
            "Area": "ND",
            "RT": "ND",
        },
    ]


def _labelled_rows():
    labels = [
        ("NL 確認", "訊號缺口"),
        ("NL 確認", ""),
        ("無 MS2", ""),
        ("無可用 MS1 峰", ""),
    ]
    return [
        row | {"依據": basis, "疑點": doubts}
        for row, (basis, doubts) in zip(_rows(), labels, strict=True)
    ]


def test_rows_needing_look_follow_their_labels_and_role():
    rows = _labelled_rows()
    look = rows_needing_look(rows, {"ISTD"})
    assert [(r["SampleName"], r["Target"]) for r in look] == [
        ("A", "ISTD"),
        ("A", "Analyte"),
    ]
    # A missing spiked ISTD needs a look; a missing analyte is a finished ND.
    rows[3] = rows[3] | {"Target": "ISTD"}
    assert ("B", "ISTD") in {
        (r["SampleName"], r["Target"]) for r in rows_needing_look(rows, {"ISTD"})
    }


def test_overview_summarizes_decisions_not_numeric_availability():
    rows = _labelled_rows()
    overview = summarize_targeted_results(
        rows, rows_needing_look(rows, {"ISTD"}), istd_targets={"ISTD"}
    )
    assert (overview.sample_count, overview.result_count) == (2, 4)
    assert (overview.counted_count, overview.needs_look_count) == (2, 2)
    assert [target.label for target in overview.targets] == ["ISTD", "Analyte"]
    istd, analyte = overview.targets
    assert (
        analyte.counted,
        analyte.needs_look,
        analyte.median_area,
        analyte.rt_min,
    ) == (0, 1, None, None)
    assert (
        istd.counted,
        istd.needs_look,
        istd.median_area,
        istd.rt_min,
        istd.rt_max,
    ) == (2, 1, 50, 9, 9.2)


def test_overview_does_not_infer_missing_legacy_decisions():
    row = _rows()[0] | {"Counted Detection": None}
    overview = summarize_targeted_results([row], [], istd_targets=set())
    assert (overview.counted_count, overview.count_unknown) == (0, 1)
    assert overview.targets[0].median_area is None
    empty = summarize_targeted_results([], [], istd_targets=set())
    assert empty.result_count == empty.counted_count == empty.needs_look_count == 0
    assert empty.targets == ()


def test_review_focus_is_the_minority_outcome_of_each_target():
    samples = ("S1", "S2", "S3", "S4", "S5")
    counted = {
        "Rare": {"S3"},
        "Common": {"S1", "S3", "S4", "S5"},
        "Absent": set(),
        "Split": {"S4", "S5"},
        "ISTD": set(samples),
    }
    rows = [
        {
            "SampleName": sample,
            "Target": target,
            "Counted Detection": "TRUE" if sample in hits else "FALSE",
        }
        for target, hits in counted.items()
        for sample in samples
    ]
    look = [
        *(r for r in rows if r["Target"] == "Absent"),
        next(r for r in rows if (r["SampleName"], r["Target"]) == ("S1", "Split")),
    ]
    overview = summarize_targeted_results(rows, look, istd_targets={"ISTD"})
    focus = {t.label: (t.focus_kind, t.focus_samples) for t in overview.targets}
    # A shared absence is the batch fact; the rare detection is what may be wrong.
    assert focus == {
        "Rare": ("minority_counted", ("S3",)),
        "Common": ("minority_not_counted", ("S2",)),
        "Split": ("mixed", ("S1",)),
        "Absent": ("uniform", ()),
        "ISTD": ("uniform", ()),
    }
    assert [t.label for t in overview.targets][:3] == ["Rare", "Common", "Split"]
    assert (overview.focus_count, overview.focus_target_count) == (3, 3)
    assert (overview.analyte_counted, overview.analyte_total) == (7, 20)
    assert (overview.istd_counted, overview.istd_total) == (5, 5)
