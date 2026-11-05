"""Helpers shared by test_workbench_lifecycle and other test modules."""

import http.client
import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


def _request(url, path, values=None):
    address = urlsplit(url)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        assert response.status == 200
        headers = {"Cookie": cookie, "X-XIC-Client": "workbench"}
        if values is not None:
            headers["Content-Type"] = "application/json"
        connection.request(
            "GET" if values is None else "POST",
            path,
            None if values is None else json.dumps(values),
            headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@contextmanager
def _launch(config, *arguments):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "xic_workbench",
            "--no-open",
            "--config-dir",
            str(config),
            *arguments,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    messages = queue.Queue()

    def read_output():
        for line in process.stdout:
            messages.put(line)
        messages.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    url = None
    output = []
    try:
        while url is None:
            line = messages.get(timeout=20)
            assert line is not None, "Workbench exited without a URL: " + "".join(
                output
            )
            output.append(line)
            if line.startswith("XIC Workbench: "):
                url = line.split(": ", 1)[1].strip()
        yield process, url
    finally:
        if url and process.poll() is None:
            _request(url, "/api/shutdown", {})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()
