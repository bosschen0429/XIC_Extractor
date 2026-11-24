"""Exclude supported derivative ion forms from a statistical Matrix.

Isotope, adduct and dimer rows are excluded when supported derivation paths
reach them from a root. Neither an exact element identity
nor a unique root is required to exclude a derivative. Mass relations compare
row-center m/z; they are not per-sample mass evidence.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.mass_accuracy import mz_window, signed_ppm_error
from xic_extractor.matrix_curation.steps import (
    CellEvidence,
    CurationInputs,
    CurationMatrix,
    FeatureIdentity,
    StepParameter,
    StepResult,
    StepTable,
)

PROTON = 1.00727647
C13_PER_C12 = 0.0107 / 0.9893
N15_PER_N14 = 0.00364 / 0.99636
K39_PER_K41 = 13.857

MZ_PPM = 7.0
MAX_APEX_SEC = 5.5
MIN_SUPPORT_SAMPLES = 3
MIN_SUPPORT_FRACTION = 0.8
ISOTOPE_MAX_LOG_MAD = 0.1
K41_MEDIAN_RANGE = (11.0, 18.0)
ISOTOPE_RATIO_SAMPLE_FACTOR = 2.0
ISOTOPE_RATIO_SAMPLE_FRACTION = 0.8
UNASSIGNED_ISOTOPE_RATIOS = (
    0.7899 / 0.1101,
    0.7899 / (2 * 0.1101),
    K39_PER_K41 / 2,
    0.4917 / 0.2773,
    0.2773 / 0.1845,
)
"""Light/heavy ratios an unassigned M+2 may show: one or two Mg, two K, and
64Zn/66Zn or 66Zn/68Zn."""
METAL_M1_ALLOWANCE = 2 * 0.1000 / 0.7899
UNASSIGNED_ISOTOPE_MEDIAN_FACTORS = (0.8, 1.3)
WEAK_REPRESENTATIVE_RATIO = 0.1
RECURRING_DELTA_WIDTH = 0.005
RECURRING_MIN_PAIRS = 3
FAMILY_MIN_MONOMER_FORMS = 3

FAMILY_MONOMERS = (
    ("[M+H]+", 1.007276452321),
    ("[M+Na]+", 22.989220702091),
    ("[M+K]+", 38.963157906491),
    ("[M+NH4]+", 18.033825553441),
    ("[M+Mg+HCOO]+", 68.982147388461),
    ("[M+Mg+CH3COO]+", 82.997797452921),
    ("[M+Ca+HCOO]+", 84.959696554461),
    ("[M+Ca+CH3COO]+", 98.975346618921),
)
FAMILY_DIMER_H_OFFSET = 1.007276452321
FAMILY_METAL_MULTIMER_COUNTS = (2, 3)
MG_MINUS_H_ION_OFFSET = 22.976668084861
CA_MINUS_H_ION_OFFSET = 38.954217250861
FAMILY_METAL_DIMERS = (
    ("[2M-H+Mg]+", MG_MINUS_H_ION_OFFSET),
    ("[2M-H+Ca]+", CA_MINUS_H_ION_OFFSET),
    ("[2M-2H+Na+Mg]+", 44.958612334631),
    ("[2M-2H+2Mg+HCOO]+", 90.951539021001),
    ("[2M-2H+2Mg+CH3COO]+", 104.967189085461),
)

CONFIRMED_MH = "confirmed_[M+H]+"
UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Relation:
    """The heavier row derived from the lighter one."""

    key: str
    kind: str
    delta: float
    charge: int = 1
    abundance: tuple[float, float, int] | None = None
    mz_factor: int = 1
    confirms_mh: bool = True

    def predict(self, light_mz: float) -> float:
        return self.mz_factor * light_mz + self.delta

    def abundance_ceiling(
        self, light_mz: float, metal_carrier: bool = False
    ) -> float | None:
        """Largest heavy/light ratio if every atom of the neutral were this element.

        The neutral mass assumes a proton, which overestimates it for any other
        cation and keeps the ceiling loose. A metal carrier's single-heavy-atom
        peak may also hold 25Mg.
        """
        if self.abundance is None:
            return None
        element_mass, per_atom, count = self.abundance
        neutral = self.charge * (light_mz - PROTON)
        atoms = max(0, math.floor(neutral / element_mass))
        ceiling = math.comb(atoms, count) * per_atom**count
        if metal_carrier and count == 1:
            ceiling += METAL_M1_ALLOWANCE
        return ceiling


RELATIONS: tuple[Relation, ...] = (
    Relation("13C_M+1", "isotope", 1.003355, abundance=(12.0, C13_PER_C12, 1)),
    Relation("15N_M+1", "isotope", 0.997035, abundance=(14.0, N15_PER_N14, 1)),
    Relation("13C2_M+2", "isotope", 2.006710, abundance=(12.0, C13_PER_C12, 2)),
    Relation("13C_z2", "isotope", 0.501678, charge=2, abundance=(12.0, C13_PER_C12, 1)),
    Relation("41K", "isotope", 1.998119),
    Relation("M+2_unassigned", "isotope", 1.997551),
    Relation("[M+Na]+", "adduct", 21.981944),
    Relation("[M+K]+", "adduct", 37.955882),
    Relation("[M+NH4]+", "adduct", 17.026549),
    Relation("[2M+H]+", "adduct", -PROTON, mz_factor=2),
    Relation("[2M-H+Mg]+", "adduct", 20.962115180219, mz_factor=2),
    Relation("[2M-H+Ca]+", "adduct", 36.939664346219, mz_factor=2),
    Relation("+NaHCOO", "adduct", 67.987423553370, confirms_mh=False),
    Relation("+NaCH3COO", "adduct", 82.003073617830, confirms_mh=False),
    Relation("[M+Mg+HCOO]+", "adduct", 67.974871),
    Relation("[M+Mg+CH3COO]+", "adduct", 81.990521),
    Relation("[M+Ca+HCOO]+", "adduct", 83.952420),
    Relation("[M+Ca+CH3COO]+", "adduct", 97.968070),
    Relation("[M+Fe+CH3COO]+", "adduct", 113.940417),
    Relation("[M+K]+/[M+Na]+", "adduct", 15.973938, confirms_mh=False),
    Relation("Ca/Mg", "adduct", 15.977549, confirms_mh=False),
    Relation("[M+Mg+CH3COO]+/[M+Na]+", "adduct", 60.008577, confirms_mh=False),
    Relation("+CH2", "review", 14.015650),
    Relation("+H2", "review", 2.015650),
    Relation("+CH2O", "review", 30.010565),
    Relation("+CH3OH", "review", 32.026215),
    Relation("H2O_loss", "review", 18.010565),
    Relation("+116.047_dR", "annotation", 116.04734),
)


REMOVAL_CAUSES = (
    ("isotope", "同位素峰"),
    ("cation_adduct", "鈉、鉀或銨加成"),
    ("metal_complex", "鎂、鈣或鐵錯合物"),
    ("salt_cluster", "鈉鹽簇"),
    ("multimer", "聚集離子"),
    ("several", "同時符合多種關係"),
    ("ion_family", "沒有質子化離子的離子家族"),
)
"""Why a row was removed, each row counted once, in display order."""


def removal_cause(relations: str) -> str:
    """The cause shared by a removed row's ``|``-joined relations."""
    isotopes = {r.key for r in RELATIONS if r.kind == "isotope"}
    causes = set()
    for relation in filter(None, relations.split("|")):
        key = relation.removeprefix("ion_family:")
        if key in isotopes:
            causes.add("isotope")
        elif key.startswith(("[2M", "[3M")):
            causes.add("multimer")
        elif key.startswith("+Na"):
            causes.add("salt_cluster")
        elif any(metal in key for metal in ("Mg", "Ca", "Fe")):
            causes.add("metal_complex")
        else:
            causes.add("cation_adduct")
    if not causes:
        return "ion_family"
    return causes.pop() if len(causes) == 1 else "several"


@dataclass(frozen=True)
class PairEvidence:
    both_direct: int
    support: tuple[str, ...]
    direct_support: tuple[str, ...]
    max_apex_diff_sec: float | None
    backfill_apex_unavailable: int
    ratios: tuple[float, ...]

    @property
    def backfill_coeluting(self) -> int:
        return len(self.support) - len(self.direct_support)

    @property
    def direct_consistent(self) -> bool:
        """Some direct co-elution, and direct detections mostly agree."""
        return (
            bool(self.direct_support)
            and len(self.direct_support) >= MIN_SUPPORT_FRACTION * self.both_direct
        )

    @property
    def coelutes(self) -> bool:
        """Enough co-eluting samples, anchored by consistent direct co-elution."""
        return len(self.support) >= MIN_SUPPORT_SAMPLES and self.direct_consistent

    @property
    def coelutes_directly(self) -> bool:
        """Co-elution from direct detections alone, for rules that waive ratios."""
        return _direct_coelution(len(self.direct_support), self.both_direct)

    @property
    def log_mad(self) -> float | None:
        if not self.ratios:
            return None
        logs = [math.log10(ratio) for ratio in self.ratios]
        center = statistics.median(logs)
        return statistics.median(abs(value - center) for value in logs)

    @property
    def median_ratio(self) -> float | None:
        return statistics.median(self.ratios) if self.ratios else None


def pair_evidence(
    light: Mapping[str, CellEvidence], heavy: Mapping[str, CellEvidence]
) -> PairEvidence:
    """Same-sample co-elution of two rows from each cell's recorded apex.

    Backfilled cells only complete missing evidence: when direct detections
    alone co-elute, they are the whole evidence. Otherwise a backfilled cell
    with an apex counts like a direct one, and only direct pairs enter the
    share of consistently co-eluting samples.
    """
    both_direct = 0
    backfill_unavailable = 0
    coeluting: list[tuple[str, float, float | None, bool]] = []
    for sample in sorted(light.keys() & heavy.keys()):
        a, b = light[sample], heavy[sample]
        direct = a.direct and b.direct
        both_direct += direct
        if a.apex_rt_min is None or b.apex_rt_min is None:
            backfill_unavailable += not direct
            continue
        apart = abs(a.apex_rt_min - b.apex_rt_min) * 60.0
        if apart <= MAX_APEX_SEC:
            ratio = (
                a.value / b.value if _positive(a.value) and _positive(b.value) else None
            )
            coeluting.append((sample, apart, ratio, direct))
    direct_support = tuple(sample for sample, *_, direct in coeluting if direct)
    if _direct_coelution(len(direct_support), both_direct):
        coeluting = [item for item in coeluting if item[3]]
    return PairEvidence(
        both_direct=both_direct,
        support=tuple(sample for sample, *_ in coeluting),
        direct_support=direct_support,
        max_apex_diff_sec=max((apart for _, apart, *_ in coeluting), default=None),
        backfill_apex_unavailable=backfill_unavailable,
        ratios=tuple(ratio for _, _, ratio, _ in coeluting if ratio is not None),
    )


def _direct_coelution(direct_support: int, both_direct: int) -> bool:
    return (
        direct_support >= MIN_SUPPORT_SAMPLES
        and direct_support >= MIN_SUPPORT_FRACTION * both_direct
    )


def relation_outcome(
    relation: Relation,
    light_mz: float,
    pair: PairEvidence,
    metal_carrier: bool = False,
) -> str:
    """Whether this relation folds the heavier row into the lighter one."""
    if relation.kind == "annotation":
        return "annotation_only"
    if not pair.coelutes:
        return "coelution_insufficient"
    if relation.kind == "review":
        return "review_only"
    if relation.kind == "adduct":
        return "collapse"
    if len(pair.ratios) < MIN_SUPPORT_SAMPLES:
        return "ratio_unavailable"
    mad = pair.log_mad
    if mad is None or mad > ISOTOPE_MAX_LOG_MAD:
        return "ratio_unstable"
    if relation.key == "41K":
        low, high = K41_MEDIAN_RANGE
        within = sum(
            K39_PER_K41 / ISOTOPE_RATIO_SAMPLE_FACTOR
            <= ratio
            <= K39_PER_K41 * ISOTOPE_RATIO_SAMPLE_FACTOR
            for ratio in pair.ratios
        )
        median = statistics.median(pair.ratios)
        if not (low <= median <= high) or within < ISOTOPE_RATIO_SAMPLE_FRACTION * len(
            pair.ratios
        ):
            return "ratio_not_41K"
        return "collapse"
    if relation.key == "M+2_unassigned":
        low, high = UNASSIGNED_ISOTOPE_MEDIAN_FACTORS
        median = statistics.median(pair.ratios)
        for expected in UNASSIGNED_ISOTOPE_RATIOS:
            within = sum(
                expected / ISOTOPE_RATIO_SAMPLE_FACTOR
                <= ratio
                <= expected * ISOTOPE_RATIO_SAMPLE_FACTOR
                for ratio in pair.ratios
            )
            if low * expected <= median <= high * expected and (
                within >= ISOTOPE_RATIO_SAMPLE_FRACTION * len(pair.ratios)
            ):
                return "collapse"
        return "isotope_ratio_incompatible"
    ceiling = relation.abundance_ceiling(light_mz, metal_carrier)
    if ceiling is not None and any(1.0 / ratio > ceiling for ratio in pair.ratios):
        return "isotope_abundance_exceeded"
    return "collapse"


@dataclass(frozen=True)
class RowDecision:
    keep: bool
    reason: str
    representative: int | None = None
    relations: tuple[str, ...] = ()
    via: tuple[int, ...] = ()
    undetermined: bool = False
    ion_form: str = ""
    member_count: int = 0


@dataclass(frozen=True)
class SameCompoundResult:
    decisions: tuple[RowDecision, ...]
    relation_rows: tuple[dict[str, object], ...]
    review_rows: tuple[dict[str, object], ...]


def collapse_same_compound(
    features: Sequence[FeatureIdentity],
    cells: Sequence[Mapping[str, CellEvidence]],
    relations: Sequence[Relation] = RELATIONS,
) -> SameCompoundResult:
    """Decide which rows fold into which representative.

    A removed row is reached from a root through folding relations, each
    passing its own checks. Its representative is the one root whose direct
    detections do not contradict co-elution with it; with none or several, its
    attribution is blank, not its artifact classification undecided. Results
    depend only on m/z, cells and relations, never on row order, feature ID or
    the order of the relation list.
    """
    if len(features) != len(cells):
        raise ValueError("features and cells must describe the same rows")
    mz = [feature.matrix_mz for feature in features]
    matches = _mass_matches(mz, relations)
    by_key = {relation.key: relation for relation in relations}
    evidence = {pair: pair_evidence(cells[pair[0]], cells[pair[1]]) for pair in matches}

    outcomes: dict[tuple[int, int], dict[str, str]] = {}
    for pair, matched in matches.items():
        outcomes[pair] = {
            key: relation_outcome(by_key[key], mz[pair[0]], evidence[pair])
            for key in matched
        }
    metal_isotope_pairs = {
        pair
        for pair, pair_outcomes in outcomes.items()
        if pair_outcomes.get("M+2_unassigned") == "collapse"
    }
    metal_carriers = {light for light, _ in metal_isotope_pairs}
    for pair, pair_outcomes in outcomes.items():
        if pair[0] not in metal_carriers:
            continue
        for key, value in pair_outcomes.items():
            if value == "isotope_abundance_exceeded":
                pair_outcomes[key] = relation_outcome(
                    by_key[key], mz[pair[0]], evidence[pair], metal_carrier=True
                )
    edges: dict[tuple[int, int], tuple[str, ...]] = {}
    for pair, pair_outcomes in outcomes.items():
        folding = tuple(
            sorted(key for key, value in pair_outcomes.items() if value == "collapse")
        )
        if folding:
            edges[pair] = folding

    parents: dict[int, set[int]] = defaultdict(set)
    children: dict[int, set[int]] = defaultdict(set)
    for light, heavy in edges:
        parents[heavy].add(light)
        children[light].add(heavy)
    roots = {index for index in range(len(features)) if not parents.get(index)}
    reached_from: dict[int, set[int]] = defaultdict(set)
    for root in roots:
        stack = [root]
        seen = {root}
        while stack:
            for child in children.get(stack.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
                    reached_from[child].add(root)
    representative: dict[int, int] = {}
    ambiguous_roots: dict[int, set[int]] = {}
    for heavy in parents:
        coeluting_roots = set()
        for root in reached_from[heavy]:
            if (root, heavy) not in evidence:
                evidence[(root, heavy)] = pair_evidence(cells[root], cells[heavy])
            if evidence[(root, heavy)].direct_consistent:
                coeluting_roots.add(root)
        if len(coeluting_roots) == 1:
            representative[heavy] = next(iter(coeluting_roots))
        else:
            ambiguous_roots[heavy] = coeluting_roots or reached_from[heavy]
    removed = representative.keys() | ambiguous_roots.keys()
    assigned_roots = set(representative.values())

    uncertain_paths: set[int] = set()
    for index in sorted(parents, key=lambda index: mz[index]):
        if index in ambiguous_roots or any(
            parent in uncertain_paths
            or len(edges[(parent, index)]) > 1
            or "M+2_unassigned" in edges[(parent, index)]
            for parent in parents[index]
        ):
            uncertain_paths.add(index)

    decisions = []
    for index in range(len(features)):
        if index in removed:
            assigned_root = representative.get(index)
            sources = sorted(parents[index])
            labels = [edges[(parent, index)] for parent in sources]
            decisions.append(
                RowDecision(
                    keep=False,
                    reason=(
                        "derived_representative_undetermined"
                        if assigned_root is None
                        else "derived_from_representative"
                        if (assigned_root, index) in edges
                        else "derived_through_group"
                    ),
                    representative=assigned_root,
                    relations=tuple(sorted({key for keys in labels for key in keys})),
                    via=tuple(parent for parent in sources if parent != assigned_root),
                    undetermined=index in uncertain_paths,
                )
            )
            continue
        decisions.append(
            RowDecision(
                keep=True,
                reason="representative"
                if index in assigned_roots
                else "no_folding_relation",
            )
        )

    decisions, family_reviews = _ion_family_decisions(
        features, cells, decisions, evidence, metal_isotope_pairs
    )
    removed = {index for index, decision in enumerate(decisions) if not decision.keep}
    representative = {
        index: decision.representative
        for index, decision in enumerate(decisions)
        if decision.representative is not None
    }
    member_counts = Counter(representative.values())
    adduct_roots = {
        root
        for member, root in representative.items()
        if (keys := edges.get((root, member)))
        and all(
            by_key[key].kind == "adduct" and by_key[key].confirms_mh for key in keys
        )
    }
    for index, decision in enumerate(decisions):
        if decision.keep:
            decisions[index] = replace(
                decision,
                member_count=member_counts[index],
                ion_form=decision.ion_form
                or (CONFIRMED_MH if index in adduct_roots else UNCONFIRMED),
            )
    relation_rows = _relation_rows(
        features, matches, outcomes, evidence, removed, by_key
    )
    review_rows = _review_rows(
        features,
        cells,
        matches,
        outcomes,
        evidence,
        edges,
        representative,
        removed,
        ambiguous_roots,
    )
    return SameCompoundResult(
        tuple(decisions),
        relation_rows,
        review_rows + family_reviews,
    )


@dataclass(frozen=True)
class _IonFamily:
    neutral_mass: float
    members: tuple[tuple[int, str], ...]
    witnesses: frozenset[int]


def _ion_family_decisions(
    features: Sequence[FeatureIdentity],
    cells: Sequence[Mapping[str, CellEvidence]],
    decisions: Sequence[RowDecision],
    evidence: dict[tuple[int, int], PairEvidence],
    metal_isotope_pairs: set[tuple[int, int]],
) -> tuple[list[RowDecision], tuple[dict[str, object], ...]]:
    """Exclude supported non-[M+H]+ families, even without a protonated row.

    Without an [M+H]+ row, the dimer independently constrains the neutral mass. A
    sodium-containing neutral salt could be misread as a new protonated molecule.
    With [M+H]+, parallel Mg/Ca multimers and a metal-isotope companion supply
    the independent witnesses instead.
    Cross-sample adduct ratios are not required to be constant in this joint
    hypothesis; the isotope witness must pass without backfilled evidence.
    """
    mz = [feature.matrix_mz for feature in features]

    def coelutes(a: int, b: int) -> bool:
        pair = (a, b) if mz[a] <= mz[b] else (b, a)
        if pair not in evidence:
            evidence[pair] = pair_evidence(cells[pair[0]], cells[pair[1]])
        return evidence[pair].coelutes_directly

    metal_isotopes = {
        pair[0] for pair in metal_isotope_pairs if evidence[pair].coelutes_directly
    }
    if not metal_isotopes:
        return list(decisions), ()
    families = _find_ion_families(mz, coelutes, metal_isotopes)
    forms: dict[int, set[str]] = defaultdict(set)
    witnesses: dict[int, set[int]] = defaultdict(set)
    protonated: dict[int, set[int]] = defaultdict(set)
    for family in families:
        mh = {index for index, model in family.members if model == "[M+H]+"}
        for index, model in family.members:
            forms[index].add(model)
            witnesses[index].update(family.witnesses)
            protonated[index].update(root for root in mh if coelutes(root, index))
    excluded = {index for index, models in forms.items() if "[M+H]+" not in models}
    conflicting_forms = {
        index: models
        for index, models in forms.items()
        if "[M+H]+" in models and len(models) > 1
    }
    result = list(decisions)
    for index, models in conflicting_forms.items():
        decision = result[index]
        if not decision.keep and all(key in models for key in decision.relations):
            result[index] = RowDecision(
                keep=True,
                reason="ion_family_form_undetermined",
                undetermined=True,
                ion_form=UNCONFIRMED,
            )
    for index in excluded:
        roots = {root for root in protonated[index] - excluded if result[root].keep}
        root = next(iter(roots)) if len(roots) == 1 else None
        result[index] = RowDecision(
            keep=False,
            reason="non_protonated_ion_family",
            representative=root,
            relations=tuple(sorted("ion_family:" + model for model in forms[index])),
            via=tuple(sorted(witnesses[index] - {index, root})),
            undetermined=True,
        )
    for index, decision in enumerate(result):
        old_root = decision.representative
        if old_root is None or index in excluded or old_root not in excluded:
            continue
        new_root = result[old_root].representative
        if new_root is not None and not coelutes(new_root, index):
            new_root = None
        result[index] = replace(
            decision,
            representative=new_root,
            reason="derived_through_non_protonated_family",
            via=tuple(sorted(set(decision.via) | {old_root})),
            undetermined=True,
        )
    for index, models in forms.items():
        decision = result[index]
        if decision.keep:
            result[index] = replace(
                decision,
                ion_form=CONFIRMED_MH if models == {"[M+H]+"} else UNCONFIRMED,
                undetermined=decision.undetermined or len(models) > 1,
            )
    reviews = tuple(
        review_row(
            "non_protonated_ion_family",
            "",
            sorted(features[index].feature_id for index in family.witnesses),
            neutral_mass=f"{family.neutral_mass:.8f}",
            models="|".join(
                sorted(
                    features[index].feature_id + ":" + model
                    for index, model in family.members
                )
            ),
            decision="drop_non_mh",
        )
        for family in families
    )
    reviews += tuple(
        review_row(
            "ion_family_form_undetermined",
            features[index].feature_id,
            sorted(features[w].feature_id for w in witnesses[index] - {index}),
            relation="|".join(sorted(models)),
            decision="keep_candidate_mh" if result[index].keep else "drop",
        )
        for index, models in conflicting_forms.items()
    )
    return result, reviews


def _find_ion_families(
    mz: Sequence[float],
    coelutes: Callable[[int, int], bool],
    metal_isotopes: Container[int],
) -> tuple[_IonFamily, ...]:
    """Joint neutral-mass hypotheses, not a transitive co-elution component."""
    order = sorted(range(len(mz)), key=lambda index: mz[index])
    ordered = [mz[index] for index in order]

    def matching(predicted: float, seed: int) -> list[int]:
        low, high = mz_window(predicted, MZ_PPM)
        return [
            order[position]
            for position in range(
                bisect_left(ordered, low), bisect_right(ordered, high)
            )
            if coelutes(seed, order[position])
        ]

    found: dict[tuple[tuple[int, str], ...], _IonFamily] = {}
    for seed in order:
        neutral = mz[seed] - FAMILY_DIMER_H_OFFSET
        if neutral > 0:
            for count in FAMILY_METAL_MULTIMER_COUNTS:
                mg = [
                    index
                    for index in matching(count * neutral + MG_MINUS_H_ION_OFFSET, seed)
                    if index in metal_isotopes
                ]
                ca = matching(count * neutral + CA_MINUS_H_ION_OFFSET, seed)
                for magnesium in mg:
                    for calcium in ca:
                        if not coelutes(magnesium, calcium):
                            continue
                        signature = tuple(
                            sorted(
                                (
                                    (seed, "[M+H]+"),
                                    (magnesium, f"[{count}M-H+Mg]+"),
                                    (calcium, f"[{count}M-H+Ca]+"),
                                )
                            )
                        )
                        found[signature] = _IonFamily(
                            neutral, signature, frozenset((seed, magnesium, calcium))
                        )
        for _, seed_offset in FAMILY_MONOMERS:
            neutral = mz[seed] - seed_offset
            if neutral <= 0:
                continue
            monomers = [
                (index, model)
                for model, offset in FAMILY_MONOMERS
                for index in matching(neutral + offset, seed)
            ]
            if len({model for _, model in monomers}) < FAMILY_MIN_MONOMER_FORMS:
                continue
            for dimer in matching(2 * neutral + FAMILY_DIMER_H_OFFSET, seed):
                core = [
                    (i, model)
                    for i, model in monomers
                    if i != dimer and coelutes(i, dimer)
                ]
                if not any(i == seed for i, _ in core):
                    continue
                if len({model for _, model in core}) < FAMILY_MIN_MONOMER_FORMS:
                    continue
                if not any(
                    index in metal_isotopes and model.startswith("[M+Mg+")
                    for index, model in core
                ):
                    continue
                members = core + [(dimer, "[2M+H]+")]
                witnesses = frozenset(index for index, _ in members)
                for model, offset in FAMILY_METAL_DIMERS:
                    members.extend(
                        (index, model)
                        for index in matching(2 * neutral + offset, seed)
                        if index in metal_isotopes and coelutes(index, dimer)
                    )
                signature = tuple(sorted(set(members)))
                if signature not in found:
                    found[signature] = _IonFamily(neutral, signature, witnesses)
    return tuple(found.values())


def _mass_matches(
    mz: Sequence[float], relations: Sequence[Relation]
) -> dict[tuple[int, int], dict[str, float]]:
    order = sorted(range(len(mz)), key=lambda index: mz[index])
    ordered = [mz[index] for index in order]
    matches: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    for light, light_mz in enumerate(mz):
        for relation in relations:
            predicted = relation.predict(light_mz)
            low, high = mz_window(predicted, 2 * MZ_PPM)
            for position in range(
                bisect_left(ordered, low), bisect_right(ordered, high)
            ):
                heavy = order[position]
                if heavy == light or mz[heavy] <= light_mz:
                    continue
                error = signed_ppm_error(mz[heavy], predicted)
                if abs(error) <= MZ_PPM:
                    matches[(light, heavy)][relation.key] = error
    return dict(matches)


def _relation_rows(
    features: Sequence[FeatureIdentity],
    matches: Mapping[tuple[int, int], Mapping[str, float]],
    outcomes: Mapping[tuple[int, int], Mapping[str, str]],
    evidence: Mapping[tuple[int, int], PairEvidence],
    removed: Container[int],
    by_key: Mapping[str, Relation],
) -> tuple[dict[str, object], ...]:
    rows = []
    for (light, heavy), matched in matches.items():
        pair = evidence[(light, heavy)]
        if not pair.support:
            continue
        a, b = features[light], features[heavy]
        for key, error in matched.items():
            relation = by_key[key]
            outcome = outcomes[(light, heavy)][key]
            rows.append(
                {
                    "light_feature_id": a.feature_id,
                    "heavy_feature_id": b.feature_id,
                    "light_mz": repr(a.matrix_mz),
                    "heavy_mz": repr(b.matrix_mz),
                    "relation": key,
                    "relation_kind": relation.kind,
                    "predicted_heavy_mz": repr(relation.predict(a.matrix_mz)),
                    "e_ppm": f"{error:.3f}",
                    "both_direct_samples": pair.both_direct,
                    "support_samples": len(pair.support),
                    "max_apex_diff_sec": _number(pair.max_apex_diff_sec),
                    "backfill_coeluting_samples": pair.backfill_coeluting,
                    "backfill_apex_unavailable_samples": (
                        pair.backfill_apex_unavailable
                    ),
                    "ratio_samples": len(pair.ratios),
                    "median_light_heavy_ratio": _number(pair.median_ratio),
                    "log_ratio_mad": _number(pair.log_mad),
                    "outcome": outcome,
                    "used_for_collapse": (
                        "TRUE"
                        if outcome == "collapse" and heavy in removed
                        else "FALSE"
                    ),
                }
            )
    rows.sort(
        key=lambda row: (
            float(str(row["light_mz"])),
            float(str(row["heavy_mz"])),
            str(row["light_feature_id"]),
            str(row["heavy_feature_id"]),
            str(row["relation"]),
        )
    )
    return tuple(rows)


def _review_rows(
    features: Sequence[FeatureIdentity],
    cells: Sequence[Mapping[str, CellEvidence]],
    matches: Mapping[tuple[int, int], Mapping[str, float]],
    outcomes: Mapping[tuple[int, int], Mapping[str, str]],
    evidence: Mapping[tuple[int, int], PairEvidence],
    edges: Mapping[tuple[int, int], tuple[str, ...]],
    representative: Mapping[int, int],
    removed: Container[int],
    ambiguous_roots: Mapping[int, set[int]],
) -> tuple[dict[str, object], ...]:
    fid = [feature.feature_id for feature in features]
    rows: list[dict[str, object]] = []

    def add(kind: str, feature: int | None, related: Sequence[int], **detail: object):
        rows.append(
            review_row(
                kind,
                "" if feature is None else fid[feature],
                [fid[index] for index in related],
                **detail,
            )
        )

    for (light, heavy), matched in matches.items():
        pair = evidence[(light, heavy)]
        same_group = representative.get(light, light) == representative.get(
            heavy, heavy
        )
        both_removed = light in removed and heavy in removed
        if same_group:
            continue
        isotope_explained = any(
            outcomes[(light, heavy)].get(label) == "collapse"
            for label in ("41K", "M+2_unassigned")
        )
        for key in sorted(matched):
            outcome = outcomes[(light, heavy)][key]
            if both_removed and key != "41K":
                continue
            detail = {
                "relation": key,
                "support_samples": len(pair.support),
                "median_light_heavy_ratio": _number(pair.median_ratio),
                "log_ratio_mad": _number(pair.log_mad),
            }
            if key == "41K" and pair.coelutes and not isotope_explained:
                add("unexplained_1998_not_41K", heavy, [light], **detail)
            elif outcome in {"ratio_unstable", "ratio_unavailable"}:
                add("unstable_ratio", heavy, [light], **detail)
            elif outcome == "review_only":
                add("review_only_relation", heavy, [light], **detail)
    for heavy, root in representative.items():
        ratio = evidence[(root, heavy)].median_ratio
        if ratio is not None and ratio <= WEAK_REPRESENTATIVE_RATIO:
            add(
                "representative_weaker_than_member",
                root,
                [heavy],
                relation="|".join(edges.get((root, heavy), ("via_group",))),
                median_light_heavy_ratio=_number(ratio),
            )
    for heavy, candidates in ambiguous_roots.items():
        add(
            "derived_representative_undetermined",
            heavy,
            sorted(candidates, key=lambda index: fid[index]),
            decision="drop",
        )
    rows.sort(
        key=lambda row: (
            str(row["review_kind"]),
            str(row["feature_id"]),
            str(row["related_feature_ids"]),
            str(row["relation"]),
        )
    )
    rows.extend(_recurring_differences(features, cells, matches, removed))
    return tuple(rows)


def _recurring_differences(
    features: Sequence[FeatureIdentity],
    cells: Sequence[Mapping[str, CellEvidence]],
    matches: Mapping[tuple[int, int], Mapping[str, float]],
    removed: Container[int],
) -> list[dict[str, object]]:
    """Mass differences between co-eluting kept rows that no relation explains.

    Removed rows are left out: their differences to other rows follow from
    their representative's and would repeat it once per removed ion.
    """
    by_sample: dict[str, list[tuple[float, int]]] = defaultdict(list)
    ordinal: dict[str, int] = {}
    direct_mask = [0] * len(cells)
    for index, row in enumerate(cells):
        if index in removed:
            continue
        for sample, cell in row.items():
            if not cell.direct:
                continue
            direct_mask[index] |= 1 << ordinal.setdefault(sample, len(ordinal))
            if cell.apex_rt_min is not None:
                by_sample[sample].append((cell.apex_rt_min, index))
    together: Counter[tuple[int, int]] = Counter()
    for detections in by_sample.values():
        detections.sort()
        for position, (apex, index) in enumerate(detections):
            for later in range(position + 1, len(detections)):
                other_apex, other = detections[later]
                if (other_apex - apex) * 60.0 > MAX_APEX_SEC:
                    break
                together[(min(index, other), max(index, other))] += 1
    mz = [feature.matrix_mz for feature in features]
    deltas: list[tuple[float, str]] = []
    for (first, second), support in together.items():
        if support < MIN_SUPPORT_SAMPLES:
            continue
        light, heavy = sorted((first, second), key=lambda index: (mz[index], index))
        if (light, heavy) in matches or signed_ppm_error(
            mz[heavy], mz[light]
        ) <= MZ_PPM:
            continue
        both_direct = (direct_mask[light] & direct_mask[heavy]).bit_count()
        if support < MIN_SUPPORT_FRACTION * both_direct:
            continue
        pair_name = f"{features[light].feature_id}~{features[heavy].feature_id}"
        deltas.append((mz[heavy] - mz[light], pair_name))
    deltas.sort()
    clusters: list[list[tuple[float, str]]] = []
    start = 0
    while start < len(deltas):
        end = start
        while (
            end < len(deltas)
            and deltas[end][0] - deltas[start][0] <= RECURRING_DELTA_WIDTH
        ):
            end += 1
        if end - start >= RECURRING_MIN_PAIRS:
            clusters.append(deltas[start:end])
        start = end
    clusters.sort(key=lambda cluster: (-len(cluster), _median_delta(cluster)))
    return [
        {
            "review_kind": "recurring_unexplained_mass_difference",
            "feature_id": "",
            "related_feature_ids": ";".join(sorted(name for _, name in cluster)),
            "relation": f"delta_mz={_median_delta(cluster):.4f}",
            "detail": f"pair_count={len(cluster)}",
        }
        for cluster in clusters
    ]


def _median_delta(cluster: Sequence[tuple[float, str]]) -> float:
    return statistics.median(delta for delta, _ in cluster)


def review_row(
    kind: str, feature_id: str, related: Sequence[str], **detail: object
) -> dict[str, object]:
    relation = str(detail.pop("relation", ""))
    return {
        "review_kind": kind,
        "feature_id": feature_id,
        "related_feature_ids": ";".join(related),
        "relation": relation,
        "detail": ";".join(f"{key}={value}" for key, value in detail.items()),
    }


REVIEW_COLUMNS = (
    "review_kind",
    "feature_id",
    "related_feature_ids",
    "relation",
    "detail",
)
RELATION_COLUMNS = (
    "light_feature_id",
    "heavy_feature_id",
    "light_mz",
    "heavy_mz",
    "relation",
    "relation_kind",
    "predicted_heavy_mz",
    "e_ppm",
    "both_direct_samples",
    "support_samples",
    "max_apex_diff_sec",
    "backfill_coeluting_samples",
    "backfill_apex_unavailable_samples",
    "ratio_samples",
    "median_light_heavy_ratio",
    "log_ratio_mad",
    "outcome",
    "used_for_collapse",
)
DECISION_COLUMNS = (
    "same_compound_decision",
    "same_compound_reason",
    "same_compound_representative_feature_id",
    "same_compound_relation",
    "same_compound_via_feature_ids",
    "same_compound_relation_undetermined",
    "same_compound_ion_form",
    "same_compound_supporting_relations",
)
RELATIONS_NAME = "same_compound_relations.tsv"
REVIEW_NAME = "same_compound_review.tsv"


def group_outcome_review(
    result: StepResult,
    feature_ids: Sequence[str],
    final_keep: Sequence[bool],
) -> tuple[list[dict[str, object]], int]:
    """Report ion families whose representative a later feature filter removes.

    Removed derivatives are not tested again or promoted as replacements.
    """
    index_of = {feature: index for index, feature in enumerate(feature_ids)}
    groups: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(result.rows):
        root = row["same_compound_representative_feature_id"]
        if root:
            groups[index_of[str(root)]].append(index)
    review = []
    empty = 0
    for root in sorted(groups, key=lambda index: feature_ids[index]):
        group = groups[root]
        if not final_keep[root] and not any(final_keep[index] for index in group):
            empty += 1
            review.append(
                review_row(
                    "group_removed_by_feature_filter",
                    feature_ids[root],
                    sorted(feature_ids[index] for index in group),
                )
            )
    return review, empty


class SameCompoundStep:
    key = "same_compound"
    label = "同化合物收斂"
    description = (
        "移除同一化合物的同位素、Na⁺／K⁺／NH₄⁺ 與 Mg／Ca adduct、鹽簇與聚集離子，"
        "保留有證據的 [M+H]⁺。判定依據是同一樣本出峰時間相差 ≤5.5 秒，"
        "且質量差與強度比例符合預期。"
    )
    parameters: tuple[StepParameter, ...] = ()
    needs_cell_evidence = True

    def unavailable_reason(self, inputs: CurationInputs) -> str:
        return inputs.cell_evidence_problem

    def evaluate(
        self, matrix: CurationMatrix, parameters: Mapping[str, float]
    ) -> StepResult:
        if matrix.cells is None:
            raise ValueError("同化合物收斂需要每格直接偵測證據。")
        result = collapse_same_compound(matrix.features, matrix.cells)
        fid = [feature.feature_id for feature in matrix.features]
        rows = tuple(
            {
                "same_compound_decision": "keep" if decision.keep else "drop",
                "same_compound_reason": decision.reason,
                "same_compound_representative_feature_id": (
                    ""
                    if decision.representative is None
                    else fid[decision.representative]
                ),
                "same_compound_relation": "|".join(decision.relations),
                "same_compound_via_feature_ids": ";".join(
                    sorted(fid[index] for index in decision.via)
                ),
                "same_compound_relation_undetermined": (
                    "TRUE" if decision.undetermined else "FALSE"
                ),
                "same_compound_ion_form": decision.ion_form,
                "same_compound_supporting_relations": (
                    decision.member_count if decision.keep else ""
                ),
            }
            for decision in result.decisions
        )
        used = Counter(
            relation for decision in result.decisions for relation in decision.relations
        )
        return StepResult(
            columns=DECISION_COLUMNS,
            rows=rows,
            keep=tuple(decision.keep for decision in result.decisions),
            summary={
                "thresholds": {
                    "mz_ppm": MZ_PPM,
                    "max_apex_sec": MAX_APEX_SEC,
                    "min_support_samples": MIN_SUPPORT_SAMPLES,
                    "min_support_fraction": MIN_SUPPORT_FRACTION,
                    "isotope_max_log_ratio_mad": ISOTOPE_MAX_LOG_MAD,
                    "k41_median_light_heavy_range": list(K41_MEDIAN_RANGE),
                    "k41_sample_factor": ISOTOPE_RATIO_SAMPLE_FACTOR,
                    "k41_sample_fraction": ISOTOPE_RATIO_SAMPLE_FRACTION,
                    "unassigned_isotope_light_heavy_ratios": list(
                        UNASSIGNED_ISOTOPE_RATIOS
                    ),
                    "unassigned_isotope_median_factors": list(
                        UNASSIGNED_ISOTOPE_MEDIAN_FACTORS
                    ),
                    "ion_family_without_mh_min_monomer_forms": FAMILY_MIN_MONOMER_FORMS,
                    "ion_family_without_mh_requires_protonated_dimer": True,
                    "ion_family_requires_metal_isotope": True,
                    "ion_family_metal_multimer_counts": list(
                        FAMILY_METAL_MULTIMER_COUNTS
                    ),
                },
                "removed_feature_count": sum(
                    not decision.keep for decision in result.decisions
                ),
                "group_count": sum(
                    decision.member_count > 0 for decision in result.decisions
                ),
                "confirmed_mh_count": sum(
                    decision.keep and decision.ion_form == CONFIRMED_MH
                    for decision in result.decisions
                ),
                "removed_through_group_count": sum(
                    decision.reason == "derived_through_group"
                    for decision in result.decisions
                ),
                "removed_representative_undetermined_count": sum(
                    decision.reason == "derived_representative_undetermined"
                    for decision in result.decisions
                ),
                "removed_non_protonated_family_count": sum(
                    decision.reason == "non_protonated_ion_family"
                    for decision in result.decisions
                ),
                "removed_by_relation": dict(sorted(used.items())),
            },
            tables={
                RELATIONS_NAME: StepTable(RELATION_COLUMNS, result.relation_rows),
                REVIEW_NAME: StepTable(REVIEW_COLUMNS, result.review_rows),
            },
        )


SAME_COMPOUND_STEP = SameCompoundStep()


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:.6g}"


def _positive(value: float) -> bool:
    return math.isfinite(value) and value > 0
