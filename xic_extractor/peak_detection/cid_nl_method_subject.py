"""Workflow-neutral identity for one CID-NL precursor method subject.

Targeted and Untargeted callers may constrain the same CID-NL assessment owner,
but a configured Target is not a universal scientific identity.  This module
keeps the common precursor/method facts explicit and retains Targeted identity
only when the subject was adapted from an owner-resolved target definition.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
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

_SUBJECT_ID_PREFIX = "xic.cid-nl-method-subject:"
_TARGET_KEY_ID_PREFIX = "xic.target-key:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition:"
_TARGET_SNAPSHOT_ID_PREFIX = "xic.target-list-snapshot:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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

    method_subject_id: str
    subject_kind: CidNlMethodSubjectKind
    precursor_mz: float
    precursor_tolerance_ppm: float
    neutral_loss_da: float | None
    target_snapshot_id: str | None = None
    target_key_id: str | None = None
    target_definition_id: str | None = None
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
        target_ids = (
            self.target_snapshot_id,
            self.target_key_id,
            self.target_definition_id,
        )
        if self.subject_kind == "targeted_definition":
            if any(value is None for value in target_ids):
                raise CidNlMethodSubjectContractError(
                    "targeted method subject requires the complete target identity"
                )
            _validate_typed_id(
                self.target_snapshot_id,
                prefix=_TARGET_SNAPSHOT_ID_PREFIX,
                field="target_snapshot_id",
            )
            _validate_typed_id(
                self.target_key_id,
                prefix=_TARGET_KEY_ID_PREFIX,
                field="target_key_id",
            )
            _validate_typed_id(
                self.target_definition_id,
                prefix=_TARGET_DEFINITION_ID_PREFIX,
                field="target_definition_id",
            )
        elif any(value is not None for value in target_ids):
            raise CidNlMethodSubjectContractError(
                "Untargeted method subject cannot carry configured Target identity"
            )

        object.__setattr__(self, "precursor_mz", precursor_mz)
        object.__setattr__(
            self,
            "precursor_tolerance_ppm",
            precursor_tolerance_ppm,
        )
        object.__setattr__(self, "neutral_loss_da", neutral_loss_da)
        expected_id = _method_subject_id(
            subject_kind=self.subject_kind,
            precursor_mz=precursor_mz,
            precursor_tolerance_ppm=precursor_tolerance_ppm,
            neutral_loss_da=neutral_loss_da,
            target_snapshot_id=self.target_snapshot_id,
            target_key_id=self.target_key_id,
            target_definition_id=self.target_definition_id,
            composition=self.composition,
        )
        if self.method_subject_id != expected_id:
            raise CidNlMethodSubjectContractError(
                "method_subject_id mismatches the canonical CID-NL subject"
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

    def __post_init__(self) -> None:
        _validate_untargeted_subject_batch(
            subjects=self.subjects,
            candidate_memberships=self.candidate_memberships,
        )

    @property
    def schema_version(self) -> str:
        return BATCH_SCHEMA_VERSION

    def subject_for_candidate(self, candidate_id: str) -> CidNlMethodSubject:
        """Return the owner subject bound to one exact candidate ID."""

        candidate = _normalized_text(candidate_id, field="candidate_id")
        membership = next(
            (
                (member_candidate, method_subject_id)
                for member_candidate, method_subject_id in self.candidate_memberships
                if member_candidate == candidate
            ),
            None,
        )
        if membership is None:
            raise CidNlMethodSubjectContractError(
                f"candidate {candidate!r} is not declared by the CID-NL subject batch"
            )
        _member_candidate, method_subject_id = membership
        return next(
            subject
            for subject in self.subjects
            if subject.method_subject_id == method_subject_id
        )

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
        target_snapshot_id=target.snapshot.snapshot_id,
        target_key_id=target.definition.target_key.target_key_id,
        target_definition_id=target.definition.target_definition_id,
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
    return subjects


def validate_cid_nl_alignment_subject_workset(
    subjects: tuple[CidNlMethodSubject, ...],
) -> tuple[CidNlMethodSubject, ...]:
    """Validate one overlap-connected, method-compatible Alignment workset."""

    validate_cid_nl_alignment_subject_compatibility(subjects)
    if len({subject.neutral_loss_da for subject in subjects}) != 1:
        raise CidNlMethodSubjectContractError(
            "overlap-only worksets cannot cross configured neutral losses"
        )
    ordered_intervals = sorted(
        (cid_nl_method_subject_resolver_interval(subject) for subject in subjects),
        key=lambda interval: (interval[0], interval[1]),
    )
    upper = ordered_intervals[0][1]
    for lower, next_upper in ordered_intervals[1:]:
        if lower > upper:
            raise CidNlMethodSubjectContractError(
                "alignment workset resolver intervals are not overlap-connected"
            )
        upper = max(upper, next_upper)
    return subjects


def cid_nl_alignment_subject_worksets(
    subjects: tuple[CidNlMethodSubject, ...],
    *,
    comparison_mz_intervals: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[tuple[CidNlMethodSubject, ...], ...]:
    """Partition subjects into compatible overlap-connected worksets.

    Additional (lower loss, upper loss, lower m/z, upper m/z) intervals connect
    comparison scopes before dispatch. These search bounds never assert feature
    identity. CrossSample binds identities from the actual observed evidence.
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

        # A bridge joins whole comparison components, including ppm-near method
        # configurations. Each subject still retains its own assessment owner.
        scalar_intervals = tuple(
            (
                index, subject.neutral_loss_da,
                *cid_nl_method_subject_resolver_interval(subject),
            )
            for index, component in enumerate(components)
            for subject in component
            if subject.subject_kind == "untargeted_precursor"
            and subject.composition is None
            and subject.neutral_loss_da is not None
        )
        for loss_lower, loss_upper, mz_lower, mz_upper in comparison_mz_intervals:
            connected = [
                index
                for index, loss, lower, upper in scalar_intervals
                if loss_lower <= loss <= loss_upper
                and mz_lower <= upper and lower <= mz_upper
            ]
            if connected:
                first = root(connected[0])
                for index in connected[1:]:
                    parents[root(index)] = first
        merged: dict[int, list[CidNlMethodSubject]] = {}
        for index, original_component in enumerate(components):
            merged.setdefault(root(index), []).extend(original_component)
        components = [
            tuple(sorted(
                component, key=lambda item: original_index[item.method_subject_id]
            ))
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
    # Keep the strict parse and its lookup in one latest-exact-payload cache.
    # Binding still compares the full subject; the index only avoids scanning
    # every other subject for each sample/subject job.
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
) -> CidNlMethodSubject:
    """Rehydrate the exact Untargeted provider configuration pinned by a request."""

    payload = _request_cid_nl_payload(request)
    if payload.get("schema_version") == BATCH_SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            "Untargeted request pins a CID-NL method subject batch"
        )
    return _subject_from_payload(payload)


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


def validate_cid_nl_method_subject_binding(
    subject: CidNlMethodSubject,
    *,
    processing_owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition | None = None,
) -> None:
    """Reject a subject not pinned by the exact processing request owner."""

    if not isinstance(subject, CidNlMethodSubject):
        raise CidNlMethodSubjectContractError(
            "subject must be an owner-normalized CID-NL method subject"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlMethodSubjectContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if subject.subject_kind == "targeted_definition":
        if target is None or not isinstance(target, ResolvedTargetDefinition):
            raise CidNlMethodSubjectContractError(
                "targeted method subject requires its exact target adapter"
            )
        expected = cid_nl_method_subject_from_target(target)
        if subject != expected:
            raise CidNlMethodSubjectContractError(
                "targeted method subject mismatches its target definition"
            )
        snapshot = processing_owners.target_snapshot
        if snapshot is None or snapshot.snapshot_id != target.snapshot.snapshot_id:
            raise CidNlMethodSubjectContractError(
                "targeted method subject is outside the processing request"
            )
        return

    if target is not None:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject cannot be paired with a configured Target"
        )
    try:
        _pinned, subjects_by_id = _request_subject_context(processing_owners.request)
    except CidNlMethodSubjectContractError as exc:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject is not pinned by the processing request"
        ) from exc
    if subjects_by_id.get(subject.method_subject_id) != subject:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject is not pinned by the processing request"
        )


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
        "method_subject_id",
        "subject_kind",
        "precursor_mz",
        "precursor_tolerance_ppm",
        "neutral_loss_da",
        "target_snapshot_id",
        "target_key_id",
        "target_definition_id",
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
    method_subject_id = payload["method_subject_id"]
    if not isinstance(method_subject_id, str):
        raise CidNlMethodSubjectContractError(
            "Untargeted request method_subject_id must be text"
        )
    target_ids = (
        payload["target_snapshot_id"],
        payload["target_key_id"],
        payload["target_definition_id"],
    )
    if any(value is not None for value in target_ids):
        raise CidNlMethodSubjectContractError(
            "Untargeted request method subject cannot carry Target identity"
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
            method_subject_id=method_subject_id,
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


def _validate_untargeted_subject_batch(
    *,
    subjects: object,
    candidate_memberships: object,
) -> None:
    if type(subjects) is not tuple or not subjects:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch requires a non-empty subject tuple"
        )
    if any(type(subject) is not CidNlMethodSubject for subject in subjects):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains a non-owner subject"
        )
    for subject in subjects:
        if subject.subject_kind != "untargeted_precursor":
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch cannot contain a Targeted subject"
            )
        try:
            CidNlMethodSubject(
                method_subject_id=subject.method_subject_id,
                subject_kind=subject.subject_kind,
                precursor_mz=subject.precursor_mz,
                precursor_tolerance_ppm=subject.precursor_tolerance_ppm,
                neutral_loss_da=subject.neutral_loss_da,
                target_snapshot_id=subject.target_snapshot_id,
                target_key_id=subject.target_key_id,
                target_definition_id=subject.target_definition_id,
                composition=subject.composition,
            )
        except (TypeError, ValueError) as exc:
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch contains a malformed subject"
            ) from exc
    subject_ids = tuple(subject.method_subject_id for subject in subjects)
    if len(subject_ids) != len(set(subject_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains duplicate subjects"
        )
    if subject_ids != tuple(sorted(subject_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch subjects are not canonically ordered"
        )

    if type(candidate_memberships) is not tuple or not candidate_memberships:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch requires candidate memberships"
        )
    normalized_memberships: list[tuple[str, str]] = []
    for membership in candidate_memberships:
        if type(membership) is not tuple or len(membership) != 2:
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch has malformed membership"
            )
        candidate_id = _normalized_text(membership[0], field="candidate_id")
        method_subject_id = _normalized_text(membership[1], field="method_subject_id")
        normalized_memberships.append((candidate_id, method_subject_id))
    if tuple(normalized_memberships) != candidate_memberships:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch memberships are not normalized"
        )
    candidate_ids = tuple(candidate_id for candidate_id, _ in candidate_memberships)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains duplicate candidates"
        )
    if candidate_ids != tuple(sorted(candidate_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch memberships are not canonically ordered"
        )
    declared_subject_ids = set(subject_ids)
    linked_subject_ids = {
        method_subject_id for _, method_subject_id in candidate_memberships
    }
    if not linked_subject_ids.issubset(declared_subject_ids):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch references an undeclared subject"
        )
    if linked_subject_ids != declared_subject_ids:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains an unused subject"
        )


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
    target_snapshot_id: str | None = None,
    target_key_id: str | None = None,
    target_definition_id: str | None = None,
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
        method_subject_id=_method_subject_id(
            subject_kind=subject_kind,
            precursor_mz=precursor,
            precursor_tolerance_ppm=tolerance,
            neutral_loss_da=neutral_loss,
            target_snapshot_id=target_snapshot_id,
            target_key_id=target_key_id,
            target_definition_id=target_definition_id,
            composition=composition,
        ),
        subject_kind=subject_kind,
        precursor_mz=precursor,
        precursor_tolerance_ppm=tolerance,
        neutral_loss_da=neutral_loss,
        target_snapshot_id=target_snapshot_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
        composition=composition,
    )


def _method_subject_id(
    *,
    subject_kind: CidNlMethodSubjectKind,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float | None,
    target_snapshot_id: str | None,
    target_key_id: str | None,
    target_definition_id: str | None,
    composition: CidNlComposition | None = None,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider_id": CID_NL_PROVIDER_ID,
        "method_id": CID_NL_METHOD_ID,
        "method_version": CID_NL_METHOD_VERSION,
        "subject_kind": subject_kind,
        "precursor_mz": precursor_mz,
        "precursor_tolerance_ppm": precursor_tolerance_ppm,
        "neutral_loss_da": neutral_loss_da,
        "target_snapshot_id": target_snapshot_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
        "composition": asdict(composition) if composition is not None else None,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _SUBJECT_ID_PREFIX + hashlib.sha256(encoded).hexdigest()


def _positive_finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CidNlMethodSubjectContractError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise CidNlMethodSubjectContractError(f"{field} must be finite and positive")
    return parsed


def _validate_typed_id(
    value: object,
    *,
    prefix: str,
    field: str,
) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlMethodSubjectContractError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlMethodSubjectContractError(f"{field} must contain a SHA-256 digest")
    return value


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
    "validate_cid_nl_alignment_subject_workset",
    "validate_cid_nl_method_subject_binding",
]
