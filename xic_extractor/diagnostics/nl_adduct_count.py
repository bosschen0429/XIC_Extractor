"""Count adducts among one sample's neutral-loss Discovery candidates.

A compound counts once. Derived ions are other ions of a compound that elute
with it: a known mass difference, elution with a nucleoside of the same sugar,
or a peak shape synchronized with another candidate or with a nucleoside of
another sugar. A compound whose every ion leaves a negative mass defect after
the loss holds no organic nucleobase: an inorganic cluster or multiply charged
ion lost the same elemental composition. A compound whose groups each had one
attached MS2 event is unconfirmed and listed apart. Each derived ion names the
ion it derives from and, where its mass difference is known, the relation.
RAW reading lives in ``nl_adduct_count_acquisition``.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.matrix_curation.same_compound import PROTON

NA = 21.981944
RELATIONS_DA = {
    "13C": 1.003355,
    "15N": 0.997035,
    "13C2": 2.00671,
    "13C15N": 2.00039,
    "+H2": 2.01565,
    "Na": NA,
    "K": 37.955882,
    "NH4": 17.026549,
    "+H2O": 18.010565,
}
ISOTOPES = ("13C", "15N", "13C2", "13C15N")
ADDUCT_LABEL_DA = {"+H4": 4.0313, "MeOH": 32.026215, "H2O2": 34.005479}
METAL_LABEL_DA = {
    "Mg-H": 21.969392,
    "Mg+Cl": 57.946069,
    "Mg+OAc": 81.990521,
    "Ca+OAc": 97.96807,
    "Fe+OAc": 113.940416,
    "FeEDTA": 345.002127,
    "ZnEDTA": 354.004157,
}
RELATION_TOL_DA = 0.003
DIMER_TOL_DA = 0.004
RELATION_RT_MIN = 0.1
NUCLEOSIDE_TOL_DA = 0.003
NUCLEOSIDE_SELF_RT_MIN = 0.1
COELUTION_RT_MIN = 0.25
BASE_MIN_MASS_DEFECT = 0.0
SECOND_LOSS_MIN_RATIO = 0.1
LATE_RT_MIN = 40.0
SHAPE_PAIR_RT_MIN = 0.06
SHAPE_MIN_MZ_GAP = 0.01
SHAPE_MIN_R = 0.9
SHAPE_MIN_POINTS = 5
MATCH_PPM = 10.0
MATCH_RT_MIN = 0.3
ANCHOR_SEARCH_RT_MIN = 1.0
KINDS = ("nucleoside", "nucleoside_mz", "two_sugar", "other")


@dataclass(frozen=True)
class Candidate:
    peak_id: str
    mz: float
    rt: float
    event_count: int
    second_loss_ratio: float = 0.0


@dataclass(frozen=True)
class Anchor:
    """A nucleoside and its apex in this sample; ``rt`` is None when not seen."""

    label: str
    mz: float
    rt: float | None


@dataclass(frozen=True)
class Group:
    """Candidates joined by known mass differences; the lightest represents it."""

    members: tuple[Candidate, ...]
    relations: Mapping[str, tuple[str, ...]]
    category: str
    base_mass_defect: float

    @property
    def mz(self) -> float:
        return self.members[0].mz

    @property
    def rt(self) -> float:
        return self.members[0].rt

    @property
    def single_event(self) -> bool:
        return max(c.event_count for c in self.members) == 1


@dataclass(frozen=True)
class Unit:
    """Kept groups whose peak shapes are synchronized: one compound."""

    groups: tuple[Group, ...]

    @property
    def unit_id(self) -> str:
        return f"{self.groups[0].mz:.4f}@{self.groups[0].rt:.2f}"

    @property
    def members(self) -> list[tuple[float, float]]:
        return [(g.mz, g.rt) for g in self.groups]

    @property
    def kind(self) -> str:
        return next(k for k in KINDS if any(g.category == k for g in self.groups))

    @property
    def unconfirmed(self) -> bool:
        return self.kind == "other" and all(g.single_event for g in self.groups)

    @property
    def no_nucleobase(self) -> bool:
        """Every ion's remainder after the loss has a negative mass defect."""
        return all(g.base_mass_defect < BASE_MIN_MASS_DEFECT for g in self.groups)

    @property
    def late(self) -> bool:
        return min(g.rt for g in self.groups) > LATE_RT_MIN


def components(count: int, edges) -> list[list[int]]:
    parent = list(range(count))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        parent[root(b)] = root(a)
    joined: dict[int, list[int]] = defaultdict(list)
    for i in range(count):
        joined[root(i)].append(i)
    return list(joined.values())


def relation(light: Candidate, heavy: Candidate, loss_da: float) -> str | None:
    """The known difference taking ``light`` to ``heavy``, if they co-elute."""
    if abs(light.rt - heavy.rt) > RELATION_RT_MIN:
        return None
    if abs(heavy.mz - (2 * light.mz - PROTON)) < DIMER_TOL_DA:
        return "2M+H"
    if abs(heavy.mz - (2 * light.mz - PROTON + NA)) < DIMER_TOL_DA:
        return "2M+Na"
    deltas = {**RELATIONS_DA, "+loss": loss_da, "+2loss": 2 * loss_da}
    return next(
        (
            n
            for n, d in deltas.items()
            if abs(heavy.mz - light.mz - d) < RELATION_TOL_DA
        ),
        None,
    )


def group_candidates(
    candidates: Sequence[Candidate], loss_da: float, anchors: Sequence[Anchor]
) -> tuple[Group, ...]:
    """Join related candidates and place each group against same-sugar nucleosides."""
    ordered = sorted(candidates, key=lambda c: (c.mz, c.rt, c.peak_id))
    reached: dict[int, list[str]] = defaultdict(list)
    edges = []
    for i, light in enumerate(ordered):
        for j, heavy in enumerate(ordered):
            name = relation(light, heavy, loss_da) if i != j else None
            if name:
                edges.append((i, j))
                reached[j].append(name)
    groups = []
    for found in components(len(ordered), edges):
        found.sort(key=lambda i: (ordered[i].mz, ordered[i].rt, ordered[i].peak_id))
        members = tuple(ordered[i] for i in found)
        relations = {
            ordered[i].peak_id: tuple(sorted(set(reached[i]))) for i in found[1:]
        }
        base = members[0].mz - loss_da
        groups.append(
            Group(
                members,
                relations,
                _category(members, relations, anchors),
                base - round(base),
            )
        )
    return tuple(sorted(groups, key=lambda g: (g.mz, g.rt)))


def _category(members, relations, anchors) -> str:
    light = members[0]
    seen = [a.rt for a in anchors if a.rt is not None]
    gap = min((abs(light.rt - rt) for rt in seen), default=float("inf"))
    if any(abs(light.mz - a.mz) < NUCLEOSIDE_TOL_DA for a in anchors):
        return "nucleoside" if gap < NUCLEOSIDE_SELF_RT_MIN else "nucleoside_mz"
    if gap <= COELUTION_RT_MIN:
        return "nucleoside_elution"
    two = any(c.second_loss_ratio >= SECOND_LOSS_MIN_RATIO for c in members)
    two = two or any(n in ("+loss", "+2loss") for v in relations.values() for n in v)
    return "two_sugar" if two else "other"


def relation_label(
    delta: float, parent_mz: float, anchors: Sequence[Anchor], loss_da: float
) -> str:
    """Name a derived ion's difference from its parent, after up to one isotope."""
    named = {
        **RELATIONS_DA,
        **ADDUCT_LABEL_DA,
        **METAL_LABEL_DA,
        "+loss": loss_da,
        "+2loss": 2 * loss_da,
        "2M+H": parent_mz - PROTON,
        "2M+Na": parent_mz - PROTON + NA,
        **{f"M+{a.label}": a.mz - PROTON for a in anchors},
    }
    for isotope, shift in (("", 0.0), *((n, RELATIONS_DA[n]) for n in ISOTOPES)):
        for name, d in named.items():
            if abs(delta - shift - d) < RELATION_TOL_DA:
                return f"{name};{isotope}" if isotope else name
    return ""


def relation_family(relation: str) -> str:
    """isotope, adduct, metal, dimer, extra_loss, or unlabeled."""
    names = [n for n in relation.split(";") if n]
    chemical = [n for n in names if n not in ISOTOPES]
    if not names:
        return "unlabeled"
    if not chemical:
        return "isotope"
    name = chemical[0]
    if name.startswith(("2M", "M+")):
        return "dimer"
    if name in ("+loss", "+2loss"):
        return "extra_loss"
    return "metal" if name in METAL_LABEL_DA else "adduct"


def shape_pairs(kept: Sequence[Group]) -> list[tuple[int, int]]:
    """Kept groups close enough in apex to test for synchronized peak shape."""
    return [
        (a, b)
        for a in range(len(kept))
        for b in range(a + 1, len(kept))
        if abs(kept[a].rt - kept[b].rt) <= SHAPE_PAIR_RT_MIN
        and abs(kept[a].mz - kept[b].mz) > SHAPE_MIN_MZ_GAP
    ]


def synced(first, second, first_rt=None, second_rt=None) -> bool:
    """Same peak shape (r >= 0.9) on shared scans; same apex when times are given."""
    a, b = np.asarray(first, float), np.asarray(second, float)
    if len(a) < SHAPE_MIN_POINTS or len(a) != len(b) or a.std() == 0 or b.std() == 0:
        return False
    if (
        first_rt is not None
        and abs(first_rt[a.argmax()] - second_rt[b.argmax()]) > SHAPE_PAIR_RT_MIN
    ):
        return False
    return float(np.corrcoef(a, b)[0, 1]) >= SHAPE_MIN_R


def build_units(kept: Sequence[Group], pairs) -> tuple[Unit, ...]:
    units = [
        Unit(tuple(kept[i] for i in found)) for found in components(len(kept), pairs)
    ]
    return tuple(sorted(units, key=lambda u: u.members[0]))


def other_sugar_checks(units: Sequence[Unit], anchors: Sequence[Anchor]):
    """(unit, member m/z, member RT, nucleoside) pairs to test for synchronized shape.

    Ions of a nucleoside with another sugar keep that sugar and cannot give this
    loss, so a unit is removed only when its shape follows such a peak.
    """
    return [
        (unit, group.mz, group.rt, anchor)
        for unit in units
        if unit.kind != "nucleoside"
        for group in unit.groups
        for anchor in anchors
        if anchor.rt is not None and abs(group.rt - anchor.rt) <= COELUTION_RT_MIN
    ]


@dataclass(frozen=True)
class RunCount:
    sample: str
    candidates: tuple[Candidate, ...]
    groups: tuple[Group, ...]
    units: tuple[Unit, ...]
    removed: frozenset[str]
    anchors: tuple[Anchor, ...]
    loss_da: float

    @property
    def remaining(self) -> list[Unit]:
        return [
            u
            for u in self.units
            if u.unit_id not in self.removed and not u.no_nucleobase
        ]

    @property
    def counted(self) -> list[Unit]:
        return [u for u in self.remaining if not u.unconfirmed]

    def summary(self) -> dict[str, object]:
        kept = sum(g.category != "nucleoside_elution" for g in self.groups)
        kinds = Counter(u.kind for u in self.counted)
        return {
            "sample": self.sample,
            "detected": len(self.candidates),
            "derived_known_difference": len(self.candidates) - len(self.groups),
            "derived_nucleoside": len(self.groups) - kept + len(self.removed),
            "derived_synchronized_shape": kept - len(self.units),
            "no_nucleobase": sum(
                u.no_nucleobase and u.unit_id not in self.removed for u in self.units
            ),
            "unconfirmed": len(self.remaining) - len(self.counted),
            "counted": len(self.counted),
            "counted_kinds": {k: kinds.get(k, 0) for k in KINDS},
            "counted_late": sum(u.late for u in self.counted),
            "derived_relations": dict(
                Counter(
                    relation_family(str(row["relation"]))
                    for row in self.candidate_rows()
                    if str(row["fate"]).startswith("derived")
                )
            ),
            "removed_other_sugar": sorted(self.removed),
            "anchor_rt": {a.label: round(a.rt, 3) for a in self.anchors if a.rt},
        }

    def candidate_rows(self) -> list[dict[str, object]]:
        """Every candidate and why it counts, is derived, or is unconfirmed."""
        unit_of = {g.members[0].peak_id: u for u in self.units for g in u.groups}
        seen = [(a.rt, a) for a in self.anchors if a.rt is not None]
        rows = []
        for group in self.groups:
            unit = unit_of.get(group.members[0].peak_id)
            parent: tuple[str, float] | None = None
            if unit is None:
                fate = "derived_nucleoside_elution"
                if seen:
                    _, anchor = min(seen, key=lambda s: abs(group.rt - s[0]))
                    parent = (anchor.label, anchor.mz)
            elif unit.unit_id in self.removed:
                fate = "derived_other_sugar_nucleoside"
            elif unit.groups[0] is not group:
                fate = "derived_synchronized_shape"
                parent = (unit.unit_id, unit.groups[0].mz)
            elif unit.no_nucleobase:
                fate = "no_nucleobase"
            else:
                fate = "unconfirmed" if unit.unconfirmed else "counted"
            head_relation = (
                relation_label(
                    group.mz - parent[1], parent[1], self.anchors, self.loss_da
                )
                if parent
                else ""
            )
            for c in group.members:
                head = c is group.members[0]
                rows.append(
                    {
                        "sample": self.sample,
                        "peak_id": c.peak_id,
                        "mz": round(c.mz, 4),
                        "rt": round(c.rt, 3),
                        "fate": fate if head else "derived_known_difference",
                        "relation": head_relation
                        if head
                        else ";".join(group.relations[c.peak_id]),
                        "derived_from": (parent[0] if parent else "")
                        if head
                        else f"{group.mz:.4f}@{group.rt:.2f}",
                        "group_category": group.category,
                        "unit": unit.unit_id if unit else "",
                        "attached_ms2_events": c.event_count,
                        "second_loss_ratio": round(c.second_loss_ratio, 3),
                    }
                )
        return rows


def _match(first, second, rt_map=lambda rt: rt) -> bool:
    return any(
        within_ppm(mz_b, mz_a, MATCH_PPM) and abs(rt_map(rt_a) - rt_b) <= MATCH_RT_MIN
        for mz_a, rt_a in first
        for mz_b, rt_b in second
    )


@dataclass(frozen=True)
class Consensus:
    """One compound across replicate injections."""

    runs: tuple[int, ...]
    members: tuple[tuple[float, float], ...]
    unconfirmed: bool


def union_runs(runs: Sequence[RunCount]) -> list[Consensus]:
    items = [(i, u) for i, run in enumerate(runs) for u in run.remaining]
    edges = [
        (a, b)
        for a in range(len(items))
        for b in range(a + 1, len(items))
        if items[a][0] != items[b][0]
        and _match(items[a][1].members, items[b][1].members)
    ]
    found = [[items[i] for i in c] for c in components(len(items), edges)]
    return sorted(
        (
            Consensus(
                tuple(sorted({i for i, _ in f})),
                tuple(sorted({m for _, u in f for m in u.members})),
                all(u.unconfirmed for _, u in f),
            )
            for f in found
        ),
        key=lambda c: c.members[0],
    )


def cumulative(consensus: Sequence[Consensus], runs: int) -> dict[str, list[int]]:
    """Distinct compounds seen by the first k injections, k = 1..runs."""
    return {
        name: [
            sum(c.unconfirmed is flag and min(c.runs) < k for c in consensus)
            for k in range(1, runs + 1)
        ]
        for name, flag in (("counted", False), ("unconfirmed", True))
    }


def rt_map(pairs: Sequence[tuple[float, float]]):
    """Piecewise-linear map through (RT here, RT there) pairs, shifted past ends."""
    if not pairs:
        return lambda rt: rt
    xs, shifts = zip(*sorted((x, y - x) for x, y in pairs), strict=True)
    return lambda rt: rt + float(np.interp(rt, xs, shifts))


def shared_rt_pairs(
    main: RunCount, controls: Sequence[RunCount], consensus: Sequence[Consensus]
) -> list[tuple[float, float]]:
    """RT pairs of nucleosides, extended past their range by paired compounds.

    Outside the nucleosides' RT range, a counted compound pairs when exactly
    one counted control compound shares its m/z within ANCHOR_SEARCH_RT_MIN,
    and that compound pairs with no other.
    """
    seen: dict[str, list[float]] = defaultdict(list)
    for run in controls:
        for a in run.anchors:
            if a.rt:
                seen[a.label].append(a.rt)
    pairs = [
        (a.rt, statistics.median(seen[a.label]))
        for a in main.anchors
        if a.rt and a.label in seen
    ]
    covered = (min(x for x, _ in pairs), max(x for x, _ in pairs)) if pairs else None
    hits: dict[int, dict[int, float]] = defaultdict(dict)
    for i, unit in enumerate(main.counted):
        mz, rt = unit.members[0]
        if covered and covered[0] <= rt <= covered[1]:
            continue
        for j, c in enumerate(consensus):
            rts = [
                y
                for x, y in c.members
                if within_ppm(x, mz, MATCH_PPM) and abs(y - rt) <= ANCHOR_SEARCH_RT_MIN
            ]
            if rts and not c.unconfirmed:
                hits[i][j] = statistics.median(rts)
    partners = Counter(j for found in hits.values() for j in found)
    for i, found in hits.items():
        (j, rt_there), *others = found.items()
        if not others and partners[j] == 1:
            pairs.append((main.counted[i].members[0][1], rt_there))
    return pairs


def overlap(main: RunCount, controls: Sequence[RunCount], consensus) -> list[dict]:
    """Counted compounds of ``main`` also found among replicate controls."""
    mapped = rt_map(shared_rt_pairs(main, controls, consensus))
    rows = []
    for unit in main.counted:
        found = [c for c in consensus if _match(unit.members, c.members, mapped)]
        if found:
            best = max(found, key=lambda c: len(c.runs))
            rows.append(
                {
                    "unit": unit.unit_id,
                    "kind": unit.kind,
                    "control_injections": len(best.runs),
                    "control_unconfirmed": best.unconfirmed,
                }
            )
    return rows
