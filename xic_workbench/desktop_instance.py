"""One desktop server for each configuration and explicit launch intent."""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from xic_extractor.configuration.atomic_write import atomic_write_text


class DesktopInstance:
    """Hold an OS lock while serving; another launcher can reopen its URL."""

    def __init__(self, config_dir: Path, defaults: dict[str, str], port: int) -> None:
        intent = json.dumps({"defaults": defaults, "port": port}, sort_keys=True)
        self.key = hashlib.sha256(intent.encode()).hexdigest()
        folder = config_dir.resolve() / ".workbench"
        folder.mkdir(parents=True, exist_ok=True)
        self._record = folder / f"{self.key}.json"
        self._stream = (folder / f"{self.key}.lock").open("a+b")
        self._stream.seek(0, os.SEEK_END)
        if not self._stream.tell():
            self._stream.write(b"\0")
            self._stream.flush()
        self._owned = False
        self.url: str | None = None

    def __enter__(self) -> DesktopInstance:
        deadline = time.monotonic() + 15
        try:
            while True:
                self._stream.seek(0)
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(
                            self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                else:
                    self._owned = True
                    return self
                self.url = self._running_url()
                if self.url:
                    return self
                if time.monotonic() >= deadline:
                    raise RuntimeError("工作區仍在啟動或結束中，請稍後再開啟。")
                time.sleep(0.1)
        except BaseException:
            self._stream.close()
            raise

    def _running_url(self) -> str | None:
        try:
            record = json.loads(self._record.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            # The lock owner may still be starting or replacing an old record.
            return None
        url = record.get("url") if isinstance(record, dict) else None
        if not isinstance(url, str):
            return None
        address = urlsplit(url)
        if (
            address.scheme != "http"
            or address.hostname != "127.0.0.1"
            or address.username is not None
            or address.password is not None
            or address.path != "/"
            or address.query
            or address.fragment
        ):
            return None
        try:
            port = address.port
        except ValueError:
            return None
        if port is None:
            return None
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/api/session")
            response = connection.getresponse()
            data = json.loads(response.read())
            if response.status == 200 and data.get("desktop_key") == self.key:
                return url
        except (OSError, http.client.HTTPException, ValueError):
            # A previous server can exit between reading the record and connecting.
            return None
        finally:
            connection.close()
        return None

    def publish(self, url: str) -> None:
        atomic_write_text(self._record, json.dumps({"url": url}))

    def __exit__(self, *exc) -> None:
        try:
            if self._owned:
                self._record.unlink(missing_ok=True)
        finally:
            # Closing releases the OS lock even after an interrupted startup.
            self._stream.close()
