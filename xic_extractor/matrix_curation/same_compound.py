"""Fold Matrix rows of one compound into one representative row.

Isotope, adduct and dimer rows are excluded when supported derivation paths
reach them from a directly co-eluting root. Neither an exact element identity
nor a unique root is required to exclude a derivative. Mass relations compare
row-center m/z; they are not per-sample mass evidence.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass

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
# NIST natural abundances.
C13_PER_C12 = 0.0107 / 0.9893
N15_PER_N14 = 0.00364 / 0.99636
K39_PER_K41 = 13.857

MZ_PPM = 10.0
MAX_APEX_SEC = 3.0
MIN_SUPPORT_SAMPLES = 3
MIN_SUPPORT_FRACTION = 0.8
ISOTOPE_MAX_LOG_MAD = 0.1
OTHER_MAX_LOG_MAD = 0.15
K41_MEDIAN_RANGE = (11.0, 18.0)
ISOTOPE_RATIO_SAMPLE_FACTOR = 2.0
ISOTOPE_RATIO_SAMPLE_FRACTION = 0.8
# NIST: one 26Mg substitution in one/two Mg atoms, or one 41K in two K.
# These overlapping models classify a derivative, not its element identity.
UNASSIGNED_ISOTOPE_RATIOS = (0.7899 / 0.1101, 0.7899 / (2 * 0.1101), K39_PER_K41 / 2)
UNASSIGNED_ISOTOPE_MEDIAN_FACTORS = (0.8, 1.3)
# A kept representative this much weaker than a removed member is reviewed.
WEAK_REPRESENTATIVE_RATIO = 0.1
RECURRING_DELTA_WIDTH = 0.005
RECURRING_MIN_PAIRS = 3

CONFIRMED_MH = "confirmed_[M+H]+"
UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Relation:
    """The heavier row derived from the lighter one."""

    key: str
    # isotope / adduct collapse; review is listed only; annotation is noted only.
    kind: str
    delta: float | None
    charge: int = 1
    # Isotopes with an abundance ceiling: (element mass, heavy/light per atom,
    # number of heavy atoms).
    abundance: tuple[float, float, int] | None = None

    def predict(self, light_mz: float) -> float:
        if self.delta is None:
            # [2M+H]+ from [M+H]+.
            return 2.0 * light_mz - PROTON
        return light_mz + self.delta

    def abundance_ceiling(self, light_mz: float) -> float | None:
        """Largest heavy/light ratio if every atom of the neutral were this element.

        The neutral mass assumes a proton, which overestimates it for any other
        cation and keeps the ceiling loose.
        """
        if self.abundance is None:
            return None
        element_mass, per_atom, count = self.abundance
        neutral = self.charge * (light_mz - PROTON)
        atoms = max(0, math.floor(neutral / element_mass))
        return math.comb(atoms, count) * per_atom**count


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
    Relation("[2M+H]+", "adduct", None),
    Relation("[M+Mg+HCOO]+", "adduct", 67.974871),
    Relation("[M+Mg+CH3COO]+", "adduct", 81.990521),
    Relation("[M+Ca+HCOO]+", "adduct", 83.952420),
    Relation("[M+Ca+CH3COO]+", "adduct", 97.968070),
    Relation("+CH2", "review", 14.015650),
    Relation("+H2", "review", 2.015650),
    Relation("+CH2O", "review", 30.010565),
    Relation("+CH3OH", "review", 32.026215),
    Relation("H2O_loss", "review", 18.010565),
    Relation("+116.047_dR", "annotation", 116.04734),
)
_COLLAPSE_KINDS = frozenset({"isotope", "adduct"})


@dataclass(frozen=True)
class PairEvidence:
    both_direct: int
    support: tuple[str, ...]
    max_apex_diff_sec: float | None
    backfill_coeluting: int
    backfill_apex_unavailable: int
    # light/heavy on support samples whose values are finite and positive.
    ratios: tuple[float, ...]

    @property
    def coelutes(self) -> bool:
        return (
            len(self.support) >= MIN_SUPPORT_SAMPLES
            and len(self.support) >= MIN_SUPPORT_FRACTION * self.both_direct
        )

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
    """Same-sample co-elution of two rows; only direct detections count."""
    both_direct = 0
    support: list[str] = []
    diffs: list[float] = []
    backfill_coeluting = backfill_unavailable = 0
    ratios: list[float] = []
    for sample in sorted(light.keys() & heavy.keys()):
        a, b = light[sample], heavy[sample]
        apart = (
            None
            if a.apex_rt_min is None or b.apex_rt_min is None
            else abs(a.apex_rt_min - b.apex_rt_min) * 60.0
        )
        if a.direct and b.direct:
            both_direct += 1
            if apart is not None and apart <= MAX_APEX_SEC:
                support.append(sample)
                diffs.append(apart)
                if _positive(a.value) and _positive(b.value):
                    ratios.append(a.value / b.value)
        elif apart is None:
            backfill_unavailable += 1
        elif apart <= MAX_APEX_SEC:
            backfill_coeluting += 1
    return PairEvidence(
        both_direct=both_direct,
        support=tuple(support),
        max_apex_diff_sec=max(diffs) if diffs else None,
        backfill_coeluting=backfill_coeluting,
        backfill_apex_unavailable=backfill_unavailable,
        ratios=tuple(ratios),
    )


def relation_outcome(relation: Relation, light_mz: float, pair: PairEvidence) -> str:
    """Whether this relation folds the heavier row into the lighter one."""
    if relation.kind == "annotation":
        return "annotation_only"
    if not pair.coelutes:
        return "coelution_insufficient"
    if relation.kind == "review":
        return "review_only"
    if len(pair.ratios) < MIN_SUPPORT_SAMPLES:
        return "ratio_unavailable"
    mad = pair.log_mad
    limit = ISOTOPE_MAX_LOG_MAD if relation.kind == "isotope" else OTHER_MAX_LOG_MAD
    if mad is None or mad > limit:
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
    ceiling = relation.abundance_ceiling(light_mz)
    if ceiling is not None and any(1.0 / ratio > ceiling for ratio in pair.ratios):
        return "isotope_abundance_exceeded"
    return "collapse"


@dataclass(frozen=True)
class RowDecision:
    keep: bool
    reason: str
    representative: int | None = None
    # Folding relations from the rows this one derives from directly.
    relations: tuple[str, ...] = ()
    # Those rows, when they are not the representative itself.
    via: tuple[int, ...] = ()
    # Element or attribution unresolved; this does not imply keep=True.
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

    A removed row is reached from at least one directly co-eluting root through
    folding relations, each passing its own checks. Multiple roots leave its
    attribution blank, not its artifact classification undecided. Rows that
    only co-elute through others are kept. Results
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
    edges: dict[tuple[int, int], tuple[str, ...]] = {}
    for pair, matched in matches.items():
        outcomes[pair] = {
            key: relation_outcome(by_key[key], mz[pair[0]], evidence[pair])
            for key in matched
        }
        folding = tuple(
            sorted(key for key, value in outcomes[pair].items() if value == "collapse")
        )
        if folding:
            edges[pair] = folding

    parents: dict[int, set[int]] = defaultdict(set)
    children: dict[int, set[int]] = defaultdict(set)
    for light, heavy in edges:
        parents[heavy].add(light)
        children[light].add(heavy)
    roots = {index for index in range(len(features)) if not parents.get(index)}
    # Folding edges always go to a higher m/z, so every path is finite.
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
    unresolved: dict[int, str] = {}
    for heavy in parents:
        coeluting_roots = set()
        for root in reached_from[heavy]:
            if (root, heavy) not in evidence:
                evidence[(root, heavy)] = pair_evidence(cells[root], cells[heavy])
            if evidence[(root, heavy)].coelutes:
                coeluting_roots.add(root)
        if len(coeluting_roots) == 1:
            representative[heavy] = next(iter(coeluting_roots))
        elif coeluting_roots:
            ambiguous_roots[heavy] = coeluting_roots
        else:
            unresolved[heavy] = "no_direct_coelution_with_representative"
    removed = representative.keys() | ambiguous_roots.keys()
    members: dict[int, list[int]] = defaultdict(list)
    for heavy, root in representative.items():
        members[root].append(heavy)

    # Element/relationship uncertainty travels with the derivation path.
    # A 13C child of an unassigned M+2 ion is not a confirmed 13C-only identity.
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
        confirmed = any(
            (index, member) in edges
            and all(by_key[key].kind == "adduct" for key in edges[(index, member)])
            for member in children.get(index, ())
            if member in removed
        )
        decisions.append(
            RowDecision(
                keep=True,
                reason=unresolved.get(
                    index,
                    "representative" if members.get(index) else "no_folding_relation",
                ),
                ion_form=CONFIRMED_MH if confirmed else UNCONFIRMED,
                member_count=len(members.get(index, ())),
            )
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
        unresolved,
        removed,
        ambiguous_roots,
        parents,
    )
    return SameCompoundResult(tuple(decisions), relation_rows, review_rows)


def _mass_matches(
    mz: Sequence[float], relations: Sequence[Relation]
) -> dict[tuple[int, int], dict[str, float]]:
    order = sorted(range(len(mz)), key=lambda index: mz[index])
    ordered = [mz[index] for index in order]
    matches: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    tolerance = MZ_PPM * 1e-6
    for light, light_mz in enumerate(mz):
        for relation in relations:
            predicted = relation.predict(light_mz)
            low = predicted * (1 - 2 * tolerance)
            high = predicted * (1 + 2 * tolerance)
            for position in range(
                bisect_left(ordered, low), bisect_right(ordered, high)
            ):
                heavy = order[position]
                if heavy == light or mz[heavy] <= light_mz:
                    continue
                error = (mz[heavy] - predicted) / predicted * 1e6
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
        # Pairs that never co-elute in any sample are different compounds.
        if not pair.support and not pair.backfill_coeluting:
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
                    "center_mz_basis": _basis(a, b),
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
    unresolved: Mapping[int, str],
    removed: Container[int],
    ambiguous_roots: Mapping[int, set[int]],
    parents: Mapping[int, set[int]],
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
            elif (
                outcome == "coelution_insufficient"
                and key != "+116.047_dR"
                and len(pair.support) < MIN_SUPPORT_SAMPLES
                and len(pair.support) + pair.backfill_coeluting >= MIN_SUPPORT_SAMPLES
            ):
                add(
                    "backfill_only_coelution",
                    heavy,
                    [light],
                    backfill_coeluting_samples=pair.backfill_coeluting,
                    **detail,
                )
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
    for heavy, reason in unresolved.items():
        add(reason, heavy, sorted(parents[heavy], key=lambda index: fid[index]))
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
    # A frequency table, most frequent difference first.
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
    # Samples each row detected directly, as bits, for the co-elution share.
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
    # Supporting samples per pair: both direct, apices within the limit, as
    # pair_evidence counts them.
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
        if (light, heavy) in matches or (mz[heavy] - mz[light]) / mz[
            light
        ] * 1e6 <= MZ_PPM:
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
    "center_mz_basis",
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
    kept_by_other_steps: Sequence[bool],
    final_keep: Sequence[bool],
) -> tuple[list[dict[str, object]], int]:
    """Groups another step empties, and groups with no row left at all.

    A representative dropped by another enabled step is not replaced by one of
    its members; each such group whose removed member would pass those steps is
    reviewed, so systematically lost adduct-forming compounds stay visible.
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
        passing = [index for index in group if kept_by_other_steps[index]]
        if not kept_by_other_steps[root] and passing:
            review.append(
                review_row(
                    "representative_dropped_member_passes",
                    feature_ids[root],
                    sorted(feature_ids[index] for index in passing),
                )
            )
    return review, empty


class SameCompoundStep:
    key = "same_compound"
    label = "同化合物收斂"
    description = (
        "排除同位素、Na⁺／K⁺／NH₄⁺、Mg／Ca adduct 與二聚體衍生列，"
        "保留有證據的 [M+H]⁺，不改選較強的其他離子。元素或母列未定不阻擋排除。"
        "只計直接偵測格：同一樣本 apex 差 "
        "≤3 秒、強度比穩定，同位素另檢查天然豐度。質量關係以列中心 m/z 比對。"
        "需要該次分析的 cid_nl_explanation.sqlite。"
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
                    "center_mz_basis": sorted(
                        {feature.center_mz_basis for feature in matrix.features}
                    ),
                    "max_apex_sec": MAX_APEX_SEC,
                    "min_support_samples": MIN_SUPPORT_SAMPLES,
                    "min_support_fraction": MIN_SUPPORT_FRACTION,
                    "isotope_max_log_ratio_mad": ISOTOPE_MAX_LOG_MAD,
                    "other_max_log_ratio_mad": OTHER_MAX_LOG_MAD,
                    "k41_median_light_heavy_range": list(K41_MEDIAN_RANGE),
                    "k41_sample_factor": ISOTOPE_RATIO_SAMPLE_FACTOR,
                    "k41_sample_fraction": ISOTOPE_RATIO_SAMPLE_FRACTION,
                    "unassigned_isotope_light_heavy_ratios": list(
                        UNASSIGNED_ISOTOPE_RATIOS
                    ),
                    "unassigned_isotope_median_factors": list(
                        UNASSIGNED_ISOTOPE_MEDIAN_FACTORS
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
                "removed_by_relation": dict(sorted(used.items())),
            },
            tables={
                RELATIONS_NAME: StepTable(RELATION_COLUMNS, result.relation_rows),
                REVIEW_NAME: StepTable(REVIEW_COLUMNS, result.review_rows),
            },
        )


SAME_COMPOUND_STEP = SameCompoundStep()


def _basis(a: FeatureIdentity, b: FeatureIdentity) -> str:
    return (
        a.center_mz_basis
        if a.center_mz_basis == b.center_mz_basis
        else f"{a.center_mz_basis}|{b.center_mz_basis}"
    )


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:.6g}"


def _positive(value: float) -> bool:
    return math.isfinite(value) and value > 0
