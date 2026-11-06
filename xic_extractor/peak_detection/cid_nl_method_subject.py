"""Workflow-neutral identity for one CID-NL precursor method subject.

Targeted and Untargeted callers may constrain the same CID-NL assessment owner,
but a configured Target is not a universal scientific identity.  This module
keeps the common precursor/method facts explicit and retains Targeted identity
only when the subject was adapted from an owner-resolved target definition.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from itertools import zip_longest
from types import MappingProxyType
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import (
    ProcessingRequestIdentity,
)

SCHEMA_VERSION = "cid_nl_method_subject"
BATCH_SCHEMA_VERSION = "cid_nl_method_subject_batch"
REQUEST_INPUT_KEY = "cid_nl_method_subject"
CID_NL_PROVIDER_ID = "dda_ddms2_cid_nl"
CID_NL_METHOD_ID = "cid_nl"
CID_NL_METHOD_VERSION = "1"

CidNlMethodSubjectKind: TypeAlias = Literal[
    "targeted_definition",
    "untargeted_precursor",
]


class CidNlMethodSubjectContractError(ValueError):
    """Raised when a CID-NL method subject is not exact or request-bound."""


@dataclass(frozen=True, slots=True)
class CidNlComposition:
    """Ordered configured requirements; tags are evidence dimensions."""

    tags: tuple[tuple[str, float], ...]
    combine_mode: Literal["union", "intersection"]

    def __post_init__(self) -> None:
        if self.combine_mode not in {"union", "intersection"}:
            raise CidNlMethodSubjectContractError(
                "composition requires union or intersection"
            )
        if type(self.tags) is not tuple or len(self.tags) < 2:
            raise CidNlMethodSubjectContractError(
                "composition requires multiple ordered tags"
            )
        normalized = tuple(
            (
                _normalized_text(name, field="tag"),
                _positive_finite_float(loss, field="neutral_loss_da"),
            )
            for name, loss in self.tags
        )
        if len({name for name, _ in normalized}) != len(normalized) or len(
            {loss for _, loss in normalized}
        ) != len(normalized):
            raise CidNlMethodSubjectContractError(
                "composition tags and losses must be distinct"
            )
        object.__setattr__(self, "tags", normalized)


@dataclass(frozen=True, slots=True)
class CidNlMethodSubject:
    """Smallest shared subject for Targeted and Untargeted CID-NL assessment."""

    subject_kind: CidNlMethodSubjectKind
    precursor_mz: float
    precursor_tolerance_ppm: float
    neutral_loss_da: float | None
    target: ResolvedTargetDefinition | None = None
    composition: CidNlComposition | None = None

    def __post_init__(self) -> None:
        precursor_mz = _positive_finite_float(self.precursor_mz, field="precursor_mz")
        precursor_tolerance_ppm = _positive_finite_float(
            self.precursor_tolerance_ppm,
            field="precursor_tolerance_ppm",
        )
        neutral_loss_da = (
            None
            if self.composition is not None
            else _positive_finite_float(
                self.neutral_loss_da,
                field="neutral_loss_da",
            )
        )
        if self.composition is not None and (
            type(self.composition) is not CidNlComposition
            or self.neutral_loss_da is not None
            or self.subject_kind != "untargeted_precursor"
        ):
            raise CidNlMethodSubjectContractError(
                "composite subject requires scalar-absent Untargeted identity"
            )
        if self.subject_kind not in {"targeted_definition", "untargeted_precursor"}:
            raise CidNlMethodSubjectContractError(
                "subject_kind must be targeted_definition or untargeted_precursor"
            )
        if self.subject_kind == "targeted_definition":
            if not isinstance(self.target, ResolvedTargetDefinition):
                raise CidNlMethodSubjectContractError(
                    "targeted method subject requires its resolved Target"
                )
        elif self.target is not None:
            raise CidNlMethodSubjectContractError(
                "Untargeted method subject cannot carry a configured Target"
            )

        object.__setattr__(self, "precursor_mz", precursor_mz)
        object.__setattr__(
            self,
            "precursor_tolerance_ppm",
            precursor_tolerance_ppm,
        )
        object.__setattr__(self, "neutral_loss_da", neutral_loss_da)

    @property
    def method_subject_id(self) -> str:
        """Natural key: the Target label, or the precursor and its losses."""

        if self.target is not None:
            return f"target|{self.target.lookup_label}"
        if self.composition is not None:
            losses = f"{self.composition.combine_mode}:" + ",".join(
                f"{name}={loss!r}" for name, loss in self.composition.tags
            )
        else:
            losses = repr(self.neutral_loss_da)
        return (
            f"precursor|{self.precursor_mz!r}|{self.precursor_tolerance_ppm!r}|{losses}"
        )

    def request_payload(self) -> dict[str, object]:
        """Return the exact value an Untargeted processing request must pin."""

        payload = asdict(self)
        return {
            "schema_version": SCHEMA_VERSION,
            **payload,
        }

    @property
    def configured_losses(self) -> tuple[float, ...]:
        if self.composition is not None:
            return tuple(loss for _, loss in self.composition.tags)
        assert self.neutral_loss_da is not None
        return (self.neutral_loss_da,)

    @property
    def label(self) -> str:
        if self.composition is not None:
            return self.method_subject_id
        return f"NL{self.neutral_loss_da:.8g}"


@dataclass(frozen=True, slots=True)
class UntargetedCidNlMethodSubjectBatch:
    """Immutable owner closure for one or more Untargeted CID-NL subjects.

    The subject and candidate tuples are deliberately kept as ordered tuples.
    Their order is part of the request payload contract, while each candidate
    is bound to one exact subject rather than allocated by an m/z tolerance.
    """

    subjects: tuple[CidNlMethodSubject, ...]
    candidate_memberships: tuple[tuple[str, str], ...]

    @property
    def schema_version(self) -> str:
        return BATCH_SCHEMA_VERSION

    def request_payload(self) -> dict[str, object]:
        """Return the canonical batch value pinned under ``REQUEST_INPUT_KEY``."""

        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "subjects": [subject.request_payload() for subject in self.subjects],
            "candidate_memberships": [
                {
                    "candidate_id": candidate_id,
                    "method_subject_id": method_subject_id,
                }
                for candidate_id, method_subject_id in self.candidate_memberships
            ],
        }


def cid_nl_method_subject_from_target(
    target: ResolvedTargetDefinition,
) -> CidNlMethodSubject:
    """Adapt one owner-resolved Target into the shared CID-NL subject."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise CidNlMethodSubjectContractError(
            "target must be an owner-resolved target definition"
        )
    target_value = target.definition.target
    neutral_loss_da = target_value.neutral_loss_da
    if neutral_loss_da is None:
        raise CidNlMethodSubjectContractError(
            "CID-NL Targeted subject requires configured neutral loss"
        )
    return _mint_subject(
        subject_kind="targeted_definition",
        precursor_mz=target_value.mz,
        precursor_tolerance_ppm=target_value.ppm_tol,
        neutral_loss_da=neutral_loss_da,
        target=target,
    )


def mint_untargeted_cid_nl_method_subject(
    *,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float | None = None,
    composition: CidNlComposition | None = None,
) -> CidNlMethodSubject:
    """Mint an Untargeted subject without fabricating configured Target IDs."""

    return _mint_subject(
        subject_kind="untargeted_precursor",
        precursor_mz=precursor_mz,
        precursor_tolerance_ppm=precursor_tolerance_ppm,
        neutral_loss_da=neutral_loss_da,
        composition=composition,
    )


def mint_untargeted_cid_nl_method_subject_batch(
    *,
    subjects: tuple[CidNlMethodSubject, ...],
    candidate_memberships: tuple[tuple[str, str], ...],
) -> UntargetedCidNlMethodSubjectBatch:
    """Mint an immutable, canonically ordered Untargeted subject batch."""

    return UntargetedCidNlMethodSubjectBatch(
        subjects=subjects,
        candidate_memberships=candidate_memberships,
    )


@dataclass(frozen=True, slots=True)
class CidNlPhysicalProof:
    """Already acquired peak facts; rt/raw are repeatable in-memory iterables."""

    acquisition_source_id: str
    apex_rt_min: float
    left_rt_min: float
    right_rt_min: float
    xic_mz: float
    xic_ppm_tol: float
    rt: Iterable[float]
    raw: Iterable[float]
    apex_centroids: tuple[Ms1PrecursorObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class CidNlPhysicalCandidateFact:
    candidate_id: str
    physical_mz: float
    configured_neutral_loss_da: float
    proof: CidNlPhysicalProof | None


def cid_nl_method_center_interval(mz: float, ppm: float) -> tuple[float, float]:
    fraction = ppm / 1_000_000.0
    return mz / (1.0 + fraction), mz / (1.0 - fraction)


def _same_physical_core(
    left: CidNlPhysicalProof,
    right: CidNlPhysicalProof,
    *,
    rt_min: float,
    rt_max: float,
) -> bool:
    if left.apex_centroids or right.apex_centroids:
        # A shared, singly occupied ion at the apex and its adjacent MS1 scans
        # identifies the measured peak even when query-window tails differ.
        # Distinct or unresolved centroids cannot be overruled by equal XICs.
        scans = left.apex_centroids
        return (
            len(scans) == 3
            and scans == right.apex_centroids
            and rt_min <= scans[0].rt_min < left.apex_rt_min
            and scans[1].rt_min == left.apex_rt_min == right.apex_rt_min
            and left.apex_rt_min < scans[2].rt_min <= rt_max
            and len({scan.scan_number for scan in scans}) == 3
            and all(len(scan.centroid_mzs) == 1 for scan in scans)
        )
    sentinel = object()
    matched = False
    left_core = (
        (float(rt), float(raw))
        for rt, raw in zip(left.rt, left.raw, strict=True)
        if rt_min <= float(rt) <= rt_max
    )
    right_core = (
        (float(rt), float(raw))
        for rt, raw in zip(right.rt, right.raw, strict=True)
        if rt_min <= float(rt) <= rt_max
    )
    for left_point, right_point in zip_longest(
        left_core,
        right_core,
        fillvalue=sentinel,
    ):
        if (
            left_point is sentinel
            or right_point is sentinel
            or left_point != right_point
        ):
            return False
        matched = True
    return matched


def group_cid_nl_physical_subject_units(
    candidates: tuple[CidNlPhysicalCandidateFact, ...],
    *,
    composition: CidNlComposition | None,
    precursor_tolerance_ppm: float,
) -> tuple[tuple[float | None, tuple[str, ...]], ...]:
    """Keep query wrappers for one proven peak in the same method workset."""

    views: dict[
        str, tuple[CidNlPhysicalProof, tuple[float, float], tuple[float, float]]
    ] = {}
    buckets: dict[
        tuple[str, float | None, float], list[CidNlPhysicalCandidateFact]
    ] = {}
    unproved: list[tuple[float | None, tuple[CidNlPhysicalCandidateFact, ...]]] = []
    for candidate in candidates:
        requirement = (
            None if composition is not None else candidate.configured_neutral_loss_da
        )
        proof = candidate.proof
        if proof is None:
            unproved.append((requirement, (candidate,)))
            continue
        xic_fraction = proof.xic_ppm_tol / 1_000_000.0
        views[candidate.candidate_id] = (
            proof,
            (
                proof.xic_mz * (1.0 - xic_fraction),
                proof.xic_mz * (1.0 + xic_fraction),
            ),
            cid_nl_method_center_interval(
                candidate.physical_mz,
                precursor_tolerance_ppm,
            ),
        )
        buckets.setdefault(
            (
                proof.acquisition_source_id,
                requirement,
                proof.apex_rt_min,
            ),
            [],
        ).append(candidate)

    proved: list[tuple[float | None, tuple[CidNlPhysicalCandidateFact, ...]]] = []
    for bucket_key, bucket_candidates in sorted(buckets.items()):
        requirement = bucket_key[1]
        ordered = sorted(
            bucket_candidates,
            key=lambda item: (item.physical_mz, item.candidate_id),
        )
        cohort: list[CidNlPhysicalCandidateFact] = []
        representative_proof: CidNlPhysicalProof | None = None
        common_rt = (0.0, 0.0)
        common_xic = (0.0, 0.0)
        common_center = (0.0, 0.0)
        for candidate in ordered:
            proof, xic_interval, center_interval = views[candidate.candidate_id]
            if not cohort:
                cohort = [candidate]
                representative_proof = proof
                common_rt = (proof.left_rt_min, proof.right_rt_min)
                common_xic = xic_interval
                common_center = center_interval
                continue
            next_rt = (
                max(common_rt[0], proof.left_rt_min),
                min(common_rt[1], proof.right_rt_min),
            )
            next_xic = (
                max(common_xic[0], xic_interval[0]),
                min(common_xic[1], xic_interval[1]),
            )
            next_center = (
                max(common_center[0], center_interval[0]),
                min(common_center[1], center_interval[1]),
            )
            apex = bucket_key[2]
            joins = (
                next_rt[0] < apex < next_rt[1]
                and next_xic[0] <= next_xic[1]
                and next_center[0] <= next_center[1]
                and representative_proof is not None
                and _same_physical_core(
                    representative_proof,
                    proof,
                    rt_min=next_rt[0],
                    rt_max=next_rt[1],
                )
            )
            if not joins:
                proved.append((requirement, tuple(cohort)))
                cohort = [candidate]
                representative_proof = proof
                common_rt = (proof.left_rt_min, proof.right_rt_min)
                common_xic = xic_interval
                common_center = center_interval
                continue
            cohort.append(candidate)
            common_rt = next_rt
            common_xic = next_xic
            common_center = next_center
        if cohort:
            proved.append((requirement, tuple(cohort)))

    return tuple(
        (
            requirement,
            tuple(candidate.candidate_id for candidate in unit),
        )
        for requirement, unit in sorted(
            (*proved, *unproved),
            key=lambda unit: (
                0.0 if unit[0] is None else unit[0],
                unit[1][0].physical_mz,
                tuple(candidate.candidate_id for candidate in unit[1]),
            ),
        )
    )


def mint_cid_nl_subject_membership_from_physical_units(
    *,
    subject_units: tuple[tuple[float | None, tuple[str, ...]], ...],
    precursor_by_id: Mapping[str, float],
    precursor_tolerance_ppm: float,
    composition: CidNlComposition | None,
) -> UntargetedCidNlMethodSubjectBatch:
    """Assign fixed query centers from compact, physically closed units."""
    subject_by_key: dict[tuple[float, float | None], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float | None]]] = []
    key_by_requirement: dict[float | None, tuple[float, float | None]] = {}
    for requirement, unit_candidates in subject_units:
        key = key_by_requirement.get(requirement)
        # A proven physical unit is indivisible. Existing fixed centers may host
        # it only when every physical query remains inside the method window.
        if key is None or any(
            abs(precursor_by_id[candidate] - key[0]) / key[0] * 1_000_000.0
            > precursor_tolerance_ppm
            for candidate in unit_candidates
        ):
            if len(unit_candidates) == 1:
                center = precursor_by_id[unit_candidates[0]]
            else:
                center_intervals = tuple(
                    cid_nl_method_center_interval(
                        precursor_by_id[candidate],
                        precursor_tolerance_ppm,
                    )
                    for candidate in unit_candidates
                )
                lower = max(interval[0] for interval in center_intervals)
                upper = min(interval[1] for interval in center_intervals)
                if lower > upper:
                    raise RuntimeError(
                        "physical subject unit lacks a common method center"
                    )
                center = (lower + upper) / 2.0
            key = (center, requirement)
            key_by_requirement[requirement] = key
            subject_by_key[key] = mint_untargeted_cid_nl_method_subject(
                precursor_mz=center,
                precursor_tolerance_ppm=precursor_tolerance_ppm,
                neutral_loss_da=requirement,
                composition=composition,
            )
        membership_keys.extend((candidate, key) for candidate in unit_candidates)

    subjects = tuple(
        sorted(subject_by_key.values(), key=lambda subject: subject.method_subject_id)
    )
    return mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            sorted(
                (
                    candidate_id,
                    subject_by_key[key].method_subject_id,
                )
                for candidate_id, key in membership_keys
            )
        ),
    )


def cid_nl_method_subject_resolver_interval(
    subject: CidNlMethodSubject,
) -> tuple[float, float]:
    """Return the exact precursor interval searched for one method subject."""

    if type(subject) is not CidNlMethodSubject:
        raise CidNlMethodSubjectContractError(
            "resolver interval requires an exact CID-NL method subject"
        )
    fraction = subject.precursor_tolerance_ppm / 1_000_000.0
    return (
        subject.precursor_mz / (1.0 + fraction),
        subject.precursor_mz / (1.0 - fraction),
    )


def validate_cid_nl_alignment_subject_compatibility(
    subjects: tuple[CidNlMethodSubject, ...],
) -> tuple[CidNlMethodSubject, ...]:
    """Validate subjects that may share one Alignment comparison universe."""

    if (
        type(subjects) is not tuple
        or not subjects
        or any(type(subject) is not CidNlMethodSubject for subject in subjects)
    ):
        raise CidNlMethodSubjectContractError(
            "alignment subjects must be a non-empty exact tuple"
        )
    subject_ids = tuple(subject.method_subject_id for subject in subjects)
    if len(subject_ids) != len(set(subject_ids)):
        raise CidNlMethodSubjectContractError(
            "alignment subjects must have unique method subject identities"
        )
    if len(subjects) == 1:
        return subjects
    if any(
        subject.subject_kind != "untargeted_precursor"
        or subject.composition is not None
        for subject in subjects
    ):
        raise CidNlMethodSubjectContractError(
            "multi-subject alignment requires scalar Untargeted CID-NL subjects"
        )
    if len({subject.neutral_loss_da for subject in subjects}) != 1:
        raise CidNlMethodSubjectContractError(
            "multi-subject alignment cannot cross configured neutral losses"
        )
    return subjects


def cid_nl_alignment_subject_worksets(
    subjects: tuple[CidNlMethodSubject, ...],
    *,
    comparison_mz_intervals: tuple[tuple[float, float], ...] = (),
) -> tuple[tuple[CidNlMethodSubject, ...], ...]:
    """Partition subjects into compatible overlap-connected worksets.

    Additional (lower m/z, upper m/z) intervals connect comparison scopes within
    each configured method before dispatch. These search bounds never assert
    feature identity. CrossSample binds identities from observed evidence.
    """

    if type(subjects) is not tuple or any(
        type(subject) is not CidNlMethodSubject for subject in subjects
    ):
        raise CidNlMethodSubjectContractError(
            "alignment workset input must be an exact subject tuple"
        )
    if not subjects:
        return ()
    if len({subject.method_subject_id for subject in subjects}) != len(subjects):
        raise CidNlMethodSubjectContractError(
            "alignment workset input contains duplicate method subjects"
        )

    original_index = {
        subject.method_subject_id: index for index, subject in enumerate(subjects)
    }
    scalar_by_loss: dict[float, list[CidNlMethodSubject]] = {}
    components: list[tuple[CidNlMethodSubject, ...]] = []
    for subject in subjects:
        if (
            subject.subject_kind == "untargeted_precursor"
            and subject.composition is None
            and subject.neutral_loss_da is not None
        ):
            scalar_by_loss.setdefault(subject.neutral_loss_da, []).append(subject)
        else:
            components.append((subject,))

    for compatible in scalar_by_loss.values():
        ordered = sorted(
            compatible,
            key=lambda subject: (
                cid_nl_method_subject_resolver_interval(subject),
                subject.method_subject_id,
            ),
        )
        component = [ordered[0]]
        upper = cid_nl_method_subject_resolver_interval(ordered[0])[1]
        for subject in ordered[1:]:
            lower, next_upper = cid_nl_method_subject_resolver_interval(subject)
            if lower > upper:
                components.append(
                    tuple(
                        sorted(
                            component,
                            key=lambda item: original_index[item.method_subject_id],
                        )
                    )
                )
                component = [subject]
                upper = next_upper
                continue
            component.append(subject)
            upper = max(upper, next_upper)
        components.append(
            tuple(
                sorted(
                    component,
                    key=lambda item: original_index[item.method_subject_id],
                )
            )
        )

    if comparison_mz_intervals:
        parents = list(range(len(components)))

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        # A bridge joins whole comparison components before dispatch. Each
        # subject still retains its own assessment owner.
        scalar_intervals = tuple(
            (
                index,
                subject.neutral_loss_da,
                *cid_nl_method_subject_resolver_interval(subject),
            )
            for index, component in enumerate(components)
            for subject in component
            if subject.subject_kind == "untargeted_precursor"
            and subject.composition is None
            and subject.neutral_loss_da is not None
        )
        for mz_lower, mz_upper in comparison_mz_intervals:
            connected_by_loss: dict[float, int] = {}
            for index, loss, lower, upper in scalar_intervals:
                if mz_lower <= upper and lower <= mz_upper:
                    first = connected_by_loss.setdefault(loss, index)
                    parents[root(index)] = root(first)
        merged: dict[int, list[CidNlMethodSubject]] = {}
        for index, original_component in enumerate(components):
            merged.setdefault(root(index), []).extend(original_component)
        components = [
            tuple(
                sorted(
                    component, key=lambda item: original_index[item.method_subject_id]
                )
            )
            for component in merged.values()
        ]

    ordered_components = tuple(
        sorted(
            components,
            key=lambda component: min(
                original_index[subject.method_subject_id] for subject in component
            ),
        )
    )
    for workset in ordered_components:
        validate_cid_nl_alignment_subject_compatibility(workset)
    return ordered_components


def cid_nl_method_subjects_from_request(
    request: ProcessingRequestIdentity,
) -> CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch:
    """Rehydrate the request's singular or batch subject configuration."""

    return _request_subject_context(request)[0]


def _request_subject_context(
    request: ProcessingRequestIdentity,
) -> tuple[
    CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    Mapping[str, CidNlMethodSubject],
]:
    if not isinstance(request, ProcessingRequestIdentity):
        raise CidNlMethodSubjectContractError(
            "request must be an owner-minted processing request"
        )
    return _subjects_from_normalized_inputs(request.normalized_scientific_inputs_json)


@lru_cache(maxsize=1)
def _subjects_from_normalized_inputs(
    normalized_inputs_json: str,
) -> tuple[
    CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    Mapping[str, CidNlMethodSubject],
]:
    # Keep the strict parse and subject lookup in one latest-exact-payload cache.
    payload = _cid_nl_payload_from_normalized_inputs(normalized_inputs_json)
    pinned = (
        _batch_from_payload(payload)
        if payload.get("schema_version") == BATCH_SCHEMA_VERSION
        else _subject_from_payload(payload)
    )
    subjects = (
        pinned.subjects
        if isinstance(pinned, UntargetedCidNlMethodSubjectBatch)
        else (pinned,)
    )
    return pinned, MappingProxyType(
        {subject.method_subject_id: subject for subject in subjects}
    )


def cid_nl_method_subject_from_request(
    request: ProcessingRequestIdentity,
    *,
    method_subject_id: str | None = None,
) -> CidNlMethodSubject:
    """Resolve a request's singular subject or one explicitly named batch member."""

    pinned, subjects_by_id = _request_subject_context(request)
    if method_subject_id is not None:
        try:
            return subjects_by_id[method_subject_id]
        except KeyError as exc:
            raise CidNlMethodSubjectContractError(
                "CID-NL method subject is not pinned by the processing request"
            ) from exc
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        raise CidNlMethodSubjectContractError(
            "Untargeted request pins a CID-NL method subject batch"
        )
    return pinned


def cid_nl_method_subject_batch_from_request(
    request: ProcessingRequestIdentity,
) -> UntargetedCidNlMethodSubjectBatch:
    """Rehydrate a request-pinned multi-subject Untargeted CID-NL batch."""

    payload = _request_cid_nl_payload(request)
    if payload.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            "Untargeted request pins a singular CID-NL method subject"
        )
    return _batch_from_payload(payload)


def _batch_from_payload(
    payload: dict[str, object],
) -> UntargetedCidNlMethodSubjectBatch:
    expected_fields = {"schema_version", "subjects", "candidate_memberships"}
    if set(payload) != expected_fields:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch has wrong fields"
        )
    if payload["schema_version"] != BATCH_SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            f"Untargeted request CID-NL batch schema must be {BATCH_SCHEMA_VERSION!r}"
        )
    raw_subjects = payload["subjects"]
    if not isinstance(raw_subjects, list):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch subjects must be a list"
        )
    subjects = tuple(_subject_from_payload(item) for item in raw_subjects)
    raw_memberships = payload["candidate_memberships"]
    if not isinstance(raw_memberships, list):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch memberships must be a list"
        )
    memberships: list[tuple[str, str]] = []
    for item in raw_memberships:
        if not isinstance(item, dict) or set(item) != {
            "candidate_id",
            "method_subject_id",
        }:
            raise CidNlMethodSubjectContractError(
                "Untargeted request CID-NL subject batch has malformed membership"
            )
        candidate_id = item["candidate_id"]
        method_subject_id = item["method_subject_id"]
        if not isinstance(candidate_id, str) or not isinstance(method_subject_id, str):
            raise CidNlMethodSubjectContractError(
                "Untargeted request CID-NL subject membership IDs must be text"
            )
        memberships.append((candidate_id, method_subject_id))
    try:
        return UntargetedCidNlMethodSubjectBatch(
            subjects=subjects,
            candidate_memberships=tuple(memberships),
        )
    except CidNlMethodSubjectContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch is malformed"
        ) from exc


def _request_cid_nl_payload(
    request: ProcessingRequestIdentity,
) -> dict[str, object]:
    if not isinstance(request, ProcessingRequestIdentity):
        raise CidNlMethodSubjectContractError(
            "request must be an owner-minted processing request"
        )
    return _cid_nl_payload_from_normalized_inputs(
        request.normalized_scientific_inputs_json
    )


def _cid_nl_payload_from_normalized_inputs(
    normalized_inputs_json: str,
) -> dict[str, object]:
    try:
        request_inputs = json.loads(normalized_inputs_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CidNlMethodSubjectContractError(
            "Untargeted request scientific inputs are malformed"
        ) from exc
    if not isinstance(request_inputs, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request scientific inputs must be a mapping"
        )
    payload = request_inputs.get(REQUEST_INPUT_KEY)
    if not isinstance(payload, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request does not pin a CID-NL method subject"
        )
    return payload


def _subject_from_payload(payload: object) -> CidNlMethodSubject:
    if not isinstance(payload, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject must be an object"
        )
    expected_fields = {
        "schema_version",
        "subject_kind",
        "precursor_mz",
        "precursor_tolerance_ppm",
        "neutral_loss_da",
        "target",
        "composition",
    }
    if set(payload) != expected_fields:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject has wrong fields"
        )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            f"Untargeted request CID-NL schema must be {SCHEMA_VERSION!r}"
        )
    subject_kind = payload["subject_kind"]
    if subject_kind != "untargeted_precursor":
        raise CidNlMethodSubjectContractError(
            "Untargeted request requires an untargeted_precursor method subject"
        )
    if payload["target"] is not None:
        raise CidNlMethodSubjectContractError(
            "Untargeted request method subject cannot carry a configured Target"
        )
    try:
        composition = None
        raw = payload["composition"]
        if raw is not None:
            if (
                not isinstance(raw, dict)
                or set(raw) != {"tags", "combine_mode"}
                or not isinstance(raw["tags"], (list, tuple))
            ):
                raise CidNlMethodSubjectContractError(
                    "composite requirement has wrong fields"
                )
            composition = CidNlComposition(
                tags=tuple(tuple(item) for item in raw["tags"]),
                combine_mode=raw["combine_mode"],
            )
            if payload["neutral_loss_da"] is not None:
                raise CidNlMethodSubjectContractError(
                    "composite subject cannot carry scalar loss"
                )
        return CidNlMethodSubject(
            subject_kind="untargeted_precursor",
            precursor_mz=_positive_finite_float(
                payload["precursor_mz"], field="precursor_mz"
            ),
            precursor_tolerance_ppm=_positive_finite_float(
                payload["precursor_tolerance_ppm"],
                field="precursor_tolerance_ppm",
            ),
            neutral_loss_da=None
            if composition is not None
            else _positive_finite_float(
                payload["neutral_loss_da"], field="neutral_loss_da"
            ),
            composition=composition,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CidNlMethodSubjectContractError):
            raise
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject is malformed"
        ) from exc


def _normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CidNlMethodSubjectContractError(
            f"{field} must be non-empty normalized text"
        )
    return value


def _mint_subject(
    *,
    subject_kind: CidNlMethodSubjectKind,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float | None,
    target: ResolvedTargetDefinition | None = None,
    composition: CidNlComposition | None = None,
) -> CidNlMethodSubject:
    precursor = _positive_finite_float(precursor_mz, field="precursor_mz")
    tolerance = _positive_finite_float(
        precursor_tolerance_ppm,
        field="precursor_tolerance_ppm",
    )
    neutral_loss = (
        None
        if composition is not None
        else _positive_finite_float(neutral_loss_da, field="neutral_loss_da")
    )
    return CidNlMethodSubject(
        subject_kind=subject_kind,
        precursor_mz=precursor,
        precursor_tolerance_ppm=tolerance,
        neutral_loss_da=neutral_loss,
        target=target,
        composition=composition,
    )


def _positive_finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CidNlMethodSubjectContractError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise CidNlMethodSubjectContractError(f"{field} must be finite and positive")
    return parsed


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "CID_NL_METHOD_ID",
    "CID_NL_METHOD_VERSION",
    "CID_NL_PROVIDER_ID",
    "CidNlMethodSubject",
    "CidNlMethodSubjectContractError",
    "CidNlMethodSubjectKind",
    "REQUEST_INPUT_KEY",
    "SCHEMA_VERSION",
    "UntargetedCidNlMethodSubjectBatch",
    "cid_nl_alignment_subject_worksets",
    "cid_nl_method_subject_batch_from_request",
    "cid_nl_method_subject_from_request",
    "cid_nl_method_subject_from_target",
    "cid_nl_method_subject_resolver_interval",
    "cid_nl_method_subjects_from_request",
    "mint_untargeted_cid_nl_method_subject",
    "mint_untargeted_cid_nl_method_subject_batch",
    "validate_cid_nl_alignment_subject_compatibility",
]
