from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class AttemptLifecycleContractError(ValueError):
    """Raised when owner-backed attempt lifecycle facts do not close."""


class AttemptLifecycleEventKind(str, Enum):
    """Runtime event kinds needed to freeze initial cancellation ordering."""

    ATTEMPT_QUEUED = "attempt_queued"
    ATTEMPT_STARTED = "attempt_started"
    CANCELLATION_ACCEPTED = "cancellation_accepted"
    MANIFEST_PREFLIGHT_FAILED = "manifest_preflight_failed"
    TERMINAL_PROPOSAL = "terminal_proposal"


class AttemptTerminalOutcome(str, Enum):
    """Canonical proposed outcomes; acknowledgement is a later contract."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkUnitTerminalOutcome(str, Enum):
    """Mechanical terminal result for one owner-declared work unit."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkUnitFailureCategory(str, Enum):
    """Workflow-neutral reason class for unsuccessful settled work."""

    EXECUTION_ERROR = "execution_error"
    WORKER_LOSS = "worker_loss"
    CANCELLATION = "cancellation"


@dataclass(frozen=True, slots=True)
class WorkUnitFailureDetail:
    """Typed producer failure retained through terminal recovery."""

    category: WorkUnitFailureCategory
    stage: str
    source_reference: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, WorkUnitFailureCategory):
            raise AttemptLifecycleContractError(
                "failure category must be a WorkUnitFailureCategory"
            )
        for field in ("stage", "source_reference", "message"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise AttemptLifecycleContractError(
                    f"failure {field} must be non-empty owner-normalized text"
                )


class WorkUnitSettlementIntegrityFailureKind(str, Enum):
    """Fail-closed violations at the manifest-to-settlement boundary."""

    UNDECLARED_WORK_UNIT = "undeclared_work_unit"
    CONFLICTING_SETTLEMENT = "conflicting_settlement"


@dataclass(frozen=True, slots=True)
class RequiredWorkManifest:
    """Closed owner-attested work required for one processing attempt."""

    attempt: ProcessingAttemptIdentity
    work_unit_keys: tuple[str, ...]
    owner_completeness_attestation: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if not isinstance(self.work_unit_keys, tuple):
            raise AttemptLifecycleContractError("work_unit_keys must be a tuple")
        if not self.work_unit_keys:
            raise AttemptLifecycleContractError(
                "work_unit_keys must contain at least one declared work unit"
            )
        if any(
            not isinstance(key, str) or not key.strip() for key in self.work_unit_keys
        ):
            raise AttemptLifecycleContractError(
                "work_unit_keys must contain non-empty opaque text keys"
            )
        if len(set(self.work_unit_keys)) != len(self.work_unit_keys):
            raise AttemptLifecycleContractError("work_unit_keys must be unique")
        if (
            not isinstance(self.owner_completeness_attestation, str)
            or not self.owner_completeness_attestation.strip()
        ):
            raise AttemptLifecycleContractError(
                "owner_completeness_attestation must be non-empty opaque text"
            )


@dataclass(frozen=True, slots=True)
class _RejectedRequiredWorkManifest:
    """Internal empty carrier for a manifest-preflight terminal attempt.

    This is not an accepted manifest and is never exposed as a construction
    surface.  It lets the existing coordinator and observability shapes
    retain the owner-issued attempt plus an empty work universe without
    fabricating ``attempt_started``.
    """

    attempt: ProcessingAttemptIdentity
    work_unit_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if self.work_unit_keys:
            raise AttemptLifecycleContractError(
                "rejected manifest carrier must have no work units"
            )


@dataclass(frozen=True, slots=True)
class WorkUnitSettlement:
    """One accepted terminal settlement over an opaque manifest work key."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    outcome: WorkUnitTerminalOutcome
    owner_result_ref: EvidenceArtifactRef | None = None
    failure_detail: WorkUnitFailureDetail | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if not isinstance(self.work_unit_key, str) or not self.work_unit_key.strip():
            raise AttemptLifecycleContractError(
                "work_unit_key must be non-empty opaque text"
            )
        if not isinstance(self.outcome, WorkUnitTerminalOutcome):
            raise AttemptLifecycleContractError(
                "outcome must be a WorkUnitTerminalOutcome"
            )
        if self.outcome is WorkUnitTerminalOutcome.SUCCEEDED:
            if not isinstance(self.owner_result_ref, EvidenceArtifactRef):
                raise AttemptLifecycleContractError(
                    "succeeded settlement requires an owner-minted EvidenceArtifactRef"
                )
            if (
                self.owner_result_ref.producer_processing_request_id
                != self.attempt.processing_request_id
                or self.owner_result_ref.producer_processing_attempt_id
                != self.attempt.processing_attempt_id
            ):
                raise AttemptLifecycleContractError(
                    "owner_result_ref must bind the settlement request and attempt"
                )
            if self.failure_detail is not None:
                raise AttemptLifecycleContractError(
                    "succeeded settlement cannot carry failure_detail"
                )
            return
        if self.owner_result_ref is not None:
            raise AttemptLifecycleContractError(
                "only succeeded settlement may carry owner_result_ref"
            )
        if not isinstance(self.failure_detail, WorkUnitFailureDetail):
            raise AttemptLifecycleContractError(
                "failed or cancelled settlement requires WorkUnitFailureDetail"
            )
        if (
            self.outcome is WorkUnitTerminalOutcome.CANCELLED
            and self.failure_detail.category is not WorkUnitFailureCategory.CANCELLATION
        ):
            raise AttemptLifecycleContractError(
                "cancelled settlement requires cancellation failure category"
            )
        if (
            self.outcome is WorkUnitTerminalOutcome.FAILED
            and self.failure_detail.category is WorkUnitFailureCategory.CANCELLATION
        ):
            raise AttemptLifecycleContractError(
                "failed settlement cannot carry cancellation failure category"
            )


@dataclass(frozen=True, slots=True)
class WorkUnitSettlementIntegrityFailure:
    """First immutable settlement violation that prevents attempt success."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    kind: WorkUnitSettlementIntegrityFailureKind
    submitted_settlement: WorkUnitSettlement
    accepted_settlement: WorkUnitSettlement | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if not isinstance(self.work_unit_key, str) or not self.work_unit_key.strip():
            raise AttemptLifecycleContractError(
                "work_unit_key must be non-empty opaque text"
            )
        if not isinstance(self.kind, WorkUnitSettlementIntegrityFailureKind):
            raise AttemptLifecycleContractError(
                "kind must be a WorkUnitSettlementIntegrityFailureKind"
            )
        if not isinstance(self.submitted_settlement, WorkUnitSettlement):
            raise AttemptLifecycleContractError(
                "submitted_settlement must be a WorkUnitSettlement"
            )
        if (
            self.submitted_settlement.attempt != self.attempt
            or self.submitted_settlement.work_unit_key != self.work_unit_key
        ):
            raise AttemptLifecycleContractError(
                "submitted settlement must bind the failure attempt and work key"
            )
        if self.kind is WorkUnitSettlementIntegrityFailureKind.UNDECLARED_WORK_UNIT:
            if self.accepted_settlement is not None:
                raise AttemptLifecycleContractError(
                    "undeclared work failure cannot carry accepted settlement"
                )
        elif not isinstance(self.accepted_settlement, WorkUnitSettlement):
            raise AttemptLifecycleContractError(
                "conflicting settlement requires accepted settlement"
            )
        elif (
            self.accepted_settlement.attempt != self.attempt
            or self.accepted_settlement.work_unit_key != self.work_unit_key
            or self.accepted_settlement == self.submitted_settlement
        ):
            raise AttemptLifecycleContractError(
                "accepted settlement must be a different result for the same work key"
            )


@dataclass(frozen=True, slots=True)
class AttemptLifecycleEvent:
    """One ordered runtime fact bound to an owner-issued processing attempt."""

    attempt: ProcessingAttemptIdentity
    sequence: int
    kind: AttemptLifecycleEventKind
    proposed_outcome: AttemptTerminalOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise AttemptLifecycleContractError("sequence must be a positive integer")
        if not isinstance(self.kind, AttemptLifecycleEventKind):
            raise AttemptLifecycleContractError(
                "kind must be an AttemptLifecycleEventKind"
            )
        if self.kind is AttemptLifecycleEventKind.TERMINAL_PROPOSAL:
            if not isinstance(self.proposed_outcome, AttemptTerminalOutcome):
                raise AttemptLifecycleContractError(
                    "terminal_proposal requires an AttemptTerminalOutcome"
                )
        elif self.proposed_outcome is not None:
            raise AttemptLifecycleContractError(
                "only terminal_proposal may carry a proposed outcome"
            )


@dataclass(frozen=True, slots=True)
class AttemptLifecycleHistory:
    """Immutable prefix of one attempt's append-only lifecycle history."""

    attempt: ProcessingAttemptIdentity
    events: tuple[AttemptLifecycleEvent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if not isinstance(self.events, tuple):
            raise AttemptLifecycleContractError("events must be a tuple")
        if self.events:
            first_event = self.events[0]
            if not isinstance(first_event, AttemptLifecycleEvent):
                raise AttemptLifecycleContractError(
                    "events must contain AttemptLifecycleEvent values"
                )
            if first_event.kind is not AttemptLifecycleEventKind.ATTEMPT_QUEUED:
                raise AttemptLifecycleContractError(
                    "first event must be attempt_queued"
                )

        queued = False
        started = False
        cancellation_accepted = False
        manifest_preflight_failed = False
        terminal_proposed = False
        for expected_sequence, event in enumerate(self.events, start=1):
            if not isinstance(event, AttemptLifecycleEvent):
                raise AttemptLifecycleContractError(
                    "events must contain AttemptLifecycleEvent values"
                )
            if event.attempt != self.attempt:
                raise AttemptLifecycleContractError(
                    "events must reference the owner-issued attempt"
                )
            if event.sequence != expected_sequence:
                raise AttemptLifecycleContractError(
                    "event sequences must be contiguous starting at 1"
                )
            if terminal_proposed:
                raise AttemptLifecycleContractError(
                    "terminal proposal is final in the coordinator history"
                )

            if event.kind is AttemptLifecycleEventKind.ATTEMPT_QUEUED:
                if queued:
                    raise AttemptLifecycleContractError(
                        "attempt_queued may appear only as the first event"
                    )
                queued = True
                continue

            if not queued:
                raise AttemptLifecycleContractError(
                    "first event must be attempt_queued"
                )
            if event.kind is AttemptLifecycleEventKind.ATTEMPT_STARTED:
                if started:
                    raise AttemptLifecycleContractError(
                        "attempt_started may appear only once"
                    )
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "attempt cannot start after manifest preflight failure"
                    )
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "attempt cannot start after queued cancellation"
                    )
                started = True
                continue
            if event.kind is AttemptLifecycleEventKind.CANCELLATION_ACCEPTED:
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "cancellation_accepted may appear only once"
                    )
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "cancellation cannot be accepted after manifest "
                        "preflight failure"
                    )
                cancellation_accepted = True
                continue
            if event.kind is AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED:
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "manifest_preflight_failed may appear only once"
                    )
                if started:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure must occur before attempt_started"
                    )
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure cannot follow queued cancellation"
                    )
                manifest_preflight_failed = True
                continue
            if event.kind is AttemptLifecycleEventKind.TERMINAL_PROPOSAL:
                if not manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "terminal proposal must follow manifest_preflight_failed"
                    )
                if event.proposed_outcome is not AttemptTerminalOutcome.FAILED:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure must propose failed"
                    )
                terminal_proposed = True
                continue
            raise AttemptLifecycleContractError(
                f"unsupported lifecycle event kind: {event.kind.value}"
            )

    @property
    def cancellation_accepted(self) -> bool:
        return any(
            event.kind is AttemptLifecycleEventKind.CANCELLATION_ACCEPTED
            for event in self.events
        )


@dataclass(frozen=True, slots=True)
class RequiredWorkManifestPreflight:
    """Accepted manifest or the existing queued-to-failed runtime proposal."""

    history: AttemptLifecycleHistory
    manifest: RequiredWorkManifest | None

    def __post_init__(self) -> None:
        if not isinstance(self.history, AttemptLifecycleHistory):
            raise AttemptLifecycleContractError(
                "history must be an AttemptLifecycleHistory"
            )
        if self.manifest is not None:
            if not isinstance(self.manifest, RequiredWorkManifest):
                raise AttemptLifecycleContractError(
                    "manifest must be a RequiredWorkManifest"
                )
            if self.manifest.attempt != self.history.attempt:
                raise AttemptLifecycleContractError(
                    "manifest must reference the history attempt"
                )
            return
        if (
            not self.history.events
            or self.history.events[-1].kind
            is not AttemptLifecycleEventKind.TERMINAL_PROPOSAL
            or self.history.events[-1].proposed_outcome
            is not AttemptTerminalOutcome.FAILED
        ):
            raise AttemptLifecycleContractError(
                "rejected manifest preflight must carry a failed terminal proposal"
            )

    @property
    def accepted(self) -> bool:
        return self.manifest is not None


@dataclass(frozen=True, slots=True)
class AttemptRuntime:
    """Pure reducer state for a running or preflight-terminalized attempt."""

    manifest: RequiredWorkManifest | _RejectedRequiredWorkManifest
    history: AttemptLifecycleHistory
    settlements: tuple[WorkUnitSettlement, ...] = ()
    integrity_failure: WorkUnitSettlementIntegrityFailure | None = None

    @property
    def all_manifest_work_settled(self) -> bool:
        settled_keys = {settlement.work_unit_key for settlement in self.settlements}
        return settled_keys == set(self.manifest.work_unit_keys)

    @property
    def must_fail(self) -> bool:
        return (
            self.manifest_preflight_failed
            or self.integrity_failure is not None
            or any(
                settlement.outcome is WorkUnitTerminalOutcome.FAILED
                for settlement in self.settlements
            )
        )

    @property
    def manifest_preflight_failed(self) -> bool:
        return isinstance(self.manifest, _RejectedRequiredWorkManifest)


def append_attempt_lifecycle_event(
    history: AttemptLifecycleHistory,
    kind: AttemptLifecycleEventKind,
    *,
    proposed_outcome: AttemptTerminalOutcome | None = None,
) -> AttemptLifecycleHistory:
    """Return a new validated history without mutating its previous prefix."""

    if not isinstance(history, AttemptLifecycleHistory):
        raise AttemptLifecycleContractError(
            "history must be an AttemptLifecycleHistory"
        )
    event = AttemptLifecycleEvent(
        attempt=history.attempt,
        sequence=len(history.events) + 1,
        kind=kind,
        proposed_outcome=proposed_outcome,
    )
    return AttemptLifecycleHistory(
        attempt=history.attempt,
        events=(*history.events, event),
    )


def propose_manifest_preflight_failure(
    history: AttemptLifecycleHistory,
) -> AttemptLifecycleHistory:
    """Append the canonical queued-to-failed proposal path atomically to a prefix."""

    failed = append_attempt_lifecycle_event(
        history,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
    )
    return append_attempt_lifecycle_event(
        failed,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
        proposed_outcome=AttemptTerminalOutcome.FAILED,
    )


def preflight_required_work_manifest(
    history: AttemptLifecycleHistory,
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
) -> RequiredWorkManifestPreflight:
    """Consume owner manifest content before dispatch, failing the attempt closed."""

    if not isinstance(history, AttemptLifecycleHistory):
        raise AttemptLifecycleContractError(
            "history must be an AttemptLifecycleHistory"
        )
    if (
        len(history.events) != 1
        or history.events[0].kind is not AttemptLifecycleEventKind.ATTEMPT_QUEUED
    ):
        raise AttemptLifecycleContractError(
            "manifest preflight requires exactly the attempt_queued history"
        )
    if (
        not isinstance(owner_completeness_attestation, str)
        or not owner_completeness_attestation.strip()
    ):
        return RequiredWorkManifestPreflight(
            history=propose_manifest_preflight_failure(history),
            manifest=None,
        )
    try:
        manifest = RequiredWorkManifest(
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation=owner_completeness_attestation,
        )
        if manifest.attempt != history.attempt:
            raise AttemptLifecycleContractError(
                "manifest must reference the queued history attempt"
            )
    except AttemptLifecycleContractError:
        return RequiredWorkManifestPreflight(
            history=propose_manifest_preflight_failure(history),
            manifest=None,
        )
    return RequiredWorkManifestPreflight(history=history, manifest=manifest)


def start_attempt_runtime(
    preflight: RequiredWorkManifestPreflight,
) -> AttemptRuntime:
    """Consume an accepted manifest and start its pure runtime reducer."""

    if not isinstance(preflight, RequiredWorkManifestPreflight):
        raise AttemptLifecycleContractError(
            "preflight must be a RequiredWorkManifestPreflight"
        )
    if not preflight.accepted or preflight.manifest is None:
        raise AttemptLifecycleContractError(
            "cannot start runtime from rejected manifest preflight"
        )
    started = append_attempt_lifecycle_event(
        preflight.history,
        AttemptLifecycleEventKind.ATTEMPT_STARTED,
    )
    return AttemptRuntime(manifest=preflight.manifest, history=started)


def _start_runtime_attempt(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
) -> AttemptRuntime:
    """Mint one running or preflight-terminalized owner attempt.

    The attempt entry lives in ``execution.attempt``.  Keeping this
    implementation private prevents the state-model module from becoming an
    alternate production construction authority.
    """

    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    preflight = preflight_required_work_manifest(
        queued,
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )
    if not preflight.accepted:
        return AttemptRuntime(
            manifest=_RejectedRequiredWorkManifest(attempt=preflight.history.attempt),
            history=preflight.history,
        )
    return start_attempt_runtime(preflight)


def mint_work_unit_settlement(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    outcome: WorkUnitTerminalOutcome,
    owner_result_ref: EvidenceArtifactRef | None = None,
    failure_detail: WorkUnitFailureDetail | None = None,
) -> WorkUnitSettlement:
    """Mint one typed work settlement through the runtime owner."""

    return WorkUnitSettlement(
        attempt=attempt,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=owner_result_ref,
        failure_detail=failure_detail,
    )


def settle_work_unit(
    runtime: AttemptRuntime,
    *,
    work_unit_key: str,
    outcome: WorkUnitTerminalOutcome,
    owner_result_ref: EvidenceArtifactRef | None = None,
    failure_detail: WorkUnitFailureDetail | None = None,
) -> AttemptRuntime:
    """Accept one settlement exactly once; record conflicts fail closed."""

    if not isinstance(runtime, AttemptRuntime):
        raise AttemptLifecycleContractError("runtime must be an AttemptRuntime")
    submitted = mint_work_unit_settlement(
        attempt=runtime.manifest.attempt,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=owner_result_ref,
        failure_detail=failure_detail,
    )
    if (
        submitted.outcome is WorkUnitTerminalOutcome.CANCELLED
        and not runtime.history.cancellation_accepted
    ):
        raise AttemptLifecycleContractError(
            "cancelled settlement requires accepted cancellation"
        )
    accepted = next(
        (
            settlement
            for settlement in runtime.settlements
            if settlement.work_unit_key == work_unit_key
        ),
        None,
    )
    failure: WorkUnitSettlementIntegrityFailure | None = None
    if work_unit_key not in runtime.manifest.work_unit_keys:
        failure = WorkUnitSettlementIntegrityFailure(
            attempt=runtime.manifest.attempt,
            work_unit_key=work_unit_key,
            kind=WorkUnitSettlementIntegrityFailureKind.UNDECLARED_WORK_UNIT,
            submitted_settlement=submitted,
        )
    elif accepted is not None:
        if accepted == submitted:
            return runtime
        failure = WorkUnitSettlementIntegrityFailure(
            attempt=runtime.manifest.attempt,
            work_unit_key=work_unit_key,
            kind=WorkUnitSettlementIntegrityFailureKind.CONFLICTING_SETTLEMENT,
            submitted_settlement=submitted,
            accepted_settlement=accepted,
        )
    if failure is not None:
        if runtime.integrity_failure is not None:
            return runtime
        return AttemptRuntime(
            manifest=runtime.manifest,
            history=runtime.history,
            settlements=runtime.settlements,
            integrity_failure=failure,
        )
    return AttemptRuntime(
        manifest=runtime.manifest,
        history=runtime.history,
        settlements=(*runtime.settlements, submitted),
        integrity_failure=runtime.integrity_failure,
    )


def _settle_work_units(
    runtime: AttemptRuntime,
    settlements: Iterable[WorkUnitSettlement],
) -> AttemptRuntime:
    """Reduce ordered owner submissions into the next runtime state."""

    if not isinstance(runtime, AttemptRuntime):
        raise AttemptLifecycleContractError("runtime must be an AttemptRuntime")
    manifest_keys = set(runtime.manifest.work_unit_keys)
    accepted_by_key = {item.work_unit_key: item for item in runtime.settlements}
    accepted_settlements = list(runtime.settlements)
    integrity_failure = runtime.integrity_failure
    for settlement in settlements:
        submitted = mint_work_unit_settlement(
            attempt=runtime.manifest.attempt,
            work_unit_key=settlement.work_unit_key,
            outcome=settlement.outcome,
            owner_result_ref=settlement.owner_result_ref,
            failure_detail=settlement.failure_detail,
        )
        if (
            submitted.outcome is WorkUnitTerminalOutcome.CANCELLED
            and not runtime.history.cancellation_accepted
        ):
            raise AttemptLifecycleContractError(
                "cancelled settlement requires accepted cancellation"
            )
        key = submitted.work_unit_key
        accepted = accepted_by_key.get(key)
        failure: WorkUnitSettlementIntegrityFailure | None = None
        if key not in manifest_keys:
            failure = WorkUnitSettlementIntegrityFailure(
                attempt=runtime.manifest.attempt,
                work_unit_key=key,
                kind=WorkUnitSettlementIntegrityFailureKind.UNDECLARED_WORK_UNIT,
                submitted_settlement=submitted,
            )
        elif accepted is not None:
            if accepted == submitted:
                continue
            failure = WorkUnitSettlementIntegrityFailure(
                attempt=runtime.manifest.attempt,
                work_unit_key=key,
                kind=WorkUnitSettlementIntegrityFailureKind.CONFLICTING_SETTLEMENT,
                submitted_settlement=submitted,
                accepted_settlement=accepted,
            )
        if failure is not None:
            if integrity_failure is None:
                integrity_failure = failure
            continue
        accepted_by_key[key] = submitted
        accepted_settlements.append(submitted)
    if (
        len(accepted_settlements) == len(runtime.settlements)
        and integrity_failure is runtime.integrity_failure
    ):
        return runtime
    return AttemptRuntime(
        manifest=runtime.manifest,
        history=runtime.history,
        settlements=tuple(accepted_settlements),
        integrity_failure=integrity_failure,
    )


__all__ = (
    "AttemptLifecycleContractError",
    "AttemptLifecycleEventKind",
    "AttemptTerminalOutcome",
    "WorkUnitFailureCategory",
    "WorkUnitFailureDetail",
    "WorkUnitSettlementIntegrityFailureKind",
    "WorkUnitTerminalOutcome",
)
