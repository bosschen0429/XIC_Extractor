"""Count adducts among one sample's neutral-loss Discovery candidates.

A compound counts once. Derived ions are other ions of a compound that elute
with it: a known mass difference, elution with a nucleoside of the same sugar,
or a peak shape synchronized with another candidate or with a nucleoside of
another sugar. A compound whose every ion leaves a negative mass defect after
the loss holds no organic nucleobase: an inorganic cluster or multiply charged
ion lost the same elemental composition. A compound whose groups each had one
attached MS2 event is unconfirmed and listed apart. Each candidate's fate is
decided once, with the ion it derives from and, where its mass difference is
known, the relation; the summary counts those fates. Chromatograms come from
an injected reader, so RAW access stays in ``nl_adduct_count_acquisition``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.matrix_curation.same_compound import PROTON
from xic_extractor.xic_models import XICTrace

NA = 21.981944
ISOTOPE_DA = {
    "13C": 1.003355,
    "15N": 0.997035,
    "13C2": 2.00671,
    "13C15N": 2.00039,
}
JOINING_DA = {
    **ISOTOPE_DA,
    "+H2": 2.01565,
    "Na": NA,
    "K": 37.955882,
    "NH4": 17.026549,
    "+H2O": 18.010565,
}
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
SAME_COMPOUND_RT_MIN = 0.1
NUCLEOSIDE_TOL_DA = 0.003
NUCLEOSIDE_ELUTION_RT_MIN = 0.25
BASE_MIN_MASS_DEFECT = 0.0
SECOND_LOSS_MIN_RATIO = 0.1
LATE_RT_MIN = 40.0
SHAPE_MIN_MZ_GAP = 0.01
SHAPE_MIN_R = 0.9
SHAPE_MIN_POINTS = 5
MATCH_PPM = 10.0
MATCH_RT_MIN = 0.3
KINDS = ("nucleoside", "nucleoside_mz", "two_sugar", "other")
COUNTED, UNCONFIRMED, NO_NUCLEOBASE = "counted", "unconfirmed", "no_nucleobase"
KNOWN_DIFFERENCE = "derived_known_difference"
NUCLEOSIDE_ELUTION = "derived_nucleoside_elution"
OTHER_SUGAR = "derived_other_sugar_nucleoside"
SYNCHRONIZED_SHAPE = "derived_synchronized_shape"

Xic = Callable[[Sequence[tuple[float, float]]], Sequence[XICTrace]]
"""Chromatograms of (m/z, centre RT) requests, in request order."""


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
    """Kept groups whose peak shapes are synchronized: one compound.

    ``other_sugar`` names the nucleoside of another sugar whose peak shape the
    compound follows; such a compound is that nucleoside's ion.
    """

    groups: tuple[Group, ...]
    loss_da: float
    other_sugar: str = ""

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
        return all(
            (g.mz - self.loss_da) - round(g.mz - self.loss_da) < BASE_MIN_MASS_DEFECT
            for g in self.groups
        )

    @property
    def fate(self) -> str:
        if self.other_sugar:
            return OTHER_SUGAR
        if self.no_nucleobase:
            return NO_NUCLEOBASE
        return UNCONFIRMED if self.unconfirmed else COUNTED

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


def relation_label(
    delta: float,
    parent_mz: float,
    anchors: Sequence[Anchor],
    loss_da: float,
    *,
    joining: bool = False,
) -> str:
    """Name the difference from an ion at ``parent_mz``.

    Joining names (isotopes, common adducts, dimers, extra losses) join ions
    into one group; the labels add solvent, metal and nucleoside partners, and
    allow one isotope on top.
    """
    dimers = {"2M+H": parent_mz - PROTON, "2M+Na": parent_mz - PROTON + NA}
    named = {**JOINING_DA, "+loss": loss_da, "+2loss": 2 * loss_da}
    shifts: list[tuple[str, float]] = [("", 0.0)]
    if not joining:
        named |= {
            **ADDUCT_LABEL_DA,
            **METAL_LABEL_DA,
            **{f"M+{a.label}": a.mz - PROTON for a in anchors},
        }
        shifts += ISOTOPE_DA.items()
    for isotope, shift in shifts:
        found = next(
            (n for n, d in dimers.items() if abs(delta - shift - d) < DIMER_TOL_DA),
            None,
        ) or next(
            (n for n, d in named.items() if abs(delta - shift - d) < RELATION_TOL_DA),
            None,
        )
        if found:
            return f"{found};{isotope}" if isotope else found
    return ""


def relation_family(relation: str) -> str:
    """isotope, adduct, metal, dimer, extra_loss, or unlabeled."""
    names = [n for n in relation.split(";") if n]
    chemical = [n for n in names if n not in ISOTOPE_DA]
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


def group_candidates(
    candidates: Sequence[Candidate], loss_da: float, anchors: Sequence[Anchor]
) -> tuple[Group, ...]:
    """Join co-eluting candidates by joining names; place groups against nucleosides."""
    ordered = sorted(candidates, key=lambda c: (c.mz, c.rt, c.peak_id))
    reached: dict[int, list[str]] = defaultdict(list)
    edges = []
    for i, light in enumerate(ordered):
        for j, heavy in enumerate(ordered):
            if i == j or abs(light.rt - heavy.rt) > SAME_COMPOUND_RT_MIN:
                continue
            name = relation_label(
                heavy.mz - light.mz, light.mz, (), loss_da, joining=True
            )
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
        groups.append(Group(members, relations, _category(members, relations, anchors)))
    return tuple(sorted(groups, key=lambda g: (g.mz, g.rt)))


def _category(members, relations, anchors) -> str:
    light = members[0]
    seen = [a.rt for a in anchors if a.rt is not None]
    gap = min((abs(light.rt - rt) for rt in seen), default=float("inf"))
    if any(abs(light.mz - a.mz) < NUCLEOSIDE_TOL_DA for a in anchors):
        return "nucleoside" if gap <= SAME_COMPOUND_RT_MIN else "nucleoside_mz"
    if gap <= NUCLEOSIDE_ELUTION_RT_MIN:
        return "nucleoside_elution"
    two = any(c.second_loss_ratio >= SECOND_LOSS_MIN_RATIO for c in members)
    two = two or any(n in ("+loss", "+2loss") for v in relations.values() for n in v)
    return "two_sugar" if two else "other"


def synced(first: XICTrace, second: XICTrace, *, same_apex: bool = False) -> bool:
    """Same peak shape (r >= 0.9) on shared scans; optionally the same apex."""
    a, b = np.asarray(first.intensity, float), np.asarray(second.intensity, float)
    if len(a) < SHAPE_MIN_POINTS or len(a) != len(b) or a.std() == 0 or b.std() == 0:
        return False
    if same_apex and (
        abs(first.rt[a.argmax()] - second.rt[b.argmax()]) > SAME_COMPOUND_RT_MIN
    ):
        return False
    return float(np.corrcoef(a, b)[0, 1]) >= SHAPE_MIN_R


def count_candidates(
    sample: str,
    candidates: Sequence[Candidate],
    loss_da: float,
    anchors: Sequence[Anchor],
    other_sugar_anchors: Sequence[Anchor],
    xic: Xic,
) -> RunCount:
    """Group candidates, join synchronized groups, and drop other-sugar ions.

    ``anchors`` are nucleosides whose sugar gives this loss; ions of a
    nucleoside with another sugar keep that sugar and cannot give it, so a
    compound is removed only when its shape follows such a peak.
    """
    groups = group_candidates(candidates, loss_da, anchors)
    kept = [g for g in groups if g.category != "nucleoside_elution"]
    pairs = [
        (a, b)
        for a in range(len(kept))
        for b in range(a + 1, len(kept))
        if abs(kept[a].rt - kept[b].rt) <= SAME_COMPOUND_RT_MIN
        and abs(kept[a].mz - kept[b].mz) > SHAPE_MIN_MZ_GAP
    ]
    traces = xic(
        [(kept[i].mz, (kept[a].rt + kept[b].rt) / 2) for a, b in pairs for i in (a, b)]
    )
    joined = [
        p for k, p in enumerate(pairs) if synced(traces[2 * k], traces[2 * k + 1])
    ]
    units = [
        Unit(tuple(kept[i] for i in found), loss_da)
        for found in components(len(kept), joined)
    ]
    checks = [
        (u, g.mz, g.rt, a)
        for u, unit in enumerate(units)
        if unit.kind != "nucleoside"
        for g in unit.groups
        for a in other_sugar_anchors
        if a.rt is not None and abs(g.rt - a.rt) <= NUCLEOSIDE_ELUTION_RT_MIN
    ]
    traces = xic([(m, rt) for _, mz, rt, a in checks for m in (mz, a.mz)])
    for k, (u, _, _, anchor) in enumerate(checks):
        if not units[u].other_sugar and synced(
            traces[2 * k], traces[2 * k + 1], same_apex=True
        ):
            units[u] = replace(units[u], other_sugar=anchor.label)
    return RunCount(
        sample,
        tuple(candidates),
        groups,
        tuple(sorted(units, key=lambda u: u.members[0])),
        tuple(anchors),
        loss_da,
    )


@dataclass(frozen=True)
class RunCount:
    sample: str
    candidates: tuple[Candidate, ...]
    groups: tuple[Group, ...]
    units: tuple[Unit, ...]
    anchors: tuple[Anchor, ...]
    loss_da: float

    @property
    def remaining(self) -> list[Unit]:
        return [u for u in self.units if u.fate in (COUNTED, UNCONFIRMED)]

    @property
    def counted(self) -> list[Unit]:
        return [u for u in self.units if u.fate == COUNTED]

    def summary(self) -> dict[str, object]:
        rows = self.candidate_rows()
        fates = Counter(str(row["fate"]) for row in rows)
        kinds = Counter(u.kind for u in self.counted)
        return {
            "sample": self.sample,
            "detected": len(rows),
            "derived_known_difference": fates[KNOWN_DIFFERENCE],
            "derived_nucleoside": fates[NUCLEOSIDE_ELUTION] + fates[OTHER_SUGAR],
            "derived_synchronized_shape": fates[SYNCHRONIZED_SHAPE],
            "no_nucleobase": fates[NO_NUCLEOBASE],
            "unconfirmed": fates[UNCONFIRMED],
            "counted": fates[COUNTED],
            "counted_kinds": {k: kinds.get(k, 0) for k in KINDS},
            "counted_late": sum(u.late for u in self.counted),
            "derived_relations": dict(
                Counter(
                    relation_family(str(row["relation"]))
                    for row in rows
                    if str(row["fate"]).startswith("derived")
                )
            ),
            "removed_other_sugar": sorted(
                u.unit_id for u in self.units if u.fate == OTHER_SUGAR
            ),
            "anchor_rt": {a.label: round(a.rt, 3) for a in self.anchors if a.rt},
        }

    def candidate_rows(self) -> list[dict[str, object]]:
        """Every candidate's fate, the ion it derives from, and their relation."""
        unit_of = {id(g): u for u in self.units for g in u.groups}
        seen = [(a.rt, a) for a in self.anchors if a.rt is not None]
        rows = []
        for group in self.groups:
            unit = unit_of.get(id(group))
            parent: tuple[str, float] | None = None
            if unit is None:
                fate = NUCLEOSIDE_ELUTION
                if seen:
                    _, anchor = min(seen, key=lambda s: abs(group.rt - s[0]))
                    parent = (anchor.label, anchor.mz)
            elif unit.fate == OTHER_SUGAR or unit.groups[0] is group:
                fate = unit.fate
            else:
                fate = SYNCHRONIZED_SHAPE
                parent = (unit.unit_id, unit.groups[0].mz)
            for c in group.members:
                head = c is group.members[0]
                if not head:
                    relation = ";".join(group.relations[c.peak_id])
                    derived_from = f"{group.mz:.4f}@{group.rt:.2f}"
                elif parent:
                    relation = relation_label(
                        group.mz - parent[1], parent[1], self.anchors, self.loss_da
                    )
                    derived_from = parent[0]
                else:
                    relation = derived_from = ""
                rows.append(
                    {
                        "sample": self.sample,
                        "peak_id": c.peak_id,
                        "mz": round(c.mz, 4),
                        "rt": round(c.rt, 3),
                        "fate": fate if head else KNOWN_DIFFERENCE,
                        "relation": relation,
                        "derived_from": derived_from,
                        "group_category": group.category,
                        "unit": unit.unit_id if unit else "",
                        "attached_ms2_events": c.event_count,
                        "second_loss_ratio": round(c.second_loss_ratio, 3),
                    }
                )
        return rows


def _match(first, second) -> bool:
    return any(
        within_ppm(mz_b, mz_a, MATCH_PPM) and abs(rt_a - rt_b) <= MATCH_RT_MIN
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
