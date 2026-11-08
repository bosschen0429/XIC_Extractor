"""Resource lifetime of the shared HTTP test client, including setup failures."""

import http.client
import threading

import pytest

from tests.workbench_config_fixtures import ConfigurationWorkspace
from tests.workbench_http import workbench_client
from xic_workbench import server as server_module


def test_session_handshake_failure_closes_client_server_and_workspace(
    tmp_path, monkeypatch
):
    workspace = ConfigurationWorkspace(config_dir=tmp_path)
    workspace_closed = threading.Event()
    server_stopped = threading.Event()
    servers = []
    connections = []
    original_close = workspace.close
    original_server = server_module.create_server

    def close_workspace():
        original_close()
        workspace_closed.set()

    def create_server(*args, **kwargs):
        server = original_server(*args, **kwargs)
        serve = server.serve_forever

        def serve_until_closed():
            try:
                serve()
            finally:
                server_stopped.set()

        monkeypatch.setattr(server, "serve_forever", serve_until_closed)
        servers.append(server)
        return server

    class FailedHandshakeConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            connections.append(self)

        def getresponse(self):
            response = super().getresponse()
            response.read()
            response.close()
            raise OSError("session handshake failed")

    monkeypatch.setattr(workspace, "close", close_workspace)
    monkeypatch.setattr(server_module, "create_server", create_server)
    monkeypatch.setattr(http.client, "HTTPConnection", FailedHandshakeConnection)
    try:
        with pytest.raises(OSError, match="session handshake failed"):
            with workbench_client(workspace):
                pytest.fail("failed setup must not yield a request client")
        observed = {
            "client_closed": connections[0].sock is None,
            "listener_closed": servers[0].socket.fileno() == -1,
            "server_stopped": server_stopped.is_set(),
            "workspace_closed": workspace_closed.is_set(),
        }
    finally:
        # The regression must leave no server behind even against the broken helper.
        for connection in connections:
            connection.close()
        for server in servers:
            server.shutdown()
            server.server_close()
        server_stopped.wait(5)
        original_close()

    assert observed == dict.fromkeys(observed, True)
