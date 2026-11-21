"""Workflow-neutral identity for one CID-NL precursor method subject.

Targeted and Untargeted callers may constrain the same CID-NL assessment owner,
but a configured Target is not a universal scientific identity.  This module
keeps the common precursor/method facts explicit and retains Targeted identity
only when the subject was adapted from a configured Target.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.models import Target
from xic_extractor.mass_accuracy import absolute_ppm_error, reference_mz_window

SCHEMA_VERSION = "cid_nl_method_subject"
BATCH_SCHEMA_VERSION = "cid_nl_method_subject_batch"
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
        object.__setattr__(
            self, "tags", tuple((name, float(loss)) for name, loss in self.tags)
        )


@dataclass(frozen=True, slots=True)
class CidNlMethodSubject:
    """Smallest shared subject for Targeted and Untargeted CID-NL assessment."""

    subject_kind: CidNlMethodSubjectKind
    precursor_mz: float
    precursor_tolerance_ppm: float
    neutral_loss_da: float | None
    target: Target | None = None
    composition: CidNlComposition | None = None

    @property
    def method_subject_id(self) -> str:
        """Natural key: the Target label, or the precursor and its losses."""

        if self.target is not None:
            return f"target|{self.target.label}"
        if self.composition is not None:
            losses = f"{self.composition.combine_mode}:" + ",".join(
                f"{name}={loss!r}" for name, loss in self.composition.tags
            )
        else:
            losses = repr(self.neutral_loss_da)
        return (
            f"precursor|{self.precursor_mz!r}|{self.precursor_tolerance_ppm!r}|{losses}"
        )

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


def cid_nl_method_subject_from_target(
    target: Target,
) -> CidNlMethodSubject:
    """Adapt one configured Target into the shared CID-NL subject."""

    neutral_loss_da = target.neutral_loss_da
    if neutral_loss_da is None:
        raise CidNlMethodSubjectContractError(
            "CID-NL Targeted subject requires configured neutral loss"
        )
    return _mint_subject(
        subject_kind="targeted_definition",
        precursor_mz=target.mz,
        precursor_tolerance_ppm=target.ppm_tol,
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


def mint_cid_nl_subject_membership(
    *,
    candidates: tuple[tuple[float | None, str], ...],
    precursor_by_id: Mapping[str, float],
    precursor_tolerance_ppm: float,
    composition: CidNlComposition | None,
) -> UntargetedCidNlMethodSubjectBatch:
    """Assign fixed query centers to (requirement, candidate) pairs in order.

    Discovery delivers one peak per ion peak, so each candidate is its own
    unit. A candidate joins the current center of its requirement while it
    lies inside that center's method window; otherwise it opens a new center.
    """
    subject_by_key: dict[tuple[float, float | None], CidNlMethodSubject] = {}
    membership_keys: list[tuple[str, tuple[float, float | None]]] = []
    key_by_requirement: dict[float | None, tuple[float, float | None]] = {}
    for requirement, candidate in candidates:
        key = key_by_requirement.get(requirement)
        precursor_mz = precursor_by_id[candidate]
        if (
            key is None
            or absolute_ppm_error(precursor_mz, key[0]) > precursor_tolerance_ppm
        ):
            key = (precursor_mz, requirement)
            key_by_requirement[requirement] = key
            subject_by_key[key] = mint_untargeted_cid_nl_method_subject(
                precursor_mz=precursor_mz,
                precursor_tolerance_ppm=precursor_tolerance_ppm,
                neutral_loss_da=requirement,
                composition=composition,
            )
        membership_keys.append((candidate, key))

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

    return reference_mz_window(subject.precursor_mz, subject.precursor_tolerance_ppm)


def validate_cid_nl_alignment_subject_compatibility(
    subjects: tuple[CidNlMethodSubject, ...],
) -> tuple[CidNlMethodSubject, ...]:
    """Validate subjects that may share one Alignment comparison universe."""

    if not subjects:
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

    return tuple(
        sorted(
            components,
            key=lambda component: min(
                original_index[subject.method_subject_id] for subject in component
            ),
        )
    )


def _mint_subject(
    *,
    subject_kind: CidNlMethodSubjectKind,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float | None,
    target: Target | None = None,
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
    "SCHEMA_VERSION",
    "UntargetedCidNlMethodSubjectBatch",
    "cid_nl_alignment_subject_worksets",
    "cid_nl_method_subject_from_target",
    "cid_nl_method_subject_resolver_interval",
    "mint_untargeted_cid_nl_method_subject",
    "mint_untargeted_cid_nl_method_subject_batch",
    "validate_cid_nl_alignment_subject_compatibility",
]
