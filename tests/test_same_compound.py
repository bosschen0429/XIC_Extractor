from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.matrix_review_fixtures import current_matrix_review_row
from xic_extractor.matrix_curation.istd import (
    IstdWindow,
    istd_window,
    windows_from_explanation,
)
from xic_extractor.matrix_curation.same_compound import (
    CONFIRMED_MH,
    RELATIONS,
    UNCONFIRMED,
    collapse_same_compound,
    group_outcome_review,
)
from xic_extractor.matrix_curation.steps import (
    CellEvidence,
    FeatureIdentity,
    StepResult,
)
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
    public_matrix_mz_text,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS, load_sample_metadata
from xic_extractor.tabular_io import read_tsv_with_header, write_tsv

SAMPLES = tuple(f"s{index}" for index in range(1, 7))
SAMPLE_SCALE = (1.0, 2.0, 3.0, 5.0, 8.0, 13.0)


def row(
    feature_id,
    mz,
    *,
    scale=1.0,
    shift_sec=0.0,
    base_rt=10.0,
    direct=True,
    samples=SAMPLES,
    ratios=None,
    shifts=None,
):
    """A row whose value is ``scale`` times a per-sample amount.

    Rows of one scenario share the per-sample amount, so their ratio is
    exactly ``scale`` unless ``ratios`` gives it per sample.
    """
    cells = {}
    for index, sample in enumerate(samples):
        factor = ratios[index] if ratios is not None else scale
        shift = shifts[index] if shifts is not None else shift_sec
        cells[sample] = CellEvidence(
            direct=direct[index] if isinstance(direct, tuple) else direct,
            value=1e6 * SAMPLE_SCALE[index] * factor,
            apex_rt_min=base_rt + index * 0.5 + shift / 60.0,
        )
    return FeatureIdentity(feature_id, mz, base_rt, "fixture_mz"), cells


def decide(*rows, relations=RELATIONS):
    features = tuple(feature for feature, _ in rows)
    result = collapse_same_compound(
        features, tuple(cells for _, cells in rows), relations
    )
    fid = [feature.feature_id for feature in features]
    decisions = {
        fid[index]: (
            decision.keep,
            decision.reason,
            None if decision.representative is None else fid[decision.representative],
            decision.relations,
            decision.ion_form,
        )
        for index, decision in enumerate(result.decisions)
    }
    return decisions, result


def outcome(result, relation):
    return {
        row["outcome"] for row in result.relation_rows if row["relation"] == relation
    }


def review_kinds(result):
    return [(row["review_kind"], row["feature_id"]) for row in result.review_rows]


@pytest.mark.parametrize(
    ("relation", "light_mz", "heavy_mz", "heavy_scale", "ion_form"),
    [
        ("13C_M+1", 300.0, 301.003355, 0.15, UNCONFIRMED),
        ("15N_M+1", 300.0, 300.997035, 0.02, UNCONFIRMED),
        ("13C2_M+2", 300.0, 302.006710, 0.02, UNCONFIRMED),
        # m/z 700 at z=2 is a neutral of about 1398 Da.
        ("13C_z2", 700.0, 700.501678, 0.5, UNCONFIRMED),
        ("41K", 400.0, 401.998119, 1 / 14, UNCONFIRMED),
        ("[M+Na]+", 300.0, 321.981944, 0.5, CONFIRMED_MH),
        ("[M+K]+", 300.0, 337.955882, 0.5, CONFIRMED_MH),
        ("[M+NH4]+", 300.0, 317.026549, 0.5, CONFIRMED_MH),
        ("[2M+H]+", 300.0, 2 * 300.0 - 1.00727647, 0.3, CONFIRMED_MH),
    ],
)
def test_each_folding_relation_removes_the_heavier_row(
    relation, light_mz, heavy_mz, heavy_scale, ion_form
):
    decisions, result = decide(
        row("L", light_mz), row("H", heavy_mz, scale=heavy_scale, shift_sec=1.0)
    )

    assert decisions["H"] == (
        False,
        "derived_from_representative",
        "L",
        (relation,),
        "",
    )
    assert decisions["L"] == (True, "representative", None, (), ion_form)
    # Isotope-only groups are common; their unconfirmed form is not reviewed.
    assert result.review_rows == ()


@pytest.mark.parametrize(
    ("light_mz", "heavy_mz", "light_to_heavy"),
    [(388.056, 389.054, 4.66), (500.143, 501.140, 2.06)],
)
def test_dr95_15n_mass_with_too_strong_heavy_row_is_not_an_isotope(
    light_mz, heavy_mz, light_to_heavy
):
    decisions, result = decide(
        row("L", light_mz), row("H", heavy_mz, scale=1 / light_to_heavy)
    )

    assert decisions["H"][:2] == (True, "no_folding_relation")
    assert outcome(result, "15N_M+1") == {"isotope_abundance_exceeded"}


def test_each_sample_must_fit_the_isotope_abundance_ceiling():
    # light/heavy 20, 20, 0.2: the log-ratio MAD is 0, but one sample's heavy
    # row is five times the light row.
    samples = SAMPLES[:3]
    decisions, result = decide(
        row("L", 300.0, samples=samples),
        row("H", 301.003355, samples=samples, ratios=(0.05, 0.05, 5.0)),
    )

    assert decisions["H"][0] is True
    assert outcome(result, "13C_M+1") == {"isotope_abundance_exceeded"}


def test_dr116_difference_is_only_annotated():
    decisions, result = decide(row("L", 300.0), row("H", 416.04734, scale=0.5))

    assert decisions["H"][:2] == (True, "no_folding_relation")
    assert outcome(result, "+116.047_dR") == {"annotation_only"}
    assert result.review_rows == ()


@pytest.mark.parametrize(
    "ratios",
    [
        (1 / 8,) * 6,
        (1 / 10,) * 6,
        # Median 14, but only four of six samples lie within 0.5-2x 13.857.
        (1 / 14, 1 / 14, 1 / 14, 1 / 14, 1 / 3, 1 / 3),
    ],
)
def test_1998_pairs_without_potassium_ratio_are_reviewed_not_folded(ratios):
    decisions, result = decide(row("L", 400.0), row("H", 401.998119, ratios=ratios))

    assert decisions["H"][:2] == (True, "no_folding_relation")
    assert outcome(result, "41K") == {"ratio_not_41K"}
    assert review_kinds(result) == [("unexplained_1998_not_41K", "H")]


def test_removed_row_coelutes_directly_with_its_representative():
    # A-B and B-C co-elute 2.4 s apart; A-C are 4.8 s apart.
    decisions, result = decide(
        row("A", 300.0),
        row("B", 301.003355, scale=0.15, shift_sec=2.4),
        row("C", 302.006710, scale=0.15 * 0.15, shift_sec=4.8),
    )

    assert decisions["A"][:2] == (True, "representative")
    assert decisions["B"][:3] == (False, "derived_from_representative", "A")
    # C is reached from A only through B and never co-elutes with A.
    assert decisions["C"][:2] == (True, "no_direct_coelution_with_representative")
    assert outcome(result, "13C2_M+2") == set()
    assert review_kinds(result) == [("no_direct_coelution_with_representative", "C")]


def test_protonated_row_with_sodium_adduct_and_isotopes_is_confirmed_and_kept():
    decisions, result = decide(
        row("MH", 300.0),
        row("MH_13C", 301.003355, scale=0.15),
        row("Na", 321.981944, scale=0.5),
        row("Na_13C", 322.985299, scale=0.075),
    )

    assert decisions["MH"] == (True, "representative", None, (), CONFIRMED_MH)
    assert decisions["MH_13C"][:4] == (
        False,
        "derived_from_representative",
        "MH",
        ("13C_M+1",),
    )
    assert decisions["Na"][:4] == (
        False,
        "derived_from_representative",
        "MH",
        ("[M+Na]+",),
    )
    # The Na isotope relates to MH only through other rows, but co-elutes
    # with MH itself.
    assert decisions["Na_13C"][:4] == (
        False,
        "derived_through_group",
        "MH",
        ("13C_M+1", "[M+Na]+"),
    )
    assert result.review_rows == ()


@pytest.mark.parametrize(
    ("parent_mz", "isotope_mz"),
    [
        # A sodium adduct and its isotope, without the protonated row.
        (321.981944, 322.985299),
        # A doubly charged parent and its isotope.
        (700.0, 700.501678),
    ],
)
def test_parent_without_adduct_evidence_stays_unconfirmed_without_review(
    parent_mz, isotope_mz
):
    decisions, result = decide(row("P", parent_mz), row("I", isotope_mz, scale=0.15))

    assert decisions["P"] == (True, "representative", None, (), UNCONFIRMED)
    assert decisions["I"][0] is False
    assert result.review_rows == ()


def test_several_passing_mass_hypotheses_do_not_depend_on_rule_order():
    # At m/z 900 the heavy row lies 3.5 ppm from both 13C and 15N.
    rows = (row("L", 900.0), row("H", 901.000195, scale=0.1))
    forward, _ = decide(*rows)
    backward, _ = decide(*rows, relations=tuple(reversed(RELATIONS)))

    assert forward == backward
    assert forward["H"][:4] == (
        False,
        "derived_from_representative",
        "L",
        ("13C_M+1", "15N_M+1"),
    )


def test_competing_representatives_keep_the_row_for_review():
    # H is A's 13C isotope and B's sodium adduct.
    decisions, result = decide(
        row("A", 300.0),
        row("B", 279.021411),
        row("H", 301.003355, scale=0.15),
    )

    assert decisions["H"][:2] == (True, "competing_representatives")
    assert ("competing_representatives", "H") in review_kinds(result)
    assert decisions["A"][:2] == decisions["B"][:2] == (True, "no_folding_relation")


def test_backfilled_cells_never_count_toward_coelution():
    only_backfill = row("H", 321.981944, scale=0.5, direct=False)
    decisions, result = decide(row("L", 300.0), only_backfill)
    assert decisions["H"][0] is True
    assert review_kinds(result) == [("backfill_only_coelution", "H")]

    # Two direct samples plus four backfilled ones are still too few.
    mixed = row(
        "H", 321.981944, scale=0.5, direct=(True, True, False, False, False, False)
    )
    decisions, result = decide(row("L", 300.0), mixed)
    assert decisions["H"][0] is True
    assert review_kinds(result) == [("backfill_only_coelution", "H")]


def test_coelution_needs_most_shared_direct_samples():
    samples = SAMPLES[:5]
    decisions, result = decide(
        row("L", 300.0, samples=samples),
        row(
            "H",
            321.981944,
            scale=0.5,
            samples=samples,
            shifts=(1.0, 1.0, 1.0, 10.0, 10.0),
        ),
    )

    assert decisions["H"][0] is True
    assert outcome(result, "[M+Na]+") == {"coelution_insufficient"}


def test_unstable_ratio_and_review_only_relations_are_reviewed():
    unstable = row("U", 321.981944, ratios=(0.5, 2.0, 0.5, 2.0, 0.5, 2.0))
    methylated = row("M", 314.01565, scale=0.5)
    decisions, result = decide(row("L", 300.0), unstable, methylated)

    assert decisions["U"][0] is decisions["M"][0] is True
    assert sorted(review_kinds(result)) == [
        ("review_only_relation", "M"),
        ("unstable_ratio", "U"),
    ]


def test_representative_much_weaker_than_removed_member_is_reviewed():
    decisions, result = decide(row("L", 300.0), row("Na", 321.981944, scale=20.0))

    assert decisions["Na"][0] is False
    assert review_kinds(result) == [("representative_weaker_than_member", "L")]


def test_recurring_unexplained_difference_is_listed_by_frequency():
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"H{index}", 310.0 + 50 * index, base_rt=base_rt, scale=0.5),
        )
    ]
    _, result = decide(*rows)

    assert [
        (row["review_kind"], row["relation"], row["detail"])
        for row in result.review_rows
    ] == [("recurring_unexplained_mass_difference", "delta_mz=10.0000", "pair_count=3")]


def _scenario():
    """Several groups, kept apart in RT so they never co-elute."""
    return (
        row("A", 300.0),
        row("B", 301.003355, scale=0.15, shift_sec=2.4),
        row("C", 302.006710, scale=0.0225, shift_sec=4.8),
        row("MH", 400.0, base_rt=20.0),
        row("MH_13C", 401.003355, scale=0.15, base_rt=20.0),
        row("Na", 421.981944, scale=0.5, base_rt=20.0),
        row("Na_13C", 422.985299, scale=0.075, base_rt=20.0),
        row("X", 900.0, base_rt=30.0),
        row("Y", 901.000195, scale=0.1, base_rt=30.0),
        row("Z", 500.0, base_rt=40.0),
    )


@pytest.mark.parametrize("seed", range(4))
def test_decisions_follow_bound_rows_not_their_order_or_ids(seed):
    import random

    rows = list(_scenario())
    expected, _ = decide(*rows)
    random.Random(seed).shuffle(rows)
    shuffled, _ = decide(*rows)
    assert shuffled == expected

    # IDs that sort in the opposite order change nothing either.
    renamed = {
        feature.feature_id: f"F{999 - index:03d}"
        for index, (feature, _) in enumerate(rows)
    }
    back = {value: key for key, value in renamed.items()}
    relabeled, _ = decide(
        *(
            (
                FeatureIdentity(
                    renamed[feature.feature_id],
                    feature.matrix_mz,
                    feature.matrix_rt,
                    feature.center_mz_basis,
                ),
                cells,
            )
            for feature, cells in rows
        )
    )
    assert {
        back[fid]: (*value[:2], back.get(value[2]), *value[3:])
        for fid, value in relabeled.items()
    } == expected


def test_group_review_follows_other_steps_and_counts_empty_groups():
    result = StepResult(
        columns=("same_compound_representative_feature_id",),
        rows=(
            {"same_compound_representative_feature_id": ""},
            {"same_compound_representative_feature_id": "R"},
            {"same_compound_representative_feature_id": "R"},
        ),
        keep=(True, False, False),
    )
    review, empty = group_outcome_review(
        result, ["R", "M1", "M2"], [False, True, False], [False, False, False]
    )
    assert [(row["review_kind"], row["feature_id"]) for row in review] == [
        ("representative_dropped_member_passes", "R")
    ]
    assert review[0]["related_feature_ids"] == "M1"
    assert empty == 1

    _, empty = group_outcome_review(
        result, ["R", "M1", "M2"], [False, True, False], [True, False, False]
    )
    assert empty == 0


def test_istd_window_uses_range_or_expected_rt():
    assert istd_window(
        label="d3", mz=245.0, rt_min=11.0, rt_max=13.0, expected_rt_min=12.0
    ) == IstdWindow("d3", 245.0, 11.0, 13.0)
    # Workbench lists record rt_min == rt_max; that is no range.
    assert istd_window(
        label="d3", mz=245.0, rt_min=12.0, rt_max=12.0, expected_rt_min=12.0
    ) == IstdWindow("d3", 245.0, 11.25, 12.75)
    assert (
        istd_window(label="x", mz=245.0, rt_min=None, rt_max=None, expected_rt_min=None)
        is None
    )

    target = {
        "label": "d3",
        "mz": 245.0,
        "rt_min": 12.0,
        "rt_max": 12.0,
        "expected_rt_min": 12.0,
    }
    summary = {
        "known_istd_reference": {
            "target_snapshot": json.dumps(
                {
                    "definitions": [
                        {"target_label": "d3", "target": {**target, "is_istd": True}},
                        {
                            "target_label": "analyte",
                            "target": {**target, "label": "analyte", "is_istd": False},
                        },
                    ]
                }
            )
        }
    }
    assert windows_from_explanation(summary) == (IstdWindow("d3", 245.0, 11.25, 12.75),)
    assert windows_from_explanation({"known_istd_reference": None}) == ()


# Export: the step with Feature Quality, internal standards and real folders.

STUDY = ("S1", "S2", "S3")


def _curation_folder(folder: Path) -> Path:
    """A Matrix whose protonated row fails finite fraction; its adduct passes."""
    folder.mkdir(parents=True)
    rows = (
        ("F000001", 300.0, 10.0, ("", "", "5")),
        ("F000002", 321.981944, 10.0, ("2", "4", "6")),
        ("F000003", 500.0, 20.0, ("1", "1", "1")),
        ("F000004", 300.0, 15.0, ("", "", "")),
    )
    matrix, review = [], []
    for index, (feature_id, mz, rt, values) in enumerate(rows, start=1):
        sample_values = dict(zip(STUDY, values, strict=True))
        matrix.append(
            {"Mz": public_matrix_mz_text(repr(mz)), "RT": str(rt), **sample_values}
        )
        review.append(
            current_matrix_review_row(
                index=index,
                mz=repr(mz),
                rt=str(rt),
                feature_id=feature_id,
                sample_values=sample_values,
            )
        )
    write_tsv(folder / "alignment_matrix.tsv", matrix, ("Mz", "RT", *STUDY))
    write_tsv(folder / "alignment_review.tsv", review, MATRIX_REVIEW_COLUMNS)
    return folder


def _samples(path: Path) -> Path:
    write_tsv(
        path,
        [
            {
                "schema_version": "sample_metadata",
                "sample_name": sample,
                "raw_stem": sample,
                "sample_role": "study_sample",
                "excluded": "FALSE",
                "exclusion_reason": "",
            }
            for sample in STUDY
        ],
        SAMPLE_METADATA_COLUMNS,
    )
    return path


@pytest.fixture
def synthetic_cells(monkeypatch):
    """Per-cell evidence for _curation_folder, as the explanation would give it."""
    by_feature = {
        "F000001": row("F000001", 300.0)[1],
        "F000002": row("F000002", 321.981944, scale=0.5)[1],
        "F000003": row("F000003", 500.0, base_rt=20.0)[1],
        "F000004": {},
    }
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.cell_evidence_problem",
        lambda source: "",
    )
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.load_cell_evidence",
        lambda source, features: tuple(
            by_feature[feature.feature_id] for feature in features
        ),
    )


def _export(tmp_path, name, steps, istd=None):
    from xic_extractor.matrix_curation.export import (
        export_matrix_curation,
        locate_curation_source,
    )

    source = locate_curation_source(tmp_path / "alignment")
    output = tmp_path / name
    summary = export_matrix_curation(
        source=source,
        sample_metadata=load_sample_metadata(tmp_path / "samples.tsv"),
        sample_source="samples.tsv",
        steps=steps,
        output_dir=output,
        istd_windows=istd,
        istd_source="fixture" if istd else "",
    )
    _, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    return (
        output,
        {row["feature_id"]: row for row in decisions},
        json.loads(summary.read_text(encoding="utf-8")),
    )


def test_steps_judge_independently_and_istd_overrides_their_conjunction(
    tmp_path, synthetic_cells
):
    _curation_folder(tmp_path / "alignment")
    _samples(tmp_path / "samples.tsv")
    fraction = {"min_finite_fraction": 0.6}
    _, both, summary = _export(
        tmp_path, "both", {"finite_fraction": fraction, "same_compound": {}}
    )
    _, fraction_only, _ = _export(tmp_path, "ff", {"finite_fraction": fraction})
    output, same_only, _ = _export(tmp_path, "sc", {"same_compound": {}})

    for feature, decision in both.items():
        for column, value in decision.items():
            if column.startswith("finite_"):
                assert fraction_only[feature][column] == value
            if column.startswith("same_compound_"):
                assert same_only[feature][column] == value
    assert both["F000002"]["same_compound_representative_feature_id"] == "F000001"
    assert both["F000001"]["same_compound_ion_form"] == CONFIRMED_MH
    assert [both[f"F00000{index}"]["decision"] for index in range(1, 5)] == [
        "drop",
        "drop",
        "keep",
        "drop",
    ]
    assert both["F000001"]["decision_reason"] == "dropped_by:finite_fraction"
    assert both["F000002"]["decision_reason"] == "dropped_by:same_compound"
    _, review = read_tsv_with_header(tmp_path / "both" / "same_compound_review.tsv")
    assert [
        (row["review_kind"], row["feature_id"], row["related_feature_ids"])
        for row in review
    ] == [("representative_dropped_member_passes", "F000001", "F000002")]
    step = summary["steps"][1]
    assert (step["removed_feature_count"], step["empty_group_count"]) == (1, 1)
    # Without another step nothing can empty a group.
    _, review = read_tsv_with_header(output / "same_compound_review.tsv")
    assert review == []

    # The standard at m/z 300 elutes at 9-11 min: F000004 at 15 min is not it.
    istd = (IstdWindow("d0", 300.0, 9.0, 11.0),)
    _, protected, summary = _export(
        tmp_path, "istd", {"finite_fraction": fraction, "same_compound": {}}, istd
    )
    assert protected["F000001"]["istd_label"] == "d0"
    assert protected["F000001"]["finite_fraction_decision"] == "drop"
    assert (
        protected["F000001"]["decision"],
        protected["F000001"]["decision_reason"],
    ) == (
        "keep",
        "istd_protected",
    )
    assert (protected["F000004"]["istd_label"], protected["F000004"]["decision"]) == (
        "",
        "drop",
    )
    assert summary["istd"]["protected_feature_count"] == 1
    assert summary["steps"][1]["empty_group_count"] == 0
    assert summary["steps"][1]["review_by_kind"] == {
        "representative_dropped_member_passes": 1
    }


def test_same_compound_is_unavailable_without_the_run_explanation(tmp_path):
    from tests.test_matrix_curation import _run
    from xic_workbench.matrix_curation import describe_source

    source = _curation_folder(tmp_path / "alignment")
    samples = _samples(tmp_path / "samples.tsv")
    steps = {
        step["key"]: step["unavailable_reason"]
        for step in describe_source(
            tmp_path / "config",
            {"source_dir": str(source), "sample_metadata_path": str(samples)},
        )["steps"]
    }
    assert steps["finite_fraction"] == ""
    assert "cid_nl_explanation.sqlite" in steps["same_compound"]

    output = tmp_path / "curated"
    result = _run(source, samples, output, "--same-compound", threshold=None)
    assert result.returncode == 2
    assert "cid_nl_explanation.sqlite" in result.stderr
    assert not output.exists()


def test_machine_output_without_backfill_ledger_is_curated(tmp_path, monkeypatch):
    from tests.test_duplicate_quantification import publish_owner_run
    from tests.test_matrix_curation import _run

    source = publish_owner_run(tmp_path, monkeypatch, "machine")
    assert not (source / "alignment_backfill_cell_evidence.tsv").exists()
    assert not (source / "alignment_cells.tsv").exists()
    output = tmp_path / "curated"

    result = _run(source, None, output, "--same-compound", threshold=None)

    assert result.returncode == 0, result.stderr
    _, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    assert decisions and {row["same_compound_decision"] for row in decisions} == {
        "keep"
    }
    assert (output / "same_compound_relations.tsv").is_file()
    summary = json.loads(
        (output / "matrix_curation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["inputs"]["explanation"].endswith("cid_nl_explanation.sqlite")


def test_frequency_table_counts_a_pair_once_whichever_row_elutes_first():
    # Each pair swaps elution order halfway through the samples.
    flip = (1.0, 1.0, 1.0, -1.0, -1.0, -1.0)
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"H{index}", 310.0 + 50 * index, base_rt=base_rt, shifts=flip),
        )
    ]
    _, result = decide(*rows)

    assert [row["detail"] for row in result.review_rows] == ["pair_count=3"]


def test_removed_ions_neither_repeat_differences_nor_review_pairs():
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"X{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"X{index}_13C", 301.003355 + 50 * index, base_rt=base_rt, scale=0.15),
            # +CH2 relative to X: a possibly different compound to review.
            row(f"Y{index}", 314.01565 + 50 * index, base_rt=base_rt, scale=0.5),
            row(
                f"Y{index}_13C",
                315.019005 + 50 * index,
                base_rt=base_rt,
                scale=0.075,
            ),
        )
    ]
    decisions, result = decide(*rows)

    assert {fid for fid, value in decisions.items() if not value[0]} == {
        f"{name}{index}_13C" for name in "XY" for index in range(3)
    }
    # Only the kept rows' pair is reviewed, not the same pair of their isotopes.
    assert sorted(review_kinds(result)) == [
        ("review_only_relation", f"Y{index}") for index in range(3)
    ]


def test_frequency_table_needs_most_shared_direct_samples_to_coelute():
    # Three of five shared direct samples co-elute: below 80%.
    samples = SAMPLES[:5]
    shifts = (1.0, 1.0, 1.0, 10.0, 10.0)
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt, samples=samples),
            row(
                f"H{index}",
                310.0 + 50 * index,
                base_rt=base_rt,
                samples=samples,
                shifts=shifts,
            ),
        )
    ]
    _, result = decide(*rows)

    assert result.review_rows == ()
