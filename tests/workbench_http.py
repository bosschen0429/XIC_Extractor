"""Loopback HTTP sessions for tests of the real Workbench server."""

import http.client
import json
import threading
from contextlib import contextmanager


@contextmanager
def workbench_client(workspace):
    """Yield requests returning (status, JSON-or-bytes payload, headers).

    This context owns the workspace and closes it even if the session handshake fails.
    """
    from xic_workbench.server import create_server

    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()

        def request(path, values=None, *, headers=None):
            body = json.dumps(values).encode() if values is not None else None
            actual_headers = {"Cookie": cookie, "X-XIC-Client": "workbench"}
            if body is not None:
                actual_headers["Content-Type"] = "application/json"
            actual_headers.update(headers or {})
            connection.request(
                "POST" if body is not None else "GET", path, body, actual_headers
            )
            result = connection.getresponse()
            data = result.read()
            if result.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data)
            return result.status, data, dict(result.getheaders())

        yield request
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        workspace.close()
