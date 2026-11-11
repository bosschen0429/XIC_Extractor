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
    ReviewEvidence,
    read_alignment_curves,
    read_alignment_explanation,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.review import (
    attention_reason,
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
    """One evidence card: a recorded fact, and which of the item's samples and
    candidates it concerns, known when the card is built."""

    kind: str
    ref: CardRef
    facts: dict[str, Any]
    keys: frozenset[CandidateKey]
    samples: frozenset[str]


@dataclass(frozen=True, slots=True)
class ItemEvidence:
    """One scoped review item and the evidence it is presented with."""

    item: CanonicalItem
    cards: tuple[Card, ...]
    source: dict[str, Any]
    cells: tuple[dict[str, Any], ...]
    sample_status: dict[str, str]
    support_origins: dict[str, dict[str, Any]]
    review_summary: dict[str, Any]
    members: tuple[dict[str, Any], ...]
    measured_mz: dict[CandidateKey, float | None]


def _candidate_ref(key: CandidateKey) -> str:
    return json.dumps(key, separators=(",", ":"))


def build_item_evidence(
    evidence: ReviewEvidence,
    attempt_id: str,
    kind: str,
    identifier: str,
    gid: str | None,
    keys: Iterable[CandidateKey],
    scope: set[str],
    source: dict[str, Any],
) -> ItemEvidence:
    """Scope one item to its candidates and build its cards from their facts."""
    keys = set(keys)
    scoped = sorted(key for key in keys if key[1] in scope)
    group = evidence.groups.get(gid) if gid is not None else None
    group_record = group.record if group is not None else {}
    found: dict[tuple[str, CardRef], tuple[dict[str, Any], set[CandidateKey]]] = {}

    def add(card_kind: str, ref: CardRef, facts: dict[str, Any], key) -> None:
        found.setdefault((card_kind, ref), (facts, set()))[1].add(key)

    for key in scoped:
        candidate = evidence.candidates[key]
        add(
            "method_subjects",
            candidate.method_subject_id,
            candidate.method_subject,
            key,
        )
        for ref, assessment in candidate.assessments:
            add("method_assessments", ref, assessment, key)
        if candidate.exclusion is not None:
            add("exclusions", _candidate_ref(key), candidate.exclusion, key)
        if candidate.terminal is not None:
            add("terminal_subjects", _candidate_ref(key), candidate.terminal, key)
        for ref in candidate.groups:
            other = evidence.groups[ref]
            # A candidate's other recorded rows belong to their own items.
            if other.feature_id not in (None, gid):
                continue
            add("final_groups", ref, other.record, key)
            if other.row is not None:
                add("rows", ref, other.row, key)
        for ref in candidate.initial_groups:
            add("initial_groups", ref, evidence.initial_groups[ref], key)
        for ref in candidate.curves:
            curve = evidence.curves[ref]
            if curve["sample_id"] == key[1]:
                add("capture_evidence", ref, curve, key)
    cards = [
        Card(
            card_kind,
            ref,
            facts,
            frozenset(linked),
            frozenset(sid for _, sid in linked),
        )
        for (card_kind, ref), (facts, linked) in found.items()
    ]
    cell_samples: set[str] = set()
    if kind == "row" and group is not None:
        for sid, cell in sorted(group.cells.items()):
            if sid not in scope:
                continue
            cards.append(
                Card(
                    "cell_decisions",
                    (identifier, sid),
                    cell,
                    frozenset(),
                    frozenset((sid,)),
                )
            )
            cell_samples.add(sid)
    cards.sort(key=lambda card: (card.kind, card.ref))
    witnesses = _witnesses(evidence, kind, identifier, gid, group_record, scoped)

    triggers: list[dict[str, Any]] = []
    if group_attention(group_record.get("disposition")):
        triggers.append(
            {"scope": "group", "ref": gid, "reason": group_record["disposition"]}
        )
    cells = []
    for card in cards:
        reason = attention_reason(card.kind, card.facts)
        if card.kind == "cell_decisions":
            cells.append({**card.facts, "quantitative_value": card.facts["value"]})
        if reason:
            trigger: dict[str, Any] = {
                "scope": "sample",
                "ref": card.ref,
                "reason": reason,
                "sample_ids": sorted(card.samples),
            }
            reason_codes = card.facts.get("reason_codes")
            if isinstance(reason_codes, list):
                trigger["reason_codes"] = [
                    code for code in reason_codes if isinstance(code, str)
                ]
            triggers.append(trigger)
    excluded = {
        sid for card in cards if card.kind == "exclusions" for sid in card.samples
    }
    stratum = classify_scoped_stratum(
        is_attention=bool(triggers),
        is_excluded=bool(excluded),
        has_null_cell=any(c["quantitative_value"] is None for c in cells),
        has_zero_cell=any(c["quantitative_value"] == 0 for c in cells),
    )
    sample_ids = sorted({sid for _, sid in scoped} | cell_samples)
    sample_status: dict[str, str] = {}
    for sid in sample_ids:
        local_cells = [c for c in cells if c["sample_key"] == sid]
        local_attention = any(sid in t.get("sample_ids", ()) for t in triggers)
        sample_status[sid] = (
            classify_scoped_stratum(
                is_attention=local_attention,
                is_excluded=sid in excluded,
                has_null_cell=any(c["quantitative_value"] is None for c in local_cells),
                has_zero_cell=any(c["quantitative_value"] == 0 for c in local_cells),
            )
            if local_cells or local_attention or sid in excluded
            else "unavailable"
        )
    _, ref = build_canonical_ref(
        kind,  # type: ignore[arg-type]
        attempt_id,
        identifier,
        sample_id=next(iter(keys))[1] if kind == "candidate" else None,
    )
    members = tuple(group_record.get("member_subjects", ()))
    support_observations = {
        sid: _support_observation_refs(sid, members, witnesses, cards)
        for sid in sample_ids
    }
    origins = {
        sid: _support_origin(sid, support_observations[sid], cards)
        for sid in sample_ids
    }
    initial_member_ids = {
        member["subject_id"]
        for card in cards
        if card.kind == "initial_groups"
        for member in card.facts.get("member_subjects", [])
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
    return ItemEvidence(
        item=item,
        cards=tuple(cards),
        source=source,
        cells=tuple(cells),
        sample_status=sample_status,
        support_origins=origins,
        review_summary=review_summary,
        # A featureless group's or a candidate's source names no group.
        members=members if kind == "row" else (),
        measured_mz={
            key: evidence.candidates[key].fields["ms1_apex_mz"] for key in scoped
        },
    )


def _witnesses(
    evidence: ReviewEvidence,
    kind: str,
    identifier: str,
    gid: str | None,
    group_record: dict[str, Any],
    scoped: list[CandidateKey],
) -> list[Witness]:
    """Each scoped candidate's terminal observation that the group kept."""
    members_by_sample: dict[str, set[str]] = {}
    for member in group_record.get("member_subjects", []):
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


def _support_observation_refs(sample_id, members, witnesses, cards):
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
    refs.update(
        peak
        for card in cards
        if card.kind == "cell_decisions" and card.facts["sample_key"] == sample_id
        for peak in card.facts["observation_ids"]
    )
    return sorted(refs)


def _support_origin(sample_id, observation_refs, cards):
    """The cell's recorded origin; without a cell, derive it from the sample's
    retained captures and supporting method assessments of its observations."""
    recorded = {
        card.facts["support_origin"]
        for card in cards
        if card.kind == "cell_decisions" and card.facts["sample_key"] == sample_id
    }
    if recorded:
        kind = next(
            (
                value
                for value in ("backfill_support", "discovery_support")
                if value in recorded
            ),
            "unavailable",
        )
        return {
            "kind": kind,
            "label": presentation.support_origin_label(kind),
            "method_refs": [],
            "capture_refs": [],
            "observation_refs": observation_refs,
        }
    observation_set = set(observation_refs)
    captures = []
    methods = []
    for card in cards:
        facts = card.facts
        if facts.get("sample_key", facts.get("sample_id")) != sample_id:
            continue
        if (
            card.kind == "capture_evidence"
            and facts["physical_peak_key"] in observation_set
        ):
            captures.append((card.ref, facts))
        elif (
            card.kind == "method_assessments"
            and facts["method_result"] == "SUPPORT"
            and observation_set.intersection(facts["candidate_physical_peak_keys"])
        ):
            methods.append((card.ref, facts))
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
                "row" if group.row is not None else "group",
                ref,
                ref,
                group.candidates,
                sample_ids,
                group.row if group.row is not None else group.record,
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
                candidate.fields,
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
                    readable_records=self._readable_records(view, projected_curves),
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

    def _readable_records(
        self, view: ItemEvidence, curves: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        item = view.item
        available_refs = {card.ref for card in view.cards} | {
            curve["evidence_ref"]
            for curve in curves
            if curve.get("evidence_ref") is not None
        }
        readable = [
            presentation.readable_record(
                item,
                card,
                available_refs,
                scope=_card_scope(item, card),
                sample_labels=self.sample_labels,
            )
            for card in view.cards
        ]
        positions = {record["ref"]: index for index, record in enumerate(readable)}
        for curve in curves:
            curve_record = presentation.readable_curve(item, curve, available_refs)
            ref = curve_record["ref"]
            if ref not in positions:
                positions[ref] = len(readable)
                readable.append(curve_record)
                continue
            index = positions[ref]
            existing = readable[index]
            curve_record["sample_ids"] = sorted(
                set(existing["sample_ids"]) | set(curve_record["sample_ids"])
            )
            curve_record["candidate_ids"] = sorted(
                set(existing["candidate_ids"]) | set(curve_record["candidate_ids"])
            )
            curve_record["fields"] = presentation.dedupe_entries(
                existing["fields"] + curve_record["fields"]
            )
            curve_record["links"] = presentation.dedupe_entries(
                existing["links"] + curve_record["links"]
            )
            curve_record["technical_refs"] = presentation.dedupe_entries(
                existing["technical_refs"] + curve_record["technical_refs"]
            )
            readable[index] = curve_record
        related = _related_assessments(view, curves, [r["ref"] for r in readable])
        for record in readable:
            if record["ref"] in related:
                record["links"] = presentation.dedupe_entries(
                    record["links"]
                    + [
                        presentation.related_assessment_link(ref)
                        for ref in related[record["ref"]]
                    ]
                )
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


def _related_assessments(
    view: ItemEvidence, curves: list[dict[str, Any]], readable_refs: list[CardRef]
) -> dict[Any, list[CardRef]]:
    """Link each curve to the method assessments of the candidates it shows."""
    assessments = [card for card in view.cards if card.kind == "method_assessments"]
    curves_by_ref = {
        curve["evidence_ref"]: curve
        for curve in curves
        if curve.get("evidence_ref") is not None
    }
    related: dict[Any, list[CardRef]] = {}
    for ref in readable_refs:
        if ref not in curves_by_ref:
            continue
        curve = curves_by_ref[ref]
        keys = {
            (candidate_id, curve["sample_id"])
            for candidate_id in curve.get("candidate_ids", [])
            if curve.get("sample_id") is not None
        }
        related[ref] = [card.ref for card in assessments if keys & card.keys]
    return related
