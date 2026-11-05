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
        # M = 300 - proton; metal dimer is 2M - H + Mg/Ca (net charge +1).
        ("[2M-H+Mg]+", 300.0, 620.962115180219, 0.3, CONFIRMED_MH),
        ("[2M-H+Ca]+", 300.0, 636.939664346219, 0.3, CONFIRMED_MH),
        # Neutral salt clusters can attach to any cation, not only [M+H]+.
        ("+NaHCOO", 300.0, 367.987423553370, 0.3, UNCONFIRMED),
        ("+NaCH3COO", 300.0, 382.003073617830, 0.3, UNCONFIRMED),
        ("[M+Mg+HCOO]+", 300.0, 367.974871, 0.3, CONFIRMED_MH),
        ("[M+Mg+CH3COO]+", 300.0, 381.990521, 0.3, CONFIRMED_MH),
        ("[M+Ca+HCOO]+", 300.0, 383.952420, 0.3, CONFIRMED_MH),
        ("[M+Ca+CH3COO]+", 300.0, 397.968070, 0.3, CONFIRMED_MH),
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


@pytest.mark.parametrize("light_heavy", [7.27, 8.68, 3.19, 2.97, 6.93])
def test_unassigned_m_plus_2_is_removed_without_claiming_an_element(light_heavy):
    # 24Mg/26Mg = 7.174; two Mg give 3.587; two K give 6.929.
    # The latter overlaps one Mg: identity ambiguity is not a keep decision.
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
    # Median 7.2 and MAD zero must not hide two samples 10x too abundant.
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


def test_competing_representatives_do_not_rescue_a_supported_derivative():
    # H is A's 13C isotope and B's sodium adduct.
    decisions, result = decide(
        row("A", 300.0),
        row("B", 279.021411),
        row("H", 301.003355, scale=0.15),
    )

    assert decisions["H"][:3] == (False, "derived_representative_undetermined", None)
    assert result.decisions[2].undetermined is True
    assert ("derived_representative_undetermined", "H") in review_kinds(result)
    assert decisions["A"][0] is decisions["B"][0] is True
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
    # C9H13N3O4 neutral exact mass; independent NIST ion masses, not owner constants.
    # Large changes in adduct response do not change this neutral-mass hypothesis.
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
        # A complex without independent isotope support stays a candidate.
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
    assert decisions["BiologicalH2"][0] is True
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


@pytest.mark.parametrize("direct", [False, (True, True, False, False, False, False)])
def test_backfilled_dimer_cannot_establish_an_ion_family(direct):
    rows = [r for r in sodium_family() if r[0].feature_id != "DimerH"]
    rows.append(row("DimerH", 455.188488252321, scale=0.3, direct=direct))
    decisions, _ = decide(*rows)
    assert decisions["Na"][0] is True


def test_ion_family_does_not_use_transitive_coelution_to_delete_a_member():
    rows = [r for r in sodium_family() if r[0].feature_id != "MgAc"]
    # Na and DimerH are 4.8 s apart: the metal rows must not bridge that gap.
    rows = [r for r in rows if r[0].feature_id != "DimerH"]
    rows.append(row("DimerH", 455.188488252321, scale=0.3, shift_sec=4.8))
    rows.append(row("MgAc", 310.088403352921, scale=0.8, shift_sec=2.4))
    decisions, _ = decide(*rows)
    assert decisions["Na"][0] is True


@pytest.mark.parametrize(
    ("protonated", "independent_isotope"),
    [(False, False), (True, False), (True, True)],
)
def test_competing_protonated_family_is_not_mistaken_for_element_only_ambiguity(
    protonated, independent_isotope
):
    # An isobaric [N+H]+ can share the [M+Na]+ feature. Unlike Na vs K
    # attribution, this changes whether the row contains a wanted ion form.
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
    # A co-eluting isobar can also satisfy another ion's isotope relation.
    decisions, _ = decide(
        *sodium_family(protonated=True),
        row("OtherRoot", 228.097882352321 - 1.003355, scale=10.0),
    )
    assert decisions["MH"][0] is False
    assert decisions["Na"][2] is None
    assert all(d[2] is None or decisions[d[2]][0] for d in decisions.values())


@pytest.mark.parametrize(
    ("isotope_supported", "calcium_shift"), [(False, 0.0), (True, 0.0), (True, 4.8)]
)
def test_parallel_metal_multimers_can_support_a_variable_adduct_ratio(
    isotope_supported, calcium_shift
):
    # C10H13N5O4 neutral: two different metal carriers constrain the same trimer.
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
    if isotope_supported:
        rows.append(
            row(
                "MgTrimer_M2",
                3 * neutral + 22.976668084861 + 1.997551271,
                scale=0.3 * 0.1101 / 0.7899,
            )
        )
    decisions, _ = decide(*rows)
    assert decisions["MH"][0] is True
    qualified = isotope_supported and calcium_shift == 0.0
    assert decisions["MgTrimer"][0] is (not qualified)
    assert decisions["CaTrimer"][0] is (not qualified)
    if qualified:
        assert decisions["CaTrimer"][2] == "MH"
        assert decisions["MgTrimer_M2"][2] == "MH"


def test_competing_paths_still_need_direct_coelution_with_a_root():
    decisions, result = decide(
        row("A", 300.0),
        row("B", 279.021411),
        row("P", 301.003355, scale=0.15, shift_sec=2.4),
        row("H", 302.006710, scale=0.0225, shift_sec=4.8),
    )
    assert decisions["P"][:3] == (False, "derived_representative_undetermined", None)
    assert decisions["H"][:2] == (True, "no_direct_coelution_with_representative")
    assert ("no_direct_coelution_with_representative", "H") in review_kinds(result)


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


def variable_adduct_family(
    *, potassium_direct=True, potassium_shift=0.0, magnesium_shift=0.0
):
    # Independent ion-mass differences from a common [M+H]+ reference.
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


def test_two_supported_carriers_can_corroborate_a_variable_adduct_ratio():
    decisions, result = decide(*variable_adduct_family())
    assert decisions["ratio_MH"][0] is True
    assert decisions["ratio_Mg"][:3] == (
        False,
        "derived_from_representative",
        "ratio_MH",
    )
    assert decisions["ratio_Mg_M2"][:3] == (False, "derived_through_group", "ratio_MH")
    assert outcome(result, "[M+Mg+CH3COO]+") == {"collapse_corroborated_adduct"}
    relation = next(
        r for r in result.relation_rows if r["relation"] == "[M+Mg+CH3COO]+"
    )
    assert relation["used_for_collapse"] == "TRUE"
    proof = next(
        r
        for r in result.review_rows
        if r["review_kind"] == "variable_ratio_adduct_supported"
    )
    assert proof["feature_id"] == "ratio_Mg"
    assert set(str(proof["related_feature_ids"]).split(";")) == {
        "ratio_MH",
        "ratio_Na",
        "ratio_K",
    }


@pytest.mark.parametrize(
    ("direct", "shift"),
    [
        (False, 0.0),
        ((True, True, False, False, False, False), 0.0),
        (True, 4.8),
    ],
)
def test_a_single_backfilled_or_noncoeluting_carrier_cannot_rescue_an_adduct(
    direct, shift
):
    decisions, result = decide(
        *variable_adduct_family(potassium_direct=direct, potassium_shift=shift)
    )
    assert decisions["ratio_Mg"][0] is True
    assert outcome(result, "[M+Mg+CH3COO]+") == {"ratio_unstable"}


def test_two_rows_of_the_same_carrier_are_not_independent_corroboration():
    rows = [r for r in variable_adduct_family() if r[0].feature_id != "ratio_K"]
    decisions, _ = decide(*rows, row("second_Na", 621.982, scale=0.4, base_rt=80.0))
    assert decisions["ratio_Mg"][0] is True


@pytest.mark.parametrize("has_second_other_carrier", [False, True])
def test_candidate_carrier_does_not_count_as_another_supported_form(
    has_second_other_carrier,
):
    rows = [
        *variable_adduct_family()[:3],
        row("variable_Na", 621.984, ratios=(0.5, 2.0) * 3, base_rt=80.0),
    ]
    if has_second_other_carrier:
        rows.append(row("stable_Mg", 681.990521, scale=0.4, base_rt=80.0))
    decisions, result = decide(*rows)

    assert decisions["variable_Na"][0] is (not has_second_other_carrier)
    relation = next(
        r for r in result.relation_rows if r["heavy_feature_id"] == "variable_Na"
    )
    assert relation["outcome"] == (
        "collapse_corroborated_adduct" if has_second_other_carrier else "ratio_unstable"
    )
    assert relation["used_for_collapse"] == (
        "TRUE" if has_second_other_carrier else "FALSE"
    )
    reviews = [
        r
        for r in result.review_rows
        if r["review_kind"] == "variable_ratio_adduct_supported"
        and r["feature_id"] == "variable_Na"
    ]
    if has_second_other_carrier:
        assert set(reviews[0]["related_feature_ids"].split(";")) == {
            "ratio_MH",
            "ratio_K",
            "stable_Mg",
        }
    else:
        assert reviews == []


def test_carrier_corroboration_needs_mutual_not_only_reference_coelution():
    decisions, _ = decide(
        *variable_adduct_family(potassium_shift=2.4, magnesium_shift=-2.4)
    )
    # Each witness co-elutes with the reference, but K and Mg are 4.8 s apart.
    assert decisions["ratio_Mg"][0] is True


@pytest.mark.parametrize(
    ("mz", "relation"),
    [
        (601.003355, "13C_M+1"),
        (1198.99272353, "[2M+H]+"),
        (682.00307361783, "+NaCH3COO"),
    ],
)
def test_monomer_carriers_do_not_override_isotope_dimer_or_salt_ratio_checks(
    mz, relation
):
    rows = variable_adduct_family()[:3]
    decisions, result = decide(
        *rows,
        row(
            "variable_other",
            mz,
            base_rt=80.0,
            ratios=(0.05, 0.15, 0.05, 0.15, 0.05, 0.15),
        ),
    )
    assert decisions["variable_other"][0] is True
    assert outcome(result, relation) == {"ratio_unstable"}


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
        (
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
                    feature.center_mz_basis,
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
                sample_values=sample_values,
            )
        )
    write_tsv(source / "alignment_matrix.tsv", matrix, ("Mz", "RT", *STUDY))
    write_tsv(source / "alignment_review.tsv", review, MATRIX_REVIEW_COLUMNS)
    _samples(tmp_path / "samples.tsv")
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.cell_evidence_problem", lambda source: ""
    )
    monkeypatch.setattr(
        "xic_extractor.matrix_curation.export.load_cell_evidence",
        lambda source, features: tuple(cells for _, cells in feature_rows),
    )

    output, decisions, summary = _export(tmp_path, "curated", {"same_compound": {}})
    assert decisions["F000003"]["decision"] == "drop"
    assert decisions["F000003"]["same_compound_representative_feature_id"] == ""
    assert decisions["F000003"]["same_compound_via_feature_ids"] == "F000001;F000002"
    _, kept = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert [r["Mz"] for r in kept] == ["300.0000", "279.0214"]
    _, reviews = read_tsv_with_header(output / "same_compound_review.tsv")
    assert reviews[0]["related_feature_ids"] == "F000001;F000002"
    assert reviews[0]["detail"] == "decision=drop"
    assert summary["steps"][0]["removed_representative_undetermined_count"] == 1
    assert summary["steps"][0]["confirmed_mh_count"] == 1


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
