"""Browser evidence retained according to the complete pytest outcome."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest


def _resource_url(value: str) -> str:
    """Identify the resource without userinfo, query values, or fragments."""
    parts = urlsplit(value)
    return urlunsplit(
        (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
    )


@dataclass
class BrowserDiagnostics:
    test: str
    directory: Path
    phase: str = "setup"
    events: list[dict] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def record(self, kind: str, **details) -> None:
        self.events.append(
            {
                "kind": kind,
                "phase": self.phase,
                "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                **details,
            }
        )

    def attach(self, page, errors: list[str]) -> None:
        def console(message):
            if message.type == "error":
                errors.append(message.text)
                location = dict(message.location)
                location["url"] = _resource_url(location.get("url", ""))
                self.record("console", text=message.text, location=location)

        def pageerror(error):
            errors.append(str(error))
            self.record("pageerror", text=str(error))

        # Keep all transport failures as evidence, including intentional navigation
        # cancellations. Console errors/page exceptions remain the failure gate.
        page.context.on(
            "requestfailed",
            lambda request: self.record(
                "requestfailed",
                url=_resource_url(request.url),
                method=request.method,
                resource_type=request.resource_type,
                failure=request.failure,
            ),
        )
        page.on("console", console)
        page.on("pageerror", pageerror)


_DIAGNOSTICS = pytest.StashKey[BrowserDiagnostics]()


@pytest.fixture
def browser_diagnostics(request):
    root = Path(
        os.environ.get(
            "XIC_BROWSER_ARTIFACT_DIR",
            str(Path(__file__).resolve().parents[1] / "output" / "browser-failures"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    prefix = re.sub(r"\W", "_", request.node.name)[:70] + "-"
    diagnostics = BrowserDiagnostics(
        request.node.nodeid, Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    )
    request.node.stash[_DIAGNOSTICS] = diagnostics
    return diagnostics


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    diagnostics = item.stash.get(_DIAGNOSTICS, None)
    if diagnostics is not None:
        diagnostics.outcomes[report.when] = report.outcome
        if report.when == "teardown":
            if "failed" in diagnostics.outcomes.values():
                path = diagnostics.directory / "browser-events.json"
                path.write_text(
                    json.dumps(
                        {
                            "test": diagnostics.test,
                            "outcomes": diagnostics.outcomes,
                            "events": diagnostics.events,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                report.sections.append(("Browser evidence", str(diagnostics.directory)))
            else:
                # Only our unique directory, after every fixture has finished.
                shutil.rmtree(diagnostics.directory)
        else:
            diagnostics.phase = (
                "call" if report.when == "setup" and report.passed else "teardown"
            )
    return report
