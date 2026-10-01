"""Sample process-tree resident and private memory without runtime dependencies."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from threading import Event, Lock, Thread, current_thread
from typing import Protocol

_SAMPLE_SOURCE = "win32_process_tree_sampled_2s"


@dataclass(frozen=True)
class ProcessMemorySample:
    rss_bytes: int | None
    private_bytes: int | None
    availability: str
    reason: str = ""


@dataclass
class ProcessMemoryPeak:
    """Maximum sampled process-tree totals within one scope."""

    rss_bytes: int | None = None
    private_bytes: int | None = None
    availability: str = "unavailable"
    reason: str = ""
    source: str = _SAMPLE_SOURCE

    def include(self, sample: ProcessMemorySample) -> None:
        had_value = self.rss_bytes is not None or self.private_bytes is not None
        if sample.rss_bytes is not None:
            self.rss_bytes = max(self.rss_bytes or 0, sample.rss_bytes)
        if sample.private_bytes is not None:
            self.private_bytes = max(self.private_bytes or 0, sample.private_bytes)
        has_value = self.rss_bytes is not None or self.private_bytes is not None
        if not has_value:
            self.availability = sample.availability
        elif not had_value:
            self.availability = sample.availability
        elif sample.availability != "measured" or self.availability != "measured":
            self.availability = "partial"
        if sample.reason and sample.reason not in self.reason.split("; "):
            self.reason = "; ".join(filter(None, (self.reason, sample.reason)))


class ProcessMemoryCollector(Protocol):
    def start(self) -> bool: ...

    def stop(self) -> ProcessMemoryPeak: ...

    def scope(self) -> AbstractContextManager[ProcessMemoryPeak]: ...


class ProcessTreeMemorySampler:
    """Sample the current Windows process tree at scope edges and every 2 seconds."""

    def __init__(
        self,
        *,
        interval_sec: float = 2.0,
        sample_reader: Callable[[], ProcessMemorySample] | None = None,
    ) -> None:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        self._interval_sec = interval_sec
        if sample_reader is not None:
            self._sample_reader = sample_reader
            self._periodic_sampling = True
        else:
            self._sample_reader, self._periodic_sampling = _default_sample_reader()
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._running = False
        self._peak = ProcessMemoryPeak()
        self._active_scopes: list[ProcessMemoryPeak] = []

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._peak = ProcessMemoryPeak()
            self._stop_event.clear()
        self._sample_now()
        if self._periodic_sampling:
            thread = Thread(
                target=self._sampling_loop,
                name="process-tree-memory-sampler",
                daemon=True,
            )
            with self._lock:
                self._thread = thread
            thread.start()
        return True

    def stop(self) -> ProcessMemoryPeak:
        with self._lock:
            if not self._running:
                return replace(self._peak)
        self._sample_now()
        with self._lock:
            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not current_thread():
            thread.join()
        with self._lock:
            return replace(self._peak)

    @contextmanager
    def scope(self) -> Iterator[ProcessMemoryPeak]:
        peak = ProcessMemoryPeak()
        with self._lock:
            self._active_scopes.append(peak)
        started_here = self.start()
        if not started_here:
            self._sample_now()
        try:
            yield peak
        finally:
            if started_here:
                self.stop()
            else:
                self._sample_now()
            with self._lock:
                for index, active_scope in enumerate(self._active_scopes):
                    if active_scope is peak:
                        del self._active_scopes[index]
                        break

    def _sampling_loop(self) -> None:
        while not self._stop_event.wait(self._interval_sec):
            self._sample_now()

    def _sample_now(self) -> None:
        try:
            sample = self._sample_reader()
        except Exception as exc:  # keep diagnostics from failing product work
            sample = ProcessMemorySample(
                rss_bytes=None,
                private_bytes=None,
                availability="failed",
                reason=f"process memory sampling failed: {type(exc).__name__}",
            )
        with self._lock:
            self._peak.include(sample)
            for scope in self._active_scopes:
                scope.include(sample)


def _default_sample_reader() -> tuple[Callable[[], ProcessMemorySample], bool]:
    if os.name != "nt":
        return (
            lambda: ProcessMemorySample(
                rss_bytes=None,
                private_bytes=None,
                availability="unavailable",
                reason="Win32 process-tree memory sampling is unavailable",
            ),
            False,
        )
    try:
        api = _WindowsProcessMemoryApi()
    except Exception as exc:
        reason = (
            "Win32 process-tree sampler initialization failed: "
            f"{type(exc).__name__}"
        )
        return (
            lambda: ProcessMemorySample(
                rss_bytes=None,
                private_bytes=None,
                availability="failed",
                reason=reason,
            ),
            False,
        )
    root_pid = os.getpid()
    return lambda: api.read_tree(root_pid), True


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _WindowsProcessMemoryApi:
    _kernel32: ctypes.CDLL
    _psapi: ctypes.CDLL
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Win32 process memory APIs are unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = ctypes.c_void_p
        dword = ctypes.c_ulong
        bool_t = ctypes.c_int

        kernel32.CreateToolhelp32Snapshot.argtypes = (dword, dword)
        kernel32.CreateToolhelp32Snapshot.restype = handle
        kernel32.Process32FirstW.argtypes = (
            handle,
            ctypes.POINTER(_ProcessEntry32W),
        )
        kernel32.Process32FirstW.restype = bool_t
        kernel32.Process32NextW.argtypes = (
            handle,
            ctypes.POINTER(_ProcessEntry32W),
        )
        kernel32.Process32NextW.restype = bool_t
        kernel32.OpenProcess.argtypes = (dword, bool_t, dword)
        kernel32.OpenProcess.restype = handle
        kernel32.CloseHandle.argtypes = (handle,)
        kernel32.CloseHandle.restype = bool_t
        psapi.GetProcessMemoryInfo.argtypes = (
            handle,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            dword,
        )
        psapi.GetProcessMemoryInfo.restype = bool_t
        self._kernel32 = kernel32
        self._psapi = psapi

    def read_tree(self, root_pid: int) -> ProcessMemorySample:
        process_ids = self._descendant_process_ids(root_pid)
        rss_bytes = 0
        private_bytes = 0
        measured = 0
        skipped = 0
        for process_id in process_ids:
            handle = self._kernel32.OpenProcess(
                self._PROCESS_QUERY_INFORMATION | self._PROCESS_VM_READ,
                False,
                process_id,
            )
            if not handle:
                skipped += 1
                continue
            try:
                counters = _ProcessMemoryCountersEx()
                counters.cb = ctypes.sizeof(counters)
                if not self._psapi.GetProcessMemoryInfo(
                    handle,
                    ctypes.byref(counters),
                    counters.cb,
                ):
                    skipped += 1
                    continue
                rss_bytes += int(counters.WorkingSetSize)
                private_bytes += int(counters.PrivateUsage)
                measured += 1
            finally:
                self._kernel32.CloseHandle(handle)
        if measured == 0:
            return ProcessMemorySample(
                rss_bytes=None,
                private_bytes=None,
                availability="unavailable",
                reason="no process-tree memory counters were readable",
            )
        return ProcessMemorySample(
            rss_bytes=rss_bytes,
            private_bytes=private_bytes,
            availability="partial" if skipped else "measured",
            reason=(
                f"{skipped} process-tree members were unreadable" if skipped else ""
            ),
        )

    def _descendant_process_ids(self, root_pid: int) -> set[int]:
        snapshot = self._kernel32.CreateToolhelp32Snapshot(
            self._TH32CS_SNAPPROCESS,
            0,
        )
        if snapshot == self._INVALID_HANDLE_VALUE:
            error: int | None = None
            if sys.platform == "win32":
                error = ctypes.get_last_error()
            raise OSError(error, "CreateToolhelp32Snapshot failed")
        parents: dict[int, int] = {}
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    if not self._kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            self._kernel32.CloseHandle(snapshot)
        process_ids = {root_pid}
        changed = True
        while changed:
            changed = False
            for process_id, parent_id in parents.items():
                if parent_id in process_ids and process_id not in process_ids:
                    process_ids.add(process_id)
                    changed = True
        return process_ids


__all__ = (
    "ProcessMemoryCollector",
    "ProcessMemoryPeak",
    "ProcessMemorySample",
    "ProcessTreeMemorySampler",
)
