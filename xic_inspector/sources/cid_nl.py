"""Load and scope retained CID-NL evidence for the shared Inspector.

The scientific reader owns payload validation and reads each concept once. This
adapter scopes an item to its candidates, builds the item's evidence cards from
the concepts those candidates relate to, and projects curves;
cid_nl_presentation assembles labels and readable cards.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    CandidateKey,
    GroupEvidence,
    ReviewEvidence,
    read_alignment_curves,
    read_alignment_explanation,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.review import (
    assessment_attention,
    cell_attention,
    group_attention,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.strata import classify_scoped_stratum
from xic_inspector.sources import cid_nl_presentation as presentation
from xic_inspector.sources.rt_replay import align_curve, read_calibration

CardRef = str | tuple[str, str]


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read compact metadata without retrieving curve payloads."""
    return read_alignment_explanation(path)


@dataclass(frozen=True, slots=True)
class Card:
    """One evidence card as presented: a recorded fact, and which of the item's
    samples and candidates it concerns."""

    kind: str
    ref: CardRef
    facts: dict[str, Any]
    keys: frozenset[CandidateKey]
    samples: frozenset[str]


@dataclass(frozen=True, slots=True)
class ItemEvidence:
    """One scoped review item: its state, computed from the evidence, and the
    cards it is presented with."""

    item: CanonicalItem
    cards: tuple[Card, ...]
    route: dict[str, Any]
    reason: Any
    state: Any
    losses: tuple[float, ...]
    cells: tuple[dict[str, Any], ...]
    sample_status: dict[str, str]
    support_origins: dict[str, dict[str, Any]]
    review_summary: dict[str, Any]
    members: tuple[dict[str, Any], ...]
    measured_mz: dict[CandidateKey, float | None]


def _candidate_ref(key: CandidateKey) -> str:
    return json.dumps(key, separators=(",", ":"))


Linked = dict[str, tuple[dict[str, Any], set[CandidateKey]]]


def build_item_evidence(
    evidence: ReviewEvidence,
    attempt_id: str,
    kind: str,
    identifier: str,
    gid: str | None,
    keys: Iterable[CandidateKey],
    scope: set[str],
) -> ItemEvidence:
    """Scope one item to its candidates, compute its state from their facts and
    the group's cells, then build its cards for presentation."""
    keys = set(keys)
    scoped = sorted(key for key in keys if key[1] in scope)
    group = evidence.groups.get(gid) if gid is not None else None
    subjects: Linked = {}
    assessments: Linked = {}
    initial: Linked = {}
    curves: Linked = {}
    groups: dict[str, set[CandidateKey]] = {}

    def link(found: Linked, ref: str, facts: dict[str, Any], key) -> None:
        found.setdefault(ref, (facts, set()))[1].add(key)

    for key in scoped:
        candidate = evidence.candidates[key]
        link(subjects, candidate.method_subject_id, candidate.method_subject, key)
        for ref, assessment in candidate.assessments:
            link(assessments, ref, assessment, key)
        for ref in candidate.groups:
            # A candidate's other recorded rows belong to their own items.
            if evidence.groups[ref].feature_id in (None, gid):
                groups.setdefault(ref, set()).add(key)
        for ref in candidate.initial_groups:
            link(initial, ref, evidence.initial_groups[ref], key)
        for ref in candidate.curves:
            if evidence.curves[ref]["sample_id"] == key[1]:
                link(curves, ref, evidence.curves[ref], key)
    excluded_keys = [key for key in scoped if evidence.candidates[key].exclusion]
    cells = [
        {**cell, "quantitative_value": cell["value"]}
        for sid, cell in sorted(group.cells.items() if kind == "row" and group else ())
        if sid in scope
    ]
    cells_by_sample = {cell["sample_key"]: cell for cell in cells}
    witnesses = _witnesses(evidence, kind, identifier, gid, group, scoped)

    triggers: list[dict[str, Any]] = []
    if group is not None and group_attention(group.disposition):
        triggers.append({"scope": "group", "ref": gid, "reason": group.disposition})
    for sid, cell in cells_by_sample.items():
        reason = cell_attention(
            cell["variant"],
            cell["value"],
            any(risk.get("risk_flags") for risk in cell["membership_risks"]),
        )
        if reason:
            triggers.append(
                _trigger((identifier, sid), reason, {sid}, cell["reason_codes"])
            )
    for ref, (assessment, linked) in sorted(assessments.items()):
        reason = assessment_attention(assessment["method_result"])
        if reason:
            triggers.append(
                _trigger(
                    ref,
                    reason,
                    {sid for _, sid in linked},
                    assessment["reason_codes"],
                )
            )
    excluded = {sid for _, sid in excluded_keys}
    stratum = classify_scoped_stratum(
        is_attention=bool(triggers),
        is_excluded=bool(excluded),
        has_null_cell=any(c["quantitative_value"] is None for c in cells),
        has_zero_cell=any(c["quantitative_value"] == 0 for c in cells),
    )
    sample_ids = sorted({sid for _, sid in scoped} | set(cells_by_sample))
    sample_status: dict[str, str] = {}
    for sid in sample_ids:
        local = cells_by_sample.get(sid)
        local_attention = any(sid in t.get("sample_ids", ()) for t in triggers)
        sample_status[sid] = (
            classify_scoped_stratum(
                is_attention=local_attention,
                is_excluded=sid in excluded,
                has_null_cell=local is not None and local["value"] is None,
                has_zero_cell=local is not None and local["value"] == 0,
            )
            if local is not None or local_attention or sid in excluded
            else "unavailable"
        )
    _, ref = build_canonical_ref(
        kind,  # type: ignore[arg-type]
        attempt_id,
        identifier,
        sample_id=next(iter(keys))[1] if kind == "candidate" else None,
    )
    members = tuple(group.members) if group is not None else ()
    support_observations = {
        sid: _support_observation_refs(
            sid, members, witnesses, cells_by_sample.get(sid)
        )
        for sid in sample_ids
    }
    origins = {
        sid: _support_origin(
            sid,
            support_observations[sid],
            cells_by_sample.get(sid),
            curves,
            assessments,
        )
        for sid in sample_ids
    }
    initial_member_ids = {
        member["subject_id"]
        for record, _ in initial.values()
        for member in record.get("member_subjects", [])
    }
    review_summary = {
        "observed_sample_count": len(
            {sid for sid, refs in support_observations.items() if refs}
        ),
        "scope_sample_count": len(sample_ids),
        "origin_kinds": sorted({origin["kind"] for origin in origins.values()}),
        "initial_member_count": (
            len(initial_member_ids) if initial_member_ids else len(keys)
        ),
        "final_member_count": len(members) if group is not None else len(keys),
    }
    item = CanonicalItem(
        kind=kind,  # type: ignore[arg-type]
        attempt_id=attempt_id,
        identifier=identifier,
        serialized_ref=ref,
        sample_ids=tuple(sample_ids),
        witnesses=tuple(witnesses),
        stratum=stratum,
        is_attention=bool(triggers),
        # Selection reads the review facts; rendering presents the triggers.
        details={"attention_triggers": triggers, "review_summary": review_summary},
    )
    row = group if kind == "row" else None
    route = {"destination": row.destination, "reason": row.disposition} if row else {}
    cards = [
        *(
            Card(card_kind, card_ref, facts, frozenset(linked), _samples(linked))
            for card_kind, found in (("capture_evidence", curves),)
            for card_ref, (facts, linked) in found.items()
        ),
        *(
            Card(
                card_kind,
                _candidate_ref(key),
                facts,
                frozenset((key,)),
                frozenset((key[1],)),
            )
            for key in scoped
            for card_kind, facts in (
                ("exclusions", evidence.candidates[key].exclusion),
            )
            if facts is not None
        ),
        *(
            Card(
                "cell_decisions",
                (identifier, cell["sample_key"]),
                {
                    key: value
                    for key, value in cell.items()
                    if key != "quantitative_value"
                },
                frozenset(),
                frozenset((cell["sample_key"],)),
            )
            for cell in cells
        ),
    ]
    cards.sort(key=lambda card: (card.kind, card.ref))
    return ItemEvidence(
        item=item,
        cards=tuple(cards),
        route=route,
        reason=group.disposition if group is not None else "unavailable",
        # The labels name the first related group's recorded disposition.
        state=evidence.groups[min(groups)].disposition
        if groups
        else route.get("reason"),
        losses=tuple(
            sorted(
                {
                    facts["neutral_loss_da"]
                    for facts, _ in subjects.values()
                    if isinstance(facts["neutral_loss_da"], (int, float))
                }
            )
        ),
        cells=tuple(cells),
        sample_status=sample_status,
        support_origins=origins,
        review_summary=review_summary,
        # A featureless group or a candidate names no published row.
        members=members if kind == "row" else (),
        measured_mz={
            key: evidence.candidates[key].fields["ms1_apex_mz"] for key in scoped
        },
    )


def _samples(keys: Iterable[CandidateKey]) -> frozenset[str]:
    return frozenset(sid for _, sid in keys)


def _trigger(ref, reason, samples, reason_codes) -> dict[str, Any]:
    return {
        "scope": "sample",
        "ref": ref,
        "reason": reason,
        "sample_ids": sorted(samples),
        "reason_codes": [code for code in reason_codes if isinstance(code, str)],
    }


def _witnesses(
    evidence: ReviewEvidence,
    kind: str,
    identifier: str,
    gid: str | None,
    group: GroupEvidence | None,
    scoped: list[CandidateKey],
) -> list[Witness]:
    """Each scoped candidate's terminal observation that the group kept."""
    members_by_sample: dict[str, set[str]] = {}
    for member in group.members if group is not None else ():
        members_by_sample.setdefault(member["sample_key"], set()).update(
            member["physical_peak_keys"]
        )
    witnesses: list[Witness] = []
    for cid, sid in scoped:
        candidate = evidence.candidates[cid, sid]
        member_peaks = members_by_sample.get(sid)
        observations = [
            observation
            for observation in (candidate.terminal or {}).get("observations", [])
            if not member_peaks or observation["physical_peak_key"] in member_peaks
        ] or [{}]
        witnesses.extend(
            Witness(
                sample_key=sid,
                candidate_id=cid,
                group_hypothesis_id=gid,
                row_key_id=identifier if kind == "row" else None,
                precursor_mz=candidate.fields["precursor_mz"],
                rt_apex_min=observation.get("rt_apex_min"),
                observation_id=observation.get("physical_peak_key"),
                sample_stem=candidate.fields["sample_stem"],
            )
            for observation in observations
        )
    return witnesses


def _support_observation_refs(sample_id, members, witnesses, cell):
    refs = {
        witness.observation_id
        for witness in witnesses
        if witness.sample_key == sample_id and witness.observation_id
    }
    refs.update(
        peak
        for member in members
        if member["sample_key"] == sample_id
        for peak in member["physical_peak_keys"]
    )
    refs.update(cell["observation_ids"] if cell is not None else ())
    return sorted(refs)


def _support_origin(sample_id, observation_refs, cell, curves, assessments):
    """The cell's recorded origin; without a cell, derive it from the sample's
    retained captures and supporting method assessments of its observations."""
    if cell is not None:
        kind = cell["support_origin"]
        return {
            "kind": kind
            if kind in ("backfill_support", "discovery_support")
            else "unavailable",
            "label": presentation.support_origin_label(
                kind
                if kind in ("backfill_support", "discovery_support")
                else "unavailable"
            ),
            "method_refs": [],
            "capture_refs": [],
            "observation_refs": observation_refs,
        }
    observation_set = set(observation_refs)
    captures = [
        (ref, facts)
        for ref, (facts, _) in curves.items()
        if facts["sample_id"] == sample_id
        and facts["physical_peak_key"] in observation_set
    ]
    methods = [
        (ref, facts)
        for ref, (facts, _) in assessments.items()
        if facts["sample_key"] == sample_id
        and facts["method_result"] == "SUPPORT"
        and observation_set.intersection(facts["candidate_physical_peak_keys"])
    ]
    capture_kinds = {facts["kind"] for _, facts in captures}
    invocations = {facts["detector_invocation_id"] for _, facts in methods}
    if (
        "backfill_observation" in capture_kinds
        and "untargeted-cid-nl-backfill" in invocations
    ):
        kind = "backfill_support"
    elif "initial_capture" in capture_kinds and any(
        isinstance(value, str) and value.startswith("untargeted-cid-nl-real-data:")
        for value in invocations
    ):
        kind = "discovery_support"
    else:
        kind = "unavailable"
    return {
        "kind": kind,
        "label": presentation.support_origin_label(kind),
        "method_refs": sorted(ref for ref, _ in methods),
        "capture_refs": sorted(ref for ref, _ in captures),
        "observation_refs": observation_refs,
    }


class CidNlSource:
    """A full-audit report: every item is built in memory for selection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.evidence = read_review_evidence(path)
        self._bind(self.evidence.summary)
        self._items: dict[str, ItemEvidence] = {}

    def _bind(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        self.attempt_id = summary.get("processing_attempt_id", "")
        self.sample_labels = {
            sample["sample_key"]: sample["sample_stem"]
            for sample in summary.get("samples", [])
        }
        self.rt_calibration = read_calibration(summary["sample_rt_calibration"])

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        evidence = self.evidence
        grouped = {
            key for group in evidence.groups.values() for key in group.candidates
        }
        built = [
            build_item_evidence(
                evidence,
                self.attempt_id,
                "row" if group.feature_id is not None else "group",
                ref,
                ref,
                group.candidates,
                sample_ids,
            )
            for ref, group in evidence.groups.items()
        ]
        built.extend(
            build_item_evidence(
                evidence,
                self.attempt_id,
                "candidate",
                key[0],
                None,
                (key,),
                sample_ids,
            )
            for key, candidate in evidence.candidates.items()
            if key not in grouped
        )
        self._items.update((view.item.serialized_ref, view) for view in built)
        return sorted((view.item for view in built), key=lambda item: item.sort_key)

    def load_render_items(
        self, items, *, capability: str, check_cancel: Callable[[], None]
    ):
        return self._render(
            [
                replace(
                    self._items[item.serialized_ref],
                    item=replace(
                        self._items[item.serialized_ref].item,
                        matched_witnesses=item.matched_witnesses,
                    ),
                )
                for item in items
            ],
            capability=capability,
            check_cancel=check_cancel,
        )

    def _render(self, views: list[ItemEvidence], *, capability, check_cancel):
        # Read each sample's selected exact references once. This avoids the
        # candidate-ID/sample cross product when IDs are shared across samples.
        refs_by_sample: dict[str, set[str]] = {}
        captures_by_item = {
            view.item.serialized_ref: [
                card.facts for card in view.cards if card.kind == "capture_evidence"
            ]
            for view in views
        }
        for view in views:
            item = view.item
            for capture in captures_by_item[item.serialized_ref]:
                refs_by_sample.setdefault(capture["sample_id"], set()).add(
                    capture["record_id"]
                )
            for witness in item.witnesses:
                refs = refs_by_sample.setdefault(witness.sample_key, set())
                if witness.observation_id:
                    refs.add(witness.observation_id)
                elif item.kind == "candidate":
                    refs.add(witness.candidate_id)
        raw_curves = []
        if capability == "retained_curves":
            for sid, refs in sorted(refs_by_sample.items()):
                if not refs:
                    continue
                check_cancel()
                raw_curves.extend(
                    read_alignment_curves(
                        self.path, sample_ids=(sid,), record_ids=tuple(sorted(refs))
                    )
                )
            present = {
                (curve.get("sample_id"), curve.get("physical_peak_key"))
                for curve in raw_curves
            }
            promised = {
                (w.sample_key, w.observation_id)
                for view in views
                for w in view.item.witnesses
                if w.observation_id
            }
            missing = promised - present
            if missing and self.summary.get("curve_retention") != "selected":
                raise ExplanationError(
                    f"Missing promised selected observation payloads: {sorted(missing)}"
                )
            promised_captures = {
                (capture["sample_id"], capture["payload_id"])
                for captures in captures_by_item.values()
                for capture in captures
            }
            present_captures = {
                (curve.get("sample_id"), curve.get("payload_id"))
                for curve in raw_curves
            }
            missing_captures = promised_captures - present_captures
            if missing_captures:
                raise ExplanationError(
                    "Missing promised selected capture payloads: "
                    f"{sorted(missing_captures)}"
                )
            for curve in raw_curves:
                provenance = curve.get("provenance", {})
                for name, expected in (("processing_attempt_id", self.attempt_id),):
                    if name in provenance and provenance[name] != expected:
                        raise ExplanationError(f"Selected curve crosses {name} binding")
        rendered = []
        for view in views:
            check_cancel()
            item = view.item
            projected_curves = self._project_curves(
                item, raw_curves, captures_by_item[item.serialized_ref]
            )
            samples = [
                presentation.build_sample(
                    view,
                    sid,
                    fallback_sample_label=self.sample_labels.get(sid, sid),
                    decision_refs=_decision_refs(view, sid),
                    projected_curves=projected_curves,
                )
                for sid in item.sample_ids
            ]
            rendered.append(
                presentation.build_render_item(
                    view,
                    samples=samples,
                    projected_curves=projected_curves,
                    readable_records=self._readable_records(view),
                    measured_mz_by_candidate=view.measured_mz,
                    relationship_members=[
                        {
                            "subject_id": member["subject_id"],
                            "sample_id": member["sample_key"],
                            "sample_label": self.sample_labels.get(
                                member["sample_key"], member["sample_key"]
                            ),
                        }
                        for member in view.members
                    ],
                )
            )
        return rendered

    def _project_curves(self, item, raw_curves, captures):
        keys = {(w.candidate_id, w.sample_key) for w in item.witnesses}
        observation_keys = {
            (w.observation_id, w.sample_key) for w in item.witnesses if w.observation_id
        }
        capture_keys = {
            (capture["sample_id"], capture["payload_id"]) for capture in captures
        }
        curves = [
            curve
            for curve in raw_curves
            if (curve.get("sample_id"), curve.get("payload_id")) in capture_keys
            or (
                (curve.get("physical_peak_key"), curve.get("sample_id"))
                in observation_keys
                if observation_keys
                else (curve.get("candidate_id"), curve.get("sample_id")) in keys
            )
        ]
        projected_curves = []
        for curve in curves:
            projected = self._curve(curve)
            projected["candidate_ids"] = sorted(
                {
                    witness.candidate_id
                    for witness in item.witnesses
                    if witness.sample_key == curve.get("sample_id")
                    and witness.observation_id
                    and witness.observation_id == curve.get("physical_peak_key")
                }
            )
            projected_curves.append(projected)
        return projected_curves

    def _readable_records(self, view: ItemEvidence) -> list[dict[str, Any]]:
        item = view.item
        readable = [
            presentation.readable_record(
                card,
                scope=_card_scope(item, card),
                sample_labels=self.sample_labels,
            )
            for card in view.cards
            if card.kind in {"cell_decisions", "exclusions"}
        ]
        return readable

    def _curve(self, payload):
        curves = payload.get("curves", {})
        integration = payload.get("integration") or {}
        rts = curves.get("rt", [])
        result = {
            "sample_id": payload.get("sample_id"),
            "candidate_id": payload.get("candidate_id"),
            "evidence_ref": payload.get("payload_id", payload.get("physical_peak_key")),
            "observation_id": payload.get("physical_peak_key"),
            "kind": payload.get("kind"),
            "stage_label": presentation.curve_stage_label(payload.get("kind")),
            "raw_rts": rts,
            "raw_intensities": curves.get("raw", []),
            "processed_curves": [
                {"kind": kind, "rts": rts, "intensities": values}
                for kind, values in curves.items()
                if kind in ("baseline", "smoothed", "positive") and values is not None
            ],
            "bound_left_rt": integration.get("rt_left_min"),
            "bound_right_rt": integration.get("rt_right_min"),
            "apex_rt": integration.get("rt_apex_min", integration.get("apex_rt_min")),
            "area": integration.get("area_raw_counts_seconds"),
            "integration": integration,
            "provenance": payload.get("provenance"),
        }
        align_curve(
            result,
            self.sample_labels.get(payload.get("sample_id")),
            self.rt_calibration,
        )
        return result


def _decision_refs(view: ItemEvidence, sample_id: str) -> list[CardRef]:
    item = view.item
    refs = []
    for card in view.cards:
        if card.kind == "exclusions":
            if item.kind == "candidate" and item.stratum == "excluded":
                if sample_id in card.samples:
                    refs.append(card.ref)
        elif card.kind == "cell_decisions" and sample_id in card.samples:
            refs.append(card.ref)
    return sorted(set(refs))


def _card_scope(item: CanonicalItem, card: Card) -> tuple[list[str], list[str]]:
    """The item samples and candidates a card shows it concerns."""
    recorded_sample = card.facts.get("sample_key", card.facts.get("sample_id"))
    if recorded_sample is not None:
        sample_ids = [recorded_sample] if recorded_sample in item.sample_ids else []
    else:
        sample_ids = sorted(card.samples)
    candidate_ids = {cid for cid, _ in card.keys}
    recorded_candidate = card.facts.get("candidate_id")
    if recorded_candidate is not None and any(
        witness.candidate_id == recorded_candidate
        and (recorded_sample is None or witness.sample_key == recorded_sample)
        for witness in item.witnesses
    ):
        candidate_ids.add(recorded_candidate)
    return sample_ids, sorted(candidate_ids)
