import io
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import ClassVar

import pytest

from xic_extractor.alignment import process_execution


class _ImmediateExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self.submitted: list[object] = []

    def submit(self, worker, job):
        self.submitted.append(job)
        future = Future()
        future.set_result(job)
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
        pass


@dataclass(frozen=True)
class _CountedAttachment:
    payload: tuple[int, ...]
    payload_reads: ClassVar[int] = 0

    def __getattribute__(self, name: str):
        if name == "payload":
            type(self).payload_reads += 1
        return super().__getattribute__(name)


class _UnknownAttachmentWrapper:
    def __init__(self) -> None:
        self.callback = lambda: None


class _CallableEnum(Enum):
    MEMBER = "member"

    def __call__(self) -> None:
        return None


class _UnsafeValueEnum(Enum):
    MEMBER = SimpleNamespace(callback=lambda: None)


def test_process_runner_does_not_eagerly_materialize_pickle_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _ImmediateExecutor()

    def forbidden_pickle(*args, **kwargs):
        raise AssertionError("process runner eagerly materialized pickle bytes")

    monkeypatch.setattr(process_execution.pickle, "dumps", forbidden_pickle)

    results = process_execution.run_process_jobs(
        ({"sample": "sample-a", "attachment": (1, 2, 3)},),
        worker=lambda job: job,
        error_factory=lambda job, exc: exc,
        max_workers=1,
        executor_factory=lambda **kwargs: executor,
    )

    assert results == [{"sample": "sample-a", "attachment": (1, 2, 3)}]
    assert executor.submitted == results


def test_process_runner_rejects_all_invalid_attachments_before_submission() -> None:
    executor_created = False

    def executor_factory(**kwargs):
        nonlocal executor_created
        executor_created = True
        return _ImmediateExecutor()

    jobs = (
        {"sample": "sample-a", "attachment": (1, 2, 3)},
        {"sample": "sample-b", "attachment": lambda: None},
    )
    with pytest.raises(TypeError, match="contains callable value"):
        process_execution.run_process_jobs(
            jobs,
            worker=lambda job: job,
            error_factory=lambda job, exc: exc,
            max_workers=1,
            executor_factory=executor_factory,
        )

    assert executor_created is False


def test_process_runner_validates_shared_attachments_once_per_batch() -> None:
    attachment = _CountedAttachment(payload=(1, 2, 3))
    _CountedAttachment.payload_reads = 0
    executor = _ImmediateExecutor()

    process_execution.run_process_jobs(
        (
            {"sample": "sample-a", "attachment": attachment},
            {"sample": "sample-b", "attachment": attachment},
        ),
        worker=lambda job: job,
        error_factory=lambda job, exc: exc,
        max_workers=1,
        executor_factory=lambda **kwargs: executor,
    )

    assert _CountedAttachment.payload_reads == 1


@pytest.mark.parametrize(
    ("attachment", "message"),
    (
        (_UnknownAttachmentWrapper(), "unsupported payload value"),
        (_CallableEnum.MEMBER, "contains callable value"),
        (_UnsafeValueEnum.MEMBER, "unsupported enum value"),
        (SimpleNamespace(callback=lambda: None), "contains callable value"),
        (io.StringIO("data"), "contains file handle"),
        (process_execution, "contains module object"),
    ),
)
def test_process_runner_rejects_hidden_or_unsupported_attachments_before_executor(
    attachment: object,
    message: str,
) -> None:
    executor_created = False

    def executor_factory(**kwargs):
        nonlocal executor_created
        executor_created = True
        return _ImmediateExecutor()

    with pytest.raises(TypeError, match=message):
        process_execution.run_process_jobs(
            ({"sample": "sample-a", "attachment": attachment},),
            worker=lambda job: job,
            error_factory=lambda job, exc: exc,
            max_workers=1,
            executor_factory=executor_factory,
        )

    assert executor_created is False


def test_process_runner_structural_validation_handles_cycles() -> None:
    cyclic_attachment: list[object] = []
    cyclic_attachment.append(cyclic_attachment)
    executor = _ImmediateExecutor()

    results = process_execution.run_process_jobs(
        ({"sample": "sample-a", "attachment": cyclic_attachment},),
        worker=lambda job: job,
        error_factory=lambda job, exc: exc,
        max_workers=1,
        executor_factory=lambda **kwargs: executor,
    )

    assert results[0]["attachment"][0] is results[0]["attachment"]
