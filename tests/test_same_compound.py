from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.matrix_review_fixtures import current_matrix_review_row
from xic_extractor.matrix_curation.istd import windows_from_target_snapshot
from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow, mz_rt_window
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
TWO_DIRECT = (True, True, False, False, False, False)


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
        detected = direct[index] if isinstance(direct, tuple) else direct
        cells[sample] = CellEvidence(
            origin="detected" if detected else "backfilled",
            value=1e6 * SAMPLE_SCALE[index] * factor,
            apex_rt_min=base_rt + index * 0.5 + shift / 60.0,
        )
    return FeatureIdentity(feature_id, mz, base_rt), cells


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
        ("13C_z2", 700.0, 700.501678, 0.5, UNCONFIRMED),
        ("41K", 400.0, 401.998119, 1 / 14, UNCONFIRMED),
        ("[M+Na]+", 300.0, 321.981944, 0.5, CONFIRMED_MH),
        ("[M+K]+", 300.0, 337.955882, 0.5, CONFIRMED_MH),
        ("[M+NH4]+", 300.0, 317.026549, 0.5, CONFIRMED_MH),
        ("[2M+H]+", 300.0, 2 * 300.0 - 1.00727647, 0.3, CONFIRMED_MH),
        ("[2M-H+Mg]+", 300.0, 620.962115180219, 0.3, CONFIRMED_MH),
        ("[2M-H+Ca]+", 300.0, 636.939664346219, 0.3, CONFIRMED_MH),
        ("+NaHCOO", 300.0, 367.987423553370, 0.3, UNCONFIRMED),
        ("+NaCH3COO", 300.0, 382.003073617830, 0.3, UNCONFIRMED),
        ("[M+Mg+HCOO]+", 300.0, 367.974871, 0.3, CONFIRMED_MH),
        ("[M+Mg+CH3COO]+", 300.0, 381.990521, 0.3, CONFIRMED_MH),
        ("[M+Ca+HCOO]+", 300.0, 383.952420, 0.3, CONFIRMED_MH),
        ("[M+Ca+CH3COO]+", 300.0, 397.968070, 0.3, CONFIRMED_MH),
        ("[M+Fe+CH3COO]+", 300.0, 413.940417, 0.3, CONFIRMED_MH),
        ("[M+K]+/[M+Na]+", 322.0, 337.973938, 0.3, UNCONFIRMED),
        ("[M+Mg+CH3COO]+/[M+Na]+", 322.0, 382.008577, 0.3, UNCONFIRMED),
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
    samples = SAMPLES[:3]
    decisions, result = decide(
        row("L", 300.0, samples=samples),
        row("H", 301.003355, samples=samples, ratios=(0.05, 0.05, 5.0)),
    )

    assert decisions["H"][0] is True
    assert outcome(result, "13C_M+1") == {"isotope_abundance_exceeded"}


@pytest.mark.parametrize(
    ("companion", "heavy_scale", "expected"),
    [
        (True, 0.38, "collapse"),
        (False, 0.38, "isotope_abundance_exceeded"),
        (True, 0.7, "isotope_abundance_exceeded"),
    ],
)
def test_a_metal_carrier_m1_ceiling_admits_25mg(companion, heavy_scale, expected):
    rows = [row("L", 400.0), row("H", 401.003355, scale=heavy_scale)]
    if companion:
        rows.append(row("M2", 401.997551, scale=1 / 3.587))
    decisions, result = decide(*rows)

    assert decisions["H"][0] is (expected != "collapse")
    found = relation_row_between(result, "L", "H", "13C_M+1")
    assert found["outcome"] == expected


def relation_row_between(result, light, heavy, relation):
    (found,) = (
        row
        for row in result.relation_rows
        if (row["light_feature_id"], row["heavy_feature_id"], row["relation"])
        == (light, heavy, relation)
    )
    return found


def test_dr116_difference_is_only_annotated():
    decisions, result = decide(row("L", 300.0), row("H", 416.04734, scale=0.5))

    assert decisions["H"][:2] == (True, "no_folding_relation")
    assert outcome(result, "+116.047_dR") == {"annotation_only"}
    assert result.review_rows == ()


@pytest.mark.parametrize(
    "ratios",
    [
        (1 / 10,) * 6,
        (1 / 14, 1 / 14, 1 / 14, 1 / 14, 1 / 3, 1 / 3),
    ],
)
def test_1998_pairs_without_potassium_ratio_are_reviewed_not_folded(ratios):
    decisions, result = decide(row("L", 400.0), row("H", 401.998119, ratios=ratios))

    assert decisions["H"][:2] == (True, "no_folding_relation")
    assert outcome(result, "41K") == {"ratio_not_41K"}
    assert review_kinds(result) == [("unexplained_1998_not_41K", "H")]


@pytest.mark.parametrize("light_heavy", [7.27, 8.68, 3.19, 2.97, 6.93, 1.91, 1.48])
def test_unassigned_m_plus_2_is_removed_without_claiming_an_element(light_heavy):
    decisions, result = decide(
        row("L", 343.118794),
        row("H", 345.116345, scale=1 / light_heavy),
    )

    assert decisions["H"][:4] == (
        False,
        "derived_from_representative",
        "L",
        ("M+2_unassigned",),
    )
    assert result.decisions[1].undetermined is True
    assert decisions["L"][4] == UNCONFIRMED
    assert outcome(result, "41K") == {"ratio_not_41K"}
    assert result.review_rows == ()


def test_unassigned_isotope_checks_individual_ratios_not_just_median_and_mad():
    decisions, result = decide(
        row("L", 343.118794),
        row("H", 345.116345, ratios=(1 / 7.2,) * 4 + (1 / 0.72,) * 2),
    )
    assert decisions["H"][0] is True
    assert outcome(result, "M+2_unassigned") == {"isotope_ratio_incompatible"}


def test_element_uncertainty_follows_a_derivative_path():
    decisions, result = decide(
        row("L", 300.0),
        row("M2", 301.997551, scale=1 / 7.174),
        row("M3", 303.000906, scale=0.1 / 7.174),
    )
    assert decisions["M3"][:3] == (False, "derived_through_group", "L")
    assert result.decisions[2].undetermined is True


def test_a_derivative_contradicting_its_root_has_no_representative():
    decisions, result = decide(
        row("A", 300.0),
        row("B", 301.003355, scale=0.15, shift_sec=4.4),
        row("C", 302.006710, scale=0.15 * 0.15, shift_sec=8.8),
    )

    assert decisions["A"][:2] == (True, "representative")
    assert decisions["B"][:3] == (False, "derived_from_representative", "A")
    assert decisions["C"][:3] == (False, "derived_representative_undetermined", None)
    assert outcome(result, "13C2_M+2") == set()
    assert result.review_rows[0]["review_kind"] == "derived_representative_undetermined"
    assert result.review_rows[0]["related_feature_ids"] == "A"


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
        (321.981944, 322.985299),
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


def test_competing_representatives_do_not_rescue_a_supported_derivative():
    decisions, result = decide(
        row("A", 300.0),
        row("B", 279.021411),
        row("H", 301.003355, scale=0.15),
    )

    assert decisions["H"][:3] == (False, "derived_representative_undetermined", None)
    assert result.decisions[2].undetermined is True
    assert ("derived_representative_undetermined", "H") in review_kinds(result)
    assert decisions["A"][0] is decisions["B"][0] is True
    assert decisions["A"][4] == decisions["B"][4] == UNCONFIRMED
    assert all(r["used_for_collapse"] == "TRUE" for r in result.relation_rows)


def test_mg_ca_family_keeps_protonated_row_and_removes_unassigned_isotope():
    decisions, result = decide(
        row("MH", 261.127824),
        row("Mg", 343.118345, scale=0.43),
        row("Ca", 359.095894, scale=0.1),
        row("Mg_M2", 345.115896, scale=0.43 / 7.174),
    )
    assert decisions["MH"][0] is True
    assert decisions["MH"][4] == CONFIRMED_MH
    assert all(not decisions[f][0] for f in ("Mg", "Ca", "Mg_M2"))
    assert decisions["Mg_M2"][:3] == (False, "derived_through_group", "MH")
    assert result.decisions[3].undetermined is True


def sodium_family(*, protonated=False):
    neutral = 227.09060590
    mg_response = (0.5, 2.0, 0.5, 2.0, 0.5, 2.0)
    rows = [
        row("Na", neutral + 22.989220702091),
        row("MgFA", neutral + 68.982147388461, ratios=mg_response),
        row("MgAc", neutral + 82.997797452921, scale=0.8),
        row("DimerH", 2 * neutral + 1.007276452321, scale=0.3),
        row(
            "MgFA_M2",
            neutral + 68.982147388461 + 1.997551271,
            ratios=tuple(x * 0.1101 / 0.7899 for x in mg_response),
        ),
        row("MetalDimer", 2 * neutral + 44.958612334631, scale=0.4),
        row(
            "MetalDimer_M2",
            2 * neutral + 44.958612334631 + 1.997551271,
            scale=0.4 * 0.1101 / 0.7899,
        ),
        row("Na_13C", neutral + 22.989220702091 + 1.003355, scale=0.1),
        row("WeakComplex", neutral + 232.975742006891, scale=0.2),
        row("BiologicalH2", neutral + 22.989220702091 + 2.015650, scale=0.6),
    ]
    if protonated:
        rows.append(row("MH", neutral + 1.007276452321))
    return rows


@pytest.mark.parametrize("protonated", [False, True])
def test_supported_ion_family_excludes_non_mh_without_requiring_a_protonated_row(
    protonated,
):
    decisions, result = decide(*sodium_family(protonated=protonated))
    for name in ("Na", "MgFA", "MgAc", "DimerH", "MetalDimer"):
        assert decisions[name][0] is False
        assert decisions[name][1] == "non_protonated_ion_family"
        assert decisions[name][2] == ("MH" if protonated else None)
    for name in ("Na_13C", "MgFA_M2", "MetalDimer_M2"):
        assert decisions[name][0] is False
        assert decisions[name][2] == ("MH" if protonated else None)
    assert decisions["WeakComplex"][0] is True
    assert decisions["BiologicalH2"][0] is False
    if protonated:
        assert decisions["MH"][0] is True
    assert any(
        r["review_kind"] == "non_protonated_ion_family" for r in result.review_rows
    )


@pytest.mark.parametrize("missing", ["DimerH", "MgFA_M2", "MgAc"])
def test_mass_compatible_family_requires_independent_dimer_and_isotope_witnesses(
    missing,
):
    rows = [r for r in sodium_family() if r[0].feature_id != missing]
    decisions, _ = decide(*rows)
    assert decisions["Na"][0] is True
    assert all(d[1] != "non_protonated_ion_family" for d in decisions.values())


def test_a_family_member_does_not_confirm_its_previous_adduct_root():
    decisions, result = decide(row("Candidate", 212.123944602091), *sodium_family())

    assert relation_row_between(result, "Candidate", "Na", "[M+K]+")["outcome"] == (
        "collapse"
    )
    assert decisions["Na"][:3] == (False, "non_protonated_ion_family", None)
    assert decisions["Candidate"][0] is True
    assert decisions["Candidate"][4] == UNCONFIRMED


@pytest.mark.parametrize(
    ("carrier", "kept"),
    [
        ("MgFA", ("Na", "MgFA", "DimerH", "MetalDimer")),
        ("MetalDimer", ("MetalDimer",)),
    ],
)
def test_backfill_can_collapse_a_metal_isotope_but_not_support_a_family(carrier, kept):
    companion = carrier + "_M2"
    rows = [
        (
            feature,
            {
                sample: replace(cell, origin="detected" if direct else "backfilled")
                for (sample, cell), direct in zip(
                    cells.items(), TWO_DIRECT, strict=True
                )
            },
        )
        if feature.feature_id == companion
        else (feature, cells)
        for feature, cells in sodium_family()
    ]
    decisions, result = decide(*rows)

    assert all(decisions[name][0] for name in kept)
    assert decisions[companion][:3] == (False, "derived_from_representative", carrier)
    pair = relation_row_between(result, carrier, companion, "M+2_unassigned")
    assert pair["backfill_coeluting_samples"] == 4
    assert pair["outcome"] == "collapse"
    if carrier == "MetalDimer":
        assert decisions["Na"][:2] == (False, "non_protonated_ion_family")


@pytest.mark.parametrize("direct", [False, TWO_DIRECT])
def test_backfilled_dimer_cannot_establish_an_ion_family(direct):
    rows = [r for r in sodium_family() if r[0].feature_id != "DimerH"]
    rows.append(row("DimerH", 455.188488252321, scale=0.3, direct=direct))
    decisions, _ = decide(*rows)
    assert decisions["Na"][0] is True


def test_ion_family_does_not_use_transitive_coelution_to_delete_a_member():
    rows = [r for r in sodium_family() if r[0].feature_id != "MgAc"]
    rows = [r for r in rows if r[0].feature_id != "DimerH"]
    rows.append(row("DimerH", 455.188488252321, scale=0.3, shift_sec=8.8))
    rows.append(row("MgAc", 310.088403352921, scale=0.8, shift_sec=4.4))
    decisions, _ = decide(*rows)
    assert decisions["Na"][0] is True


@pytest.mark.parametrize(
    ("protonated", "independent_isotope"),
    [(False, False), (True, False), (True, True)],
)
def test_competing_protonated_family_is_not_mistaken_for_element_only_ambiguity(
    protonated, independent_isotope
):
    neutral_n = 227.09060590 + 22.989220702091 - 1.007276452321
    rows = [
        *sodium_family(protonated=protonated),
        row("N_Na", neutral_n + 22.989220702091),
        row("N_MgFA", neutral_n + 68.982147388461, scale=0.5),
        row("N_MgAc", neutral_n + 82.997797452921, scale=0.6),
        row("N_DimerH", 2 * neutral_n + 1.007276452321, scale=0.3),
        row(
            "N_MgFA_M2",
            neutral_n + 68.982147388461 + 1.997551271,
            scale=0.5 * 0.1101 / 0.7899,
        ),
    ]
    if independent_isotope:
        rows.append(row("OtherRoot", 250.079826602091 - 1.003355, scale=10.0))
    decisions, result = decide(*rows)
    assert decisions["Na"][0] is (not independent_isotope)
    if not independent_isotope:
        assert decisions["Na"][4] == UNCONFIRMED
        assert result.decisions[0].undetermined is True
        assert decisions["N_Na"][2] == "Na"
    review = next(
        r
        for r in result.review_rows
        if r["review_kind"] == "ion_family_form_undetermined"
        and r["feature_id"] == "Na"
    )
    assert review["detail"] == (
        "decision=drop" if independent_isotope else "decision=keep_candidate_mh"
    )
    assert all(d[2] is None or decisions[d[2]][0] for d in decisions.values())
    reversed_decisions, _ = decide(*reversed(rows))
    assert reversed_decisions == decisions


def test_family_never_assigns_a_removed_protonated_candidate_as_representative():
    decisions, _ = decide(
        *sodium_family(protonated=True),
        row("OtherRoot", 228.097882352321 - 1.003355, scale=10.0),
    )
    assert decisions["MH"][0] is False
    assert decisions["Na"][2] is None
    assert all(d[2] is None or decisions[d[2]][0] for d in decisions.values())


@pytest.mark.parametrize(
    ("isotope_direct", "calcium_shift", "qualified"),
    [
        (None, 0.0, False),
        (True, 0.0, True),
        (True, 8.8, False),
        (TWO_DIRECT, 0.0, False),
    ],
)
def test_parallel_metal_multimers_can_support_a_variable_adduct_ratio(
    isotope_direct, calcium_shift, qualified
):
    neutral = 267.09675390
    rows = [
        row("MH", neutral + 1.007276452321),
        row(
            "MgTrimer",
            3 * neutral + 22.976668084861,
            scale=0.3,
            shift_sec=calcium_shift / 2,
        ),
        row(
            "CaTrimer",
            3 * neutral + 38.954217250861,
            ratios=(0.1, 0.6, 0.1, 0.6, 0.1, 0.6),
            shift_sec=calcium_shift,
        ),
    ]
    if isotope_direct is not None:
        rows.append(
            row(
                "MgTrimer_M2",
                3 * neutral + 22.976668084861 + 1.997551271,
                scale=0.3 * 0.1101 / 0.7899,
                direct=isotope_direct,
            )
        )
    decisions, _ = decide(*rows)
    assert decisions["MH"][0] is True
    assert decisions["MgTrimer"][0] is (not qualified)
    assert decisions["CaTrimer"][2] == ("MH" if qualified else "MgTrimer")
    if qualified:
        assert decisions["MgTrimer_M2"][2] == "MH"


def test_competing_paths_without_a_coeluting_root_list_every_root():
    decisions, result = decide(
        row("A", 300.0),
        row("B", 279.021411),
        row("P", 301.003355, scale=0.15, shift_sec=4.4),
        row("H", 302.006710, scale=0.0225, shift_sec=8.8),
    )
    assert decisions["P"][:3] == (False, "derived_representative_undetermined", None)
    assert decisions["H"][:3] == (False, "derived_representative_undetermined", None)
    assert {
        row["related_feature_ids"]
        for row in result.review_rows
        if row["review_kind"] == "derived_representative_undetermined"
    } == {"A;B"}


def only_in(scenario, samples):
    feature, cells = scenario
    return feature, {sample: cells[sample] for sample in samples}


def test_a_sparse_root_without_contradiction_still_represents_the_chain():
    decisions, _ = decide(
        only_in(row("L", 300.0), SAMPLES[:3]),
        row("N", 321.981944, scale=0.5),
        only_in(row("H", 322.985299, scale=0.08), SAMPLES[:1] + SAMPLES[3:]),
    )

    assert decisions["N"][:3] == (False, "derived_from_representative", "L")
    assert decisions["H"][:3] == (False, "derived_through_group", "L")


def without_apex(scenario, samples):
    feature, cells = scenario
    return feature, {
        sample: replace(cell, apex_rt_min=None) if sample in samples else cell
        for sample, cell in cells.items()
    }


def relation_row(result, relation):
    (found,) = (row for row in result.relation_rows if row["relation"] == relation)
    return found


def test_backfilled_apices_complete_direct_coelution():
    decisions, result = decide(
        row("L", 300.0), row("H", 321.981944, scale=0.5, direct=TWO_DIRECT)
    )

    assert decisions["H"][:3] == (False, "derived_from_representative", "L")
    found = relation_row(result, "[M+Na]+")
    assert found["outcome"] == "collapse"
    assert found["both_direct_samples"] == 2
    assert found["support_samples"] == found["ratio_samples"] == 6
    assert found["backfill_coeluting_samples"] == 4
    assert review_kinds(result) == []


def test_backfilled_ratios_must_stay_stable():
    unstable = row(
        "H",
        301.003355,
        ratios=(0.05, 0.05, 0.2, 0.2, 0.2, 0.05),
        direct=TWO_DIRECT,
    )
    decisions, result = decide(row("L", 300.0), unstable)

    assert decisions["H"][0] is True
    assert outcome(result, "13C_M+1") == {"ratio_unstable"}


def test_backfilled_isotope_ratio_obeys_the_abundance_ceiling():
    exceeded = row(
        "H",
        301.003355,
        ratios=(0.15, 0.15, 0.15, 0.15, 0.5, 0.15),
        direct=TWO_DIRECT,
    )
    decisions, result = decide(row("L", 300.0), exceeded)

    assert decisions["H"][0] is True
    assert outcome(result, "13C_M+1") == {"isotope_abundance_exceeded"}


@pytest.mark.parametrize(
    ("relation", "heavy_mz", "ratios"),
    [
        ("13C_M+1", 301.003355, (0.05, 0.05, 0.05, 0.2, 0.2, 0.2)),
        ("13C_M+1", 301.003355, (0.15, 0.15, 0.15, 0.5, 0.5, 0.5)),
    ],
)
def test_backfill_never_overrides_sufficient_direct_evidence(
    relation, heavy_mz, ratios
):
    three_direct = (True, True, True, False, False, False)
    decisions, result = decide(
        row("L", 300.0), row("H", heavy_mz, ratios=ratios, direct=three_direct)
    )

    assert decisions["H"][:3] == (False, "derived_from_representative", "L")
    found = relation_row(result, relation)
    assert found["outcome"] == "collapse"
    assert found["support_samples"] == found["ratio_samples"] == 3
    assert found["backfill_coeluting_samples"] == 0


def test_backfill_alone_never_establishes_coelution():
    only_backfill = row("H", 321.981944, scale=0.5, direct=False)
    decisions, result = decide(row("L", 300.0), only_backfill)

    assert decisions["H"][0] is True
    found = relation_row(result, "[M+Na]+")
    assert found["outcome"] == "coelution_insufficient"
    assert found["support_samples"] == found["backfill_coeluting_samples"] == 6
    assert review_kinds(result) == []


def test_backfilled_cells_without_an_apex_do_not_count():
    mixed = without_apex(
        row("H", 321.981944, scale=0.5, direct=TWO_DIRECT), SAMPLES[2:]
    )
    decisions, result = decide(row("L", 300.0), mixed)

    assert decisions["H"][0] is True
    found = relation_row(result, "[M+Na]+")
    assert found["outcome"] == "coelution_insufficient"
    assert found["support_samples"] == 2
    assert found["backfill_apex_unavailable_samples"] == 4


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


def test_unstable_isotope_ratio_is_reviewed_and_a_coeluting_methylene_is_removed():
    unstable = row("U", 301.003355, ratios=(0.05, 0.2, 0.05, 0.2, 0.05, 0.2))
    methylated = row("M", 314.01565, scale=0.5)
    decisions, result = decide(row("L", 300.0), unstable, methylated)

    assert decisions["U"][0] is True
    assert decisions["M"][:4] == (False, "derived_from_representative", "L", ("+CH2",))
    assert sorted(review_kinds(result)) == [("unstable_ratio", "U")]


@pytest.mark.parametrize(
    ("heavy_mz", "relation", "removed"),
    [
        (321.981944, "[M+Na]+", True),
        (2 * 300.0 - 1.00727647, "[2M+H]+", True),
        (382.003073617830, "+NaCH3COO", True),
        (301.003355, "13C_M+1", False),
    ],
)
def test_only_isotopes_need_a_stable_cross_sample_ratio(heavy_mz, relation, removed):
    varying = row("H", heavy_mz, ratios=(0.05, 0.2, 0.05, 0.2, 0.05, 0.2))
    decisions, result = decide(row("L", 300.0), varying)

    assert decisions["H"][0] is not removed
    assert outcome(result, relation) == {"collapse" if removed else "ratio_unstable"}


def variable_adduct_family(
    *, potassium_direct=True, potassium_shift=0.0, magnesium_shift=0.0
):
    return (
        row("ratio_MH", 600.0, base_rt=80.0),
        row("ratio_Na", 621.981944, scale=0.5, base_rt=80.0),
        row(
            "ratio_K",
            637.955882,
            scale=0.25,
            base_rt=80.0,
            direct=potassium_direct,
            shift_sec=potassium_shift,
        ),
        row(
            "ratio_Mg",
            681.990521,
            ratios=(0.5, 2.0, 0.5, 2.0, 0.5, 2.0),
            base_rt=80.0,
            shift_sec=magnesium_shift,
        ),
        row(
            "ratio_Mg_M2",
            683.988072,
            ratios=tuple(x * 0.1101 / 0.7899 for x in (0.5, 2.0) * 3),
            base_rt=80.0,
        ),
    )


def test_representative_much_weaker_than_removed_member_is_reviewed():
    decisions, result = decide(row("L", 300.0), row("Na", 321.981944, scale=20.0))

    assert decisions["Na"][0] is False
    assert review_kinds(result) == [("representative_weaker_than_member", "L")]


def test_recurring_unexplained_difference_folds_the_weaker_row():
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"H{index}", 310.0 + 50 * index, base_rt=base_rt, scale=0.5),
        )
    ]
    decisions, result = decide(*rows)

    for index in range(3):
        assert decisions[f"H{index}"][:4] == (
            False,
            "recurring_mass_difference",
            f"L{index}",
            ("delta_mz=10.0000",),
        )
        assert decisions[f"L{index}"][0] is True
    assert result.review_rows == ()


def test_recurring_difference_keeps_the_stronger_row_even_when_it_is_heavier():
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt, scale=0.5),
            row(f"H{index}", 310.0 + 50 * index, base_rt=base_rt),
        )
    ]
    decisions, _ = decide(*rows)

    for index in range(3):
        assert decisions[f"L{index}"][:3] == (
            False,
            "recurring_mass_difference",
            f"H{index}",
        )
        assert decisions[f"H{index}"][0] is True


def _scenario():
    """Several groups, kept apart in RT so they never co-elute."""
    return (
        (
            row("A", 300.0),
            row("B", 301.003355, scale=0.15, shift_sec=4.4),
            row("C", 302.006710, scale=0.0225, shift_sec=8.8),
            row("MH", 400.0, base_rt=20.0),
            row("MH_13C", 401.003355, scale=0.15, base_rt=20.0),
            row("Na", 421.981944, scale=0.5, base_rt=20.0),
            row("Na_13C", 422.985299, scale=0.075, base_rt=20.0),
            row("X", 900.0, base_rt=30.0),
            row("Y", 901.000195, scale=0.1, base_rt=30.0),
            row("Z", 500.0, base_rt=40.0),
            row("root1", 600.0, base_rt=50.0),
            row("root2", 579.021411, base_rt=50.0),
            row("artifact", 601.003355, scale=0.15, base_rt=50.0),
            row("Mg", 343.118794, base_rt=60.0),
            row("M2", 345.116345, scale=1 / 7.174, base_rt=60.0),
        )
        + tuple(
            (
                FeatureIdentity(
                    "family_" + feature.feature_id,
                    feature.matrix_mz,
                    feature.matrix_rt,
                ),
                cells,
            )
            for feature, cells in sodium_family()
        )
        + variable_adduct_family()
    )


@pytest.mark.parametrize("seed", range(4))
def test_decisions_follow_bound_rows_not_their_order_or_ids(seed):
    import random

    rows = list(_scenario())
    expected, _ = decide(*rows)
    random.Random(seed).shuffle(rows)
    shuffled, _ = decide(*rows)
    assert shuffled == expected

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
        result, ["R", "M1", "M2"], [False, False, False]
    )
    assert [(row["review_kind"], row["feature_id"]) for row in review] == [
        ("group_removed_by_feature_filter", "R")
    ]
    assert review[0]["related_feature_ids"] == "M1;M2"
    assert empty == 1

    _, empty = group_outcome_review(result, ["R", "M1", "M2"], [True, False, False])
    assert empty == 0


def test_istd_window_uses_range_or_expected_rt():
    assert mz_rt_window(
        label="d3", mz=245.0, rt_min=11.0, rt_max=13.0, expected_rt_min=12.0
    ) == MzRtWindow("d3", 245.0, 11.0, 13.0)
    assert mz_rt_window(
        label="d3", mz=245.0, rt_min=12.0, rt_max=12.0, expected_rt_min=12.0
    ) == MzRtWindow("d3", 245.0, 11.25, 12.75)
    assert (
        mz_rt_window(
            label="x", mz=245.0, rt_min=None, rt_max=None, expected_rt_min=None
        )
        is None
    )

    target = {
        "label": "d3",
        "mz": 245.0,
        "rt_min": 12.0,
        "rt_max": 12.0,
        "expected_rt_min": 12.0,
    }
    snapshot = json.dumps(
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
    assert windows_from_target_snapshot(snapshot) == (
        MzRtWindow("d3", 245.0, 11.25, 12.75),
    )
    assert windows_from_target_snapshot(None) == ()


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
                "sample_role": "sample",
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
        "xic_extractor.matrix_curation.export._run_evidence",
        lambda source: ({}, ""),
    )
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.load_cell_evidence",
        lambda source, features: tuple(
            by_feature[feature.feature_id] for feature in features
        ),
    )


def _export(tmp_path, name, steps, istd=None, *, nucleoside_list=None):
    from xic_extractor.matrix_curation.export import prepare_matrix_curation
    from xic_extractor.matrix_curation.source import locate_curation_source

    source = locate_curation_source(tmp_path / "alignment")
    output = tmp_path / name
    summary = prepare_matrix_curation(
        source=source,
        sample_metadata=load_sample_metadata(tmp_path / "samples.tsv"),
        sample_source="samples.tsv",
        steps=steps,
        istd_windows=istd,
        nucleoside_list=nucleoside_list,
        istd_source="fixture" if istd else "",
    ).write_to(output)
    _, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    return (
        output,
        {row["feature_id"]: row for row in decisions},
        json.loads(summary.read_text(encoding="utf-8")),
    )


def test_a_row_that_absorbed_a_listed_nucleoside_leaves_with_it(
    tmp_path, synthetic_cells
):
    _curation_folder(tmp_path / "alignment")
    _samples(tmp_path / "samples.tsv")
    listing = tmp_path / "windows.tsv"
    listing.write_text(
        "label\tmz\texpected_rt_min\nfolded\t321.981944\t10\n", encoding="utf-8"
    )
    _, decisions, _ = _export(
        tmp_path,
        "linked",
        {"same_compound": {}, "known_nucleosides": {}},
        nucleoside_list=listing,
    )

    assert decisions["F000002"]["same_compound_representative_feature_id"] == "F000001"
    assert decisions["F000001"]["decision"] == "drop"
    assert decisions["F000001"]["known_nucleosides_label"] == "folded"
    assert decisions["F000001"]["known_nucleosides_reason"] == "member_window_match"
    assert decisions["F000003"]["decision"] == "keep"


def test_nucleoside_removal_is_attributed_only_to_rows_entering_the_step(
    tmp_path, synthetic_cells
):
    _curation_folder(tmp_path / "alignment")
    _samples(tmp_path / "samples.tsv")
    listing = tmp_path / "windows.tsv"
    listing.write_text(
        "label\tmz\texpected_rt_min\nroot\t300\t10\n"
        "derivative\t321.981944\t10\nprotected\t500\t20\n",
        encoding="utf-8",
    )
    protection = (MzRtWindow("standard", 500, 19, 21),)
    steps = {"finite_fraction": {"min_finite_fraction": 0.6}, "same_compound": {}}
    _, before, _ = _export(tmp_path, "off", steps, protection, nucleoside_list=listing)
    _, after, summary = _export(
        tmp_path,
        "on",
        steps | {"known_nucleosides": {}},
        protection,
        nucleoside_list=listing,
    )
    assert [row["decision"] for row in before.values()] == [
        row["decision"] for row in after.values()
    ]
    assert before["F000001"]["decision_reason"] == "dropped_by:finite_fraction"
    assert after["F000001"]["decision_reason"] == "dropped_by:known_nucleosides"
    assert after["F000001"]["finite_fraction_decision"] == "not_evaluated"
    assert after["F000002"]["decision_reason"] == "dropped_by:same_compound"
    assert after["F000002"]["same_compound_representative_feature_id"] == "F000001"
    assert after["F000002"]["known_nucleosides_decision"] == "not_evaluated"
    assert after["F000002"]["known_nucleosides_label"] == ""
    assert after["F000003"]["known_nucleosides_label"] == "protected"
    assert after["F000003"]["known_nucleosides_decision"] == "drop"
    assert after["F000003"]["decision_reason"] == "istd_protected"
    assert summary["inputs"]["nucleoside_list"] == str(listing)
    assert [
        (s["key"], s["input_feature_count"], s["removed_feature_count"])
        for s in summary["steps"]
    ] == [
        ("same_compound", 4, 1),
        ("known_nucleosides", 3, 1),
        ("finite_fraction", 2, 1),
    ]
    listing.write_text("label\tmz\texpected_rt_min\nroot\t300\t10\n", encoding="utf-8")
    _, alone, _ = _export(
        tmp_path, "alone", {"known_nucleosides": {}}, nucleoside_list=listing
    )
    assert alone["F000002"]["decision"] == "keep"


def test_export_excludes_unassigned_derivative_without_inventing_a_parent(
    tmp_path, monkeypatch
):
    source = tmp_path / "alignment"
    source.mkdir()
    feature_rows = (
        row("F000001", 300.0, samples=STUDY),
        row("F000002", 279.021411, samples=STUDY),
        row("F000003", 301.003355, samples=STUDY, scale=0.15),
    )
    matrix, review = [], []
    for index, (feature, cells) in enumerate(feature_rows, 1):
        sample_values = {sample: str(cells[sample].value) for sample in STUDY}
        matrix.append(
            {
                "Mz": public_matrix_mz_text(repr(feature.matrix_mz)),
                "RT": "10.0",
                **sample_values,
            }
        )
        review.append(
            current_matrix_review_row(
                index=index,
                mz=repr(feature.matrix_mz),
                rt="10.0",
                feature_id=feature.feature_id,
            )
        )
    write_tsv(source / "alignment_matrix.tsv", matrix, ("Mz", "RT", *STUDY))
    write_tsv(source / "alignment_review.tsv", review, MATRIX_REVIEW_COLUMNS)
    _samples(tmp_path / "samples.tsv")
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export._run_evidence", lambda source: ({}, "")
    )
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.load_cell_evidence",
        lambda source, features: tuple(cells for _, cells in feature_rows),
    )

    output, decisions, summary = _export(tmp_path, "curated", {"same_compound": {}})
    assert decisions["F000003"]["decision"] == "drop"
    assert decisions["F000003"]["same_compound_representative_feature_id"] == ""
    assert decisions["F000003"]["same_compound_via_feature_ids"] == "F000001;F000002"
    assert decisions["F000001"]["same_compound_ion_form"] == UNCONFIRMED
    assert decisions["F000002"]["same_compound_ion_form"] == UNCONFIRMED
    _, kept = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert [r["Mz"] for r in kept] == ["300.0000", "279.0214"]
    _, reviews = read_tsv_with_header(output / "same_compound_review.tsv")
    assert reviews[0]["related_feature_ids"] == "F000001;F000002"
    assert reviews[0]["detail"] == "decision=drop"
    assert summary["steps"][0]["removed_representative_undetermined_count"] == 1
    assert summary["steps"][0]["confirmed_mh_count"] == 0


def test_derivatives_are_removed_before_feature_filtering_and_istd_is_protected(
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
            if column.startswith("finite_") and feature != "F000002":
                assert fraction_only[feature][column] == value
            if column.startswith("same_compound_"):
                assert same_only[feature][column] == value
    assert both["F000002"]["same_compound_representative_feature_id"] == "F000001"
    assert both["F000002"]["finite_fraction_decision"] == "not_evaluated"
    assert both["F000002"]["finite_count"] == ""
    assert [step["key"] for step in summary["steps"]] == [
        "same_compound",
        "finite_fraction",
    ]
    assert [step["input_feature_count"] for step in summary["steps"]] == [4, 3]
    assert summary["steps"][0]["removed_by_cause"] == {"cation_adduct": 1}
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
    ] == [("group_removed_by_feature_filter", "F000001", "F000002")]
    step = summary["steps"][0]
    assert (step["removed_feature_count"], step["empty_group_count"]) == (1, 1)
    _, review = read_tsv_with_header(output / "same_compound_review.tsv")
    assert review == []

    istd = (MzRtWindow("d0", 300.0, 9.0, 11.0),)
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
    assert summary["steps"][0]["empty_group_count"] == 0
    assert summary["steps"][0]["review_by_kind"] == {}


def test_same_compound_is_unavailable_without_the_run_explanation(tmp_path):
    from tests.matrix_curation_fixtures import _run
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
    assert "alignment_explanation.sqlite" in steps["same_compound"]

    output = tmp_path / "curated"
    result = _run(source, samples, output, "--same-compound", threshold=None)
    assert result.returncode == 2
    assert "alignment_explanation.sqlite" in result.stderr
    assert not output.exists()


def test_machine_output_without_backfill_ledger_is_curated(tmp_path, monkeypatch):
    from tests.duplicate_quantification_fixtures import publish_owner_run
    from tests.matrix_curation_fixtures import _run

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
    assert summary["inputs"]["explanation"].endswith("alignment_explanation.sqlite")


def test_recurring_difference_counts_a_pair_once_whichever_row_elutes_first():
    flip = (1.0, 1.0, 1.0, -1.0, -1.0, -1.0)
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"L{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"H{index}", 310.0 + 50 * index, base_rt=base_rt, shifts=flip),
        )
    ]
    decisions, _ = decide(*rows)

    assert [decisions[f"H{index}"][1] for index in range(3)] == [
        "recurring_mass_difference"
    ] * 3


def test_removed_ions_neither_repeat_differences_nor_review_pairs():
    rows = [
        member
        for index, base_rt in enumerate((10.0, 20.0, 30.0))
        for member in (
            row(f"X{index}", 300.0 + 50 * index, base_rt=base_rt),
            row(f"X{index}_13C", 301.003355 + 50 * index, base_rt=base_rt, scale=0.15),
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
    } | {f"Y{index}" for index in range(3)}
    assert review_kinds(result) == []


def test_frequency_table_needs_most_shared_direct_samples_to_coelute():
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


@pytest.mark.parametrize(
    ("relations", "cause"),
    [
        ("13C_M+1", "isotope"),
        ("[M+Na]+", "cation_adduct"),
        ("ion_family:[M+K]+", "cation_adduct"),
        ("13C_M+1|[M+Na]+", "several"),
        ("[M+Fe+CH3COO]+", "metal_complex"),
        ("+CH3OH", "solvent"),
        ("delta_mz=10.9910", "recurring"),
        ("", "ion_family"),
    ],
)
def test_a_removed_row_is_counted_under_the_relation_that_removed_it(relations, cause):
    from xic_extractor.matrix_curation.same_compound import removal_cause

    assert removal_cause(relations) == cause
